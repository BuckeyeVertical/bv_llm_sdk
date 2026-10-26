"""bv-agent: talk to the drone (or another robot, with --toolkit).

    uv run --extra agent bv-agent                       # chat (Ollama via bv_llm_host)
    uv run --extra agent bv-agent --model qwen3:32b
    uv run --extra agent --extra claude bv-agent --backend anthropic
    uv run --extra agent bv-agent --once "take off to 10 m and land"

Settings also come from BV_AGENT_* environment variables (see config.py).
Ctrl-C while the agent is working stops it and makes the robot safe
(the drone holds position).
"""

from __future__ import annotations

import argparse
import json
import sys

from ..registry import ToolError
from ..toolkits import TOOLKITS, toolkit_class
from .agent import Agent
from .config import AgentConfig
from .events import AgentEvent
from .models import ModelError, make_model

HELP = """commands: /tools  /reset  /help  /quit{extra}
anything else is an instruction for the {name}."""


def _help(agent: Agent) -> str:
    extra = "".join(f"  {c}" for c in agent.toolkit.commands)
    return HELP.format(extra=extra, name=agent.toolkit.name)


class Renderer:
    """Prints agent events to the terminal."""

    def __init__(self, verbose: bool = False, color: bool = True, name: str = "drone"):
        self.verbose = verbose
        self.color = color
        self.name = name

    def _c(self, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.color else text

    def __call__(self, event: AgentEvent) -> None:
        d = event.data
        if event.kind == "tool_call":
            args = ", ".join(f"{k}={json.dumps(v)}" for k, v in d["args"].items())
            print(self._c("36", f"  → {d['name']}({args})"), flush=True)
        elif event.kind == "tool_result":
            result = d["result"]
            if result.get("ok", True):
                shown = json.dumps(result) if self.verbose else _short(result)
                print(self._c("2", f"    ✓ {d['seconds']}s  {shown}"), flush=True)
            else:
                print(self._c("31", f"    ✗ {d['seconds']}s  {result.get('error')}"), flush=True)
        elif event.kind == "assistant_text" and self.verbose:
            print(self._c("2", f"  (model) {d['text']}"), flush=True)
        elif event.kind == "done":
            print(self._c("1", f"{self.name}> {d['text']}"), flush=True)
        elif event.kind == "error":
            print(self._c("31", f"{self.name}> {d['text']}"), flush=True)


def _short(result: dict, limit: int = 110) -> str:
    """A one-line summary of a tool result."""
    keep = {k: v for k, v in result.items() if k not in ("ok", "latitude", "longitude",
                                                          "altitude_amsl_m")}
    for key in ("message", "found", "count", "counts"):
        if key in keep:
            text = f"{key}: {json.dumps(keep[key])}"
            break
    else:
        text = json.dumps(keep)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bv-agent", description="Drive a robot in plain language.")
    p.add_argument("--toolkit", choices=sorted(TOOLKITS), help="which robot (default: drone)")
    p.add_argument("--backend", choices=["ollama", "openai", "anthropic"])
    p.add_argument("--model", help="e.g. gpt-oss:20b, qwen3:32b, claude-sonnet-5-5")
    p.add_argument("--base-url", help="model server (default: Ollama on localhost:11434)")
    p.add_argument("--toolset", help="core, standard, full, or a comma list of tools")
    p.add_argument("--num-ctx", type=int, help="Ollama context window in tokens")
    p.add_argument("--think", help="reasoning: true/false (Qwen-style) or low/medium/high "
                                   "(gpt-oss); Ollama and vLLM")
    p.add_argument("--vision", action="store_true", default=None,
                   help="the model accepts images (needed for `look` on vLLM/OpenAI servers)")
    p.add_argument("--max-steps", type=int, help="model calls allowed per request")
    p.add_argument("--no-state", action="store_true", help="do not send the robot state line")
    p.add_argument("--once", metavar="REQUEST", help="run one request and exit")
    p.add_argument("-v", "--verbose", action="store_true", help="show full tool results")
    return p


def config_from_args(args: argparse.Namespace) -> AgentConfig:
    think = args.think
    if think is not None and think.lower() in ("true", "false"):
        think = think.lower() == "true"
    return AgentConfig.from_env(
        toolkit=args.toolkit, backend=args.backend, model=args.model, base_url=args.base_url, toolset=args.toolset,
        num_ctx=args.num_ctx, think=think, max_steps=args.max_steps, vision=args.vision,
        state_summary=False if args.no_state else None,
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = config_from_args(args)
        model = make_model(config)
    except (ValueError, ModelError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        toolkit = toolkit_class(config.toolkit).connect()
    except (ToolError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    render = Renderer(verbose=args.verbose, color=sys.stdout.isatty(), name=toolkit.name)
    with toolkit:
        agent = Agent(toolkit, model, config, on_event=render)
        if args.once:
            return _run(agent, args.once)
        print(f"bv-agent: {toolkit.name}, {config.backend} {config.model}, "
              f"{len(agent.tool_names)} tools ({config.toolset}). {_help(agent)}")
        while True:
            try:
                line = input("you> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
            if not line:
                continue
            if line.startswith("/"):
                if _command(line, agent) == "quit":
                    return 0
                continue
            _run(agent, line)


def _run(agent: Agent, text: str) -> int:
    try:
        agent.run(text)
        return 0
    except ModelError as exc:
        print(f"model error: {exc}", file=sys.stderr)
        return 1


def _command(line: str, agent: Agent) -> str | None:
    cmd = line.split()[0].lower()
    if cmd in ("/quit", "/exit", "/q"):
        return "quit"
    if cmd == "/tools":
        print(", ".join(agent.tool_names))
    elif cmd in agent.toolkit.commands:
        result = agent.toolkit.registry.call(agent.toolkit.robot, agent.toolkit.commands[cmd])
        print(json.dumps(result, indent=2))
    elif cmd == "/reset":
        agent.reset()
        print("conversation cleared")
    else:
        print(_help(agent))
    return None


if __name__ == "__main__":
    sys.exit(main())
