"""Convert between meters and latitude and longitude.

Uses a flat earth model which is accurate over a few kilometers.
"""

import math

EARTH_RADIUS_M = 6_378_137.0


def offset(lat: float, lon: float, north_m: float, east_m: float) -> tuple[float, float]:
    """Return the point north_m and east_m meters away."""
    dlat = math.degrees(north_m / EARTH_RADIUS_M)
    dlon = math.degrees(east_m / (EARTH_RADIUS_M * math.cos(math.radians(lat))))
    return lat + dlat, lon + dlon


def north_east(lat: float, lon: float, to_lat: float, to_lon: float) -> tuple[float, float]:
    """Return the north and east distance in meters between two points."""
    north = math.radians(to_lat - lat) * EARTH_RADIUS_M
    east = math.radians(to_lon - lon) * EARTH_RADIUS_M * math.cos(math.radians(lat))
    return north, east


def distance(lat: float, lon: float, to_lat: float, to_lon: float) -> float:
    """Horizontal distance in meters."""
    return math.hypot(*north_east(lat, lon, to_lat, to_lon))


def bearing(lat: float, lon: float, to_lat: float, to_lon: float) -> float:
    """Compass bearing in degrees to the other point."""
    north, east = north_east(lat, lon, to_lat, to_lon)
    return math.degrees(math.atan2(east, north)) % 360.0


def body_to_ned(forward_m: float, right_m: float, heading_deg: float) -> tuple[float, float]:
    """Rotate a forward and right offset into north and east for a heading."""
    h = math.radians(heading_deg)
    north = forward_m * math.cos(h) - right_m * math.sin(h)
    east = forward_m * math.sin(h) + right_m * math.cos(h)
    return north, east


def wrap_180(angle_deg: float) -> float:
    """Wrap an angle into the range negative 180 to 180."""
    return (angle_deg + 180.0) % 360.0 - 180.0
