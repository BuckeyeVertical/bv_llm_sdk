"""An LLM agent that drives a robot (the drone, by default) with its tools.

    from bv_llm_sdk.agent import Agent, AgentConfig, make_model
    from bv_llm_sdk.toolkits.drone import DroneToolkit

    config = AgentConfig(backend="ollama", model="qwen3-coder:30b")
    with DroneToolkit.connect() as drone:
        agent = Agent(drone, make_model(config), config, on_event=print)
        print(agent.run("take off to 10 m, fly a square, then land"))

Or from the shell: ``uv run --extra agent bv-agent``.
"""

from .agent import Agent
from .config import AgentConfig
from .events import AgentEvent
from .models import ChatModel, ModelError, make_model
from .toolsets import toolset

__all__ = [
    "Agent", "AgentConfig", "AgentEvent", "ChatModel", "ModelError", "make_model",
    "toolset",
]
