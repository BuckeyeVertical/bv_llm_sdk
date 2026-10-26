"""Routes and named places: multi-point flights, shapes, and remembered spots.

Places let an agent refer back to things: "remember this spot as base",
"go back to where you saw the person". Detections save themselves as places
automatically (see perception.py), and "home" always exists.
"""

from __future__ import annotations

import itertools
import math
from typing import Literal, TypedDict

from .. import geo
from ..drone import Drone, DroneError
from ..tools import tool
from ..telemetry import battery_verdict, get_position
from ._common import (
    check_geofence,
    fly_to,
    ground_amsl_m,
    home,
    require_airborne,
)
from .flight import land
from .navigation import _check_altitude, _check_speed, set_heading


class Waypoint(TypedDict, total=False):
    """One point on a route. Give exactly one of: latitude + longitude,
    north_of_home_m + east_of_home_m, place, or forward_of_start_m /
    right_of_start_m / above_start_m. The *_of_start offsets are all measured
    from the same point, where the drone was (and the way it faced) when the
    route started; they are not steps from the previous waypoint. Use them to
    draw shapes and letters. To draw in the vertical plane facing the drone
    (like a picture on a wall), use right_of_start_m as x and above_start_m
    as y, and leave forward_of_start_m at 0.

    Args:
        latitude: Latitude in decimal degrees.
        longitude: Longitude in decimal degrees.
        north_of_home_m: Metres north of home (negative = south).
        east_of_home_m: Metres east of home (negative = west).
        place: Name of a saved place, e.g. "home" or "person".
        forward_of_start_m: Metres ahead of the route's start point (negative
            = behind).
        right_of_start_m: Metres right of the route's start point (negative
            = left).
        above_start_m: Metres above the route's starting altitude (negative
            = below).
        altitude_m: Height above launch at this point. Leave out to keep the
            current altitude.
        hold_s: Seconds to hover at this point before moving on.
    """

    latitude: float
    longitude: float
    north_of_home_m: float
    east_of_home_m: float
    place: str
    forward_of_start_m: float
    right_of_start_m: float
    above_start_m: float
    altitude_m: float
    hold_s: float


_START_KEYS = ("forward_of_start_m", "right_of_start_m", "above_start_m")


class _Origin(TypedDict):
    lat: float
    lon: float
    alt_m: float
    heading_deg: float


def _origin(drone: Drone) -> _Origin:
    pos = drone.require("GLOBAL_POSITION_INT")
    return {"lat": pos.lat / 1e7, "lon": pos.lon / 1e7,
            "alt_m": pos.relative_alt / 1000.0, "heading_deg": pos.hdg / 100.0}


def place_position(drone: Drone, name: str) -> tuple[float, float, float | None]:
    """(lat, lon, altitude_m or None) of a saved place, or raise."""
    key = name.strip().lower()
    if key == "home":
        h = home(drone)
        if h is None:
            raise DroneError("PX4 has not reported a home position yet.")
        return h[0], h[1], None
    if key not in drone.places:
        known = ", ".join(["home", *drone.places]) or "home"
        raise DroneError(f"no saved place called {name!r}. Saved places: {known}.")
    return drone.places[key]


def _resolve(
    drone: Drone, wp: Waypoint, index: int, origin: _Origin
) -> tuple[float, float, float | None]:
    if any(k in wp for k in _START_KEYS):
        north, east = geo.body_to_ned(wp.get("forward_of_start_m", 0.0),
                                      wp.get("right_of_start_m", 0.0), origin["heading_deg"])
        lat, lon = geo.offset(origin["lat"], origin["lon"], north, east)
        alt = origin["alt_m"] + wp["above_start_m"] if "above_start_m" in wp else None
    elif "place" in wp:
        lat, lon, alt = place_position(drone, wp["place"])
    elif "latitude" in wp and "longitude" in wp:
        lat, lon, alt = wp["latitude"], wp["longitude"], None
    elif "north_of_home_m" in wp or "east_of_home_m" in wp:
        h = home(drone)
        if h is None:
            raise DroneError("PX4 has not reported a home position yet.")
        lat, lon = geo.offset(h[0], h[1], wp.get("north_of_home_m", 0.0),
                              wp.get("east_of_home_m", 0.0))
        alt = None
    else:
        raise DroneError(
            f"waypoint {index + 1} needs latitude+longitude, "
            "north_of_home_m/east_of_home_m, place, or forward_of_start_m/"
            "right_of_start_m/above_start_m."
        )
    if "altitude_m" in wp:
        alt = wp["altitude_m"]
    return lat, lon, alt


@tool
def fly_waypoints(
    drone: Drone,
    waypoints: list[Waypoint],
    speed_m_s: float | None = None,
    smooth: bool = False,
) -> dict:
    """Fly a route through several points in order. Points can be GPS
    coordinates, offsets from home, saved place names, or forward/right/up
    offsets from the start (for drawing shapes, letters and pictures in the
    sky), and each can set its own altitude and a hover time.

    Args:
        waypoints: The points to visit, in order (at most 100).
        speed_m_s: Horizontal speed for the whole route in m/s (0.5 to 12).
        smooth: If true, fly through each point without stopping (for
            curves and drawings); points with hold_s still stop. If false,
            stop precisely at every point.
    """
    require_airborne(drone)
    _check_speed(speed_m_s)
    route = _resolve_route(drone, waypoints)
    start = drone.require("GLOBAL_POSITION_INT")
    ground = ground_amsl_m(drone)
    for i, (lat, lon, alt, hold_s) in enumerate(route):
        last = i == len(route) - 1
        fly_to(drone, lat, lon, ground + alt, speed_m_s=speed_m_s,
               settle=not smooth or hold_s > 0 or last)
        if hold_s > 0:
            drone.sleep(min(hold_s, 300.0))
    return {"message": f"flew {len(route)} waypoint(s)",
            "ended_from_start_m": _from_start(drone, start), **get_position(drone)}


class Point(TypedDict):
    """A point on the drawing canvas.

    Args:
        x_m: Metres to the right of where the drawing starts (negative = left).
        y_m: Metres up from the start in the vertical plane, or forward of it
            in the horizontal plane (negative = down / behind).
    """

    x_m: float
    y_m: float


@tool
def draw(
    drone: Drone,
    points: list[Point],
    plane: Literal["vertical", "horizontal"] = "vertical",
    smooth: bool = False,
    speed_m_s: float | None = None,
) -> dict:
    """Draw a picture, letter or shape in the sky by flying through points on
    a flat canvas, like a pen. (0, 0) is where the drone is now and x points
    to the drone's right. In the "vertical" plane the picture stands up in
    front of the viewer like a drawing on a wall and y is up; in the
    "horizontal" plane it lies flat, seen from above, and y is forward. Lines
    are straight between consecutive points, so list the outline in drawing
    order, repeating points to retrace, and use many points for curves.
    People watching from the ground see a vertical drawing best, so pictures
    "in the sky" (a house, a letter, a smiley) are usually vertical; use
    horizontal for paths on the ground or when asked to draw it flat.

    Args:
        points: The outline, in drawing order (at most 100 points).
        plane: "vertical" (y is up) or "horizontal" (y is forward).
        smooth: Fly through the points without stopping (for curves). By
            default the drone stops at each point, which keeps corners sharp.
        speed_m_s: Speed in m/s (0.5 to 12).
    """
    if not points or any(not isinstance(p, dict) or "x_m" not in p or "y_m" not in p
                         for p in points):
        raise DroneError('each point needs x_m and y_m, e.g. {"x_m": 4, "y_m": 8}.')
    y_key = "above_start_m" if plane == "vertical" else "forward_of_start_m"
    waypoints: list[Waypoint] = [
        {"right_of_start_m": p["x_m"], y_key: p["y_m"]} for p in points  # type: ignore[misc]
    ]
    result = fly_waypoints(drone, waypoints, speed_m_s=speed_m_s, smooth=smooth)
    result["message"] = f"drew {len(points)} points in the {plane} plane"
    return result


def _resolve_route(
    drone: Drone, waypoints: list[Waypoint], max_points: int = 100, flying: bool = True
) -> list[tuple[float, float, float, float]]:
    """(lat, lon, altitude_m, hold_s) for each waypoint, all checked.

    Resolves and checks every point before the drone moves, so a bad point
    5 does not strand it after point 4. ``flying=False`` (planning only)
    skips the altitude check, so a route can be planned on the ground.
    """
    if not 1 <= len(waypoints) <= max_points:
        raise DroneError(f"give between 1 and {max_points} waypoints.")
    origin = _origin(drone)
    current_alt = origin["alt_m"]
    route = []
    for i, wp in enumerate(waypoints):
        lat, lon, alt = _resolve(drone, wp, i, origin)
        alt = current_alt if alt is None else alt
        if flying:
            _check_altitude(alt)
        check_geofence(drone, lat, lon)
        route.append((lat, lon, alt, wp.get("hold_s", 0.0)))
        current_alt = alt
    return route


def _from_start(drone: Drone, start) -> float:
    """Horizontal metres between a start position message and now. Lets the
    model check a closed shape really closed instead of assuming it did."""
    now = drone.require("GLOBAL_POSITION_INT")
    return round(geo.distance(start.lat / 1e7, start.lon / 1e7, now.lat / 1e7, now.lon / 1e7), 1)


def _legs(drone: Drone, route, return_home: bool) -> list[float]:
    """Leg lengths in metres (3D) from the drone through the route."""
    pos = drone.require("GLOBAL_POSITION_INT")
    points = [(pos.lat / 1e7, pos.lon / 1e7, pos.relative_alt / 1000.0)]
    points += [(lat, lon, alt) for lat, lon, alt, _ in route]
    if return_home:
        h = place_position(drone, "home")
        points.append((h[0], h[1], points[-1][2]))
    return [
        math.hypot(geo.distance(a[0], a[1], b[0], b[1]), b[2] - a[2])
        for a, b in zip(points, points[1:])
    ]


def _estimate_s(drone: Drone, distance_m: float, route) -> float:
    speed = drone.cruise_speed_m_s or 5.0
    # Each stop costs a few seconds of braking and settling.
    return distance_m / speed + 4.0 * len(route) + sum(r[3] for r in route)


def _limit_verdict(total_m: float, max_total_m: float | None) -> dict:
    """Whether a distance is within a limit, stated outright."""
    if max_total_m is None:
        return {}
    within = total_m <= max_total_m
    return {
        "within_limit": within,
        "limit_verdict": f"{total_m:.0f} m is {'within' if within else 'OVER'} the "
                         f"{max_total_m:.0f} m limit",
    }


@tool
def measure_route(
    drone: Drone,
    waypoints: list[Waypoint],
    return_home: bool = False,
    max_total_m: float | None = None,
) -> dict:
    """Measure a route without flying it: the length of each leg starting
    from the drone's current position, the total distance, and a time
    estimate. Use it to check a plan against a distance limit or to compare
    orderings before calling fly_waypoints.

    Args:
        waypoints: The route, in the same form fly_waypoints takes.
        return_home: Add a final leg back to home.
        max_total_m: A distance limit to check the route against; the result
            then says whether it is within it.
    """
    route = _resolve_route(drone, waypoints, flying=False)
    legs = _legs(drone, route, return_home)
    total = sum(legs)
    seconds = _estimate_s(drone, total, route)
    return {
        "legs_m": [round(d, 1) for d in legs],
        "total_m": round(total, 1),
        **_limit_verdict(total, max_total_m),
        "estimated_time_s": round(seconds),
        **battery_verdict(drone, seconds),
    }


@tool
def plan_route(
    drone: Drone,
    waypoints: list[Waypoint],
    return_home: bool = False,
    start: Literal["nearest", "farthest", "as_given"] = "nearest",
    max_total_m: float | None = None,
) -> dict:
    """Work out the order to visit a set of points with the least total
    distance, starting from the drone's current position, without flying.
    Returns the waypoints reordered (pass them to fly_waypoints) and the
    distance saved. Optimal for up to 8 points.

    Args:
        waypoints: The points to visit, in the same form fly_waypoints takes.
            Do not use the *_of_start offsets here.
        return_home: Whether the route must end back at home.
        start: Which point to visit first: "nearest" lets the planner choose
            freely, "farthest" forces the farthest point first (then the
            shortest way through the rest), "as_given" keeps the first point
            first.
        max_total_m: A distance limit; the result says whether even the best
            order is within it.
    """
    if any(k in wp for wp in waypoints for k in _START_KEYS):
        raise DroneError("plan_route needs fixed points, not *_of_start offsets.")
    route = _resolve_route(drone, waypoints, max_points=12, flying=False)
    n = len(route)
    given = sum(_legs(drone, route, return_home))

    def length(order):
        return sum(_legs(drone, [route[i] for i in order], return_home))

    pinned = None
    if start == "as_given":
        pinned = 0
    elif start == "farthest":
        first_legs = [_legs(drone, [r], False)[0] for r in route]
        pinned = max(range(n), key=first_legs.__getitem__)
    rest = [i for i in range(n) if i != pinned]
    prefix = [] if pinned is None else [pinned]
    if len(rest) <= 7:
        best = min((prefix + list(p) for p in itertools.permutations(rest)), key=length)
    else:
        # Too many for brute force: nearest neighbour, then 2-opt.
        best, todo = prefix[:], rest[:]
        while todo:
            nxt = min(todo, key=lambda i: length(best + [i]))
            best.append(nxt)
            todo.remove(nxt)
        improved = True
        while improved:
            improved = False
            for a in range(len(prefix), n - 1):
                for b in range(a + 1, n):
                    cand = best[:a] + best[a:b + 1][::-1] + best[b + 1:]
                    if length(cand) < length(best) - 1e-6:
                        best, improved = cand, True
    total = length(best)
    return {
        "waypoints": [waypoints[i] for i in best],
        "order": [i + 1 for i in best],
        "total_m": round(total, 1),
        **_limit_verdict(total, max_total_m),
        "as_given_m": round(given, 1),
        "saved_m": round(given - total, 1),
        "estimated_time_s": round(_estimate_s(drone, total, route)),
    }


def _shape_points(
    shape: str, size_m: float, clockwise: bool, width_m: float | None = None
) -> tuple[list[tuple[float, float]], bool]:
    """(forward, right) offsets from the start, and whether to stop at each."""
    turn = 1 if clockwise else -1
    if shape == "rectangle":
        long_side, short_side = size_m, width_m if width_m is not None else size_m / 2
        t = turn
        return [(long_side, 0.0), (long_side, t * short_side), (0.0, t * short_side),
                (0.0, 0.0)], True
    if shape in ("triangle", "square", "pentagon", "hexagon", "star"):
        sides = {"triangle": 3, "square": 4, "pentagon": 5, "hexagon": 6, "star": 5}[shape]
        exterior = 144.0 if shape == "star" else 360.0 / sides
        points, f, r, bearing = [], 0.0, 0.0, 0.0
        for _ in range(sides):
            f += size_m * math.cos(math.radians(bearing))
            r += size_m * math.sin(math.radians(bearing))
            points.append((f, r))
            bearing += turn * exterior
        return points, True
    if shape == "circle":
        radius = size_m / 2
        return _arc((0.0, turn * radius), radius, -90.0 * turn, turn), False
    if shape == "figure_eight":
        radius = size_m / 4
        first = _arc((0.0, turn * radius), radius, -90.0 * turn, turn)
        second = _arc((0.0, -turn * radius), radius, 90.0 * turn, -turn)
        return first + second, False
    raise DroneError(f"unknown shape {shape!r}.")


def _arc(center, radius, start_deg, direction, steps=24):
    """Points around a full circle, as (forward, right), ending at the start.

    Angles are bearings from the centre in the drone's frame (0 = forward,
    90 = right); direction +1 goes clockwise seen from above.
    """
    points = []
    for i in range(1, steps + 1):
        b = math.radians(start_deg + direction * 360.0 * i / steps)
        points.append((center[0] + radius * math.cos(b), center[1] + radius * math.sin(b)))
    return points


@tool
def fly_shape(
    drone: Drone,
    shape: Literal[
        "triangle", "square", "rectangle", "pentagon", "hexagon", "star", "circle", "figure_eight"
    ],
    size_m: float = 20.0,
    clockwise: bool = True,
    speed_m_s: float | None = None,
    width_m: float | None = None,
    pause_s: float = 0.0,
) -> dict:
    """Fly a shape in the air, starting and ending at the current position
    and starting in the direction the drone is facing. Good for demos and
    for covering ground around a point.

    Args:
        shape: The shape to fly.
        size_m: Side length for polygons and the star; long side for a
            rectangle; diameter for a circle;
            total length for a figure eight. In metres (4 to 200).
        clockwise: Turn right (true) or left (false) seen from above.
        speed_m_s: Horizontal speed in m/s (0.5 to 12).
        width_m: Rectangle only: the short side (size_m is the long side,
            flown first). Defaults to half of size_m.
        pause_s: Seconds to hover at each corner (polygons, rectangle, star).
    """
    require_airborne(drone)
    _check_speed(speed_m_s)
    if not 4 <= size_m <= 200:
        raise DroneError("size_m must be between 4 and 200.")
    pos = drone.require("GLOBAL_POSITION_INT")
    start_lat, start_lon, alt = pos.lat / 1e7, pos.lon / 1e7, pos.alt / 1000.0
    heading = pos.hdg / 100.0
    if not 0 <= pause_s <= 60:
        raise DroneError("pause_s must be between 0 and 60.")
    offsets, stop_at_each = _shape_points(shape, size_m, clockwise, width_m)
    targets = []
    for forward, right in offsets:
        north, east = geo.body_to_ned(forward, right, heading)
        targets.append(geo.offset(start_lat, start_lon, north, east))
    for lat, lon in targets:
        check_geofence(drone, lat, lon)
    for i, (lat, lon) in enumerate(targets):
        last = i == len(targets) - 1
        fly_to(drone, lat, lon, alt, speed_m_s=speed_m_s, settle=stop_at_each or last)
        if stop_at_each and pause_s > 0:
            drone.sleep(pause_s)
    return {"message": f"flew a {shape}", "ended_from_start_m": _from_start(drone, pos),
            **get_position(drone)}


@tool
def face_location(drone: Drone, latitude: float, longitude: float) -> dict:
    """Turn in place to face a GPS location.

    Args:
        latitude: Latitude of the point to face.
        longitude: Longitude of the point to face.
    """
    require_airborne(drone)
    pos = drone.require("GLOBAL_POSITION_INT")
    return set_heading(drone, geo.bearing(pos.lat / 1e7, pos.lon / 1e7, latitude, longitude))


@tool
def fly_home(drone: Drone, altitude_m: float | None = None, speed_m_s: float | None = None) -> dict:
    """Fly back over the home (launch) position and hover there, without
    landing. Use return_to_launch to fly home and land.

    Args:
        altitude_m: Height above launch to arrive at. Leave out to keep the
            current altitude.
        speed_m_s: Horizontal speed in m/s (0.5 to 12).
    """
    require_airborne(drone)
    lat, lon, _ = place_position(drone, "home")
    if altitude_m is None:
        altitude_m = drone.require("GLOBAL_POSITION_INT").relative_alt / 1000.0
    _check_altitude(altitude_m)
    _check_speed(speed_m_s)
    fly_to(drone, lat, lon, ground_amsl_m(drone) + altitude_m, speed_m_s=speed_m_s)
    return get_position(drone)


@tool
def land_at(drone: Drone, latitude: float, longitude: float) -> dict:
    """Fly to a GPS location and land there.

    Args:
        latitude: Latitude of the landing spot.
        longitude: Longitude of the landing spot.
    """
    require_airborne(drone)
    alt = drone.require("GLOBAL_POSITION_INT").alt / 1000.0
    fly_to(drone, latitude, longitude, alt)
    return land(drone)


@tool
def save_location(
    drone: Drone, name: str, latitude: float | None = None, longitude: float | None = None
) -> dict:
    """Remember a location under a name, to fly back to later with
    goto_saved_location or use in fly_waypoints. Saves the drone's current
    position (and altitude) unless a latitude and longitude are given.

    Args:
        name: Name for the place, e.g. "base" or "red car".
        latitude: Latitude to save instead of the current position.
        longitude: Longitude to save instead of the current position.
    """
    key = name.strip().lower()
    if key == "home":
        raise DroneError('"home" is reserved; pick another name.')
    if (latitude is None) != (longitude is None):
        raise DroneError("give both latitude and longitude, or neither.")
    if latitude is None:
        pos = drone.require("GLOBAL_POSITION_INT")
        drone.places[key] = (pos.lat / 1e7, pos.lon / 1e7, round(pos.relative_alt / 1000.0, 1))
    else:
        drone.places[key] = (latitude, longitude, None)
    lat, lon, alt = drone.places[key]
    return {"saved": key, "latitude": round(lat, 7), "longitude": round(lon, 7), "altitude_m": alt}


@tool
def list_saved_locations(drone: Drone) -> dict:
    """List every saved place (including "home" and objects found by the
    camera) with its position and distance from the drone.
    """
    pos = drone.require("GLOBAL_POSITION_INT")
    here = (pos.lat / 1e7, pos.lon / 1e7)
    out = []
    for name in ["home", *drone.places]:
        try:
            lat, lon, alt = place_position(drone, name)
        except DroneError:
            continue
        out.append({
            "name": name,
            "latitude": round(lat, 7),
            "longitude": round(lon, 7),
            "altitude_m": None if alt is None else round(alt, 1),
            "distance_m": round(geo.distance(*here, lat, lon), 1),
            "bearing_deg": round(geo.bearing(*here, lat, lon), 1),
        })
    return {"places": out}


@tool
def goto_saved_location(
    drone: Drone,
    name: str,
    north_m: float = 0.0,
    east_m: float = 0.0,
    altitude_m: float | None = None,
    speed_m_s: float | None = None,
) -> dict:
    """Fly to a saved place, optionally offset from it, e.g. "10 m north of
    the person" is name="person", north_m=10.

    Args:
        name: The saved place ("home", a name from save_location, or an
            object the camera found such as "person").
        north_m: Metres north of the place (negative = south).
        east_m: Metres east of the place (negative = west).
        altitude_m: Height above launch to arrive at. Leave out to use the
            altitude saved with the place, or else the current altitude.
        speed_m_s: Horizontal speed in m/s (0.5 to 12).
    """
    require_airborne(drone)
    lat, lon, saved_alt = place_position(drone, name)
    lat, lon = geo.offset(lat, lon, north_m, east_m)
    if altitude_m is None:
        altitude_m = saved_alt
    if altitude_m is None:
        altitude_m = drone.require("GLOBAL_POSITION_INT").relative_alt / 1000.0
    _check_altitude(altitude_m)
    _check_speed(speed_m_s)
    fly_to(drone, lat, lon, ground_amsl_m(drone) + altitude_m, speed_m_s=speed_m_s)
    return {"arrived_at": name, **get_position(drone)}
