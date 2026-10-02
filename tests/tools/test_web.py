import httpx
import pytest

from coding_agent.services.approvals import ApprovalPolicy, Risk
from coding_agent.tools.filesystem import ToolError
from coding_agent.tools.web import is_public_ip, make_web_tools

PUBLIC = "93.184.216.34"


def resolver_for(mapping):
    async def resolve(host):
        if host not in mapping:
            raise OSError("unknown host")
        return mapping[host]

    return resolve


def handler(routes):
    def handle(request: httpx.Request) -> httpx.Response:
        key = f"{request.url.host}{request.url.path}"
        if key not in routes:
            return httpx.Response(404)
        status, headers, body = routes[key]
        return httpx.Response(status, headers=headers, content=body)

    return httpx.MockTransport(handle)


def tools(settings, routes=None, hosts=None, **overrides):
    s = settings.with_overrides(**overrides) if overrides else settings
    made = make_web_tools(s, transport=handler(routes or {}), resolver=resolver_for(hosts or {}))
    return {t.name: t for t in made}


HTML = (
    b"<html><head><title>Docs</title><script>alert(1)</script></head><body><h1>Install</h1><p>Run <code>uv add x</code>.</p></body></html>"
)


async def test_fetch_converts_html(settings):
    t = tools(
        settings, {"docs.example.com/install": (200, {"content-type": "text/html; charset=utf-8"}, HTML)}, {"docs.example.com": [PUBLIC]}
    )
    result = await t["fetch_url"].ainvoke({"url": "https://docs.example.com/install"})
    assert result.startswith("Untrusted web content from docs.example.com")
    assert "# Install" in result and "uv add x" in result
    assert "alert(1)" not in result


async def test_fetch_truncates_and_refuses_binary(settings):
    big = b"x" * 50_000
    routes = {
        "a.example.com/big": (200, {"content-type": "text/plain"}, big),
        "a.example.com/img": (200, {"content-type": "image/png"}, b"\x89PNG"),
        "a.example.com/gone": (404, {}, b""),
    }
    t = tools(settings, routes, {"a.example.com": [PUBLIC]})
    result = await t["fetch_url"].ainvoke({"url": "https://a.example.com/big", "max_chars": 1000})
    assert "characters truncated" in result and len(result) < 1500
    with pytest.raises(ToolError, match="not a text page"):
        await t["fetch_url"].ainvoke({"url": "https://a.example.com/img"})
    with pytest.raises(ToolError, match="HTTP 404"):
        await t["fetch_url"].ainvoke({"url": "https://a.example.com/gone"})


@pytest.mark.parametrize(
    "url,hosts",
    [
        ("http://127.0.0.1/admin", {}),
        ("http://169.254.169.254/latest/meta-data", {}),
        ("http://[::1]/", {}),
        ("http://intranet.corp/", {"intranet.corp": ["10.0.0.5"]}),
        ("http://sneaky.example.com/", {"sneaky.example.com": [PUBLIC, "192.168.1.10"]}),
        ("file:///etc/passwd", {}),
    ],
)
async def test_ssrf_is_blocked(settings, url, hosts):
    t = tools(settings, {}, hosts)
    with pytest.raises(ToolError, match="private or local|Only http"):
        await t["fetch_url"].ainvoke({"url": url})


async def test_redirect_to_private_address_is_blocked(settings):
    routes = {"public.example.com/r": (302, {"location": "http://127.0.0.1:8765/health"}, b"")}
    t = tools(settings, routes, {"public.example.com": [PUBLIC]})
    with pytest.raises(ToolError, match="private or local"):
        await t["fetch_url"].ainvoke({"url": "https://public.example.com/r"})


async def test_redirects_are_followed(settings):
    routes = {
        "a.example.com/old": (301, {"location": "/new"}, b""),
        "a.example.com/new": (200, {"content-type": "text/plain"}, b"moved here"),
    }
    t = tools(settings, routes, {"a.example.com": [PUBLIC]})
    assert "moved here" in await t["fetch_url"].ainvoke({"url": "https://a.example.com/old"})


async def test_allow_private_opt_in(settings):
    t = tools(settings, {"127.0.0.1/": (200, {"content-type": "text/plain"}, b"local ok")}, {}, web_allow_private=True)
    assert "local ok" in await t["fetch_url"].ainvoke({"url": "http://127.0.0.1/"})


def test_is_public_ip():
    assert is_public_ip(PUBLIC) and is_public_ip("2606:4700:4700::1111")
    for ip in ["127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.0.1", "169.254.169.254", "::1", "fc00::1", "::ffff:127.0.0.1", "224.0.0.1"]:
        assert not is_public_ip(ip), ip


def test_search_tool_only_with_a_key(settings):
    assert set(tools(settings)) == {"fetch_url"}
    assert set(tools(settings, tavily_api_key="k")) == {"fetch_url", "web_search"}


async def test_search_tavily(settings):
    body = b'{"results": [{"title": "LangGraph docs", "url": "https://langchain.com/langgraph", "content": "Build   agents"}]}'
    t = tools(settings, {"api.tavily.com/search": (200, {"content-type": "application/json"}, body)}, tavily_api_key="k")
    result = await t["web_search"].ainvoke({"query": "langgraph interrupt"})
    assert "Untrusted search results (tavily)" in result
    assert "1. LangGraph docs\n   https://langchain.com/langgraph\n   Build agents" in result


async def test_search_brave(settings):
    body = b'{"web": {"results": [{"title": "T", "url": "https://x.dev", "description": "D"}]}}'
    t = tools(settings, {"api.search.brave.com/res/v1/web/search": (200, {}, body)}, brave_api_key="k")
    assert "1. T\n   https://x.dev\n   D" in await t["web_search"].ainvoke({"query": "q"})


def test_web_policy(settings, workspace):
    policy = ApprovalPolicy(settings, workspace)
    decision = policy.evaluate("fetch_url", {"url": "https://docs.python.org/3/"})
    assert decision.risk is Risk.SENSITIVE and "docs.python.org" in decision.reason
    assert policy.rule_key("fetch_url", {"url": "https://docs.python.org/3/x"}) == "fetch_url:docs.python.org"
    assert policy.evaluate("web_search", {"query": "x"}).risk is Risk.SENSITIVE
    assert policy.rule_key("web_search", {"query": "x"}) == "web_search"
