"""Small flat-earth helpers for turning metres into lat/lon and back.

Accurate to well under a metre over the few kilometres a drone flies here.
"""

import math

EARTH_RADIUS_M = 6_378_137.0


def offset(lat: float, lon: float, north_m: float, east_m: float) -> tuple[float, float]:
    """The point north_m and east_m metres away from (lat, lon)."""
    dlat = math.degrees(north_m / EARTH_RADIUS_M)
    dlon = math.degrees(east_m / (EARTH_RADIUS_M * math.cos(math.radians(lat))))
    return lat + dlat, lon + dlon


def north_east(lat: float, lon: float, to_lat: float, to_lon: float) -> tuple[float, float]:
    """Metres (north, east) from (lat, lon) to (to_lat, to_lon)."""
    north = math.radians(to_lat - lat) * EARTH_RADIUS_M
    east = math.radians(to_lon - lon) * EARTH_RADIUS_M * math.cos(math.radians(lat))
    return north, east


def distance(lat: float, lon: float, to_lat: float, to_lon: float) -> float:
    """Horizontal distance in metres."""
    return math.hypot(*north_east(lat, lon, to_lat, to_lon))


def bearing(lat: float, lon: float, to_lat: float, to_lon: float) -> float:
    """Compass bearing in degrees (0 = north, 90 = east) to the other point."""
    north, east = north_east(lat, lon, to_lat, to_lon)
    return math.degrees(math.atan2(east, north)) % 360.0


def body_to_ned(forward_m: float, right_m: float, heading_deg: float) -> tuple[float, float]:
    """Rotate a (forward, right) offset into (north, east) for a heading."""
    h = math.radians(heading_deg)
    north = forward_m * math.cos(h) - right_m * math.sin(h)
    east = forward_m * math.sin(h) + right_m * math.cos(h)
    return north, east


def wrap_180(angle_deg: float) -> float:
    """Wrap an angle into [-180, 180)."""
    return (angle_deg + 180.0) % 360.0 - 180.0
