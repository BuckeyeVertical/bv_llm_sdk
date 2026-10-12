"""Summarise eval results, one model or many side by side.

    uv run scripts/eval_report.py                       # every file in evals/results/
    uv run scripts/eval_report.py evals/results/*gpt-oss*.jsonl
    uv run scripts/eval_report.py --markdown > evals/RESULTS.md

ERROR rows (setup or server failures) are shown but left out of pass rates.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path


def load(paths: list[Path]) -> list[dict]:
    rows = []
    for path in paths:
        for line in path.read_text().splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _model_key(row: dict) -> str:
    key = f"{row['backend']}:{row['model']}"
    if row.get("think") not in (None, False):
        key += f" think={row['think']}"
    if row.get("toolset") not in (None, "full"):
        key += f" [{row['toolset']}]"
    if row.get("label"):
        key += f" @{row['label']}"
    return key


def _cell(results: list[dict]) -> str:
    scored = [r for r in results if r["outcome"] != "ERROR"]
    errors = len(results) - len(scored)
    if not scored:
        return f"err×{errors}" if errors else "-"
    passed = sum(r["outcome"] == "PASS" for r in scored)
    text = f"{passed}/{len(scored)}"
    return text + (f" (+{errors}err)" if errors else "")


def summarise(rows: list[dict], markdown: bool = False) -> str:
    models = sorted({_model_key(r) for r in rows})
    scenarios = list(dict.fromkeys(r["scenario"] for r in rows))
    grid: dict = defaultdict(list)
    for r in rows:
        grid[(r["scenario"], _model_key(r))].append(r)

    header = ["scenario"] + models
    body = [[s] + [_cell(grid[(s, m)]) for m in models] for s in scenarios]
    totals, timing = ["TOTAL"], ["avg s / scenario"]
    for m in models:
        mine = [r for r in rows if _model_key(r) == m]
        scored = [r for r in mine if r["outcome"] != "ERROR"]
        passed = sum(r["outcome"] == "PASS" for r in scored)
        pct = f" ({100 * passed / len(scored):.0f}%)" if scored else ""
        totals.append(f"{passed}/{len(scored)}{pct}")
        secs = [r["seconds"] for r in scored if "seconds" in r]
        timing.append(f"{sum(secs) / len(secs):.0f}" if secs else "-")
    body += [totals, timing]

    if markdown:
        lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
        lines += ["| " + " | ".join(row) + " |" for row in body]
        return "\n".join(lines)
    widths = [max(len(row[i]) for row in [header] + body) for i in range(len(header))]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    return "\n".join(fmt.format(*row) for row in [header] + body)


def failures(rows: list[dict]) -> str:
    """One line per FAIL/ERROR, to see what went wrong."""
    out = []
    for r in rows:
        if r["outcome"] != "PASS":
            out.append(f"{_model_key(r):<28} {r['scenario']:<16} {r['outcome']:<5} "
                       f"{r.get('detail', '')[:90]}  tools={r.get('tools', [])}")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="eval_report", description=__doc__.split("\n")[0])
    p.add_argument("files", nargs="*", type=Path)
    p.add_argument("--markdown", action="store_true")
    p.add_argument("--failures", action="store_true", help="also list every failure")
    args = p.parse_args(argv)
    files = args.files or sorted(Path("evals/results").glob("*.jsonl"))
    if not files:
        print("no results yet (run scripts/agent_eval.py)", file=sys.stderr)
        return 1
    rows = load(files)
    print(summarise(rows, markdown=args.markdown))
    if args.failures:
        print("\n" + failures(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
