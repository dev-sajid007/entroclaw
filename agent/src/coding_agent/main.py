"""Entry point.

coding-agent serve [--workspace DIR] [--host H] [--port P]   start the HTTP/SSE API (default)
coding-agent run "prompt" [--workspace DIR] [--yes]          one-shot headless run in the terminal
coding-agent eval [--tasks DIR] [--only ID] [--baseline F]   run the evaluation benchmark
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid

from coding_agent.config.settings import Settings
from coding_agent.utils.logging import configure_logging


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    from coding_agent import __version__

    parser = argparse.ArgumentParser(prog="entroclaw-agent", description="Agent runtime for entroclaw")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="start the HTTP/SSE API")
    serve.add_argument("--workspace")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)

    run = sub.add_parser("run", help="run a single task headlessly")
    run.add_argument("prompt")
    run.add_argument("--workspace")
    run.add_argument("--session", help="resume an existing session id")
    run.add_argument("--yes", action="store_true", help="approve sensitive actions automatically (denied actions stay denied)")

    ev = sub.add_parser("eval", help="run the evaluation benchmark")
    ev.add_argument("--tasks", help="directory of task definitions")
    ev.add_argument("--only", action="append", help="run only these task ids")
    ev.add_argument("--baseline", help="previous report to compare against for regressions")
    ev.add_argument("--output", help="where to write the JSON report")

    argv = sys.argv[1:] if argv is None else argv
    if not argv or (argv[0].startswith("-") and argv[0] not in {"-h", "--help", "--version"}):
        argv = ["serve", *argv]
    return parser.parse_args(argv)


def serve(settings: Settings) -> None:
    import uvicorn

    from coding_agent.server.api import create_app

    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level="warning")


async def run_once(settings: Settings, prompt: str, session: str | None, auto_yes: bool) -> int:
    from langchain_core.messages import HumanMessage
    from langgraph.types import Command

    from coding_agent.agent.graph import open_runtime
    from coding_agent.server.events import stream_events

    async with open_runtime(settings) as runtime:
        session_id = session or uuid.uuid4().hex
        config = {"configurable": {"thread_id": session_id}, "recursion_limit": settings.max_iterations * 4 + 10}
        graph_input: object = {"messages": [HumanMessage(prompt)], "iterations": 0}
        print(f"session {session_id}", file=sys.stderr)
        while True:
            pending = None
            async for event in stream_events(runtime.graph, graph_input, config):
                kind = event["type"]
                if kind == "agent_message":
                    print(f"\n{event['content']}")
                elif kind == "tool_start":
                    print(f"→ {event['tool']}({json.dumps(event['args'])[:200]})", file=sys.stderr)
                elif kind == "tool_output":
                    print(event["content"], end="", file=sys.stderr)
                elif kind == "tool_end" and event["status"] != "success":
                    print(f"  [{event['status']}] {event['result'][:300]}", file=sys.stderr)
                elif kind == "approval_required":
                    pending = event
                elif kind == "error":
                    print(f"error: {event['message']}", file=sys.stderr)
                    return 1
            if pending is None:
                return 0
            approved = auto_yes or ask_approval(pending)
            graph_input = Command(resume={"approved": approved})


def ask_approval(event: dict) -> bool:
    print(f"\n── Approval required ({event['risk']}): {event['action']} — {event['reason']}", file=sys.stderr)
    if event.get("diff"):
        print(event["diff"], file=sys.stderr)
    else:
        print(json.dumps(event["arguments"], indent=2)[:2000], file=sys.stderr)
    if not sys.stdin.isatty():
        print("stdin is not a terminal; rejecting.", file=sys.stderr)
        return False
    return input("Allow? [y/N] ").strip().lower() in {"y", "yes"}


def main(argv: list[str] | None = None) -> None:
    from coding_agent.agent.llm import ConfigurationError, build_model

    args = parse_args(argv)
    # A headless run prints its own progress; structured logs are only noise there unless asked for.
    configure_logging(None if os.environ.get("LOG_LEVEL") or args.command != "run" else "WARNING")
    try:
        if args.command == "eval":
            from coding_agent.evals.runner import main as eval_main

            sys.exit(eval_main(args))
        settings = Settings.from_env(workspace=args.workspace)
        build_model(settings)  # fail fast on missing credentials before starting anything
        if args.command == "serve":
            serve(settings.with_overrides(host=args.host, port=args.port))
        elif args.command == "run":
            sys.exit(asyncio.run(run_once(settings, args.prompt, args.session, args.yes)))
    except (ConfigurationError, ValueError) as exc:
        print(f"entroclaw-agent: {exc}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
