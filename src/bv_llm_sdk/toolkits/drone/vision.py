"""Object detection on camera frames with a YOLO model.

The model loads on first use and is shared by every tool. Each frame is checked as is
and rotated 90 degrees since objects seen from above can be missed or mislabeled at one
angle.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path

from .drone import DroneError

# Model folder at the repo root. Override with BV_YOLO_MODEL.
DEFAULT_MODEL = Path(__file__).resolve().parents[4] / "models" / "yolo11m.pt"
# Objects look small from the air so use a larger image size.
IMAGE_SIZE = int(os.environ.get("BV_YOLO_IMGSZ", "1280"))

# Common words mapped to COCO class names.
_SYNONYMS = {
    "people": "person", "human": "person", "man": "person", "woman": "person",
    "child": "person", "kid": "person", "mannequin": "person", "body": "person",
    "pedestrian": "person", "someone": "person",
    "bike": "bicycle", "motorbike": "motorcycle", "plane": "airplane",
    "aeroplane": "airplane", "phone": "cell phone", "cellphone": "cell phone",
    "mobile phone": "cell phone", "sofa": "couch", "television": "tv",
    "monitor": "tv", "puppy": "dog", "kitten": "cat", "ball": "sports ball",
    "football": "sports ball", "soccer ball": "sports ball", "table": "dining table",
    "plant": "potted plant", "lorry": "truck", "pickup": "truck", "van": "car",
    "automobile": "car", "hydrant": "fire hydrant", "glasses": "wine glass",
    "racket": "tennis racket", "backpack": "backpack", "bag": "handbag",
}


COLORS = ("red", "orange", "yellow", "green", "blue", "purple", "brown",
          "white", "gray", "black")
_COLOR_SYNONYMS = {"grey": "gray", "silver": "gray", "maroon": "red", "pink": "red",
                   "violet": "purple", "navy": "blue", "tan": "brown", "beige": "brown",
                   "gold": "yellow", "dark": "black"}


# How much more a color must cover in the box than around it to count.
_CHROMATIC_MIN = 0.08


def resolve_color(color: str) -> str:
    name = color.strip().lower()
    name = _COLOR_SYNONYMS.get(name, name)
    if name not in COLORS:
        raise DroneError(f"unknown colour {color!r}. Known: {', '.join(COLORS)}.")
    return name


def _color_bins(hsv):
    """Return a color index for each pixel in an HSV region."""
    import numpy as np

    h, s, v = hsv[..., 0].astype(int), hsv[..., 1].astype(int), hsv[..., 2].astype(int)
    out = np.full(h.shape, COLORS.index("gray"))
    chroma = s >= 70
    hue_bins = np.select(
        [(h < 8) | (h >= 165), h < 20, h < 33, h < 85, h < 130],
        [0, 1, 2, 3, 4], default=5,
    )
    out[chroma] = hue_bins[chroma]
    out[chroma & (hue_bins == 1) & (v < 130)] = COLORS.index("brown")
    out[~chroma & (v >= 170)] = COLORS.index("white")
    out[~chroma & (v < 60)] = COLORS.index("black")
    out[chroma & (v < 40)] = COLORS.index("black")
    return out


def dominant_color(image, det: "Detection") -> str:
    """Return the color that sets the object apart from the ground around it."""
    import cv2
    import numpy as np

    H, W = image.shape[:2]
    x1, y1, x2, y2 = int(det.x1), int(det.y1), int(det.x2), int(det.y2)
    pad = max(4, int(0.25 * max(x2 - x1, y2 - y1)))
    X1, Y1, X2, Y2 = max(0, x1 - pad), max(0, y1 - pad), min(W, x2 + pad), min(H, y2 + pad)
    region = cv2.cvtColor(image[Y1:Y2, X1:X2], cv2.COLOR_BGR2HSV)
    bins = _color_bins(region)
    inside = np.zeros(bins.shape, bool)
    inside[y1 - Y1:y2 - Y1, x1 - X1:x2 - X1] = True
    n = len(COLORS)
    hist_in = np.bincount(bins[inside], minlength=n) / max(1, inside.sum())
    hist_out = np.bincount(bins[~inside], minlength=n) / max(1, (~inside).sum())
    excess = hist_in - hist_out if (~inside).sum() else hist_in
    # Prefer a real color over white or gray or black even if it covers less of the box.
    chromatic = [COLORS.index(c) for c in COLORS[:7]]
    best = max(chromatic, key=lambda i: excess[i])
    if excess[best] >= _CHROMATIC_MIN:
        return COLORS[best]
    return COLORS[int(excess.argmax())]


@dataclass
class Detection:
    label: str
    confidence: float
    x1: float
    y1: float
    x2: float
    y2: float
    color: str = ""

    @property
    def center(self) -> tuple[float, float]:
        return (self.x1 + self.x2) / 2, (self.y1 + self.y2) / 2

    def iou(self, other: Detection) -> float:
        ix = max(0.0, min(self.x2, other.x2) - max(self.x1, other.x1))
        iy = max(0.0, min(self.y2, other.y2) - max(self.y1, other.y1))
        inter = ix * iy
        union = (
            (self.x2 - self.x1) * (self.y2 - self.y1)
            + (other.x2 - other.x1) * (other.y2 - other.y1)
            - inter
        )
        return inter / union if union > 0 else 0.0


class Detector:
    """A YOLO model that checks each frame at two rotations."""

    def __init__(self, model_path: str | Path | None = None):
        try:
            from ultralytics import YOLO
        except ImportError as exc:
            raise DroneError(
                "object detection is not installed. Run: uv sync --extra vision"
            ) from exc
        import torch

        path = Path(model_path or os.environ.get("BV_YOLO_MODEL", DEFAULT_MODEL))
        path.parent.mkdir(parents=True, exist_ok=True)
        self.model = YOLO(str(path))  # Downloads the weights if missing.
        self.names: dict[int, str] = self.model.names
        if torch.backends.mps.is_available():
            self.device = "mps"
        elif torch.cuda.is_available():
            self.device = "cuda"
        else:
            self.device = "cpu"
        self._lock = threading.Lock()

    @property
    def classes(self) -> list[str]:
        return list(self.names.values())

    def resolve(self, object_name: str) -> str:
        """Map an object name to a COCO class or raise an error."""
        name = object_name.strip().lower()
        for article in ("a ", "an ", "the ", "any "):
            name = name.removeprefix(article)
        candidates = [name, _SYNONYMS.get(name)]
        if name.endswith("s"):
            candidates += [name[:-1], _SYNONYMS.get(name[:-1])]
        if name.endswith("es"):
            candidates.append(name[:-2])
        for candidate in candidates:
            if candidate in self.classes:
                return candidate
        raise DroneError(
            f"the detector does not know {object_name!r}. It can find: "
            f"{', '.join(self.classes)}. For anything else, use look and "
            "judge the image yourself."
        )

    def detect(
        self,
        jpeg: bytes,
        labels: set[str] | None = None,
        min_confidence: float = 0.35,
        color: str | None = None,
    ) -> list[Detection]:
        """Return objects in a JPEG image with the most confident first. Pass a color to
        keep only objects of that color.
        """
        import cv2
        import numpy as np

        image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise DroneError("could not decode the camera image.")
        height = image.shape[0]
        rotated = cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)

        with self._lock:
            results = self.model.predict(
                [image, rotated],
                imgsz=IMAGE_SIZE,
                conf=min_confidence,
                device=self.device,
                verbose=False,
            )

        found: list[Detection] = []
        for index, result in enumerate(results):
            for box in result.boxes:
                label = self.names[int(box.cls)]
                if labels and label not in labels:
                    continue
                x1, y1, x2, y2 = (float(v) for v in box.xyxy[0])
                if index == 1:
                    # Map boxes from the rotated image back to the original.
                    x1, y1, x2, y2 = y1, height - 1 - x2, y2, height - 1 - x1
                found.append(Detection(label, float(box.conf), x1, y1, x2, y2))

        # Keep the most confident of overlapping boxes so one object is reported once.
        found.sort(key=lambda d: d.confidence, reverse=True)
        kept: list[Detection] = []
        for det in found:
            if all(det.iou(k) < 0.5 for k in kept):
                det.color = dominant_color(image, det)
                if color is None or det.color == color:
                    kept.append(det)
        return kept


_detector: Detector | None = None
_detector_lock = threading.Lock()


def get_detector() -> Detector:
    """Return the shared detector. Loads the model on first call."""
    global _detector
    with _detector_lock:
        if _detector is None:
            _detector = Detector()
        return _detector
