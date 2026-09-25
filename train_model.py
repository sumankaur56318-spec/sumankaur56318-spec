"""Train and evaluate the simple CNN used by the live attendance page."""

from __future__ import annotations

import json
import os
import sys
import traceback

os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from sklearn.metrics import classification_report, confusion_matrix
from sklearn.model_selection import train_test_split
from tensorflow.keras import Sequential
from tensorflow.keras.layers import Conv2D, Dense, Dropout, Flatten, Input, MaxPooling2D
from tensorflow.keras.utils import to_categorical
from database import DATA_DIR, connect_db

DATASET_DIR = DATA_DIR / "dataset"
MODEL_DIR = DATA_DIR / "model"
IMAGE_SIZE = 64


def write_status(state: str, message: str, **extra: object) -> None:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"state": state, "message": message, **extra}
    (MODEL_DIR / "training_status.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_dataset() -> tuple[np.ndarray, np.ndarray, list[str]]:
    import cv2

    images: list[np.ndarray] = []
    labels: list[int] = []
    ids: list[str] = []
    with connect_db() as db:
        active_students = {
            f"student_{row['student_id']}": row["university_id"]
            for row in db.execute("SELECT student_id, university_id FROM students WHERE status = 'active'")
        }
    for student_folder in sorted(path for path in DATASET_DIR.iterdir() if path.is_dir()):
        if student_folder.name not in active_students:
            continue
        files = sorted(student_folder.glob("*.jpg")) + sorted(student_folder.glob("*.png"))
        if files:
            ids.append(active_students[student_folder.name])
            class_index = len(ids) - 1
            for path in files:
                image = cv2.imread(str(path))
                if image is not None:
                    image = cv2.resize(image, (IMAGE_SIZE, IMAGE_SIZE), interpolation=cv2.INTER_AREA)
                    images.append(image.astype("float32") / 255.0)
                    labels.append(class_index)
    if len(ids) < 2:
        raise ValueError("Capture faces for at least two active students before training the classifier.")
    counts = np.bincount(labels, minlength=len(ids))
    if np.any(counts < 5):
        missing = [ids[index] for index, count in enumerate(counts) if count < 5]
        raise ValueError("Each student needs at least five usable photos for a stratified train/test split "
                         "with validation examples. Missing: " + ", ".join(missing))
    return np.asarray(images, dtype="float32"), np.asarray(labels, dtype="int32"), ids


def build_model(number_of_students: int) -> Sequential:
    model = Sequential([
        Input(shape=(IMAGE_SIZE, IMAGE_SIZE, 3)),
        Conv2D(16, (3, 3), activation="relu", padding="same"),
        MaxPooling2D((2, 2)),
        Conv2D(32, (3, 3), activation="relu", padding="same"),
        MaxPooling2D((2, 2)),
        Conv2D(64, (3, 3), activation="relu", padding="same"),
        MaxPooling2D((2, 2)),
        Flatten(),
        Dense(128, activation="relu"),
        Dropout(0.35),
        Dense(number_of_students, activation="softmax"),
    ])
    model.compile(optimizer="adam", loss="categorical_crossentropy", metrics=["accuracy"])
    return model


def train() -> dict[str, object]:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    write_status("running", "Loading captured face images…")
    images, labels, university_ids = load_dataset()
    test_count = max(len(university_ids), int(np.ceil(len(labels) * 0.2)))
    if len(labels) - test_count < len(university_ids) * 2:
        raise ValueError("Add more face samples. Aim for at least five per student so the model can keep examples "
                         "for training, validation and testing.")
    x_train, x_test, y_train, y_test = train_test_split(
        images, labels, test_size=test_count, random_state=42, stratify=labels
    )
    y_train_one_hot = to_categorical(y_train, num_classes=len(university_ids))
    y_test_one_hot = to_categorical(y_test, num_classes=len(university_ids))

    write_status("running", f"Training on {len(x_train)} photos; holding out {len(x_test)} for testing…")
    model = build_model(len(university_ids))
    history = model.fit(
        x_train, y_train_one_hot, epochs=18, batch_size=16,
        validation_split=0.2, shuffle=True, verbose=0,
    )
    test_loss, test_accuracy = model.evaluate(x_test, y_test_one_hot, verbose=0)
    probabilities = model.predict(x_test, verbose=0)
    predictions = np.argmax(probabilities, axis=1)
    matrix = confusion_matrix(y_test, predictions, labels=list(range(len(university_ids))))
    report = classification_report(
        y_test, predictions, labels=list(range(len(university_ids))),
        target_names=university_ids, output_dict=True, zero_division=0,
    )
    model.save(MODEL_DIR / "attendance_cnn.keras")
    (MODEL_DIR / "labels.json").write_text(
        json.dumps({"university_ids": university_ids}, indent=2), encoding="utf-8"
    )

    plt.figure(figsize=(8, 4.5))
    plt.plot(history.history["accuracy"], label="Training accuracy", color="#4f46e5")
    plt.plot(history.history["val_accuracy"], label="Validation accuracy", color="#0f9f8f")
    plt.xlabel("Epoch")
    plt.ylabel("Accuracy")
    plt.ylim(0, 1.02)
    plt.title("CNN accuracy by epoch")
    plt.legend(frameon=False)
    plt.grid(alpha=0.2)
    plt.tight_layout()
    plt.savefig(MODEL_DIR / "accuracy.png", dpi=150)
    plt.close()

    plt.figure(figsize=(8, 4.5))
    plt.plot(history.history["loss"], label="Training loss", color="#4f46e5")
    plt.plot(history.history["val_loss"], label="Validation loss", color="#0f9f8f")
    plt.xlabel("Epoch")
    plt.ylabel("Categorical cross-entropy")
    plt.title("CNN loss by epoch")
    plt.legend(frameon=False)
    plt.grid(alpha=0.2)
    plt.tight_layout()
    plt.savefig(MODEL_DIR / "loss.png", dpi=150)
    plt.close()

    plt.figure(figsize=(max(6, len(university_ids) * 1.2), 5))
    plt.imshow(matrix, interpolation="nearest", cmap="Purples")
    plt.title("Test-set confusion matrix")
    plt.colorbar()
    positions = np.arange(len(university_ids))
    plt.xticks(positions, university_ids, rotation=45, ha="right")
    plt.yticks(positions, university_ids)
    for row in range(matrix.shape[0]):
        for column in range(matrix.shape[1]):
            plt.text(column, row, str(matrix[row, column]), ha="center", va="center", color="#29243d")
    plt.xlabel("Predicted university ID")
    plt.ylabel("True university ID")
    plt.tight_layout()
    plt.savefig(MODEL_DIR / "confusion_matrix.png", dpi=150)
    plt.close()

    metrics = {
        "training_accuracy": float(history.history["accuracy"][-1]),
        "validation_accuracy": float(history.history["val_accuracy"][-1]),
        "testing_accuracy": float(test_accuracy),
        "test_loss": float(test_loss),
        "training_loss": float(history.history["loss"][-1]),
        "students": university_ids,
        "samples": int(len(labels)),
        "train_samples": int(len(x_train)),
        "test_samples": int(len(x_test)),
        "classification_report": report,
        "confusion_matrix": matrix.tolist(),
        "trained_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
    }
    (MODEL_DIR / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    write_status("ready", "CNN training and evaluation finished.", metrics={
        key: metrics[key] for key in ("training_accuracy", "validation_accuracy", "testing_accuracy", "test_loss")
    })
    return metrics


if __name__ == "__main__":
    try:
        result = train()
        print(json.dumps({key: result[key] for key in ("training_accuracy", "validation_accuracy", "testing_accuracy", "test_loss")}))
    except Exception as error:
        write_status("error", str(error))
        traceback.print_exc()
        sys.exit(1)

