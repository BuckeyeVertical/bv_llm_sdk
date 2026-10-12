"""Checks that need no simulator: schemas, geometry and error handling."""

import json

import pytest

from bv_llm_sdk import TOOLS, run_tool, tool_schemas
from bv_llm_sdk import geo
from bv_llm_sdk.drone import mav


def test_every_tool_has_a_valid_schema():
    schemas = tool_schemas()
    assert {s["name"] for s in schemas} == set(TOOLS)
    for s in schemas:
        assert s["description"], s["name"]
        assert "\n" not in s["description"].split("\n\n")[0]
        for name, prop in s["input_schema"]["properties"].items():
            assert prop["type"] in ("number", "integer", "boolean", "string", "array")
            assert prop.get("description"), f"{s['name']}.{name} has no description"
        json.dumps(s)


def test_openai_format():
    s = next(t for t in tool_schemas("openai") if t["function"]["name"] == "move")
    assert s["type"] == "function"
    assert set(s["function"]["parameters"]["properties"]) == {"north_m", "east_m", "up_m", "speed_m_s"}


def test_required_and_optional_params():
    goto = next(t for t in tool_schemas() if t["name"] == "goto_location")
    assert goto["input_schema"]["required"] == ["latitude", "longitude"]


def test_offset_round_trip():
    lat, lon = geo.offset(36.2, -96.0, 30.0, -40.0)
    north, east = geo.north_east(36.2, -96.0, lat, lon)
    assert north == pytest.approx(30.0, abs=0.01)
    assert east == pytest.approx(-40.0, abs=0.01)
    assert geo.distance(36.2, -96.0, lat, lon) == pytest.approx(50.0, abs=0.01)


@pytest.mark.parametrize(
    "heading, expected",
    [(0, (10, 0)), (90, (0, 10)), (180, (-10, 0)), (270, (0, -10))],
)
def test_body_to_ned(heading, expected):
    north, east = geo.body_to_ned(10, 0, heading)
    assert (north, east) == pytest.approx(expected, abs=1e-9)


def test_bearing_and_wrap():
    lat, lon = geo.offset(36.2, -96.0, 0, 100)
    assert geo.bearing(36.2, -96.0, lat, lon) == pytest.approx(90.0, abs=0.01)
    assert geo.wrap_180(270) == -90
    assert geo.wrap_180(-190) == 170


def test_run_tool_reports_errors_instead_of_raising():
    assert json.loads(run_tool(None, "fly_to_the_moon"))["ok"] is False
    # Validation happens before the drone is touched.
    result = json.loads(run_tool(None, "set_speed", {"speed_m_s": 50}))
    assert result == {"ok": False, "error": "speed_m_s must be between 0.5 and 12."}
    result = json.loads(run_tool(None, "move", {"sideways_m": 3}))
    assert result["ok"] is False and "sideways_m" in result["error"]


def test_dialect_is_px4s():
    assert mav.WIRE_PROTOCOL_VERSION == "2.0"
    assert hasattr(mav, "MAV_CMD_DO_REPOSITION")


def _frame(heading_deg):
    from bv_llm_sdk.camera import Frame

    return Frame(b"", 1920, 1080, 1148.2, 1148.2, 960, 540, 0, 0.0,
                 36.2, -96.0, 14.5, heading_deg)


def test_pixel_to_ground_matches_camera_mounting():
    # Measured in the forest world: 14.5 m up, an object 5.3 m north of the
    # drone appeared ~455 px above centre when facing north, and the same
    # distance left of centre when facing east.
    north, east = _frame(0).pixel_to_ground(960, 540 - 455)
    assert north == pytest.approx(5.75, abs=0.05) and east == pytest.approx(0, abs=1e-6)
    north, east = _frame(90).pixel_to_ground(960 - 455, 540)
    assert north == pytest.approx(5.75, abs=0.05) and east == pytest.approx(0, abs=1e-6)
    assert _frame(0).ground_width_m == pytest.approx(24.2, abs=0.1)


def test_waypoint_list_schema():
    wp = next(t for t in tool_schemas() if t["name"] == "fly_waypoints")
    items = wp["input_schema"]["properties"]["waypoints"]["items"]
    assert items["type"] == "object"
    assert "exactly one of" in items["description"]
    assert set(items["properties"]) >= {"latitude", "longitude", "place", "hold_s"}


def test_shapes_close_on_the_start():
    from bv_llm_sdk.actions.routes import _shape_points

    for shape in ("triangle", "square", "pentagon", "hexagon", "star", "circle", "figure_eight"):
        for clockwise in (True, False):
            points, _ = _shape_points(shape, 20.0, clockwise)
            assert points[-1] == pytest.approx((0.0, 0.0), abs=1e-6), shape
    # A clockwise square goes forward first, then right.
    square, stop_each = _shape_points("square", 10.0, True)
    assert stop_each and square[0] == pytest.approx((10, 0)) and square[1] == pytest.approx((10, 10))
    # A figure eight's first loop is on the right when clockwise.
    eight, _ = _shape_points("figure_eight", 40.0, True)
    assert max(p[1] for p in eight[:24]) == pytest.approx(20.0, abs=0.01)


def test_color_ignores_background_and_prefers_clothes():
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    from bv_llm_sdk.vision import Detection, dominant_color

    image = np.zeros((200, 200, 3), np.uint8)
    image[:] = (40, 120, 60)  # grass (BGR)
    image[80:120, 80:120] = (200, 200, 200)  # pale "skin" fills most of the box
    image[85:100, 85:115] = (30, 30, 160)  # a red shirt
    box = Detection("person", 0.9, 80, 80, 120, 120)
    assert dominant_color(image, box) == "red"
    image[85:100, 85:115] = (200, 200, 200)
    assert dominant_color(image, box) in ("white", "gray")
