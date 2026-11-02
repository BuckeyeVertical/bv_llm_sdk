"""Chat with a robot through the agent loop."""

import argparse
import sys

from bv_llm_sdk.agent import Agent, AgentConfig, make_model
from bv_llm_sdk.agent.cli import Renderer
from bv_llm_sdk.toolkits import TOOLKITS, toolkit_class


def chat(agent: Agent) -> None:
    print("Ctrl+C cancels a request. Type quit to exit.")
    while True:
        try:
            text = input("you> ").strip()
        except EOFError:
            print()
            return
        except KeyboardInterrupt:
            print("\nType quit to exit.")
            continue
        if text in ("quit", "exit"):
            return
        if text:
            try:
                agent.run(text)
            except KeyboardInterrupt:
                print("\ncancelled", file=sys.stderr)
                try:
                    agent.toolkit.stop()
                except KeyboardInterrupt:
                    pass
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
