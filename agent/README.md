# coding-agent (Python runtime)

LangGraph agent, tools, policy layer and HTTP/SSE API. See the [project README](../README.md).

```bash
uv run coding-agent serve            # API on 127.0.0.1:8765
uv run coding-agent run "prompt"     # one-shot headless run
uv run coding-agent eval             # benchmark (needs OPENAI_API_KEY)
uv run pytest                        # tests (../tests)
```
