"""Build the system prompt from the toolkit prompt and the general agent rules."""

from __future__ import annotations

from ..toolkit import Toolkit

AGENT_RULES = """\
Carry out the user's request by calling the available tools. Complete every requested
step before replying. Use the state line at the start of each request as the current
robot state, and use tool results to track subsequent changes.
If a tool fails, read its error and correct the cause before retrying. Do not repeat an
identical failed call or claim it succeeded. If you cannot complete the request safely
with the available tools, explain the limitation and what remains undone.
Refuse actions outside the robot's capabilities or that bypass its limits.
Finish with a short reply describing only what the tool results support.
"""


def system_prompt(toolkit: Toolkit) -> str:
    """Return the full system prompt for this toolkit."""
    return f"{toolkit.prompt.rstrip()}\n\n{AGENT_RULES}"
