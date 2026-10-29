"""Fix tool calls from models that do not follow the format exactly."""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from .messages import ToolCall


def new_call_id() -> str:
    return "call_" + uuid.uuid4().hex[:12]


def parse_arguments(raw: Any) -> dict[str, Any]:
    """Return tool arguments as a dict. Accepts a dict or a JSON string. Raises ValueError
    if the arguments are not a JSON object.
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
    """Convert argument values to the types in the tool schema.

    Only simple conversions are done. Anything else is passed through for the tool to
    reject.
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
        # Models often write points as a list of numbers. Map them to the object fields
        # by position.
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
    """Parse tool calls written in the Qwen3-Coder XML format."""
    calls = []
    for name, body in _XML_CALL.findall(text):
        args: dict[str, Any] = {}
        for key, raw in _XML_PARAM.findall(body):
            value = raw.strip()
            try:
                args[key] = json.loads(value)
            except json.JSONDecodeError:
                args[key] = value  # Keep it as text. coerce fixes the type later.
        calls.append(ToolCall(new_call_id(), name, args))
    return calls


def extract_text_tool_calls(text: str, allowed: set[str]) -> list[ToolCall]:
    """Find tool calls that a model wrote as text.

    Looks for JSON objects with a tool name and arguments and for the Qwen3-Coder XML
    format. An unknown tool name only counts when the object clearly looks like a call.
    The agent can then tell the model that the tool does not exist.
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
        # Otherwise try every top level JSON object in the text.
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
