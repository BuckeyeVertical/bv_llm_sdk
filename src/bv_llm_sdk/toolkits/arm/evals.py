from __future__ import annotations

import argparse
import math
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Callable

from ...agent import Agent, AgentConfig, make_model
from ...agent.cli import Renderer
from ...agent.events import AgentEvent
from .arm import Arm, connect
from .planner import Step
from .toolkit import ArmToolkit

Point = tuple[float, float]


@dataclass
class Shape:
    points: list[Point]

    @property
    def tall(self) -> float:
        xs = [p[0] for p in self.points]
        return max(xs) - min(xs)

    @property
    def wide(self) -> float:
        ys = [p[1] for p in self.points]
        return max(ys) - min(ys)

    @property
    def center(self) -> Point:
        xs = [p[0] for p in self.points]
        ys = [p[1] for p in self.points]
        return (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2

    @property
    def closed(self) -> bool:
        return math.dist(self.points[0], self.points[-1]) < 0.5

    def overlaps(self, other: Shape) -> bool:
        return (abs(self.center[0] - other.center[0]) * 2 < self.tall + other.tall
                and abs(self.center[1] - other.center[1]) * 2 < self.wide + other.wide)

    def band_width(self, top: bool) -> float:
        xs = [p[0] for p in self.points]
        edge = max(xs) if top else min(xs)
        ys = [y for x, y in self.points if abs(x - edge) <= self.tall * 0.15]
        return max(ys) - min(ys)


@dataclass
class Result:
    shapes: list[Shape]
    reply: str
    calls: list[tuple[str, dict, bool]] = field(default_factory=list)
    moved: bool = False
    tip: Point = (0.0, 0.0)
    pen_up: bool = True

    def used(self, *names: str) -> bool:
        return any(call[0] in names for call in self.calls)

    def drawing(self) -> Shape:
        return Shape([p for s in self.shapes for p in s.points])

    def written(self) -> str:
        return "".join(args.get("text", "") for name, args, ok in self.calls
                       if name == "write_text" and ok)


Check = Callable[[Result], tuple[bool, str]]


@dataclass
class Scenario:
    name: str
    turns: list[str]
    check: Check


def _says(text: str, *patterns: str) -> bool:
    return any(re.search(p, text, re.I) for p in patterns)


def _sized(result: Result, size: float) -> tuple[bool, str]:
    if not result.shapes:
        return False, "nothing was drawn"
    shape = result.drawing()
    ok = shape.closed and abs(shape.tall - size) <= 0.5 and abs(shape.wide - size) <= 0.5
    return ok, f"{shape.tall:.1f} by {shape.wide:.1f} cm, closed={shape.closed}"


def square_5(result: Result) -> tuple[bool, str]:
    return _sized(result, 5)


def centered_circle(result: Result) -> tuple[bool, str]:
    ok, detail = _sized(result, 6)
    if not result.shapes:
        return ok, detail
    x, y = result.drawing().center
    return ok and math.dist((x, y), (24, 0)) <= 3, f"{detail}, center x {x:.1f} y {y:.1f}"


def left_and_right(result: Result) -> tuple[bool, str]:
    if len(result.shapes) != 2:
        return False, f"{len(result.shapes)} shapes"
    ys = [s.center[1] for s in result.shapes]
    return ys[0] > 0 > ys[1], f"first shape at y {ys[0]:.1f}, second at y {ys[1]:.1f}"


def upright_house(result: Result) -> tuple[bool, str]:
    if not result.shapes:
        return False, "nothing was drawn"
    shape = result.drawing()
    top, bottom = shape.band_width(True), shape.band_width(False)
    return top < bottom * 0.5, f"{top:.1f} cm wide at the top, {bottom:.1f} cm at the bottom"


def three_apart(result: Result) -> tuple[bool, str]:
    shapes = result.shapes
    if len(shapes) != 3:
        return False, f"{len(shapes)} shapes"
    apart = not any(a.overlaps(b) for i, a in enumerate(shapes) for b in shapes[i + 1:])
    return apart, f"apart={apart}"


def hello(result: Result) -> tuple[bool, str]:
    return result.written().upper() == "HELLO", f"wrote {result.written()!r}"


def big_hello(result: Result) -> tuple[bool, str]:
    if not result.shapes:
        return False, "nothing was drawn"
    tall = result.drawing().tall
    ok = result.written().upper() == "HELLO" and abs(tall - 8) <= 0.5
    return ok, f"wrote {result.written()!r}, {tall:.1f} cm tall"


def big_second_word(result: Result) -> tuple[bool, str]:
    writes = [args for name, args, ok in result.calls if name == "write_text" and ok]
    if len(writes) < 2:
        return False, f"{len(writes)} words written"
    size = writes[-1].get("size_cm") or 0
    ok = writes[-1].get("text", "").upper() == "SARVY" and size >= 8
    return ok, f"second word {writes[-1].get('text')!r} at {size} cm"


def cleared_and_big(result: Result) -> tuple[bool, str]:
    writes = [args for name, args, ok in result.calls if name == "write_text" and ok]
    cleared = result.used("clear_board")
    size = (writes[-1].get("size_cm") or 0) if writes else 0
    ok = cleared and bool(writes) and writes[-1].get("text", "").upper() == "HI" and size >= 8
    return ok, f"cleared={cleared}, last text at {size} cm"


def two_lines(result: Result) -> tuple[bool, str]:
    if not result.shapes:
        return False, "nothing was drawn"
    words = re.sub(r"\s+", " ", result.written()).strip().upper()
    first, last = result.shapes[0], result.shapes[-1]
    below = last.center[0] < min(p[0] for p in first.points)
    return words in ("CAT DOG", "CATDOG") and below, f"wrote {words!r}, below={below}"


def whole_phrase(result: Result) -> tuple[bool, str]:
    written = re.sub(r"\s+", "", result.written()).upper()
    return written == "BUCKEYEVERTICAL", f"wrote {result.written()!r}"


def refused_too_big(result: Result) -> tuple[bool, str]:
    told = _says(result.reply, r"cannot|can't|can not|too (big|large)|out of reach|smaller")
    return told, f"told the user={told}, drew {len(result.shapes)} shapes"


def fixed_too_close(result: Result) -> tuple[bool, str]:
    told = _says(result.reply, r"cannot|can't|can not|too close|out of reach|reach|moved|farther")
    return told, f"told the user={told}, drew {len(result.shapes)} shapes"


def reports_tip(result: Result) -> tuple[bool, str]:
    numbers = re.findall(r"-?\d+(?:\.\d+)?", result.reply)
    return (not result.moved and result.used("get_status") and len(numbers) >= 2,
            f"moved={result.moved}, reply has {len(numbers)} numbers")


def left_of_triangle(result: Result) -> tuple[bool, str]:
    if len(result.shapes) != 2:
        return False, f"{len(result.shapes)} shapes"
    first, second = result.shapes
    left = second.center[1] > first.center[1] and not first.overlaps(second)
    return left, f"triangle at y {first.center[1]:.1f}, square at y {second.center[1]:.1f}"


def ends_home(result: Result) -> tuple[bool, str]:
    home = math.dist(result.tip, (20, 0)) <= 1
    drew = bool(result.shapes)
    return drew and home and result.pen_up, f"drew={drew}, tip {result.tip}, pen_up={result.pen_up}"


SCENARIOS = [
    Scenario("square", ["Draw a square with 5 cm sides."], square_5),
    Scenario("circle", ["Draw a circle 6 cm wide in the middle of the page."], centered_circle),
    Scenario("left_right",
             ["Draw a 3 cm square on the left side of the page, then a 3 cm circle on the right."],
             left_and_right),
    Scenario("house", ["Draw a house with a triangular roof."], upright_house),
    Scenario("row", ["Draw three 3 cm squares in a row, not touching."], three_apart),
    Scenario("text", ["Write HELLO."], hello),
    Scenario("big_text", ["Write HELLO with letters 8 cm tall."], big_hello),
    Scenario("second_word", ["Write HELLO.", "Now write SARVY."], big_second_word),
    Scenario("erased", ["Write HELLO.", "I erased the board. Write HI."], cleared_and_big),
    Scenario("two_lines", ["Write CAT on one line and DOG on the line below it."], two_lines),
    Scenario("long_text", ["Write BUCKEYE VERTICAL."], whole_phrase),
    Scenario("too_big", ["Draw a circle 60 cm wide."], refused_too_big),
    Scenario("too_close", ["Draw a 4 cm square 10 cm out from the base."], fixed_too_close),
    Scenario("status", ["Where is the pen tip right now?"], reports_tip),
    Scenario("next_to", ["Draw a triangle with 5 cm sides.",
                         "Now draw a 3 cm square to the left of it, not touching it."],
             left_of_triangle),
    Scenario("go_home", ["Draw a triangle with 4 cm sides, then go home."], ends_home),
]


def record_steps(arm: Arm) -> list[Step]:
    steps: list[Step] = []
    stream = arm._stream

    def recording(new: list[Step]) -> None:
        steps.extend(s for s in new if s.kind != "settle")
        stream(new)

    arm._stream = recording
    return steps


def shapes_from(steps: list[Step], paper_z: float) -> list[Shape]:
    shapes: list[Shape] = []
    points: list[Point] = []
    for step in [*steps, None]:
        if step is not None and step.z <= paper_z + 0.05:
            points.append((step.x, step.y))
            continue
        if len(set(points)) > 1:
            shapes.append(Shape(points))
        points = []
    return shapes


def run_one(scenario: Scenario, model, config: AgentConfig, render) -> dict:
    arm = connect(sim=True)
    arm.attach()
    steps = record_steps(arm)
    calls: list[list] = []

    def on_event(event: AgentEvent) -> None:
        render(event)
        if event.kind == "tool_call":
            calls.append([event.data["name"], event.data["args"], True])
        elif event.kind == "tool_result":
            calls[-1][2] = event.data["result"].get("ok", True)

    agent = Agent(ArmToolkit(arm), model, config, on_event=on_event)
    start = time.time()
    reply = ""
    try:
        for turn in scenario.turns:
            print(f"  you> {turn}")
            reply = agent.run(turn)
    except RuntimeError as exc:
        return {"scenario": scenario.name, "outcome": "ERROR", "detail": str(exc)}
    status = arm.status()
    result = Result(
        shapes=shapes_from(steps, arm.settings.paper_z),
        reply=reply,
        calls=[(name, args, ok) for name, args, ok in calls],
        moved=bool(steps),
        tip=(status["tip_x_cm"], status["tip_y_cm"]),
        pen_up=not status["pen_on_paper"],
    )
    passed, detail = scenario.check(result)
    return {"scenario": scenario.name, "outcome": "PASS" if passed else "FAIL", "detail": detail,
            "seconds": round(time.time() - start, 1)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Score the agent on arm requests with a fake arm.")
    parser.add_argument("--only", help="comma list of scenario names")
    args = parser.parse_args()
    names = args.only.split(",") if args.only else [s.name for s in SCENARIOS]
    scenarios = [s for s in SCENARIOS if s.name in names]

    config = AgentConfig.from_env(toolkit="arm")
    model = make_model(config)
    render = Renderer(name="arm")
    rows = []
    for scenario in scenarios:
        print(f"\n=== {scenario.name}")
        row = run_one(scenario, model, config, render)
        print(f"  -> {row['outcome']}: {row['detail']}")
        rows.append(row)

    passed = sum(r["outcome"] == "PASS" for r in rows)
    print(f"\n{passed} of {len(rows)} passed")
    for row in rows:
        if row["outcome"] != "PASS":
            print(f"  {row['outcome']} {row['scenario']}: {row['detail']}")
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
