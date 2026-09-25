"""OpenCV face detection and inference helpers for the trained Keras CNN."""

from __future__ import annotations

import json

try:
    import cv2
except ImportError:
    cv2 = None
import numpy as np

from database import DATA_DIR

MODEL_DIR = DATA_DIR / "model"
MODEL_PATH = MODEL_DIR / "attendance_cnn.keras"
LABELS_PATH = MODEL_DIR / "labels.json"
IMAGE_SIZE = 64
MIN_MATCH_CONFIDENCE = 0.72
MIN_MATCH_MARGIN = 0.18


class UnregisteredFaceError(ValueError):
    """Raised when the image does not match one enrolled student clearly."""

    def __init__(self) -> None:
        super().__init__("You are not registered here.")


_classifier = None
_model = None
_labels: list[str] = []
_loaded_model_mtime: int | None = None


def detect_single_face(image: np.ndarray) -> tuple[np.ndarray | None, tuple[int, int, int, int] | None]:
    """Return one resized color face crop; reject empty or multi-person frames."""
    if cv2 is None or image is None or image.size == 0:
        return None, None
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    global _classifier
    if _classifier is None:
        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        _classifier = cv2.CascadeClassifier(cascade_path)
    faces = _classifier.detectMultiScale(gray, scaleFactor=1.12, minNeighbors=5, minSize=(48, 48))
    if len(faces) != 1:
        return None, None
    x, y, width, height = faces[0]
    margin_x, margin_y = int(width * 0.12), int(height * 0.12)
    x1, y1 = max(0, x - margin_x), max(0, y - margin_y)
    x2, y2 = min(image.shape[1], x + width + margin_x), min(image.shape[0], y + height + margin_y)
    crop = image[y1:y2, x1:x2]
    crop = cv2.resize(crop, (IMAGE_SIZE, IMAGE_SIZE), interpolation=cv2.INTER_AREA)
    return crop, (int(x), int(y), int(width), int(height))


def model_ready() -> bool:
    return MODEL_PATH.is_file() and LABELS_PATH.is_file()


def _load_model() -> None:
    global _model, _labels, _loaded_model_mtime
    if not model_ready():
        raise RuntimeError("Train the CNN model first. Capture at least five face images per active student.")
    model_mtime = MODEL_PATH.stat().st_mtime_ns
    if _model is None or model_mtime != _loaded_model_mtime:
        try:
            from tensorflow.keras.models import load_model
        except ImportError:
            raise RuntimeError(
                "TensorFlow is required for offline CNN inference. "
                "For cloud deployments without GPU/TensorFlow, use manual ID check-in."
            )

        _model = load_model(MODEL_PATH)
        _labels = json.loads(LABELS_PATH.read_text(encoding="utf-8"))["university_ids"]
        _loaded_model_mtime = model_mtime


def recognize(image: np.ndarray) -> tuple[str, float, tuple[int, int, int, int]]:
    """Predict the university ID for the largest detected face."""
    face, box = detect_single_face(image)
    if face is None or box is None:
        raise ValueError("Show one clear face at a time, move closer and face the camera.")
    _load_model()
    prepared = face.astype("float32") / 255.0
    probabilities = _model.predict(np.expand_dims(prepared, axis=0), verbose=0)[0]
    class_index = int(np.argmax(probabilities))
    confidence = float(probabilities[class_index])
    if len(probabilities) < 2:
        raise RuntimeError("Train the model with at least two active students before scanning.")
    second_best = float(np.partition(probabilities, -2)[-2])
    if confidence < MIN_MATCH_CONFIDENCE or confidence - second_best < MIN_MATCH_MARGIN:
        raise UnregisteredFaceError()
    return _labels[class_index], confidence, box

