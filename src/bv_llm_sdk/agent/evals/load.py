"""How a model server holds up when many people use it at once.

Builder Night will have one model server and many teams calling it. This runs
N simulated users at the same time, each sending typical multi-step requests
through the real agent loop (same system prompt, same 43 tool schemas, same
growing conversation), with tool results faked so no drone or simulator is
needed. It reports how long requests and individual model calls take as the
number of users grows, and, for vLLM, how full the KV cache got and how many
requests had to wait.

    uv run --extra agent scripts/load_test.py --backend openai \\
        --base-url http://localhost:8000/v1 --model openai/gpt-oss-20b --users 1,5,10,20
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from ..agent import Agent
from ..cli import build_parser, config_from_args
from ..models import ModelError, make_model

# Requests a team might send, each needing several model calls.
REQUESTS = [
    "Take off to 12 meters, fly 20 meters north, then turn to face east.",
    "Fly a square with 15 meter sides, then tell me your battery level.",
    "Search the 40 by 40 meter field north-east of home for a person.",
    "Draw the letter L in the sky in front of you, 6 meters tall.",
    "Visit (10 N, 0 E), (10 N, 15 E) and (0 N, 15 E) in the shortest order.",
]


def fake_tool(drone, name: str, args: dict) -> dict:
    """Plausible, instant tool results, so only the model is being measured."""
    time.sleep(0.05)
    base = {"ok": True, "latitude": 36.2147, "longitude": -96.0065, "altitude_m": 12.0,
            "north_of_home_m": 0.0, "east_of_home_m": 0.0, "heading_deg": 90.0}
    if name in ("get_status", "preflight_check"):
        return {**base, "armed": True, "flight_mode": "LOITER", "landed_state": "IN_AIR",
                "battery_percent": 92, "ready_to_arm": True}
    if name == "get_battery":
        return {"ok": True, "battery_percent": 92, "estimated_flight_minutes_left": 13.4}
    if name in ("search_area", "search_for_object"):
        return {"ok": True, "found": True, "object": {"label": "person", "latitude": 36.21484,
                "longitude": -96.00639, "north_of_home_m": 18.0, "east_of_home_m": 14.0}}
    if name in ("plan_route", "measure_route"):
        return {"ok": True, "waypoints": args.get("waypoints", []), "total_m": 45.0,
                "within_limit": True, "estimated_time_s": 30}
    return {**base, "message": f"{name} done"}


class MetricsSampler:
    """Polls vLLM's Prometheus /metrics for KV-cache use and queue length."""

    def __init__(self, base_url: str):
        root = re.sub(r"/v1/?$", "", base_url.rstrip("/"))
        self.url = root + "/metrics"
        self.peak_kv = 0.0
        self.peak_running = 0.0
        self.peak_waiting = 0.0
        self.available = True
        self._stop = threading.Event()

    def _read(self) -> dict[str, float]:
        text = urllib.request.urlopen(self.url, timeout=5).read().decode()
        out: dict[str, float] = {}
        for line in text.splitlines():
            if line.startswith("#"):
                continue
            m = re.match(r"^(vllm:[a-z_]+)(?:\{[^}]*\})? ([0-9.eE+-]+)$", line)
            if m:
                out[m.group(1)] = out.get(m.group(1), 0.0) + float(m.group(2))
        return out

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                m = self._read()
            except Exception:
                self.available = False
                return
            kv = m.get("vllm:kv_cache_usage_perc", m.get("vllm:gpu_cache_usage_perc", 0.0))
            self.peak_kv = max(self.peak_kv, kv)
            self.peak_running = max(self.peak_running, m.get("vllm:num_requests_running", 0))
            self.peak_waiting = max(self.peak_waiting, m.get("vllm:num_requests_waiting", 0))
            self._stop.wait(0.5)

    def stop(self) -> None:
        self._stop.set()


class TimedModel:
    """Wraps a ChatModel to record how long each call takes."""

    def __init__(self, model):
        self.model = model
        self.schema_format = model.schema_format
        self.supports_vision = model.supports_vision
        self.calls: list[float] = []
        self._lock = threading.Lock()

    def complete(self, system, transcript, tools):
        t = time.time()
        try:
            return self.model.complete(system, transcript, tools)
        finally:
            with self._lock:
                self.calls.append(time.time() - t)


def _pct(values: list[float], q: float) -> float:
    if not values:
        return float("nan")
    values = sorted(values)
    return values[min(len(values) - 1, int(round(q * (len(values) - 1))))]


def run_level(config, users: int, requests_per_user: int, base_url: str | None) -> dict:
    model = TimedModel(make_model(config))
    request_times: list[float] = []
    errors: list[str] = []
    lock = threading.Lock()

    def user(i: int) -> None:
        agent = Agent(None, model, config, executor=fake_tool)
        for k in range(requests_per_user):
            text = REQUESTS[(i + k) % len(REQUESTS)]
            t = time.time()
            try:
                agent.run(text)
                with lock:
                    request_times.append(time.time() - t)
            except ModelError as exc:
                with lock:
                    errors.append(str(exc)[:120])

    sampler = MetricsSampler(base_url) if base_url and config.backend == "openai" else None
    if sampler:
        threading.Thread(target=sampler.run, daemon=True).start()
    t0 = time.time()
    with ThreadPoolExecutor(users) as pool:
        list(pool.map(user, range(users)))
    wall = time.time() - t0
    if sampler:
        sampler.stop()
    out = {
        "users": users,
        "requests": len(request_times),
        "errors": len(errors),
        "model_calls": len(model.calls),
        "call_p50_s": round(_pct(model.calls, 0.5), 1),
        "call_p95_s": round(_pct(model.calls, 0.95), 1),
        "request_p50_s": round(_pct(request_times, 0.5), 1),
        "request_p95_s": round(_pct(request_times, 0.95), 1),
        "wall_s": round(wall, 1),
    }
    if sampler and sampler.available:
        out.update(peak_kv_cache_pct=round(100 * sampler.peak_kv, 1),
                   peak_running=int(sampler.peak_running),
                   peak_waiting=int(sampler.peak_waiting))
    if errors:
        out["first_error"] = errors[0]
    return out


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    parser.prog = "load_test"
    parser.description = "Simulate many people using one model server at once."
    parser.add_argument("--users", default="1,5,10,20",
                        help="comma list of concurrent-user counts to try")
    parser.add_argument("--requests-per-user", type=int, default=2)
    parser.add_argument("--label", help="where it ran, saved with the results")
    parser.add_argument("--out", default="evals/results/load.jsonl")
    args = parser.parse_args(argv)
    config = config_from_args(args)
    levels = [int(u) for u in args.users.split(",")]

    rows = []
    for users in levels:
        print(f"--- {users} concurrent user(s)", flush=True)
        row = {"backend": config.backend, "model": config.model, "label": args.label,
               **run_level(config, users, args.requests_per_user, config.base_url)}
        print(json.dumps(row), flush=True)
        rows.append(row)
        with open(args.out, "a") as f:
            f.write(json.dumps(row) + "\n")

    print(f"\n{config.model}: seconds per model call / per whole request (p50, p95)")
    print(f"{'users':>5} {'call p50':>9} {'call p95':>9} {'req p50':>8} {'req p95':>8} "
          f"{'errors':>6} {'KV peak%':>9} {'waiting':>7}")
    for r in rows:
        print(f"{r['users']:>5} {r['call_p50_s']:>9} {r['call_p95_s']:>9} {r['request_p50_s']:>8} "
              f"{r['request_p95_s']:>8} {r['errors']:>6} {r.get('peak_kv_cache_pct', '-'):>9} "
              f"{r.get('peak_waiting', '-'):>7}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
