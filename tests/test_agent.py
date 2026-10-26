"""Agent loop and provider conversion, with a scripted model and no drone."""

import json

import pytest

from bv_llm_sdk.agent import Agent, AgentConfig
from bv_llm_sdk.agent.args import coerce, extract_text_tool_calls, parse_arguments
from bv_llm_sdk.agent.messages import (
    AssistantMessage, ToolCall, ToolResult, UserMessage, MAX_RESULT_CHARS,
)
from bv_llm_sdk.agent.toolsets import toolset
from bv_llm_sdk.toolkits.drone import CORE, STANDARD, DroneToolkit
from bv_llm_sdk.registry import TOOLS, tool_schemas


class FakeModel:
    """Replays scripted replies and records what it was sent."""

    schema_format = "openai"
    supports_vision = False

    def __init__(self, replies):
        self.replies = list(replies)
        self.seen = []

    def complete(self, system, transcript, tools):
        self.seen.append(list(transcript))
        if not self.replies:
            return AssistantMessage(text="(out of script)")
        return self.replies.pop(0)


def call(name, cid="c1", **args):
    return ToolCall(cid, name, args)


class FakeExecutor:
    def __init__(self, results=None):
        self.calls = []
        self.results = results or {}

    def __call__(self, drone, name, args):
        self.calls.append((name, args))
        result = self.results.get(name, {"ok": True, "message": f"{name} done"})
        return dict(result(args) if callable(result) else result)


def make(replies, executor=None, **config):
    config.setdefault("state_summary", False)
    model = FakeModel(replies)
    events = []
    agent = Agent(DroneToolkit(), model, AgentConfig(**config), on_event=events.append,
                  executor=executor or FakeExecutor())
    return agent, model, events


def test_chains_tools_in_order_and_returns_the_final_text():
    ex = FakeExecutor()
    agent, model, events = make([
        AssistantMessage(tool_calls=[call("takeoff", "a", altitude_m=10)]),
        AssistantMessage(tool_calls=[call("move", "b", north_m=20), call("land", "c")]),
        AssistantMessage(text="Done: flew 20 m north and landed."),
    ], ex)
    assert agent.run("take off, go north 20 m, land") == "Done: flew 20 m north and landed."
    assert [c[0] for c in ex.calls] == ["takeoff", "move", "land"]
    kinds = [type(m).__name__ for m in agent.transcript]
    assert kinds == ["UserMessage", "AssistantMessage", "ToolResult", "AssistantMessage",
                     "ToolResult", "ToolResult", "AssistantMessage"]
    assert [e.kind for e in events].count("tool_call") == 3
    assert events[-1].kind == "done"


def test_conversation_persists_across_runs_and_reset_clears_it():
    agent, model, _ = make([AssistantMessage(text="hi"), AssistantMessage(text="again")])
    agent.run("first")
    agent.run("second")
    assert len(model.seen[1]) == 3  # first user, first reply, second user
    agent.reset()
    assert agent.transcript == []


def test_stops_at_max_steps():
    replies = [AssistantMessage(tool_calls=[call("get_status", f"c{i}")]) for i in range(10)]
    agent, _, _ = make(replies, max_steps=3)
    assert "stopped after 3 steps" in agent.run("loop forever")


def test_repeated_identical_failure_stops_the_request():
    ex = FakeExecutor({"move": {"ok": False, "error": "the drone is on the ground"}})
    replies = [AssistantMessage(tool_calls=[call("move", f"c{i}", north_m=5)]) for i in range(10)]
    agent, _, _ = make(replies, ex, max_consecutive_errors=3)
    reply = agent.run("move")
    assert "kept failing" in reply and "on the ground" in reply
    assert len(ex.calls) == 3


def test_unknown_tool_and_tool_outside_toolset_are_reported_not_run():
    ex = FakeExecutor()
    agent, model, _ = make([
        AssistantMessage(tool_calls=[call("fly_to_the_moon"), call("survey_area", "c2")]),
        AssistantMessage(text="ok"),
    ], ex, toolset="core")
    agent.run("go")
    results = [m for m in agent.transcript if isinstance(m, ToolResult)]
    assert all(not r.ok for r in results)
    assert "no tool 'fly_to_the_moon'" in results[0].content["error"]
    assert ex.calls == []


def test_arguments_are_coerced_to_schema_types():
    ex = FakeExecutor()
    agent, _, _ = make([
        AssistantMessage(tool_calls=[call("move", north_m="12.5", up_m="2")]),
        AssistantMessage(text="ok"),
    ], ex)
    agent.run("go")
    assert ex.calls[0] == ("move", {"north_m": 12.5, "up_m": 2.0})


def test_invalid_arguments_go_back_to_the_model_as_an_error():
    agent, _, _ = make([
        AssistantMessage(tool_calls=[ToolCall("x", "move", {"__invalid_arguments__": "bad json"})]),
        AssistantMessage(text="sorry"),
    ])
    agent.run("go")
    result = next(m for m in agent.transcript if isinstance(m, ToolResult))
    assert result.content == {"ok": False, "error": "bad json"}


def test_text_tool_call_fallback():
    ex = FakeExecutor()
    agent, _, _ = make([
        AssistantMessage(text='Sure! ```json\n{"name": "takeoff", "arguments": {"altitude_m": 8}}\n```'),
        AssistantMessage(text="up"),
    ], ex)
    agent.run("take off")
    assert ex.calls == [("takeoff", {"altitude_m": 8.0})]


def test_text_fallback_ignores_plain_json_but_reports_unknown_calls():
    assert extract_text_tool_calls('the result was {"altitude": 3}', {"takeoff"}) == []
    assert extract_text_tool_calls('the person {"name": "Bob"}', {"takeoff"}) == []
    # A call-shaped object with a made-up tool is returned, so the agent can
    # tell the model the tool does not exist (it is never executed).
    calls = extract_text_tool_calls('{"name": "fly_to", "parameters": {"north_m": 1}}', {"takeoff"})
    assert [c.name for c in calls] == ["fly_to"]
    calls = extract_text_tool_calls(
        'first {"name": "takeoff", "parameters": {"altitude_m": 5}} then '
        '{"function": {"name": "land", "arguments": "{}"}}', {"takeoff", "land"})
    assert [(c.name, c.args) for c in calls] == [("takeoff", {"altitude_m": 5}), ("land", {})]


def test_text_fallback_reads_qwen3_coder_xml():
    text = """I'll fly a square.

<function=fly_shape>
<parameter=shape>
square
</parameter>
<parameter=size_m>
12
</parameter>
</function>
</tool_call>"""
    calls = extract_text_tool_calls(text, {"fly_shape"})
    assert [(c.name, c.args) for c in calls] == [("fly_shape", {"shape": "square", "size_m": 12})]
    assert extract_text_tool_calls("<function=get_position>\n</function>", {"get_position"})[0].args == {}
    assert [c.name for c in extract_text_tool_calls("<function=rm_rf></function>", {"takeoff"})] == ["rm_rf"]


def test_parse_arguments_repairs_common_mistakes():
    assert parse_arguments('```json\n{"a": 1,}\n```') == {"a": 1}
    assert parse_arguments("") == {}
    assert parse_arguments({"a": 1}) == {"a": 1}
    with pytest.raises(ValueError):
        parse_arguments("[1, 2]")
    with pytest.raises(ValueError):
        parse_arguments("not json")


def test_coerce_handles_nested_waypoints():
    schema = tool_schemas("anthropic", ["fly_waypoints"])[0]["input_schema"]
    out = coerce({"waypoints": '[{"north_of_home_m": "10", "hold_s": "2"}]', "smooth": "true"},
                 schema)
    assert out == {"waypoints": [{"north_of_home_m": 10.0, "hold_s": 2.0}], "smooth": True}


def test_look_is_only_offered_to_vision_models():
    agent, _, _ = make([])
    assert "look" not in agent.tool_names
    FakeModel.supports_vision = True
    try:
        agent, _, _ = make([])
        assert "look" in agent.tool_names
    finally:
        FakeModel.supports_vision = False


def test_image_results_and_non_vision_note():
    ex = FakeExecutor({"look": {"ok": True, "image_path": "p.jpg", "image_for_model": "p.jpg"}})
    agent, _, _ = make([AssistantMessage(tool_calls=[call("look")]), AssistantMessage(text="ok")],
                       ex, toolset="look,get_status")
    agent.run("look")
    result = next(m for m in agent.transcript if isinstance(m, ToolResult))
    assert result.image_path == "p.jpg" and "image_for_model" not in result.content
    assert "cannot view images" in result.text(vision=False)
    assert "cannot view images" not in result.text(vision=True)


def test_long_results_are_truncated():
    r = ToolResult("c", "survey_area", {"ok": True, "objects": ["x" * 50] * 500})
    assert len(r.text(vision=True)) < MAX_RESULT_CHARS + 50


def test_toolsets():
    assert set(CORE) <= set(STANDARD) <= set(TOOLS)
    drone = DroneToolkit()
    assert toolset("full", drone) == list(TOOLS)
    assert toolset("core", drone) == CORE
    assert toolset("takeoff, land", drone) == ["takeoff", "land"]
    with pytest.raises(ValueError):
        toolset("bogus_tool", drone)
    core_tokens = len(json.dumps(tool_schemas("openai", CORE))) / 4
    assert core_tokens < 2000


# --- provider wire formats -------------------------------------------------------

TRANSCRIPT = [
    UserMessage("look down"),
    AssistantMessage(text="Looking.", tool_calls=[ToolCall("c1", "look", {}),
                                                 ToolCall("c2", "get_status", {})]),
    ToolResult("c1", "look", {"ok": True}, image_path="img.jpg"),
    ToolResult("c2", "get_status", {"ok": True, "armed": True}),
    AssistantMessage(text="I see grass."),
    UserMessage("thanks"),
]


@pytest.fixture
def fake_image(monkeypatch):
    import bv_llm_sdk.agent.models.base as base

    monkeypatch.setattr(base, "_image_for_model", lambda path: b"JPEG")
    for mod in ("ollama", "openai_compat", "anthropic"):
        try:
            m = __import__(f"bv_llm_sdk.agent.models.{mod}", fromlist=["image_b64"])
            monkeypatch.setattr(m, "image_b64", lambda path: "SlBFRw==")
        except ImportError:
            pass


def test_ollama_wire_format(fake_image):
    pytest.importorskip("httpx")
    from bv_llm_sdk.agent.models.ollama import OllamaModel

    model = OllamaModel(AgentConfig())
    model._vision = True
    wire = model.to_wire("SYS", TRANSCRIPT)
    roles = [m["role"] for m in wire]
    assert roles == ["system", "user", "assistant", "tool", "tool", "user", "assistant", "user"]
    assert wire[2]["tool_calls"][0] == {"function": {"name": "look", "arguments": {}}}
    assert wire[3]["tool_name"] == "look"
    assert wire[5]["images"] == ["SlBFRw=="]  # image after the run of tool results
    model._vision = False
    assert not any("images" in m for m in model.to_wire("SYS", TRANSCRIPT))


def test_openai_wire_format(fake_image):
    pytest.importorskip("openai")
    from bv_llm_sdk.agent.models.openai_compat import OpenAICompatModel

    model = OpenAICompatModel(AgentConfig(backend="openai"), supports_vision=True)
    wire = model.to_wire("SYS", TRANSCRIPT)
    assert [m["role"] for m in wire] == ["system", "user", "assistant", "tool", "tool", "user",
                                         "assistant", "user"]
    assert wire[2]["tool_calls"][1]["function"] == {"name": "get_status", "arguments": "{}"}
    assert wire[3]["tool_call_id"] == "c1"
    assert wire[5]["content"][1]["image_url"]["url"] == "data:image/jpeg;base64,SlBFRw=="


def test_anthropic_wire_format(fake_image):
    pytest.importorskip("anthropic")
    from bv_llm_sdk.agent.models.anthropic import AnthropicModel

    model = AnthropicModel(AgentConfig(backend="anthropic", api_key="test"))
    wire = model.to_wire(TRANSCRIPT)
    assert [m["role"] for m in wire] == ["user", "assistant", "user", "assistant", "user"]
    tool_results = wire[2]["content"]
    assert [b["tool_use_id"] for b in tool_results] == ["c1", "c2"]
    assert tool_results[0]["content"][1]["source"]["data"] == "SlBFRw=="
    assert wire[1]["content"][1] == {"type": "tool_use", "id": "c1", "name": "look", "input": {}}


def test_config_from_env(monkeypatch):
    monkeypatch.setenv("BV_AGENT_BACKEND", "anthropic")
    monkeypatch.setenv("BV_AGENT_MAX_STEPS", "7")
    monkeypatch.setenv("BV_AGENT_STATE_SUMMARY", "false")
    monkeypatch.setenv("BV_AGENT_THINK", "low")
    c = AgentConfig.from_env(toolset="core", model=None)
    assert (c.backend, c.model, c.max_steps, c.state_summary, c.think, c.toolset) == (
        "anthropic", "claude-sonnet-5-5", 7, False, "low", "core")


def test_limit_verdict():
    from bv_llm_sdk.actions.routes import _limit_verdict

    assert _limit_verdict(234.5, 100)["within_limit"] is False
    assert "OVER" in _limit_verdict(234.5, 100)["limit_verdict"]
    assert _limit_verdict(60, 100)["within_limit"] is True
    assert _limit_verdict(60, None) == {}


def test_coerce_maps_point_pairs_to_objects():
    schema = tool_schemas("anthropic", ["draw"])[0]["input_schema"]
    out = coerce({"points": [[-4, 0], ["4", 8]]}, schema)
    assert out["points"] == [{"x_m": -4, "y_m": 0}, {"x_m": 4.0, "y_m": 8.0}]


def test_call_tool_never_raises():
    from bv_llm_sdk.registry import call_tool

    result = call_tool(None, "draw", {"points": [{"x": 1, "y": 2}]})
    assert result["ok"] is False and "x_m" in result["error"]


def test_made_up_tool_in_text_gets_an_error_and_a_retry():
    ex = FakeExecutor()
    agent, _, _ = make([
        AssistantMessage(text='{"name": "fly_to", "parameters": {"north_of_home_m": -10}}'),
        AssistantMessage(tool_calls=[call("goto_location", latitude=1, longitude=2)]),
        AssistantMessage(text="done"),
    ], ex)
    assert agent.run("go") == "done"
    first = next(m for m in agent.transcript if isinstance(m, ToolResult))
    assert "no tool 'fly_to'" in first.content["error"]
    assert ex.calls == [("goto_location", {"latitude": 1.0, "longitude": 2.0})]


def test_junk_arguments_on_no_argument_tools_are_dropped():
    ex = FakeExecutor()
    agent, _, _ = make([
        AssistantMessage(tool_calls=[call("get_battery", **{"": "{}"})]),
        AssistantMessage(tool_calls=[ToolCall("c2", "move", {"": 1, "north_m": 5})]),
        AssistantMessage(text="ok"),
    ], ex, toolset="get_battery,move")
    agent.run("go")
    assert ex.calls == [("get_battery", {}), ("move", {"north_m": 5.0})]


def test_openai_thinking_options():
    pytest.importorskip("openai")
    from bv_llm_sdk.agent.models.openai_compat import OpenAICompatModel

    def opts(think):
        return OpenAICompatModel(AgentConfig(backend="openai", think=think))._thinking_options()
    assert opts(None) == {}
    assert opts(False) == {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}}
    assert opts("low") == {"reasoning_effort": "low"}


def test_agent_drives_any_toolkit():
    from bv_llm_sdk.agent.prompt import AGENT_RULES
    from bv_llm_sdk.registry import ToolError, ToolRegistry
    from bv_llm_sdk.toolkit import Toolkit

    arm_tools = ToolRegistry()

    @arm_tools.tool
    def close_gripper(arm: dict, force_n: float = 10.0) -> dict:
        """Close the gripper.

        Args:
            force_n: Grip force in newtons.
        """
        if arm["holding"]:
            raise ToolError("already holding something")
        arm["holding"] = True
        return {"force_n": force_n}

    class ArmToolkit(Toolkit):
        name = "arm"
        registry = arm_tools
        prompt = "You control a robot arm."

        def state(self):
            return f"Arm state: holding={self.robot['holding']}"

    arm = {"holding": False}
    model = FakeModel([
        AssistantMessage(tool_calls=[call("close_gripper", "a", force_n="5")]),
        AssistantMessage(tool_calls=[call("close_gripper", "b")]),
        AssistantMessage(text="Gripped."),
    ])
    agent = Agent(ArmToolkit(arm), model, AgentConfig())
    assert agent.run("grab it") == "Gripped."
    assert arm["holding"] is True
    assert agent.tool_names == ["close_gripper"]
    assert agent.system_prompt.startswith("You control a robot arm.")
    assert agent.system_prompt.endswith(AGENT_RULES)
    assert agent.transcript[0].text.startswith("[Arm state: holding=False]")
    results = [m for m in agent.transcript if isinstance(m, ToolResult)]
    assert results[0].content == {"ok": True, "force_n": 5.0}
    assert results[1].content == {"ok": False, "error": "already holding something"}
