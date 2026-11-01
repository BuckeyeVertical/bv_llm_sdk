"""Build the system prompt from the toolkit prompt and the general agent rules."""

from __future__ import annotations

from ..toolkit import Toolkit

# TODO: write the rules any tool-using agent should follow, whatever the robot.
# Cover:
#
#   - Its job: act by calling tools, then say briefly what happened.
#   - Failed tools: what to do when a tool returns an error.
#   - The state line at the start of each request.
#   - Finishing the whole request, and what to refuse.
#   - How to answer: short, and only what the tool results say.
AGENT_RULES = """\
Use the tools to do what the user asks.
"""


def system_prompt(toolkit: Toolkit) -> str:
    """Return the full system prompt for this toolkit."""
    return f"{toolkit.prompt.rstrip()}\n\n{AGENT_RULES}"
