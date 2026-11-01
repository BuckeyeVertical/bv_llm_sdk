import sys
import types

import pytest

from bv_llm_sdk.toolkits import toolkit_class
from bv_llm_sdk.toolkits.arm import ArmError, ArmToolkit, connect
from bv_llm_sdk.toolkits.arm.link import Link, find_port
from bv_llm_sdk.toolkits.arm.tools import ARM_TOOLS


@pytest.fixture
def arm():
    robot = connect(sim=True)
    robot.attach()
    return robot


def call(arm, name, **args):
    return ARM_TOOLS.call(arm, name, args)


def moves(arm):
    return [line for line in arm.link.sent if line.startswith("M")]


def test_toolkit_is_registered():
    assert toolkit_class("arm") is ArmToolkit


def test_schemas_hide_the_robot_and_keep_units():
    schemas = {s["name"]: s for s in ARM_TOOLS.schemas("anthropic")}
    assert "arm" not in schemas["move_to"]["input_schema"]["properties"]
    assert schemas["move_to"]["input_schema"]["required"] == ["x_cm", "y_cm", "z_cm"]
    assert schemas["draw_path"]["input_schema"]["properties"]["points"]["items"]["type"] == "object"


def test_model_cannot_change_calibration_or_attach():
    assert not {"attach", "calibrate", "set", "raw", "limp"} & set(ARM_TOOLS.tools)


def test_status_starts_at_home(arm):
    result = call(arm, "get_status")
    assert result["simulated"] and result["holding"]
    assert result["tip_x_cm"] == pytest.approx(20.0, abs=0.2)
    assert result["tip_z_cm"] == pytest.approx(6.0, abs=0.2)
    assert not result["pen_on_paper"]


def test_draw_square_moves_and_leaves_pen_up(arm):
    result = call(arm, "draw_square", size_cm=4)
    assert result["ok"] and result["ink_cm"] > 16
    assert moves(arm)
    assert not call(arm, "get_status")["pen_on_paper"]


def test_out_of_reach_job_moves_nothing(arm):
    result = call(arm, "draw_square", size_cm=4, center_x_cm=80.0, center_y_cm=0.0)
    assert not result["ok"] and "nothing moved" in result["error"]
    assert not moves(arm)


def test_rotate_base_turns_left_and_right_from_where_it_is(arm):
    assert call(arm, "rotate_base", degrees=20)["ok"]
    assert call(arm, "get_status")["tip_theta_deg"] == pytest.approx(20, abs=0.5)
    assert call(arm, "rotate_base", degrees=-30)["ok"]
    status = call(arm, "get_status")
    assert status["tip_theta_deg"] == pytest.approx(-10, abs=0.5)
    assert status["tip_r_cm"] == pytest.approx(20.0, abs=0.2)
    assert status["tip_z_cm"] == pytest.approx(6.0, abs=0.2)


def test_rotate_base_lifts_the_pen_and_leaves_it_up(arm):
    assert call(arm, "move_to", x_cm=24, y_cm=0, z_cm=arm.settings.paper_z)["ok"]
    assert call(arm, "rotate_base", degrees=10)["ok"]
    status = call(arm, "get_status")
    assert not status["pen_on_paper"]
    assert status["tip_theta_deg"] == pytest.approx(10, abs=0.5)


@pytest.mark.parametrize("degrees", [80, -80, 360])
def test_rotate_base_past_its_limit_moves_nothing(arm, degrees):
    assert not call(arm, "rotate_base", degrees=degrees)["ok"]
    assert not moves(arm)


def test_limp_servos_refuse_to_move():
    robot = connect(sim=True)
    result = call(robot, "draw_circle", diameter_cm=3)
    assert not result["ok"] and "limp" in result["error"]
    assert not moves(robot)


def test_text_cursor_only_moves_when_text_is_drawn(arm):
    start = arm.text_cursor()
    assert not call(arm, "write_text", text="#")["ok"]
    assert arm.text_cursor() == start
    assert call(arm, "write_text", text="HI")["ok"]
    assert arm.text_cursor()["y_cm"] < start["y_cm"]
    call(arm, "new_line")
    assert arm.text_cursor()["x_cm"] < start["x_cm"]
    call(arm, "reset_text_cursor")
    assert arm.text_cursor() == start


def test_check_point_reports_a_verdict(arm):
    assert call(arm, "check_point", x_cm=24, y_cm=0)["drawable"]
    assert not call(arm, "check_point", x_cm=80, y_cm=0)["drawable"]
    assert not call(arm, "check_point", x_cm=80, y_cm=0, z_cm=5)["reachable"]


def test_go_home_recovers_from_an_unusual_pose(arm):
    arm.link.position[3] += 30
    assert not call(arm, "move_to", x_cm=24, y_cm=0, z_cm=6)["ok"]
    assert call(arm, "go_home")["ok"]
    assert call(arm, "move_to", x_cm=24, y_cm=0, z_cm=6)["ok"]


def test_interrupt_stops_streaming_without_going_limp(arm):
    send = arm.link.send

    def interrupted(line, *args, **kwargs):
        if len(moves(arm)) == 20:
            raise KeyboardInterrupt
        return send(line, *args, **kwargs)

    arm.link.send = interrupted
    with pytest.raises(KeyboardInterrupt):
        ARM_TOOLS.call(arm, "draw_square", {"size_cm": 4})
    arm.link.send = send
    ArmToolkit(arm).stop()
    assert len(moves(arm)) == 20
    assert not any(line in ("X", "D") for line in arm.link.sent)
    assert call(arm, "get_status")["holding"]


def test_stop_with_no_robot_does_nothing():
    ArmToolkit(None).stop()


def plug_in(monkeypatch, *ports):
    listing = types.SimpleNamespace(comports=lambda: [types.SimpleNamespace(device=d, vid=v) for d, v in ports])
    tools = types.SimpleNamespace(list_ports=listing)
    monkeypatch.setitem(sys.modules, "serial", types.SimpleNamespace(tools=tools))
    monkeypatch.setitem(sys.modules, "serial.tools", tools)
    monkeypatch.setitem(sys.modules, "serial.tools.list_ports", listing)


def test_find_port_picks_the_one_arduino(monkeypatch):
    plug_in(monkeypatch, ("/dev/cu.Bluetooth", None), ("/dev/cu.usbmodem101", 0x2341))
    assert find_port() == "/dev/cu.usbmodem101"


def test_find_port_accepts_clone_boards(monkeypatch):
    plug_in(monkeypatch, ("/dev/cu.usbserial-110", 0x1A86))
    assert find_port() == "/dev/cu.usbserial-110"


def test_no_arduino_says_how_to_fix_it(monkeypatch):
    plug_in(monkeypatch, ("/dev/cu.Bluetooth", None))
    with pytest.raises(ArmError, match="Plug in the arm"):
        connect()


def test_two_arduinos_ask_for_the_port(monkeypatch):
    plug_in(monkeypatch, ("/dev/a", 0x2341), ("/dev/b", 0x2341))
    with pytest.raises(ArmError, match="BV_ARM_PORT"):
        find_port()


class FakeSerial:
    def __init__(self, lines):
        self.lines = [line.encode() + b"\n" for line in lines]
        self.written = []

    def write(self, data):
        self.written.append(data)

    def readline(self):
        return self.lines.pop(0) if self.lines else b""

    def reset_input_buffer(self):
        pass


def fake_link(lines):
    link = Link.__new__(Link)
    link.ser = FakeSerial(lines)
    link._serial_error = OSError
    return link


def test_link_reads_the_firmware_calibration_format():
    link = fake_link([f"cfg {n} 10.0 170.0 90.0 -1 300 1" for n in range(1, 5)] + ["info park not saved", "ok"])
    calibration = link.read_calibration()
    assert calibration[3] == {"min": 10.0, "max": 170.0, "offset": 90.0, "dir": -1,
                              "travel": 300.0, "enabled": True}


def test_link_reads_the_firmware_position_format():
    angles, attached = fake_link(["pos 96.0 120.0 200.0 150.0 att 1111 mov 0", "ok"]).status()
    assert angles == [96.0, 120.0, 200.0, 150.0] and attached == "1111"


def test_link_stops_the_job_when_the_firmware_clamps_a_move():
    link = fake_link(["warn S2 clamped to 77.5", "ok"])
    with pytest.raises(ArmError, match="overrode"):
        link.send("M 96 70 200 150 50", strict=True)


def test_link_turns_firmware_errors_into_arm_errors():
    with pytest.raises(ArmError, match="bad number"):
        fake_link(["err bad number"]).send("M x")


def test_handshake_with_no_reset_reads_the_position_once():
    link = fake_link(["pos 96.0 120.0 200.0 150.0 att 1111 mov 0", "ok"])
    link.resync = lambda: pytest.fail("no reset happened so nothing is stale")
    link._handshake()
    assert link.ser.written == [b"P\n"]


def test_handshake_after_a_reset_waits_for_ready_and_clears_stale_replies():
    link = fake_link(["info servos are limp. Pose the arm at park, then send A.", "ready"])
    resyncs = []
    link.resync = lambda: resyncs.append(True)
    link._handshake()
    assert resyncs == [True]


def test_handshake_times_out_when_the_arduino_never_answers(monkeypatch):
    times = iter(range(0, 100, 5))
    monkeypatch.setattr("bv_llm_sdk.toolkits.arm.link.time.time", lambda: next(times))
    with pytest.raises(ArmError, match="no reply"):
        fake_link([])._handshake()


def toolkit_with(monkeypatch, holding):
    arm = connect(sim=True)
    if holding:
        arm.attach()
    arm.link.sent.clear()
    prompts = []
    monkeypatch.delenv("BV_ARM_SIM", raising=False)
    monkeypatch.setattr("bv_llm_sdk.toolkits.arm.toolkit.connect", lambda *a, **k: arm)
    monkeypatch.setattr("builtins.input", prompts.append)
    return ArmToolkit.connect(), prompts


def test_connect_takes_over_an_arm_that_is_already_holding(monkeypatch):
    toolkit, prompts = toolkit_with(monkeypatch, holding=True)
    assert not prompts
    assert "A" not in toolkit.robot.link.sent


def test_connect_prompts_and_attaches_a_limp_arm(monkeypatch):
    toolkit, prompts = toolkit_with(monkeypatch, holding=False)
    assert len(prompts) == 1
    assert "A" in toolkit.robot.link.sent


def test_a_new_session_can_go_home_from_wherever_the_arm_stopped():
    first = connect(sim=True)
    first.attach()
    call(first, "move_to", x_cm=24, y_cm=8, z_cm=7)
    second = connect(sim=True)
    second.link = first.link
    assert second.is_holding()
    assert call(second, "go_home")["ok"]
    assert call(second, "get_status")["tip_x_cm"] == pytest.approx(20.0, abs=0.2)


def test_a_shape_keeps_the_one_center_value_it_was_given(arm):
    from bv_llm_sdk.toolkits.arm.evals import record_steps, shapes_from

    steps = record_steps(arm)
    assert call(arm, "draw_square", size_cm=3, center_y_cm=8)["ok"]
    x, y = shapes_from(steps, arm.settings.paper_z)[0].center
    assert (x, y) == pytest.approx((arm.settings.center_x, 8), abs=0.1)


def drawn(arm, *calls):
    from bv_llm_sdk.toolkits.arm.evals import Result, record_steps, shapes_from

    steps = record_steps(arm)
    results = [(name, args, ARM_TOOLS.call(arm, name, args)["ok"]) for name, args in calls]
    return Result(shapes_from(steps, arm.settings.paper_z), "", results, bool(steps))


def path(*points):
    return "draw_path", {"points": [{"x_cm": x, "y_cm": y} for x, y in points]}


def test_eval_measures_what_was_drawn(arm):
    from bv_llm_sdk.toolkits.arm.evals import square_5

    assert square_5(drawn(arm, ("draw_square", {"size_cm": 5})))[0]
    assert not square_5(drawn(arm, ("draw_square", {"size_cm": 3})))[0]


def test_eval_house_needs_its_roof_away_from_the_base(arm):
    from bv_llm_sdk.toolkits.arm.evals import upright_house

    up = path((20, -3), (20, 3), (25, 3), (28, 0), (25, -3), (20, -3))
    down = path((28, -3), (28, 3), (23, 3), (20, 0), (23, -3), (28, -3))
    assert upright_house(drawn(arm, up))[0]
    assert not upright_house(drawn(arm, down))[0]


def test_eval_counts_only_text_that_was_written(arm):
    from bv_llm_sdk.toolkits.arm.evals import whole_phrase

    result = drawn(arm, ("write_text", {"text": "BUCKEYE VERTICAL"}))
    assert result.written() == ""
    assert not whole_phrase(result)[0]
