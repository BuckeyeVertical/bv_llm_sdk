"""Making sense of the tool calls models actually produce.

Self-hosted models are loose: arguments arrive as a JSON string wrapped in a
code fence, numbers arrive as "10", and some models write the whole tool call
as JSON text instead of using the tool-calling channel. These helpers repair
what can be repaired so a near-miss does not cost a whole round trip.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from .messages import ToolCall


def new_call_id() -> str:
    return "call_" + uuid.uuid4().hex[:12]


def parse_arguments(raw: Any) -> dict[str, Any]:
    """Tool arguments as a dict, from a dict or a (possibly sloppy) JSON string.

    Raises:
        ValueError: the arguments cannot be read as a JSON object.
    """
    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        raise ValueError(f"tool arguments must be a JSON object, got {type(raw).__name__}")
    text = raw.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.S)
    if fenced:
        text = fenced.group(1)
    for candidate in (text, re.sub(r",\s*([}\]])", r"\1", text)):
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
        raise ValueError("tool arguments must be a JSON object")
    raise ValueError(f"could not read the tool arguments as JSON: {raw[:200]!r}")


def coerce(args: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    """Convert argument values to the types the tool's JSON schema declares.

    Only obvious conversions ("10" -> 10.0, "true" -> True, a JSON string
    holding a list -> the list); anything else is passed through and left for
    the tool's own validation to report.
    """
    properties = schema.get("properties", {})
    return {k: _coerce_value(v, properties.get(k, {})) for k, v in args.items()}


def _coerce_value(value: Any, prop: dict[str, Any]) -> Any:
    kind = prop.get("type")
    if value is None:
        return None
    if kind == "number" and isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return value
    if kind == "integer" and isinstance(value, (str, float)):
        try:
            number = float(value)
            return int(number) if number.is_integer() else value
        except ValueError:
            return value
    if kind == "boolean" and isinstance(value, str):
        low = value.strip().lower()
        if low in ("true", "yes", "1"):
            return True
        if low in ("false", "no", "0"):
            return False
        return value
    if kind == "array":
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                return value
        if isinstance(value, list):
            return [_coerce_value(v, prop.get("items", {})) for v in value]
        return value
    if kind == "object" and isinstance(value, dict):
        return coerce(value, prop)
    if kind == "object" and isinstance(value, list):
        # [x, y] for an object with fields (x_m, y_m): models often write
        # points as pairs. Map positionally when the lengths match.
        keys = list(prop.get("properties", {}))
        if keys and len(value) == len(keys):
            return coerce(dict(zip(keys, value)), prop)
        required = prop.get("required", [])
        if required and len(value) == len(required):
            return coerce(dict(zip(required, value)), prop)
        return value
    if kind == "string" and isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return value


_XML_CALL = re.compile(r"<function=([\w.-]+)>(.*?)(?:</function>|$)", re.S)
_XML_PARAM = re.compile(r"<parameter=([\w.-]+)>(.*?)(?:</parameter>|(?=<parameter=)|$)", re.S)


def _xml_tool_calls(text: str, allowed: set[str]) -> list[ToolCall]:
    """Calls in Qwen3-Coder's own format, which Ollama sometimes passes through
    as text: <function=NAME><parameter=KEY>VALUE</parameter>...</function>."""
    calls = []
    for name, body in _XML_CALL.findall(text):
        args: dict[str, Any] = {}
        for key, raw in _XML_PARAM.findall(body):
            value = raw.strip()
            try:
                args[key] = json.loads(value)
            except json.JSONDecodeError:
                args[key] = value  # a plain string; coerce() fixes types later
        calls.append(ToolCall(new_call_id(), name, args))
    return calls


def extract_text_tool_calls(text: str, allowed: set[str]) -> list[ToolCall]:
    """Tool calls a model wrote as text instead of real tool calls.

    Accepts JSON objects like {"name": "takeoff", "arguments": {...}} (or
    "parameters"), alone, in a list, or inside a code fence, and Qwen3-Coder's
    <function=...> XML. A known tool name makes any {"name": ...} object a
    call; an unknown name counts only in an unmistakable call shape (with
    "arguments"/"parameters", or the XML), so the agent can answer "there is
    no tool X" and the model can retry, while ordinary JSON in a reply is
    left alone. The agent never runs a tool outside its toolset.
    """
    if not text:
        return []
    if "<function=" in text:
        calls = _xml_tool_calls(text, allowed)
        if calls:
            return calls
    if "{" not in text:
        return []
    candidates: list[Any] = []
    blocks = re.findall(r"```(?:json)?\s*(.*?)```", text, re.S) or [text]
    for block in blocks:
        block = block.strip()
        try:
            candidates.append(json.loads(block))
            continue
        except json.JSONDecodeError:
            pass
        # Fall back to every balanced top-level {...} in the text.
        depth, start = 0, None
        for i, ch in enumerate(block):
            if ch == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "}" and depth:
                depth -= 1
                if depth == 0 and start is not None:
                    try:
                        candidates.append(json.loads(block[start : i + 1]))
                    except json.JSONDecodeError:
                        pass
    calls = []
    flat: list[Any] = []
    for c in candidates:
        flat.extend(c if isinstance(c, list) else [c])
    for item in flat:
        if not isinstance(item, dict):
            continue
        if "function" in item and isinstance(item["function"], dict):
            item = item["function"]
        name = item.get("name")
        if not isinstance(name, str):
            continue
        if name not in allowed and not ("arguments" in item or "parameters" in item):
            continue
        try:
            args = parse_arguments(item.get("arguments", item.get("parameters", {})))
        except ValueError:
            continue
        calls.append(ToolCall(new_call_id(), name, args))
    return calls
