"""What the model is told: the robot's prompt plus the agent's own rules.

The system prompt has two halves. The toolkit's ``prompt`` says what the
robot is and how its world works (see ``toolkits/drone/``). ``AGENT_RULES``
below is how any tool-using agent should behave, whatever the robot.
"""

from __future__ import annotations

from ..toolkit import Toolkit

# TODO: write the agent's rules: how any tool-using agent should behave,
# whatever robot it drives. (What the drone is and how its world works is the
# other half of the prompt, PROMPT in toolkits/drone/toolkit.py.)
#
# The one-liner below runs, but the model will quit halfway through
# multi-step requests, retry the same failing call forever, and report what it
# expected instead of what happened. Tell it:
#
#   1. Its job: people ask in plain language; it acts by calling tools, then
#      says briefly what happened.
#   2. How tools behave: each blocks until done, so calls happen in order; a
#      failed tool returns {"ok": false, "error": "..."}, so read the error and
#      fix the cause or explain it, never repeat the same failing call.
#   3. That a [... state] line at the start of a request says where things stood.
#   4. Finish the whole request in order; refuse dangerous or impossible ones.
#   5. How to answer: short, and only what the tool results actually say.
AGENT_RULES = """\
Use the tools to do what the user asks.
"""


def system_prompt(toolkit: Toolkit) -> str:
    """The full system prompt for an agent driving this toolkit."""
    return f"{toolkit.prompt.rstrip()}\n\n{AGENT_RULES}"
