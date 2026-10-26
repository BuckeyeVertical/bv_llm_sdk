"""Seeing with the drone's camera: photos, object detection and searches.

The camera points straight down, so everything here works best a little
above the treetops' clearance, around 10-30 m. Higher sees more ground but
makes objects smaller; a person is reliably detected up to about 30 m.

Detections come back with a position on the ground (latitude/longitude and
metres north/east of the drone and of home), so the result of a search can
be passed straight to goto_location, orbit, or fly_to_object.
"""

from __future__ import annotations

import dataclasses
import math
import time
from pathlib import Path

from .. import geo
from ..camera import Frame
from ..drone import Drone, DroneError
from ..tools import tool
from ..telemetry import _home, battery_verdict, get_position
from ..vision import Detection, get_detector, resolve_color
from ._common import fly_to, ground_amsl_m, reposition, require_airborne, stop

PHOTO_DIR = Path("photos")

# A search must see the object in this many frames in a row before it stops,
# so one odd frame cannot end it.
_CONFIRM_FRAMES = 2
# Overlap between neighbouring search_area lanes, as a share of image width.
_LANE_OVERLAP = 0.3
# survey_area treats sightings this close together as one object. Positions
# estimated in forward flight are off by up to a few metres, so one object
# seen from two lanes can land ~5 m apart.
_SAME_OBJECT_M = 6.0


def _save(frame: Frame, detections: list[Detection] | None = None, tag: str = "photo") -> str:
    """Write the frame to photos/, with boxes drawn if detections are given."""
    PHOTO_DIR.mkdir(exist_ok=True)
    path = PHOTO_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}-{frame.sequence}-{tag}.jpg"
    if not detections:
        path.write_bytes(frame.jpeg)
        return str(path)
    import cv2
    import numpy as np

    image = cv2.imdecode(np.frombuffer(frame.jpeg, np.uint8), cv2.IMREAD_COLOR)
    for det in detections:
        p1, p2 = (int(det.x1), int(det.y1)), (int(det.x2), int(det.y2))
        cv2.rectangle(image, p1, p2, (0, 0, 255), 3)
        cv2.putText(image, f"{det.label} {det.confidence:.2f}", (p1[0], max(p1[1] - 8, 20)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 255), 2)
    cv2.imwrite(str(path), image)
    return str(path)


def _ground_size(frame: Frame, det: Detection) -> float:
    """Length on the ground of the box's longer side, in metres."""
    return max(det.x2 - det.x1, det.y2 - det.y1) / frame.fx * frame.altitude_m


def _locate(drone: Drone, frame: Frame, det: Detection) -> dict:
    """Where a detection is on the ground, in every form an LLM might want."""
    north, east = frame.pixel_to_ground(*det.center)
    lat, lon = geo.offset(frame.latitude, frame.longitude, north, east)
    # Offsets from the drone's position *now*, which may differ from the
    # frame's if the drone has moved since.
    pos = drone.require("GLOBAL_POSITION_INT")
    now_north, now_east = geo.north_east(pos.lat / 1e7, pos.lon / 1e7, lat, lon)
    out = {
        "label": det.label,
        "confidence": round(det.confidence, 2),
        "color": det.color,
        "size_m": round(_ground_size(frame, det), 1),
        "latitude": round(lat, 7),
        "longitude": round(lon, 7),
        "north_of_drone_m": round(now_north, 1),
        "east_of_drone_m": round(now_east, 1),
        "distance_m": round(math.hypot(now_north, now_east), 1),
        "bearing_deg": round(math.degrees(math.atan2(now_east, now_north)) % 360, 1),
    }
    home = _home(drone)
    if home is not None:
        hn, he = geo.north_east(home.latitude / 1e7, home.longitude / 1e7, lat, lon)
        out["north_of_home_m"] = round(hn, 1)
        out["east_of_home_m"] = round(he, 1)
    return out


def _remember(drone: Drone, located: dict, name: str | None = None) -> None:
    """Save a located object as a place so later tools can refer to it."""
    key = name or located["label"]
    drone.places[key] = (located["latitude"], located["longitude"], None)
    located["saved_as"] = key


def _query(object_name: str | None, color: str | None = None) -> tuple:
    """(labels, colour) to filter detections by; either may be None."""
    labels = {get_detector().resolve(object_name)} if object_name else None
    return labels, resolve_color(color) if color else None


def _detect(frame: Frame, query: tuple, min_confidence: float) -> list[Detection]:
    labels, color = query
    return get_detector().detect(frame.jpeg, labels, min_confidence, color)


def _fresh_frame(drone: Drone) -> Frame:
    """A frame taken after this call, so it reflects the drone's current view."""
    camera = drone.camera
    return camera.next(camera.latest(max_age_s=float("inf")))


def _settle(drone: Drone, timeout_s: float = 8.0) -> None:
    """Wait until the drone has (nearly) stopped moving."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        pos = drone.wait_for("GLOBAL_POSITION_INT", timeout_s=3.0)
        if math.sqrt(pos.vx**2 + pos.vy**2 + pos.vz**2) / 100.0 < 0.3:
            return


@tool
def take_photo(drone: Drone) -> dict:
    """Take a photo with the downward-facing camera and save it to disk.
    Returns the file path and how much ground the photo covers.
    """
    frame = _fresh_frame(drone)
    return {
        "image_path": _save(frame),
        "ground_width_m": round(frame.ground_width_m, 1),
        "ground_height_m": round(frame.ground_height_m, 1),
    }


@tool
def look(drone: Drone) -> dict:
    """Look through the downward-facing camera yourself: the current image is
    returned to you so you can describe the scene or spot things the object
    detector does not know (it only knows the 80 COCO classes). The top of
    the image is the direction the drone is facing.
    """
    frame = _fresh_frame(drone)
    path = _save(frame, tag="look")
    return {
        "image_path": path,
        "image_for_model": path,
        "heading_deg": round(frame.heading_deg, 1),
        "altitude_m": round(frame.altitude_m, 1),
        "ground_width_m": round(frame.ground_width_m, 1),
        "ground_height_m": round(frame.ground_height_m, 1),
    }


@tool
def detect_objects(
    drone: Drone,
    object_name: str | None = None,
    min_confidence: float = 0.35,
    color: str | None = None,
) -> dict:
    """Run the object detector on what the camera sees right now (the ground
    below the drone) and report each object with its GPS position and its
    distance and direction from the drone. Knows the 80 COCO classes such as
    person, car, truck, bicycle, dog, boat, backpack, umbrella, chair, and
    reports each object's main colour.

    Args:
        object_name: Only report this kind of object, e.g. "person" or "car".
            Leave out to report everything.
        min_confidence: Ignore detections less certain than this (0 to 1).
        color: Only objects of this colour, e.g. "red" (red, orange, yellow,
            green, blue, purple, brown, white, gray, black).
    """
    query = _query(object_name, color)
    frame = _fresh_frame(drone)
    detections = _detect(frame, query, min_confidence)
    located = [_locate(drone, frame, d) for d in detections]
    # Save each kind's best detection under its label ("person", "car").
    seen: set[str] = set()
    for item in located:
        if item["label"] not in seen:
            _remember(drone, item)
            seen.add(item["label"])
    return {
        "count": len(located),
        "detections": located,
        "image_path": _save(frame, detections, tag="detect"),
        "ground_width_m": round(frame.ground_width_m, 1),
        "ground_height_m": round(frame.ground_height_m, 1),
    }


def _scan_while_flying(
    drone: Drone,
    query: tuple,
    min_confidence: float,
    lat: float,
    lon: float,
    alt_amsl_m: float,
    speed_m_s: float,
) -> tuple[Frame, Detection] | None:
    """Fly to a point, running the detector on each new frame on the way.

    Once the object is confirmed the drone brakes, flies back over where it
    was seen, and looks again from overhead. Returns (frame, detection), or
    None if the drone arrived without seeing it.
    """
    state: dict = {"last": None, "hits": 0, "found": None}

    def check() -> bool:
        frame = drone.camera.latest(max_age_s=float("inf"))
        if state["last"] is not None and frame.sequence == state["last"].sequence:
            return False
        state["last"] = frame
        detections = _detect(frame, query, min_confidence)
        if detections:
            state["hits"] += 1
            state["found"] = (frame, detections[0])
        else:
            state["hits"] = 0
        return state["hits"] >= _CONFIRM_FRAMES

    if fly_to(drone, lat, lon, alt_amsl_m, speed_m_s=speed_m_s, on_sample=check):
        return None
    stop(drone)
    _settle(drone)
    # By the time two frames agree the drone has flown on, possibly far
    # enough that the object is behind the camera's view. Go back over it,
    # then look again: level and still gives a more accurate position.
    seen_frame, seen = state["found"]
    north, east = seen_frame.pixel_to_ground(*seen.center)
    lat, lon = geo.offset(seen_frame.latitude, seen_frame.longitude, north, east)
    fly_to(drone, lat, lon, alt_amsl_m)
    frame = _fresh_frame(drone)
    detections = _detect(frame, query, min_confidence * 0.8)
    if detections:
        return frame, detections[0]
    return state["found"]


def _found(drone: Drone, frame: Frame, det: Detection) -> dict:
    located = _locate(drone, frame, det)
    _remember(drone, located)
    return {
        "found": True,
        "object": located,
        "image_path": _save(frame, [det], tag="found"),
        "drone": get_position(drone),
    }


@tool
def search_for_object(
    drone: Drone,
    object_name: str,
    distance_m: float = 50.0,
    heading_deg: float | None = None,
    speed_m_s: float = 3.0,
    min_confidence: float = 0.5,
    color: str | None = None,
) -> dict:
    """Fly in a straight line looking for an object with the camera, and stop
    over it as soon as it is seen. Returns whether it was found and, if so,
    its GPS position. If not found, the drone hovers at the end of the line.

    Args:
        object_name: What to look for, e.g. "person", "car", "dog" (one of the
            80 COCO classes).
        distance_m: How far to fly while searching, in metres.
        heading_deg: Compass direction to fly (0 = north, 90 = east). Leave
            out to fly the way the drone is facing.
        speed_m_s: Search speed. Slower is more reliable; 2 to 5 is sensible.
        min_confidence: How sure the detector must be (0 to 1).
        color: Only objects of this colour, e.g. "red" (red, orange, yellow,
            green, blue, purple, brown, white, gray, black).
    """
    require_airborne(drone)
    query = _query(object_name, color)
    if not 1 <= distance_m <= 1000:
        raise DroneError("distance_m must be between 1 and 1000.")
    if not 0.5 <= speed_m_s <= 8:
        raise DroneError("speed_m_s must be between 0.5 and 8 for a search.")
    pos = drone.require("GLOBAL_POSITION_INT")
    if heading_deg is None:
        heading_deg = pos.hdg / 100.0
    north = distance_m * math.cos(math.radians(heading_deg))
    east = distance_m * math.sin(math.radians(heading_deg))
    lat, lon = geo.offset(pos.lat / 1e7, pos.lon / 1e7, north, east)

    # It may already be in view.
    frame = _fresh_frame(drone)
    detections = _detect(frame, query, min_confidence)
    if detections:
        return _found(drone, frame, detections[0])

    hit = _scan_while_flying(drone, query, min_confidence, lat, lon, pos.alt / 1000.0, speed_m_s)
    if hit:
        return _found(drone, *hit)
    return {"found": False, "searched_m": distance_m, "drone": get_position(drone)}


def _lawnmower(
    frame: Frame, north_m: float, east_m: float
) -> tuple[list[tuple[float, float]], int, float]:
    """Lane end points covering a rectangle from the frame's position.

    Lanes run north-south; the drone faces along them, so the image's width
    spans across them. Returns (points, lane count, lane spacing).
    """
    spacing = frame.ground_width_m * (1 - _LANE_OVERLAP)
    lanes = max(1, math.ceil(abs(east_m) / spacing) + 1)
    lane_step = east_m / (lanes - 1) if lanes > 1 else 0.0
    points = []
    for lane in range(lanes):
        ends = (0.0, north_m) if lane % 2 == 0 else (north_m, 0.0)
        for end_north in ends:
            points.append(geo.offset(frame.latitude, frame.longitude, end_north, lane * lane_step))
    return points, lanes, spacing


def _go_to_start(drone: Drone, place: str | None, altitude_m: float | None) -> None:
    """Fly to where an area search or survey should begin."""
    if place is None and altitude_m is None:
        return
    if altitude_m is not None and not 2.0 <= altitude_m <= 120.0:
        raise DroneError("altitude_m must be between 2 and 120.")
    pos = drone.require("GLOBAL_POSITION_INT")
    lat, lon = pos.lat / 1e7, pos.lon / 1e7
    if place is not None:
        from .routes import place_position

        lat, lon, _ = place_position(drone, place)
    alt = ground_amsl_m(drone) + altitude_m if altitude_m is not None else pos.alt / 1000.0
    fly_to(drone, lat, lon, alt)


def _plan(
    drone: Drone, north_m: float, east_m: float, speed_m_s: float, altitude_m: float | None
) -> dict:
    """What a lawnmower pattern over the area would cost, without flying it."""
    pos = drone.require("GLOBAL_POSITION_INT")
    alt = altitude_m if altitude_m is not None else pos.relative_alt / 1000.0
    if alt < 2.0:
        alt = 15.0  # on the ground: plan for a typical search altitude
    # The footprint only depends on altitude and the camera, so a synthetic
    # frame at the planned altitude gives the lane spacing.
    frame = Frame(b"", 1920, 1080, 1148.2, 1148.2, 960, 540, 0, 0.0,
                  pos.lat / 1e7, pos.lon / 1e7, alt, 0.0)
    try:
        frame = dataclasses.replace(drone.camera.latest(max_age_s=5.0, timeout_s=1.0),
                                    altitude_m=alt)
    except DroneError:
        pass
    legs, lanes, spacing = _lawnmower(frame, north_m, east_m)
    path = sum(geo.distance(*a, *b) for a, b in zip([(frame.latitude, frame.longitude)] + legs, legs))
    seconds = path / speed_m_s + 6.0 * len(legs)
    return {
        "plan_only": True,
        "altitude_m": round(alt, 1),
        "lanes": lanes,
        "lane_spacing_m": round(spacing, 1),
        "path_m": round(path),
        "estimated_time_s": round(seconds),
        **battery_verdict(drone, seconds),
        "note": "fly higher for wider lanes and a shorter path, at the cost of smaller objects",
    }


@tool
def search_area(
    drone: Drone,
    object_name: str,
    north_m: float,
    east_m: float,
    speed_m_s: float = 4.0,
    min_confidence: float = 0.5,
    color: str | None = None,
    plan_only: bool = False,
    altitude_m: float | None = None,
    start_place: str | None = None,
) -> dict:
    """Search a rectangle of ground for an object by flying back-and-forth
    lanes over it (a lawnmower pattern), stopping over the object as soon as
    it is seen. The rectangle starts at the drone's position and extends
    north_m north and east_m east (negative for south / west), or from
    start_place if given, e.g. start_place="home" for "the field north-east of
    home". Lane spacing
    comes from the camera's footprint, so fly higher to cover ground faster.

    Args:
        object_name: What to look for, e.g. "person", "car" (one of the 80
            COCO classes).
        north_m: Length of the area to the north in metres (negative = south).
        east_m: Width of the area to the east in metres (negative = west).
        speed_m_s: Search speed. Slower is more reliable.
        min_confidence: How sure the detector must be (0 to 1).
        color: Only objects of this colour, e.g. "red" (red, orange, yellow,
            green, blue, purple, brown, white, gray, black).
        plan_only: Do not fly; just report the lanes, distance, time and
            whether it fits in the battery.
        altitude_m: Height above launch to search at (climbs or descends
            there first). Leave out to keep the current altitude.
        start_place: A saved place (e.g. "home") to start the area from;
            the drone flies there first. Leave out to start where it is.
    """
    query = _query(object_name, color)
    if plan_only:
        return _plan(drone, north_m, east_m, speed_m_s, altitude_m)
    require_airborne(drone)
    _go_to_start(drone, start_place, altitude_m)
    if abs(north_m) > 500 or abs(east_m) > 500:
        raise DroneError("the search area is limited to 500 m on each side.")
    if not 0.5 <= speed_m_s <= 8:
        raise DroneError("speed_m_s must be between 0.5 and 8 for a search.")

    frame = _fresh_frame(drone)
    detections = _detect(frame, query, min_confidence)
    if detections:
        return _found(drone, frame, detections[0])

    legs, lanes, spacing = _lawnmower(frame, north_m, east_m)
    alt = drone.require("GLOBAL_POSITION_INT").alt / 1000.0
    for lat, lon in legs:
        hit = _scan_while_flying(drone, query, min_confidence, lat, lon, alt, speed_m_s)
        if hit:
            return _found(drone, *hit)
    return {
        "found": False,
        "lanes_flown": lanes,
        "lane_spacing_m": round(spacing, 1),
        "drone": get_position(drone),
    }


@tool
def fly_to_object(
    drone: Drone,
    object_name: str,
    altitude_m: float | None = None,
    min_confidence: float = 0.35,
) -> dict:
    """Fly to hover directly above an object the camera can currently see,
    such as one just found by detect_objects or a search. If several are
    visible, goes to the most confident one.

    Args:
        object_name: The object to go to, e.g. "person".
        altitude_m: Height above the launch point to hover at once there.
            Leave out to keep the current altitude.
        min_confidence: How sure the detector must be (0 to 1).
    """
    require_airborne(drone)
    query = _query(object_name)
    target = None
    # Two passes: the second, taken from nearly overhead, corrects the
    # first estimate.
    for _ in range(2):
        frame = _fresh_frame(drone)
        detections = _detect(frame, query, min_confidence)
        if not detections:
            if target is None:
                raise DroneError(
                    f"no {object_name} in view. Use search_for_object or "
                    "search_area to find one first."
                )
            break
        target = _locate(drone, frame, detections[0])
        alt_amsl = drone.require("GLOBAL_POSITION_INT").alt / 1000.0
        fly_to(drone, target["latitude"], target["longitude"], alt_amsl)
        if target["distance_m"] < 1.0:
            break
    if altitude_m is not None:
        fly_to(drone, target["latitude"], target["longitude"], ground_amsl_m(drone) + altitude_m)
    return {"object": target, "drone": get_position(drone)}


@tool
def survey_area(
    drone: Drone,
    north_m: float,
    east_m: float,
    object_name: str | None = None,
    speed_m_s: float = 4.0,
    min_confidence: float = 0.5,
    color: str | None = None,
    save_photos: bool = False,
    plan_only: bool = False,
    altitude_m: float | None = None,
    start_place: str | None = None,
) -> dict:
    """Fly over a whole rectangle and report every object seen, with a count
    per kind and the position of each one, e.g. "how many people are in the
    field?". Unlike search_area it does not stop at the first find. Each
    object is saved as a place named like "person_1", "person_2".

    The detector was trained on ground-level photos, so from above it can
    mislabel things (a person lying down may come out as "kite"). Pass
    object_name when you know what you are counting; otherwise check each
    object's size_m and also_seen_as, or go and look at it.

    Args:
        north_m: Length of the area to the north in metres (negative = south).
        east_m: Width of the area to the east in metres (negative = west).
        object_name: Only count this kind of object. Leave out to count
            everything the detector knows.
        speed_m_s: Survey speed. Slower is more reliable.
        min_confidence: How sure the detector must be (0 to 1).
        color: Only objects of this colour, e.g. "red" (red, orange, yellow,
            green, blue, purple, brown, white, gray, black).
        save_photos: Also save a photo every time the drone has moved a
            frame's length, giving a gap-free image series of the area.
        plan_only: Do not fly; just report the lanes, distance, time and
            whether it fits in the battery.
        altitude_m: Height above launch to survey at (climbs or descends
            there first). Leave out to keep the current altitude.
        start_place: A saved place (e.g. "home") to start the area from;
            the drone flies there first. Leave out to start where it is.
    """
    query = _query(object_name, color)
    if plan_only:
        return _plan(drone, north_m, east_m, speed_m_s, altitude_m)
    require_airborne(drone)
    _go_to_start(drone, start_place, altitude_m)
    if abs(north_m) > 500 or abs(east_m) > 500:
        raise DroneError("the survey area is limited to 500 m on each side.")
    if not 0.5 <= speed_m_s <= 8:
        raise DroneError("speed_m_s must be between 0.5 and 8 for a survey.")

    # One cluster per physical object: sightings within _SAME_OBJECT_M of each
    # other are the same thing seen from another frame, even if the detector
    # named it differently (seen from above, a lying person can come out as
    # "person" in one frame and "kite" in the next). Each cluster keeps a
    # confidence-weighted vote per label.
    clusters: list[dict] = []
    state: dict = {"last": None, "photo_at": None}
    photos: list[str] = []

    def collect() -> bool:
        frame = drone.camera.latest(max_age_s=float("inf"))
        if state["last"] is not None and frame.sequence == state["last"].sequence:
            return False
        state["last"] = frame
        at = (frame.latitude, frame.longitude)
        if save_photos and (
            state["photo_at"] is None
            or geo.distance(*state["photo_at"], *at) >= 0.8 * frame.ground_height_m
        ):
            photos.append(_save(frame, tag="survey"))
            state["photo_at"] = at
        for det in _detect(frame, query, min_confidence):
            north, east = frame.pixel_to_ground(*det.center)
            lat, lon = geo.offset(frame.latitude, frame.longitude, north, east)
            size = _ground_size(frame, det)
            for c in clusters:
                if geo.distance(c["lat"] / c["n"], c["lon"] / c["n"], lat, lon) < _SAME_OBJECT_M:
                    break
            else:
                c = {"votes": {}, "best": {}, "colors": {}, "lat": 0.0, "lon": 0.0,
                     "n": 0, "size": 0.0}
                clusters.append(c)
            c["lat"] += lat
            c["lon"] += lon
            c["n"] += 1
            c["size"] = max(c["size"], size)
            c["votes"][det.label] = c["votes"].get(det.label, 0.0) + det.confidence
            c["best"][det.label] = max(c["best"].get(det.label, 0.0), det.confidence)
            c["colors"][det.color] = c["colors"].get(det.color, 0) + 1
        return False

    frame = _fresh_frame(drone)
    legs, lanes, spacing = _lawnmower(frame, north_m, east_m)
    alt = drone.require("GLOBAL_POSITION_INT").alt / 1000.0
    for lat, lon in legs:
        fly_to(drone, lat, lon, alt, speed_m_s=speed_m_s, on_sample=collect)

    # Seen once is likely a false alarm; real objects stay in view for
    # several frames.
    objects, counts = [], {}
    for c in clusters:
        if c["n"] < _CONFIRM_FRAMES:
            continue
        ranked = sorted(c["votes"], key=c["votes"].get, reverse=True)
        label = ranked[0]
        counts[label] = counts.get(label, 0) + 1
        located = {
            "label": label,
            "confidence": round(c["best"][label], 2),
            "color": max(c["colors"], key=c["colors"].get),
            "size_m": round(c["size"], 1),
            "latitude": round(c["lat"] / c["n"], 7),
            "longitude": round(c["lon"] / c["n"], 7),
            "sightings": c["n"],
        }
        if len(ranked) > 1:
            located["also_seen_as"] = ranked[1:]
        _remember(drone, located, f"{label}_{counts[label]}")
        objects.append(located)
    return {
        "counts": counts,
        "objects": objects,
        "lanes_flown": lanes,
        "lane_spacing_m": round(spacing, 1),
        **({"photos_saved": len(photos), "photo_dir": str(PHOTO_DIR)} if save_photos else {}),
        "drone": get_position(drone),
    }


@tool
def track_object(
    drone: Drone, object_name: str, duration_s: float = 30.0, min_confidence: float = 0.35
) -> dict:
    """Follow an object the camera can see, staying directly above it as it
    moves, for a set time. Stops early if the object is lost for more than
    5 seconds. The drone hovers in place afterwards.

    Args:
        object_name: The object to follow, e.g. "person", "car", "dog".
        duration_s: How long to follow it, in seconds (at most 300).
        min_confidence: How sure the detector must be (0 to 1).
    """
    require_airborne(drone)
    query = _query(object_name)
    if not 1 <= duration_s <= 300:
        raise DroneError("duration_s must be between 1 and 300.")
    alt = drone.require("GLOBAL_POSITION_INT").alt / 1000.0
    start = time.time()
    last_seen = time.time()
    frame = None
    located = None
    updates = 0
    while time.time() - start < duration_s:
        frame = drone.camera.next(frame)
        detections = _detect(frame, query, min_confidence)
        if not detections:
            if time.time() - last_seen > 5.0:
                stop(drone)
                return {"following": False, "message": f"lost the {object_name}",
                        "last_seen": located, "drone": get_position(drone)}
            continue
        # Follow the detection nearest the image centre: the one we are on.
        det = min(detections, key=lambda d: (d.center[0] - frame.cx) ** 2 + (d.center[1] - frame.cy) ** 2)
        located = _locate(drone, frame, det)
        last_seen = time.time()
        updates += 1
        if located["distance_m"] > 0.7:
            reposition(drone, located["latitude"], located["longitude"], alt)
    stop(drone)
    if located is None:
        raise DroneError(f"no {object_name} in view. Find one first with a search.")
    _remember(drone, located)
    return {"following": True, "message": f"followed the {object_name} for {duration_s:g} s",
            "updates": updates, "object": located, "drone": get_position(drone)}


@tool
def record_video(drone: Drone, seconds: float = 10.0) -> dict:
    """Record a video from the camera for a number of seconds while the drone
    keeps doing whatever it is doing (hovering, unless another command is
    running). Saves an .mp4 file and returns its path.

    Args:
        seconds: Length of the recording (1 to 60).
    """
    if not 1 <= seconds <= 60:
        raise DroneError("seconds must be between 1 and 60.")
    import cv2
    import numpy as np

    frames: list[bytes] = []
    frame = None
    start = time.time()
    while time.time() - start < seconds:
        frame = drone.camera.next(frame)
        frames.append(frame.jpeg)
    elapsed = time.time() - start
    PHOTO_DIR.mkdir(exist_ok=True)
    path = PHOTO_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}-video.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"),
                             max(1.0, len(frames) / elapsed), (frame.width, frame.height))
    for jpeg in frames:
        writer.write(cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR))
    writer.release()
    return {"video_path": str(path), "frames": len(frames), "seconds": round(elapsed, 1)}
