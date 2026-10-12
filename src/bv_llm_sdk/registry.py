"""Turn plain Python functions into LLM tools.

Mark a function with ``@tool`` and it becomes available to the agent. The
schema the LLM sees is built from the function itself:

- the tool name is the function name,
- the description is the docstring up to ``Args:``,
- each parameter after ``drone`` becomes an input, typed from its annotation
  (float, int, bool, str, Literal[...], list[...], or a TypedDict for an
  object) and described by its line under ``Args:``,
- parameters with defaults are optional.

So to add a tool, write a normal function with type hints and a Google-style
docstring, decorate it, and import its module from ``bv_llm_sdk/__init__.py``.
Return a dict describing what happened; ``run_tool`` turns it into JSON for
the model and turns a ``DroneError`` into ``{"ok": false, "error": ...}`` so
the model can read the failure and decide what to do next.
"""

from __future__ import annotations

import base64
import inspect
import json
import re
import types
import typing
from typing import Any, Callable, Literal

from .drone import Drone, DroneError

TOOLS: dict[str, Callable[..., Any]] = {}

_JSON_TYPES = {float: "number", int: "integer", bool: "boolean", str: "string"}


def tool(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Register ``fn`` as an LLM tool. Its first parameter must be the drone."""
    TOOLS[fn.__name__] = fn
    return fn


def _split_docstring(doc: str) -> tuple[str, dict[str, str]]:
    """Return (description, {arg: description}) from a Google-style docstring."""
    doc = inspect.cleandoc(doc or "")
    sections = re.split(r"^(Args|Returns|Raises):\s*$", doc, flags=re.M)
    # Rejoin wrapped lines; keep blank lines as paragraph breaks.
    description = re.sub(r"(?<!\n)\n(?!\n)", " ", sections[0].strip())
    args: dict[str, str] = {}
    for header, body in zip(sections[1::2], sections[2::2]):
        if header != "Args":
            continue
        current = None
        for line in body.splitlines():
            match = re.match(r"^\s{0,4}(\w+)(?:\s*\(.*?\))?:\s*(.*)$", line)
            if match:
                current = match.group(1)
                args[current] = match.group(2).strip()
            elif current and line.strip():
                args[current] += " " + line.strip()
    return description, args


def _json_schema(annotation: Any) -> dict[str, Any]:
    origin = typing.get_origin(annotation)
    if origin is Literal:
        values = typing.get_args(annotation)
        return {"type": _JSON_TYPES[type(values[0])], "enum": list(values)}
    if origin in (typing.Union, types.UnionType):
        inner = [a for a in typing.get_args(annotation) if a is not type(None)]
        return _json_schema(inner[0])
    if origin is list:
        (item,) = typing.get_args(annotation)
        return {"type": "array", "items": _json_schema(item)}
    if typing.is_typeddict(annotation):
        # A TypedDict is an object; its docstring's Args: describe the keys.
        description, key_docs = _split_docstring(annotation.__doc__)
        hints = typing.get_type_hints(annotation)
        properties = {}
        for key, hint in hints.items():
            properties[key] = _json_schema(hint)
            if key in key_docs:
                properties[key]["description"] = key_docs[key]
        return {
            "type": "object",
            "description": description,
            "properties": properties,
            "required": sorted(annotation.__required_keys__),
        }
    if annotation in _JSON_TYPES:
        return {"type": _JSON_TYPES[annotation]}
    raise TypeError(f"unsupported tool parameter type: {annotation!r}")


def _input_schema(fn: Callable[..., Any]) -> tuple[str, dict[str, Any]]:
    description, arg_docs = _split_docstring(fn.__doc__)
    hints = typing.get_type_hints(fn)
    properties: dict[str, Any] = {}
    required: list[str] = []
    params = list(inspect.signature(fn).parameters.values())[1:]  # skip drone
    for param in params:
        schema = _json_schema(hints[param.name])
        if param.name in arg_docs:
            schema["description"] = arg_docs[param.name]
        if param.default is inspect.Parameter.empty:
            required.append(param.name)
        elif param.default is not None:
            schema["default"] = param.default
        properties[param.name] = schema
    return description, {
        "type": "object",
        "properties": properties,
        "required": required,
    }


def tool_schemas(
    format: Literal["anthropic", "openai"] = "anthropic",
    names: list[str] | None = None,
) -> list[dict]:
    """Registered tools, in the shape the chosen LLM API expects.

    ``names`` limits the list to those tools, in that order; unknown names
    raise ``KeyError``. Leave it out for every tool.

    ``"anthropic"`` gives ``{name, description, input_schema}`` for the
    Messages API ``tools=`` argument. ``"openai"`` gives
    ``{type: "function", function: {name, description, parameters}}``.
    """
    out = []
    for name in names if names is not None else TOOLS:
        description, schema = _input_schema(TOOLS[name])
        if format == "openai":
            out.append({
                "type": "function",
                "function": {"name": name, "description": description, "parameters": schema},
            })
        else:
            out.append({"name": name, "description": description, "input_schema": schema})
    return out


def call_tool(drone: Drone, name: str, args: dict[str, Any] | None = None) -> dict:
    """Run a tool by name and return its result as a dict, never raising.

    Failures come back as ``{"ok": False, "error": "..."}`` so an agent loop
    can hand them straight to the model.
    """
    fn = TOOLS.get(name)
    if fn is None:
        return {"ok": False, "error": f"unknown tool {name!r}. Known: {sorted(TOOLS)}"}
    try:
        result = fn(drone, **(args or {}))
    except (DroneError, TypeError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:  # a tool bug or input it did not expect
        # One bad call must not take down the agent loop; report it instead.
        return {"ok": False, "error": f"{name} failed: {type(exc).__name__}: {exc}"}
    return {"ok": True, **(result or {})}


def run_tool(drone: Drone, name: str, args: dict[str, Any] | None = None) -> str:
    """``call_tool``, serialised to the JSON string an LLM tool result wants.

    Text only. Use ``run_tool_blocks`` to also hand images (from ``look``)
    to a vision model.
    """
    result = call_tool(drone, name, args)
    result.pop("image_for_model", None)
    return json.dumps(result)


def run_tool_blocks(
    drone: Drone, name: str, args: dict[str, Any] | None = None
) -> list[dict]:
    """``call_tool`` as Anthropic tool_result content blocks.

    A JSON text block, plus an image block when the tool returned an image
    meant for the model (``look`` does). Pass the list as the ``content`` of
    a ``tool_result``.
    """
    result = call_tool(drone, name, args)
    image_path = result.pop("image_for_model", None)
    blocks: list[dict] = [{"type": "text", "text": json.dumps(result)}]
    if image_path:
        blocks.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": base64.b64encode(_image_for_model(image_path)).decode(),
            },
        })
    return blocks


def _image_for_model(path: str, max_width: int = 1280) -> bytes:
    """The JPEG at ``path``, shrunk to max_width if OpenCV is available."""
    data = open(path, "rb").read()
    try:
        import cv2
        import numpy as np
    except ImportError:
        return data
    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if image is None or image.shape[1] <= max_width:
        return data
    scale = max_width / image.shape[1]
    image = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    return cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tobytes()
