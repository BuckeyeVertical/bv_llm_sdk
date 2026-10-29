"""Run the eval scenarios against a model and record the results.

Needs the bv_bevy forest world running. Each run writes one JSON line per scenario
attempt to the evals results folder. A scenario passes or fails based on what the model
did. Errors mean the setup or model server broke and say nothing about the model.
"""

from __future__ import annotations

import json
import re
import sys
import threading
import time
from dataclasses import asdict
from pathlib import Path

from ..drone import Drone, DroneError, connect
from ..toolkit import DroneToolkit
from ....agent import Agent
from ....agent.cli import Renderer, build_parser, config_from_args
from ....agent.config import AgentConfig
from ....agent.events import AgentEvent
from ....agent.models import ModelError, make_model
from .scenarios import Result, Scenario, select
from .world import Tracker, pose, restart_simulator

RESULTS_DIR = Path("evals/results")

# PX4 refusing because the simulator is unhealthy. This is a setup error and not a wrong
# answer.
SIM_FAULTS = ("system health failures", "Battery unhealthy")


def run_one(drone: Drone, agent: Agent, scenario: Scenario, render, restart_sim: bool) -> dict:
    """Set up and run and check one scenario. Never raises."""
    record: dict = {"scenario": scenario.name, "tags": list(scenario.tags)}
    for attempt in (1, 2):
        try:
            scenario.setup(drone)
            break
        except DroneError as exc:
            if attempt == 2 or not restart_sim:
                return {**record, "outcome": "ERROR", "detail": f"setup failed: {exc}"}
            print(f"  setup failed ({exc}); restarting the simulator")
            restart_simulator()

    tools: list[str] = []
    tool_args: list[dict] = []
    errors = 0
    sim_fault = ""

    def on_event(event: AgentEvent):
        nonlocal errors, sim_fault
        render(event)
        if event.kind == "tool_call":
            tools.append(event.data["name"])
            tool_args.append(event.data["args"])
        elif event.kind == "tool_result" and not event.data["result"].get("ok", True):
            errors += 1
            text = str(event.data["result"].get("error", ""))
            if any(f in text for f in SIM_FAULTS):
                sim_fault = text

    agent.on_event = on_event
    agent.reset()
    start = pose(drone)
    tracker = Tracker(drone)
    thread = threading.Thread(target=tracker.run, args=(start,), daemon=True)
    thread.start()
    t0 = time.time()
    reply = ""
    try:
        for turn in scenario.turns:
            print(f"  you> {turn}")
            reply = agent.run(turn)
    except ModelError as exc:
        tracker.stop()
        return {**record, "outcome": "ERROR", "detail": f"model error: {exc}",
                "seconds": round(time.time() - t0, 1)}
    except Exception as exc:  # A bug in the agent or a tool. Record it and keep going.
        tracker.stop()
        return {**record, "outcome": "ERROR",
                "detail": f"crashed: {type(exc).__name__}: {exc}",
                "seconds": round(time.time() - t0, 1)}
    seconds = time.time() - t0
    time.sleep(1.0)  # Let the last position sample arrive.
    tracker.stop()
    thread.join()

    result = Result(start=start, end=pose(drone), track=tracker.track, seconds=seconds,
                    reply=reply, tools=tools, tool_args=tool_args, errors=errors,
                    places=dict(drone.places))
    passed, detail = scenario.check(result)
    outcome = "PASS" if passed else "FAIL"
    if not passed and sim_fault:
        outcome, detail = "ERROR", f"simulator fault: {sim_fault[:100]}"
        if restart_sim:
            print("  simulator unhealthy; restarting it")
            restart_simulator()
    return {**record, "outcome": outcome, "detail": detail,
            "seconds": round(seconds, 1), "tool_calls": len(tools), "tool_errors": errors,
            "tools": tools, "reply": reply,
            "path_m": round(result.track.path_m, 1)}


def _results_path(config: AgentConfig) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    model = re.sub(r"[^\w.-]+", "-", config.model)
    return RESULTS_DIR / f"{config.backend}_{model}_{time.strftime('%Y%m%d-%H%M%S')}.jsonl"


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    parser.prog = "agent_eval"
    parser.description = "Score a model on drone requests in the simulator."
    parser.add_argument("--only", help="comma list of scenario names")
    parser.add_argument("--tags", help="comma list of tags, e.g. quick, vision, multi_turn")
    parser.add_argument("--repeat", type=int, default=1, help="run the set this many times")
    parser.add_argument("--no-restart-sim", action="store_true",
                        help="do not restart PX4 when setup fails")
    parser.add_argument("--list", action="store_true", help="list scenarios and exit")
    parser.add_argument("--label", help="where it ran, e.g. pitzer-v100 or ascend-a100-vllm; "
                                        "shown in reports so runs on different setups stay apart")
    args = parser.parse_args(argv)

    scenarios = select(args.only, args.tags)
    if args.list:
        for s in scenarios:
            print(f"{s.name:<18}{','.join(s.tags):<22}{s.turns[0][:70]}")
        return 0

    config = config_from_args(args)
    model = make_model(config)
    render = Renderer(verbose=args.verbose, color=sys.stdout.isatty())
    out_path = _results_path(config)
    meta = {"backend": config.backend, "model": config.model, "toolset": config.toolset,
            "think": config.think, "num_ctx": config.num_ctx, "label": args.label,
            "vision": model.supports_vision,
            "started": time.strftime("%Y-%m-%dT%H:%M:%S")}
    rows = []
    with connect() as drone, out_path.open("w") as out:
        agent = Agent(DroneToolkit(drone), model, config)
        for rep in range(1, args.repeat + 1):
            for s in scenarios:
                print(f"\n=== [{rep}/{args.repeat}] {s.name}")
                row = {**meta, "repeat": rep,
                       **run_one(drone, agent, s, render, not args.no_restart_sim)}
                print(f"  -> {row['outcome']}: {row.get('detail', '')}")
                out.write(json.dumps(row) + "\n")
                out.flush()
                rows.append(row)

    print(f"\nresults: {out_path}")
    from .report import summarise

    print(summarise(rows))
    return 0 if all(r["outcome"] == "PASS" for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
