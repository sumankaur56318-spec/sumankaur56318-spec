# codecraft--demo

This is my first git repository
Author - suman and prabh

---

# Smart Attendance System Using Face Recognition

An MCA final-year project demonstration built with Python, Flask, OpenCV, TensorFlow/Keras, scikit-learn, Pandas and SQLite. The local dashboard manages student profiles, captures face samples from a browser camera, trains a small CNN, records daily attendance and exports reports.

## Demo login

- **Username:** `admin`
- **Password:** `SmartAttend@123`

The first launch creates this local admin account and six clearly labeled sample student records. Change the password from **Settings** after signing in. Set `ADMIN_USERNAME` and `ADMIN_PASSWORD` before the first launch to choose different initial credentials.

## Features

- Admin sign-in with hashed password storage and CSRF checks.
- Student profile management using a unique university ID; profiles can be edited or deactivated.
- Browser camera face capture with OpenCV face detection and private local image storage.
- Small CNN using TensorFlow/Keras, stratified train/test split, validation tracking, accuracy/loss graphs, test metrics, confusion matrix and classification report.
- Live camera face match after a model has been trained, with a confidence threshold.
- University-ID check-in as a dependable alternative to face matching.
- SQLite attendance table with a database-level rule allowing one record per student per day.
- Date-filtered attendance history, remove-and-recheck correction for mistaken records, attendance-rate summaries and CSV export.
- A built-in guide and a complete academic report with 30 viva questions and answers.

## Requirements

- Windows 10/11, 64-bit.
- Python 3.11, 64-bit (Python 3.10–3.12 is also suitable for the TensorFlow range in `requirements.txt`).
- A recent Microsoft Visual C++ Redistributable on Windows if TensorFlow reports a missing runtime DLL.
- Chrome or Edge for camera access. Allow camera permission when the browser prompts.

TensorFlow uses CPU on native Windows; a GPU is not required. TensorFlow's current pip guide lists Python 3.9–3.12 and notes that native Windows installs the Intel CPU build, while native-Windows GPU support ended after TensorFlow 2.10. See [TensorFlow's installation guide](https://www.tensorflow.org/install/pip) for current platform details.

## Install and run on Windows

Open PowerShell in this `smart_attendance` folder and run:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements-train.txt
python app.py
```

If PowerShell blocks virtual-environment activation, run this once in the same terminal and activate again:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

Open <http://127.0.0.1:5000> in Chrome while the server is running. To stop the local server, press `Ctrl+C` in the terminal where `app.py` is running.

### Open on a phone on the same Wi-Fi

Keep the app running on the computer and connect the phone to the same Wi-Fi. Find the computer's IPv4 address with `ipconfig`, then open `http://<computer-ip>:5000` on the phone. The phone browser may block camera access over this plain HTTP address; camera access on phones requires an HTTPS deployment. University-ID attendance remains available from the phone.

### Camera and face-model workflow

1. Open **Students** and register each real student with the official university ID and name.
2. Open **Capture** on that student's row. Allow browser camera access, center one consenting participant's face and save at least five varied photos. Repeat for at least two active students. Use the sample students only for interface exploration; they do not have face images.
3. Open **Face model** and choose **Train model**. The dashboard updates with training progress and evaluation results.
4. Open **Attendance** → **Face check-in** → enable the camera → start scanning.
5. For a camera-free demonstration, use **University ID check-in**. A second check-in on the same day is reported as a duplicate and does not make another row.

Photos are cropped, resized and stored in `dataset/student_<internal-id>/`; model labels map back to the official university ID. The trained model and its charts are stored in `model/`. Both folders are excluded from Git because they may contain biometric data or class-specific model information.

To start again with the original demo records, stop the server, remove `instance\smart_attendance.db`, then launch the app again. SQLite will create the demo data on its next first run. This resets the admin password to its first-run value too; face photos and trained model files are separate local data.

## Data and privacy

Student and attendance records are stored in `instance/smart_attendance.db`. Face photos and trained model weights stay in this project folder. Do not commit these files or capture anyone without appropriate permission. This teaching prototype is not a production identity-verification service; check camera predictions against university IDs during demonstrations and use supervision for official records.

### Persistent storage for deployment

The SQLite database, captured face photos, and trained model must share a persistent writable directory. Set `ATTENDANCE_DATA_DIR` to that directory on a host with a persistent disk. Vercel serverless storage at `/tmp` is temporary and may be isolated between function instances; a Vercel deployment using the default configuration cannot reliably retain student IDs, face photos, or trained models. The training workflow also starts a local child process, so this demo should run on a persistent server rather than a serverless function unless its database, file storage, and training workflow are replaced with hosted services.

## Project structure

```text
smart_attendance/
├── app.py                       # Routes, sign-in, dashboard, check-in and reports
├── database.py                  # SQLite schema, demo records, daily duplicate guard
├── face_engine.py               # OpenCV face detection and CNN prediction
├── train_model.py               # CNN training, held-out evaluation and plots
├── requirements.txt
├── templates/                   # Dashboard and sign-in pages
├── static/css/style.css         # Responsive dashboard styling
├── static/js/app.js             # Dialogs, camera capture and camera check-in
├── dataset/                     # Local face images (ignored by Git)
├── model/                       # Local CNN, labels and evaluation artifacts (ignored)
├── instance/                    # SQLite database and training log (ignored)
└── docs/ACADEMIC_REPORT.md      # Full project report and viva preparation
```

## Optional configuration

Set these environment variables before launching `app.py`:

```powershell
$env:ADMIN_USERNAME = "campus-admin"
$env:ADMIN_PASSWORD = "Use-a-unique-password-here"
$env:FLASK_SECRET_KEY = "A-long-random-local-secret"
# Optional persistent data directory (use a mounted persistent disk on a server host):
$env:ATTENDANCE_DATA_DIR = "D:\attendance-data"
# Optional alternate SQLite file:
$env:ATTENDANCE_DB = "C:\\attendance-data\\smart_attendance.db"
python app.py
```

Initial admin environment variables are read only when the database is first created. They do not reset an existing admin password.

## GitHub

The source code and project report are hosted in this GitHub repository. The `.gitignore` excludes the SQLite database, captured face images, model weights, virtual environments and local logs. Keep `dataset/`, trained model files and `instance/smart_attendance.db` out of commits because they contain local student or biometric data.

## Troubleshooting

- **`py` is not recognized:** install 64-bit Python 3.11 from python.org and select **Add Python to PATH**, then reopen PowerShell.
- **TensorFlow has no matching distribution:** confirm 64-bit Python 3.10, 3.11 or 3.12 and update pip in the activated virtual environment.
- **TensorFlow reports a missing DLL:** install the current Microsoft Visual C++ Redistributable, restart the terminal and retry.
- **Camera is blocked or missing:** use `http://127.0.0.1:5000` or `http://localhost:5000`, grant camera permission to the browser and close other apps using the camera.
- **No face is detected:** improve light, face the camera, move closer and remove obstructions. OpenCV's Haar detector works best on a clear, front-facing view.
- **Training asks for more images:** capture at least five clear photos for each of at least two active students. The model reserves images for both training and test evaluation.
- **Face prediction is uncertain:** capture more varied images, keep camera distance and lighting similar, retrain and verify using the ID check-in. Face matching is a small educational model and may not generalize reliably.
- **Port 5000 is already in use:** set `$env:PORT = "5001"`, then rerun `python app.py` and open `http://127.0.0.1:5001`.

For the abstract, architecture, methodology, requirements, ANN/CNN explanations, database design, results notes, limitations, conclusion, references and viva answers, see `docs/ACADEMIC_REPORT.md`.

