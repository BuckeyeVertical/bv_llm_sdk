from __future__ import annotations

import math

Point = tuple[float, float]


def _rotate(points: list[Point], cx: float, cy: float, deg: float) -> list[Point]:
    a = math.radians(deg)
    ca, sa = math.cos(a), math.sin(a)
    return [(cx + u * ca - v * sa, cy + u * sa + v * ca) for u, v in points]


def square(size: float, cx: float, cy: float, angle: float = 0.0) -> list[Point]:
    h = size / 2
    return _rotate([(-h, -h), (h, -h), (h, h), (-h, h), (-h, -h)], cx, cy, angle)


def triangle(size: float, cx: float, cy: float, angle: float = 0.0) -> list[Point]:
    rad = size / math.sqrt(3)
    points = [(rad * math.cos(math.radians(90 + 120 * k)), rad * math.sin(math.radians(90 + 120 * k)))
              for k in range(4)]
    return _rotate(points, cx, cy, angle)


def circle(diameter: float, cx: float, cy: float) -> list[Point]:
    rad = diameter / 2
    n = max(36, math.ceil(math.pi * diameter / 0.4))
    return [(cx + rad * math.cos(2 * math.pi * k / n), cy + rad * math.sin(2 * math.pi * k / n))
            for k in range(n + 1)]
