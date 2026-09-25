"""SQLite setup and small database helpers for Smart Attendance."""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterator

from werkzeug.security import generate_password_hash

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = Path("/tmp") if os.environ.get("VERCEL") else BASE_DIR
DATA_DIR = Path(os.environ.get("ATTENDANCE_DATA_DIR") or DEFAULT_DATA_DIR).expanduser()
INSTANCE_DIR = DATA_DIR / "instance"
DATABASE_PATH = Path(os.environ.get("ATTENDANCE_DB") or INSTANCE_DIR / "smart_attendance.db").expanduser()


@contextmanager
def connect_db() -> Iterator[sqlite3.Connection]:
    """Yield a configured SQLite connection and always commit or close it."""
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_db() -> None:
    """Create tables and add a first-run admin plus clearly marked sample data."""
    INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
    with connect_db() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS admins (
                admin_id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                is_demo INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS students (
                student_id INTEGER PRIMARY KEY AUTOINCREMENT,
                university_id TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                program TEXT NOT NULL DEFAULT 'MCA',
                year TEXT NOT NULL DEFAULT 'Year 1',
                email TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'inactive')),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS attendance (
                attendance_id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL REFERENCES students(student_id),
                attendance_date TEXT NOT NULL,
                attendance_time TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'present' CHECK(status IN ('present', 'late')),
                method TEXT NOT NULL DEFAULT 'ID check-in',
                UNIQUE(student_id, attendance_date)
            );
            CREATE INDEX IF NOT EXISTS idx_attendance_date ON attendance(attendance_date);
            CREATE INDEX IF NOT EXISTS idx_attendance_student ON attendance(student_id);
            """
        )

        admin_count = db.execute("SELECT COUNT(*) FROM admins").fetchone()[0]
        if admin_count == 0:
            username = os.environ.get("ADMIN_USERNAME", "admin").strip() or "admin"
            password = os.environ.get("ADMIN_PASSWORD", "SmartAttend@123")
            db.execute(
                "INSERT INTO admins (username, password_hash, is_demo) VALUES (?, ?, ?)",
                (username, generate_password_hash(password), int(not os.environ.get("ADMIN_USERNAME") and not os.environ.get("ADMIN_PASSWORD"))),
            )

        if db.execute("SELECT COUNT(*) FROM students").fetchone()[0] == 0:
            sample_students = [
                ("UNI-2026-001", "Aarav Sharma", "MCA", "Year 1", "aarav.demo@example.edu"),
                ("UNI-2026-002", "Meera Patel", "MCA", "Year 1", "meera.demo@example.edu"),
                ("UNI-2026-003", "Kabir Singh", "MCA", "Year 2", "kabir.demo@example.edu"),
                ("UNI-2026-004", "Ananya Rao", "MCA", "Year 2", "ananya.demo@example.edu"),
                ("UNI-2026-005", "Ishaan Gupta", "MCA", "Year 1", "ishaan.demo@example.edu"),
                ("UNI-2026-006", "Sara Khan", "MCA", "Year 2", "sara.demo@example.edu"),
            ]
            db.executemany(
                "INSERT INTO students (university_id, name, program, year, email) VALUES (?, ?, ?, ?, ?)",
                sample_students,
            )
            # A small synthetic history makes the first dashboard useful to explore.
            student_rows = db.execute("SELECT student_id FROM students ORDER BY student_id").fetchall()
            today = date.today()
            day = today - timedelta(days=12)
            while day <= today:
                if day.weekday() < 5:
                    for index, student in enumerate(student_rows):
                        if (day.toordinal() + index) % 7 != 0:
                            db.execute(
                                "INSERT OR IGNORE INTO attendance "
                                "(student_id, attendance_date, attendance_time, status, method) "
                                "VALUES (?, ?, ?, 'present', 'Sample data')",
                                (student["student_id"], day.isoformat(), f"09:{10 + index:02d}:00"),
                            )
                day += timedelta(days=1)


def get_student_by_university_id(university_id: str) -> sqlite3.Row | None:
    with connect_db() as db:
        return db.execute(
            "SELECT * FROM students WHERE university_id = ?", (university_id.strip(),)
        ).fetchone()


def record_attendance(student_id: int, method: str) -> tuple[bool, sqlite3.Row | None]:
    """Insert today's attendance once. Return (inserted, existing_or_new_record)."""
    today = date.today().isoformat()
    now = datetime.now().strftime("%H:%M:%S")
    with connect_db() as db:
        student = db.execute(
            "SELECT * FROM students WHERE student_id = ? AND status = 'active'", (student_id,)
        ).fetchone()
        if student is None:
            return False, None
        cursor = db.execute(
            "INSERT OR IGNORE INTO attendance "
            "(student_id, attendance_date, attendance_time, status, method) VALUES (?, ?, ?, 'present', ?)",
            (student_id, today, now, method),
        )
        row = db.execute(
            "SELECT a.*, s.university_id, s.name, s.program FROM attendance a "
            "JOIN students s ON s.student_id = a.student_id "
            "WHERE a.student_id = ? AND a.attendance_date = ?",
            (student_id, today),
        ).fetchone()
        return cursor.rowcount == 1, row

