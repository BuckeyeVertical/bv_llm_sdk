"""Turn Python functions into LLM tools.

Mark a function with the registry tool decorator to add it. The tool name comes from the
function name. The description and argument help come from the docstring and the
argument types come from the type hints. The first parameter is the robot and is not
shown to the model. Errors are returned to the model so it can decide what to do next.
"""

from __future__ import annotations

import base64
import inspect
import json
import re
import types
import typing
from typing import Any, Callable, Literal

_JSON_TYPES = {float: "number", int: "integer", bool: "boolean", str: "string"}


class ToolError(RuntimeError):
    """Raised when a tool cannot do what it was asked."""


class ToolRegistry:
    """The tools for one robot by name."""

    def __init__(self) -> None:
        self.tools: dict[str, Callable[..., Any]] = {}

    def tool(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        """Register a function as a tool. Its first parameter must be the robot."""
        self.tools[fn.__name__] = fn
        return fn

    def schemas(
        self,
        format: Literal["anthropic", "openai"] = "anthropic",
        names: list[str] | None = None,
    ) -> list[dict]:
        """Return tool schemas in the Anthropic or OpenAI format. Pass names to limit the
        list.
        """
        out = []
        for name in names if names is not None else self.tools:
            description, schema = _input_schema(self.tools[name])
            if format == "openai":
                out.append({
                    "type": "function",
                    "function": {"name": name, "description": description, "parameters": schema},
                })
            else:
                out.append({"name": name, "description": description, "input_schema": schema})
        return out

    def call(self, robot: Any, name: str, args: dict[str, Any] | None = None) -> dict:
        """Run a tool by name and return its result as a dict. Errors are returned instead
        of raised.
        """
        fn = self.tools.get(name)
        if fn is None:
            return {"ok": False, "error": f"unknown tool {name!r}. Known: {sorted(self.tools)}"}
        try:
            result = fn(robot, **(args or {}))
        except (ToolError, TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:  # Any other error.
            # One bad call should not stop the agent so report it.
            return {"ok": False, "error": f"{name} failed: {type(exc).__name__}: {exc}"}
        return {"ok": True, **(result or {})}

    def run(self, robot: Any, name: str, args: dict[str, Any] | None = None) -> str:
        """Run a tool and return its result as a JSON string. Images are dropped."""
        result = self.call(robot, name, args)
        result.pop("image_for_model", None)
        return json.dumps(result)

    def run_blocks(
        self, robot: Any, name: str, args: dict[str, Any] | None = None
    ) -> list[dict]:
        """Run a tool and return its result as Anthropic content blocks. Includes any
        image.
        """
        result = self.call(robot, name, args)
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


def _split_docstring(doc: str) -> tuple[str, dict[str, str]]:
    """Split a docstring into the description and the argument descriptions."""
    doc = inspect.cleandoc(doc or "")
    sections = re.split(r"^(Args|Returns|Raises):\s*$", doc, flags=re.M)
    # Join wrapped lines but keep paragraph breaks.
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
        # A TypedDict becomes an object. Its docstring describes the keys.
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
    params = list(inspect.signature(fn).parameters.values())[1:]  # Skip the robot.
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


def _image_for_model(path: str, max_width: int = 1280) -> bytes:
    """Return the JPEG at path resized to max_width when OpenCV is installed."""
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
