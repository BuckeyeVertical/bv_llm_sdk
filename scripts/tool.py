"""Run a toolkit tool from the command line the same way the model calls it.

Pass each argument as name equals value. Values are read as JSON when possible and as
plain text otherwise. Join several calls with a plus sign to run them in order.
"""

import json
import sys

from bv_llm_sdk.toolkits.drone import TOOLS, call_tool, connect, tool_schemas


def parse_value(text: str):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def main(argv: list[str]) -> int:
    if not argv:
        for schema in tool_schemas():
            params = ", ".join(schema["input_schema"]["properties"])
            print(f"{schema['name']}({params})")
        return 0

    calls, current = [], []
    for word in argv + ["+"]:
        if word == "+":
            if current:
                calls.append(current)
            current = []
        else:
            current.append(word)

    for name, *_ in calls:
        if name not in TOOLS:
            print(f"unknown tool {name!r}; run with no arguments to list them")
            return 2

    with connect() as drone:
        for name, *pairs in calls:
            args = {k: parse_value(v) for k, v in (p.split("=", 1) for p in pairs)}
            print(f"> {name} {args}")
            result = call_tool(drone, name, args)
            print(json.dumps(result, indent=2))
            if not result["ok"]:
                return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
