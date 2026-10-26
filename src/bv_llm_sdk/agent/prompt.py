"""What the model is told: the robot's prompt plus the agent's own rules.

The system prompt has two halves. The toolkit's ``prompt`` says what the
robot is and how its world works (see ``toolkits/drone/``). ``AGENT_RULES``
below is how any tool-using agent should behave, whatever the robot.
"""

from __future__ import annotations

from ..toolkit import Toolkit

AGENT_RULES = """\
How you work:
- People give you instructions in plain language; you carry them out by \
calling tools, then tell them briefly what happened.
- Every tool blocks until it is finished, so calls happen one after another.
- A tool that fails returns {"ok": false, "error": "..."}. Read the error and \
fix the problem (for example take off first) or explain it; do not repeat the \
same failing call.
- A [... state] line at the start of a request, when present, tells you where \
things stood.
- Break multi-step requests into tool calls in order. Do not stop halfway; \
finish the whole request unless something fails.
- Stay safe: keep within the limits the tools enforce, and if a request is \
dangerous or impossible, say so instead of trying.
- Keep your final reply short: what you did and anything the user should know \
(positions found, counts, problems). Report what the tool results say, not \
what you expected; if a result shows something went wrong, say so.
"""


def system_prompt(toolkit: Toolkit) -> str:
    """The full system prompt for an agent driving this toolkit."""
    return f"{toolkit.prompt.rstrip()}\n\n{AGENT_RULES}"
