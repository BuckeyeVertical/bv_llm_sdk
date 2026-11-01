# Your task

Your job is to fill in the three
TODOs that turn them into an agent.

| # | TODO | File | Robot |
| --- | --- | --- | --- |
| 1 | The agent loop | `src/bv_llm_sdk/agent/agent.py` | Both |
| 2 | The agent rules | `src/bv_llm_sdk/agent/prompt.py` | Both |
| 3 | The robot prompt | `src/bv_llm_sdk/toolkits/drone/toolkit.py` | Drone |
| 3 | The robot prompt | `src/bv_llm_sdk/toolkits/arm/toolkit.py` | Arm |

Start with TODO 1. The chat cannot do anything until the loop works. Each TODO in the code
has more detail.

## 1. The agent loop

Find this in `_loop`:

```python
failures: dict[str, int] = {}  # Failure counts for this request.

# TODO: your loop here.
raise NotImplementedError("write the agent loop in Agent._loop (agent/agent.py)")
```

Replace the TODO and the `raise` with a loop that asks the model for tools, runs them,
gives the results back, and repeats until the model replies with no tool calls. The
pseudocode is in the docstring above it.

| Name | What it is |
| --- | --- |
| `text` | What the user typed. |
| `failures` | Failure counts. Pass it to `self._is_stuck`. |
| `self.transcript` | The conversation so far. A list you append to. |
| `self.config.max_steps` | How many times the model can be called for one request. |
| `self._user_message(text)` | Turns `text` into a message for the transcript. |
| `self._ask_model()` | Calls the model and returns its reply. |
| `reply.text` | The words in the reply. |
| `reply.tool_calls` | The tools the model wants to run. Empty when it is finished. |
| `self._execute(call)` | Runs one tool call and returns its result. |
| `call.name` | The name of the tool. |
| `result.content` | The dict the tool returned. A failure has an `"error"` key. |
| `self._is_stuck(call, result, failures)` | True when the same call keeps failing. |
| `self._finish(text)` | Ends the request with your own message and returns it. |

Check it:

```bash
uv run pytest tests/test_agent.py
```

## 2. The agent rules

Find this in `prompt.py`:

```python
AGENT_RULES = """\
Use the tools to do what the user asks.
"""
```

Rewrite it to say how any tool-using agent should behave, whatever the robot. The TODO above
it lists what to cover. A good set of rules is short and tells the model exactly what to do
in situations it will really hit, like a tool failing, without naming any robot or tool.

Check it by chatting with your robot and asking for something with several steps:

```bash
uv run scripts/chat.py --toolkit drone
```

For the arm, use `--toolkit arm`.

## 3. The robot prompt

Do the section for your robot. Each `PROMPT` should say what the robot is, how its world
works, and the rules for its tools. The TODO above it lists what to cover.

### Drone

Find this in `toolkits/drone/toolkit.py`:

```python
PROMPT = """\
You are a drone pilot.
"""
```

The model already reads every tool's description, so do not repeat those. A good drone prompt
says what the tools cannot, like when to stay hovering and what never to do, with a clear
rule for each mistake you see it make.

Check it with the evals. Keep the simulator running, read each failure, and add a rule for
the mistake you see:

```bash
uv run scripts/agent_eval.py --tags quick
```

### Arm

Find this in `toolkits/arm/toolkit.py`:

```python
PROMPT = """\
You control a robot arm.
"""
```

The model already reads every tool's description, so do not repeat those. A good arm prompt
says what the tools cannot, like how far the arm reaches and where a drawing should go, with
a clear rule for each mistake you see it make.

Check it with the evals. They use a fake arm, so you do not need the real one. Read each
failure and add a rule for the mistake you see:

```bash
uv run scripts/arm_eval.py
```
