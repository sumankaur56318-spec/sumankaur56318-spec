"""Smart Attendance web application (Flask + SQLite)."""

from __future__ import annotations

import base64
import csv
import hmac
import io
import json
import os
import re
import secrets
import subprocess
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from functools import wraps

import cv2
from flask import (
    Flask, Response, abort, flash, jsonify, redirect, render_template, request,
    send_from_directory, session, url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from database import BASE_DIR, DATA_DIR, INSTANCE_DIR, connect_db, get_student_by_university_id, init_db, record_attendance
from face_engine import UnregisteredFaceError, detect_single_face, model_ready, recognize

DATASET_DIR = DATA_DIR / "dataset"
MODEL_DIR = DATA_DIR / "model"
TRAINING_PROCESS: subprocess.Popen | None = None

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024
init_db()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin_id"):
            if request.is_json or request.path.startswith("/api/"):
                return jsonify({"ok": False, "error": "Please sign in again."}), 401
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


@app.context_processor
def inject_template_values():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    admin_id = session.get("admin_id")
    admin = (query_one("SELECT is_demo FROM admins WHERE admin_id = ?", (admin_id,)) if admin_id
             else query_one("SELECT is_demo FROM admins ORDER BY admin_id LIMIT 1"))
    is_demo = bool(admin and admin["is_demo"])
    return {"csrf_token": session["csrf_token"], "active_admin": session.get("username", "Administrator"),
            "now": datetime.now(), "demo_credentials_enabled": is_demo,
            "ephemeral_storage": bool(os.environ.get("VERCEL"))}


@app.before_request
def validate_csrf():
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        submitted = request.form.get("csrf_token") or request.headers.get("X-CSRFToken", "")
        expected = session.get("csrf_token", "")
        if not expected or not submitted or not hmac.compare_digest(str(expected), str(submitted)):
            return "Your session token expired. Refresh the page and try again.", 400


def query_all(sql: str, values: tuple = ()) -> list:
    with connect_db() as db:
        return db.execute(sql, values).fetchall()


def query_one(sql: str, values: tuple = ()):
    with connect_db() as db:
        return db.execute(sql, values).fetchone()


def safe_date(value: str, fallback: date) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return fallback


def decode_image(data_url: str):
    if not isinstance(data_url, str) or "," not in data_url:
        raise ValueError("The camera image could not be read. Try capturing the photo again.")
    try:
        encoded = data_url.split(",", 1)[1]
        image_bytes = base64.b64decode(encoded, validate=True)
        image = cv2.imdecode(__import__("numpy").frombuffer(image_bytes, dtype=__import__("numpy").uint8), cv2.IMREAD_COLOR)
    except Exception as error:
        raise ValueError("The camera image could not be decoded. Please try again.") from error
    if image is None:
        raise ValueError("The camera image is empty. Please try again.")
    return image


def count_face_images() -> tuple[int, int]:
    active_student_ids = {row[0] for row in query_all("SELECT student_id FROM students WHERE status = 'active'")}
    folders = [
        DATASET_DIR / f"student_{student_id}"
        for student_id in active_student_ids
        if (DATASET_DIR / f"student_{student_id}").is_dir()
    ] if DATASET_DIR.exists() else []
    counts = [len(list(path.glob("*.jpg"))) + len(list(path.glob("*.png"))) for path in folders]
    return sum(counts), sum(count > 0 for count in counts)


def attendance_query(start: str, end: str, search: str = "") -> list:
    params: list = [start, end]
    sql = (
        "SELECT a.attendance_id, a.attendance_date, a.attendance_time, a.status, a.method, "
        "s.university_id, s.name, s.program FROM attendance a "
        "JOIN students s ON s.student_id = a.student_id "
        "WHERE a.attendance_date BETWEEN ? AND ?"
    )
    if search:
        sql += " AND (s.name LIKE ? OR s.university_id LIKE ?)"
        params.extend([f"%{search}%", f"%{search}%"])
    sql += " ORDER BY a.attendance_date DESC, a.attendance_time DESC"
    return query_all(sql, tuple(params))


@app.get("/login")
def login():
    if session.get("admin_id"):
        return redirect(url_for("dashboard"))
    return render_template("login.html", page_title="Sign in")


@app.post("/login")
def login_post():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    admin = query_one("SELECT * FROM admins WHERE username = ?", (username,))
    if not admin or not check_password_hash(admin["password_hash"], password):
        flash("That username and password did not match. Try the demo credentials shown below.", "error")
        return render_template("login.html", page_title="Sign in"), 401
    session.clear()
    session["admin_id"] = admin["admin_id"]
    session["username"] = admin["username"]
    session["csrf_token"] = secrets.token_urlsafe(32)
    destination = request.args.get("next", "")
    if not destination.startswith("/") or destination.startswith("//"):
        destination = url_for("dashboard")
    return redirect(destination)


@app.post("/logout")
@login_required
def logout():
    session.clear()
    flash("You have been signed out.", "success")
    return redirect(url_for("login"))


@app.get("/")
@login_required
def dashboard():
    today = date.today()
    first_of_month = today.replace(day=1)
    active_count = query_one("SELECT COUNT(*) AS n FROM students WHERE status = 'active'")["n"]
    present_today = query_one(
        "SELECT COUNT(*) AS n FROM attendance WHERE attendance_date = ?", (today.isoformat(),)
    )["n"]
    month_rows = query_all(
        "SELECT attendance_date, COUNT(*) AS n FROM attendance WHERE attendance_date BETWEEN ? AND ? GROUP BY attendance_date",
        (first_of_month.isoformat(), today.isoformat()),
    )
    elapsed_weekdays = sum(1 for offset in range(today.day) if (first_of_month + timedelta(days=offset)).weekday() < 5)
    expected = active_count * elapsed_weekdays
    logged = sum(row["n"] for row in month_rows)
    rate = round((logged / expected) * 100) if expected else 0

    last_seven = [today - timedelta(days=offset) for offset in range(6, -1, -1)]
    daily_rows = query_all(
        "SELECT attendance_date, COUNT(*) AS n FROM attendance WHERE attendance_date BETWEEN ? AND ? GROUP BY attendance_date",
        (last_seven[0].isoformat(), today.isoformat()),
    )
    daily_counts = {row["attendance_date"]: row["n"] for row in daily_rows}
    week = [{"label": day.strftime("%a"), "date": day.strftime("%b %d"),
             "count": daily_counts.get(day.isoformat(), 0),
             "percent": min(100, round(daily_counts.get(day.isoformat(), 0) / active_count * 100)) if active_count else 0}
            for day in last_seven]
    recent = query_all(
        "SELECT a.attendance_date, a.attendance_time, a.status, a.method, s.university_id, s.name, s.program "
        "FROM attendance a JOIN students s ON s.student_id = a.student_id "
        "ORDER BY a.attendance_date DESC, a.attendance_time DESC LIMIT 7"
    )
    return render_template("dashboard.html", page_title="Overview", nav="dashboard",
                           active_count=active_count, present_today=present_today, rate=rate,
                           week=week, recent=recent, model_ready=model_ready())


@app.get("/students")
@login_required
def students():
    search = request.args.get("q", "").strip()
    status = request.args.get("status", "all")
    sql = "SELECT * FROM students WHERE 1=1"
    values: list = []
    if status in {"active", "inactive"}:
        sql += " AND status = ?"
        values.append(status)
    if search:
        sql += " AND (name LIKE ? OR university_id LIKE ? OR program LIKE ?)"
        values.extend([f"%{search}%"] * 3)
    sql += " ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END, name COLLATE NOCASE"
    student_rows = query_all(sql, tuple(values))
    image_counts = {}
    for student in student_rows:
        folder = DATASET_DIR / f"student_{student['student_id']}"
        image_counts[student["student_id"]] = len(list(folder.glob("*.jpg"))) if folder.exists() else 0
    return render_template("students.html", page_title="Students", nav="students", students=student_rows,
                           query=search, status_filter=status, image_counts=image_counts)


@app.post("/students")
@login_required
def create_student():
    university_id = request.form.get("university_id", "").strip().upper()
    name = " ".join(request.form.get("name", "").split())
    program = " ".join(request.form.get("program", "MCA").split())[:80]
    year = request.form.get("year", "Year 1").strip()[:30]
    email = request.form.get("email", "").strip()[:160]
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9._/-]{1,39}", university_id):
        flash("Use a university ID with 2–40 letters, digits, dots, slashes, hyphens or underscores.", "error")
    elif not name or len(name) > 100:
        flash("Enter a student name between 1 and 100 characters.", "error")
    else:
        try:
            with connect_db() as db:
                db.execute(
                    "INSERT INTO students (university_id, name, program, year, email) VALUES (?, ?, ?, ?, ?)",
                    (university_id, name, program or "MCA", year or "Year 1", email),
                )
            flash(f"Student {university_id} was added.", "success")
        except Exception as error:
            if "UNIQUE" in str(error):
                flash("That university ID is already registered.", "error")
            else:
                flash("The student could not be saved. Please review the details and try again.", "error")
    return redirect(url_for("students"))


@app.post("/students/<int:student_id>/edit")
@login_required
def edit_student(student_id: int):
    student = query_one("SELECT * FROM students WHERE student_id = ?", (student_id,))
    if student is None:
        abort(404)
    name = " ".join(request.form.get("name", "").split())[:100]
    program = " ".join(request.form.get("program", "").split())[:80]
    year = request.form.get("year", "").strip()[:30]
    email = request.form.get("email", "").strip()[:160]
    if not name:
        flash("Student name cannot be empty.", "error")
    else:
        with connect_db() as db:
            db.execute("UPDATE students SET name = ?, program = ?, year = ?, email = ? WHERE student_id = ?",
                       (name, program, year, email, student_id))
        flash(f"Student {student['university_id']} was updated.", "success")
    return redirect(url_for("students"))


@app.post("/students/<int:student_id>/status")
@login_required
def set_student_status(student_id: int):
    student = query_one("SELECT * FROM students WHERE student_id = ?", (student_id,))
    if student is None:
        abort(404)
    new_status = "inactive" if student["status"] == "active" else "active"
    with connect_db() as db:
        db.execute("UPDATE students SET status = ? WHERE student_id = ?", (new_status, student_id))
    flash(f"{student['university_id']} is now {new_status}.", "success")
    return redirect(url_for("students"))


@app.get("/students/<int:student_id>/capture")
@login_required
def capture_student_faces(student_id: int):
    student = query_one("SELECT * FROM students WHERE student_id = ?", (student_id,))
    if student is None:
        abort(404)
    if student["status"] != "active":
        flash("Activate this student before capturing face photos.", "info")
        return redirect(url_for("students"))
    folder = DATASET_DIR / f"student_{student['student_id']}"
    captured_count = len(list(folder.glob("*.jpg"))) if folder.exists() else 0
    return render_template("capture.html", page_title="Capture face photos", nav="students",
                           student=student, captured_count=captured_count)


@app.post("/api/students/<int:student_id>/faces")
@login_required
def save_face_image(student_id: int):
    student = query_one("SELECT * FROM students WHERE student_id = ? AND status = 'active'", (student_id,))
    if student is None:
        return jsonify({"ok": False, "error": "Active student not found."}), 404
    try:
        image = decode_image((request.get_json(silent=True) or {}).get("image"))
        face, _box = detect_single_face(image)
        if face is None:
            return jsonify({"ok": False, "error": "Show one clear face at a time. Center your face and try again."}), 422
        folder = DATASET_DIR / f"student_{student['student_id']}"
        folder.mkdir(parents=True, exist_ok=True)
        filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.jpg"
        if not cv2.imwrite(str(folder / filename), face):
            return jsonify({"ok": False, "error": "The photo could not be saved."}), 500
        count = len(list(folder.glob("*.jpg")))
        return jsonify({"ok": True, "count": count, "message": f"Photo {count} captured."})
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 422


@app.get("/attendance")
@login_required
def attendance():
    today = date.today()
    start_date = safe_date(request.args.get("start"), today.replace(day=1))
    end_date = safe_date(request.args.get("end"), today)
    if start_date > end_date:
        start_date, end_date = end_date, start_date
    search = request.args.get("q", "").strip()
    rows = attendance_query(start_date.isoformat(), end_date.isoformat(), search)
    active_students = query_all("SELECT student_id, university_id, name FROM students WHERE status = 'active' ORDER BY name")
    present_today = query_one("SELECT COUNT(*) AS n FROM attendance WHERE attendance_date = ?", (today.isoformat(),))["n"]
    return render_template("attendance.html", page_title="Attendance", nav="attendance",
                           rows=rows, students=active_students, start_date=start_date.isoformat(),
                           end_date=end_date.isoformat(), query=search, present_today=present_today,
                           model_ready=model_ready())


@app.post("/api/attendance/manual")
@login_required
def manual_checkin():
    payload = request.get_json(silent=True) or {}
    university_id = str(payload.get("university_id", "")).strip().upper()
    student = get_student_by_university_id(university_id)
    if student is None or student["status"] != "active":
        return jsonify({"ok": False, "error": "No active student has that university ID."}), 404
    inserted, record = record_attendance(student["student_id"], "University ID")
    return jsonify({"ok": True, "inserted": inserted, "student": student["name"],
                    "university_id": university_id, "time": record["attendance_time"],
                    "message": "Attendance marked." if inserted else "Already marked present today."})


@app.post("/attendance/<int:attendance_id>/delete")
@login_required
def delete_attendance_record(attendance_id: int):
    record = query_one(
        "SELECT a.attendance_id, a.attendance_date, s.name, s.university_id "
        "FROM attendance a JOIN students s ON s.student_id = a.student_id WHERE a.attendance_id = ?",
        (attendance_id,),
    )
    if record is None:
        abort(404)
    with connect_db() as db:
        db.execute("DELETE FROM attendance WHERE attendance_id = ?", (attendance_id,))
    flash(f"Attendance for {record['name']} on {record['attendance_date']} was removed.", "success")
    return redirect(url_for("attendance", start=request.args.get("start"), end=request.args.get("end"),
                            q=request.args.get("q", "") or None) + "#attendance-records")


@app.post("/api/attendance/recognize")
@login_required
def camera_checkin():
    payload = request.get_json(silent=True) or {}
    try:
        image = decode_image(payload.get("image"))
        university_id, confidence, _box = recognize(image)
    except UnregisteredFaceError as error:
        return jsonify({"ok": False, "recognized": False, "error": str(error)}), 200
    except (ValueError, RuntimeError) as error:
        return jsonify({"ok": False, "error": str(error)}), 422
    student = get_student_by_university_id(university_id)
    if student is None or student["status"] != "active":
        return jsonify({"ok": False, "recognized": False, "error": "You are not registered here."}), 200
    inserted, record = record_attendance(student["student_id"], "CNN face match")
    return jsonify({"ok": True, "inserted": inserted, "recognized": True,
                    "student": student["name"], "university_id": university_id,
                    "confidence": confidence, "time": record["attendance_time"],
                    "message": "Attendance marked by face match." if inserted else "Already marked present today."})


@app.get("/attendance/export.csv")
@login_required
def export_attendance():
    today = date.today()
    start_date = safe_date(request.args.get("start"), today.replace(day=1))
    end_date = safe_date(request.args.get("end"), today)
    search = request.args.get("q", "").strip()
    rows = attendance_query(start_date.isoformat(), end_date.isoformat(), search)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["record_id", "date", "time", "status", "method", "university_id", "name", "program"])
    for row in rows:
        writer.writerow([
            row["attendance_id"],
            row["attendance_date"],
            row["attendance_time"],
            row["status"],
            row["method"],
            row["university_id"],
            row["name"],
            row["program"],
        ])
    return Response(output.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=attendance-{start_date}-{end_date}.csv"})


@app.get("/reports")
@login_required
def reports():
    today = date.today()
    month_start = today.replace(day=1)
    start_date = safe_date(request.args.get("start"), month_start)
    end_date = safe_date(request.args.get("end"), today)
    if start_date > end_date:
        start_date, end_date = end_date, start_date
    rows = attendance_query(start_date.isoformat(), end_date.isoformat())
    if not rows:
        daily = []
        students_summary = []
        total_records = 0
    else:
        per_day = Counter(row["attendance_date"] for row in rows)
        dates = []
        current = start_date
        while current <= end_date:
            if current.weekday() < 5:
                dates.append(current)
            current += timedelta(days=1)
        daily = [{"label": day.strftime("%d %b"), "count": int(per_day.get(day.isoformat(), 0))} for day in dates]

        # Group count by (university_id, name)
        student_counts = Counter((row["university_id"], row["name"]) for row in rows)
        denominator = sum(1 for offset in range((end_date - start_date).days + 1)
                          if (start_date + timedelta(days=offset)).weekday() < 5)
        students_summary = []
        for (uid, name), count in student_counts.items():
            rate = min(100, round(count / denominator * 100)) if denominator else 0
            students_summary.append({
                "university_id": uid,
                "name": name,
                "days_present": count,
                "rate": rate,
            })
        students_summary.sort(key=lambda row: (-row["rate"], row["name"]))
        total_records = len(rows)
    active_count = query_one("SELECT COUNT(*) AS n FROM students WHERE status = 'active'")["n"]
    weekday_count = sum(1 for offset in range((end_date - start_date).days + 1)
                        if (start_date + timedelta(days=offset)).weekday() < 5)
    possible = active_count * weekday_count
    overall_rate = round(total_records / possible * 100) if possible else 0
    return render_template("reports.html", page_title="Reports", nav="reports", start_date=start_date.isoformat(),
                           end_date=end_date.isoformat(), daily=daily, students_summary=students_summary,
                           total_records=total_records, overall_rate=overall_rate, weekday_count=weekday_count,
                           export_url=url_for("export_attendance", start=start_date.isoformat(), end=end_date.isoformat()))


def read_json(path: Path, fallback: dict) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fallback


@app.get("/model")
@login_required
def model_page():
    sample_count, student_count = count_face_images()
    metrics = read_json(MODEL_DIR / "metrics.json", {})
    status = read_json(MODEL_DIR / "training_status.json", {"state": "ready" if model_ready() else "idle", "message": ""})
    return render_template("model.html", page_title="Face model", nav="model", metrics=metrics,
                           training_status=status, sample_count=sample_count, trained_student_count=student_count,
                           ready=model_ready())


@app.post("/model/train")
@login_required
def start_training():
    global TRAINING_PROCESS
    if TRAINING_PROCESS and TRAINING_PROCESS.poll() is None:
        flash("Model training is already running. This page will update when it finishes.", "info")
        return redirect(url_for("model_page"))
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    (MODEL_DIR / "training_status.json").write_text(
        json.dumps({"state": "running", "message": "Starting the training process…"}), encoding="utf-8"
    )
    log_file = open(INSTANCE_DIR / "training.log", "w", encoding="utf-8")
    try:
        TRAINING_PROCESS = subprocess.Popen(
            [sys.executable, str(BASE_DIR / "train_model.py")], cwd=BASE_DIR,
            stdout=log_file, stderr=subprocess.STDOUT, creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
    finally:
        log_file.close()
    flash("Training started. Keep this page open to see progress.", "success")
    return redirect(url_for("model_page"))


@app.get("/api/model/status")
@login_required
def model_status():
    fallback = {"state": "ready" if model_ready() else "idle", "message": ""}
    payload = read_json(MODEL_DIR / "training_status.json", fallback)
    if payload.get("state") == "running" and TRAINING_PROCESS is None:
        payload = {"state": "error", "message": "The last training run was interrupted. Start training again."}
    if TRAINING_PROCESS and TRAINING_PROCESS.poll() is not None and payload.get("state") == "running":
        payload = {"state": "error", "message": "Training stopped unexpectedly. Check the training log in instance/training.log."}
    return jsonify(payload)


@app.get("/model/artifact/<path:filename>")
@login_required
def model_artifact(filename: str):
    if filename not in {"accuracy.png", "loss.png", "confusion_matrix.png"}:
        abort(404)
    return send_from_directory(MODEL_DIR, filename)


@app.get("/guide")
@login_required
def guide():
    return render_template("guide.html", page_title="Project guide", nav="guide")


@app.get("/academic-report")
@login_required
def academic_report():
    return send_from_directory(BASE_DIR / "docs", "ACADEMIC_REPORT.md", mimetype="text/markdown")


@app.get("/settings")
@login_required
def settings():
    return render_template("settings.html", page_title="Settings", nav="settings")


@app.post("/settings/password")
@login_required
def change_password():
    current_password = request.form.get("current_password", "")
    new_password = request.form.get("new_password", "")
    if len(new_password) < 12:
        flash("Choose a new password with at least 12 characters.", "error")
        return redirect(url_for("settings"))
    admin = query_one("SELECT * FROM admins WHERE admin_id = ?", (session["admin_id"],))
    if not admin or not check_password_hash(admin["password_hash"], current_password):
        flash("The current password is incorrect.", "error")
        return redirect(url_for("settings"))
    with connect_db() as db:
        db.execute("UPDATE admins SET password_hash = ?, is_demo = 0 WHERE admin_id = ?",
                   (generate_password_hash(new_password), session["admin_id"]))
    flash("Your admin password was updated.", "success")
    return redirect(url_for("settings"))


@app.errorhandler(413)
def file_too_large(_error):
    return jsonify({"ok": False, "error": "That image is too large. Try again with the camera preview at normal size."}), 413


if __name__ == "__main__":
    app.run(host=os.environ.get("HOST", "0.0.0.0"), port=int(os.environ.get("PORT", "5000")), debug=False)

