"""Chat with a robot through the agent loop."""

import argparse
import sys

from bv_llm_sdk.agent import Agent, AgentConfig, make_model
from bv_llm_sdk.agent.cli import Renderer
from bv_llm_sdk.toolkits import TOOLKITS, toolkit_class


def chat(agent: Agent) -> None:
    while True:
        try:
            text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if text in ("quit", "exit"):
            return
        if text:
            try:
                agent.run(text)
            except RuntimeError as exc:
                print(f"error: {exc}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description="Chat with a robot.")
    parser.add_argument("--toolkit", choices=sorted(TOOLKITS), default="drone")
    toolkit_name = parser.parse_args().toolkit
    try:
        config = AgentConfig.from_env(toolkit=toolkit_name)
        with toolkit_class(toolkit_name).connect() as toolkit:
            render = Renderer(name=toolkit.name)
            chat(Agent(toolkit, make_model(config), config, on_event=render))
    except (RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
