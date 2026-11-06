# NIET Examination Management System 🎓

An enterprise-grade, offline-first Examination Management and Anti-Cheat Platform designed for college computer laboratories, universities, and educational institutions. Built with **Python Flask**, **SQLite (WAL Mode)**, **WebSockets**, and fronted by an **Nginx Load Balancer**, it allows hundreds of students to participate concurrently in supervised Coding and MCQ examinations across local area networks (LAN) without internet dependency.

---

## 🌟 Key Features

### 1. 🎯 Permanent Individual Student Question Assignment System (Latest Feature)
Allows Administrators and Teachers to configure permanent question assignments per student, completely independent of room keys:
- **Permanent Teacher-Student Profiles:** Assignments belong to `(teacher_id, student_id, exam_type)` rather than temporary room keys. Once assigned, they persist permanently across all future room keys, stops, restarts, and clear key operations.
- **Mode 1: Common Questions (Default):** All unassigned students receive the complete common question bank in source order.
- **Mode 2: Individual Assignment:** Teachers can select any student and assign a specific question (e.g., Question 5 only) or multiple questions (e.g., Questions 2, 5, 8).
- **Replacement (Not Merged):** Individual assignments replace the common question set completely for that student.
- **Bulk & 1-to-1 Distribution Tools:** Fast multi-student selection with Department and Section filtering, Bulk Assignment of selected questions, and 1-to-1 unique Question Distribution.
- **Attempt Locking & Snapshots:** When an exam attempt starts, the effective question set is snapshotted into `exam_attempt_snapshots`. Mid-exam teacher modifications take effect on the student's *next* exam, guaranteeing active attempts are never altered mid-flight.
- **Gap-Free Palette & Navigation:** Questions are numbered sequentially (1..N) in the student interface regardless of original database IDs, with zero gaps.
- **Dynamic Scorecards:** Maximum marks and percentages are dynamically computed strictly from the student's assigned questions.
- **Strict Isolation:** Coding and MCQ assignments are strictly separated with dedicated management subtabs.

### 2. 💻 Multi-Language Coding Examination Engine
- **Supported Languages:** Python 3, C (GCC), and Java (OpenJDK).
- **Real-Time Interactive Terminal:** Uses `flask-sock` WebSockets (`/ws/run`) for bidirectional communication, enabling interactive stdin input (e.g. `input()` in Python or `scanf()` in C).
- **Test Case Evaluation:** Support for visible and hidden test cases, each with individual mark weighting.
- **Auto-Save & Draft Recovery:** Code drafts are continuously synchronized so student work is never lost across reconnects or accidental reloads.
- **Navigation Controls:** Direct switching between assigned questions via the clean question selector dropdown and dynamic question badges.

### 3. 📝 Multiple Choice Question (MCQ) Engine
- **Instant Auto-Grading:** Multiple-choice examinations with configurable per-question marks and timers.
- **Answer Sheet Protection:** Correct answer options are strictly stripped from student API payloads to eliminate devtools inspection leaks.
- **Real-Time Auto-Save:** Student selections are automatically recorded and preserved in SQLite.

### 4. 🛡️ Robust Multi-Layered Anti-Cheat System
Designed for controlled offline lab computers:
- **5-Strike Violation System:** Automatic exam lockout if a student reaches 5 violations (with proctor unlock capabilities).
- **Tab Switching & Window Blur:** Monitors when students leave the exam window.
- **Fullscreen Enforcement:** Forces student into fullscreen mode; exits are flagged as illegal violations.
- **Extension & DevTools Scanner:** Continuously scans for unauthorized browser extensions and developer tools every second. Includes a cache-busting `[Recheck & Reverify]` protocol.
- **Dual Monitor / External Display Detection:** Detects secondary connected displays or resolution changes.
- **Clipboard & Key Blocking:** Restricts copy, cut, paste, and common shortcut keys.

### 5. 🔍 Automated Plagiarism Detection Engine
- **Token & AST-Normalized Code Matching:** Compares submissions pairwise within an exam session using normalized string similarity (difflib SequenceMatcher) combined with Jaccard token set overlap.
- **Risk Categorization:** Automatically categorizes matching submissions into `HIGH`, `MODERATE`, or `LOW` risk tiers for fast faculty review.

### 6. 📊 Real-Time Proctoring & Analytics Portals
- **Teacher Dashboard:** Real-time proctor view showing student status, live scores, time remaining, and violation feeds.
- **Live Code Inspection:** Teachers can inspect what code any student is currently writing in their editor in real time.
- **Leaderboards & Reports:** Real-time rankings with department/class/section filters, downloadable in Excel (`.xlsx`) or CSV (`.csv`) formats.
- **Question & User Import:** Bulk import questions, test cases, teachers, and students via CSV/Excel spreadsheets.

### 7. 🚀 High-Concurrency Nginx Load Balancer Cluster
- **Cluster Architecture:** Starts 5 isolated Python worker instances (ports 5001–5005) fronted by a high-performance **Nginx** reverse proxy running on port 5000.
- **Lab Ready:** Handles concurrent connections smoothly in high-density computer laboratories.

---

## 🏗️ Architecture & Database

The system operates on an optimized SQLite database (`exam_lab.db`) with Write-Ahead Logging (`PRAGMA journal_mode=WAL`) and high busy timeouts for robust concurrency:

| Table | Purpose |
|---|---|
| `users` | User credentials, roles (`admin`, `teacher`, `student`), and session tokens. |
| `student_profiles` | Academic data: register number, department, year, semester, and section. |
| `teacher_profiles` | Teacher department, class, and designation. |
| `questions` | Coding questions, prompts, languages, and active flags. |
| `test_cases` | Input, expected output, marks, and visibility per test case. |
| `mcqs` | MCQ questions, 4 options, correct answer, and marks. |
| `mcq_status` | Student MCQ selections and live grading results. |
| `student_question_profiles` | Permanent question profiles per teacher, student, and exam type (`COMMON` or `INDIVIDUAL`). |
| `student_question_profile_items` | Specific question IDs and ordering linked to a permanent profile. |
| `exam_attempt_snapshots` | Immutable question snapshots frozen at student attempt start for zero mid-exam disruption. |
| `student_question_assignments` | Backward-compatibility assignment mappings and room key session snapshots. |
| `exam_state` | Active exam status, coding/MCQ access codes, duration, and start time. |
| `exam_access` | Student participation state, violation counter, lock status, and final scores. |
| `submissions` | Student final code submissions, test case pass counts, and live scores. |
| `drafts` | Real-time working code drafts per student and question. |
| `activity` | Comprehensive audit trail of all student anti-cheat violation events. |
| `exam_history` | Historical archive snapshots of completed exams for permanent record-keeping. |

---

## ⚙️ Installation & Setup

### Prerequisites
- **Python**: Version 3.10 or higher.
- **Compilers (Optional for multi-language execution):**
  - GCC / MinGW (for C compilation)
  - OpenJDK / Java JDK (for Java compilation)
- **Web Browser:** Chrome, Edge, or Brave (recommended in Kiosk Mode).

### 1. Clone the Repository
```powershell
git clone https://github.com/DEEPAK-47-S/Offline-Exam-Portal.git
cd Offline-Exam-Portal
```

### 2. Set Up Virtual Environment & Dependencies
```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Environment Configuration
Create a `.env` file in the project root (or copy from `.env.example`):
```env
NIT_EXAM_SECRET=super_secret_for_local_dev
FLASK_ENV=development
DATABASE_PATH=exam_lab.db
CODE_EXECUTION_TIMEOUT=5
CODE_MAX_OUTPUT=102400
```

---

## 🚦 Running the Application

### Option A: High-Concurrency Load Balanced Cluster (Recommended for Labs)
To start 5 backend workers (ports 5001-5005) with an automated Nginx load balancer on port 5000:
```powershell
.\start_servers.bat
```
To stop the cluster cleanly:
```powershell
.\stop_servers.bat
```

### Option B: Single Server (For Development / Testing)
```powershell
.\.venv\Scripts\python.exe app.py
```

### Connecting to the Portal
- **Local Computer:** `http://localhost:5000`
- **LAN / Lab Computers:** `http://<TEACHER_IP>:5000` (The script automatically prints the detected IP)

---

## 🔑 Default Credentials

| Role | Username | Default Password | Permissions |
|---|---|---|---|
| **Admin** | `admin` | `Admin123!` | System configuration, teacher management, student profiles, global assignment overview. |
| **Teacher** | `teacher` | `ChangeMe123!` | Exam lifecycle, question bank CRUD, individual question assignments, proctoring, scorecards. |
| **Student** | `student001` | `Student123!` | Exam room access (requires 6-digit exam key), code editor, MCQ answer sheet. |

> **Note:** Teachers and administrators can create new student accounts or bulk import them using Excel/CSV templates. Default passwords should be changed upon first login.

---

## 🧪 Automated Test Suites & Test Isolation

All test suites run in strictly isolated temporary databases using synthetic test fixtures. Test executions never touch, alter, or expose production examination records or student passwords.

### 1. Permanent Student-Based Question Assignment Suite (29 Scenarios)
Verifies permanent persistence across unlimited room keys, clear keys, exam shutdowns, and application restarts; bulk assignments, 1-to-1 distribution, active attempt snapshot freeze, invalid assignment protection, cross-teacher ownership isolation, idempotency, and migration:
```powershell
.\.venv\Scripts\python.exe test_permanent_assignments.py
```
*Result: 14 Test Suites / 29 Scenarios Passed (100% Success).*

### 2. Complete Anti-Cheat Lifecycle & Leave Confirmation Suite (13 Scenarios)
Verifies continuous focus loss detection, Alt+Tab and visibility monitoring while confirmation modals are open, fullscreen exit detection, timer continuation, and safe finalization:
```powershell
.\.venv\Scripts\python.exe test_anticheat_lifecycle.py
```
*Result: 13/13 Scenarios Passed (100% Success).*

### 3. Individual Question Assignment & Audit Matrix (21 Scenarios)
Verifies common-mode attempt snapshots, explicit invalid assignment states (`COMMON`, `INDIVIDUAL`, `INVALID`), assignment API authorization and ownership validation, MCQ duplicate submission idempotency, backend independent code score calculation, attempt locking, cross-exam isolation, and result calculation:
```powershell
.\.venv\Scripts\python.exe test_individual_question_assignment.py
```
*Result: 7 Test Suites / 21 Scenarios Passed (100% Success).*

### 4. Full System Regression & Buttons Audit (11 Suites)
Verifies authentication, question authoring, anti-cheat 5-strike locking, multi-language execution in sandboxed Windows Job Objects (Python, C, Java), live code inspection, plagiarism matching, time-over exit, and score downloads:
```powershell
.\.venv\Scripts\python.exe test_all_buttons_and_backend.py
```
*Result: 11/11 Test Suites Passed (100% Success).*

---

## 📁 Project Structure

```
├── app.py                                # Main Flask server, WebSocket handler, and REST APIs
├── nginx.conf                            # Nginx load balancer reverse-proxy configuration
├── start_servers.bat                     # Launches 5 Python workers + Nginx load balancer
├── stop_servers.bat                      # Terminates all running cluster instances
├── START_LAN.bat                         # Quick single-server LAN launcher
├── requirements.txt                      # Python dependencies
├── .env.example                          # Environment variable template
├── .gitignore                            # Clean repository exclusion list (ignores *.db, .env)
├── static/
│   ├── css/style.css                     # Responsive UI styling & dark/light theme
│   ├── js/script-v2.js                   # Client-side SPA controllers, editor, and WebSocket
│   └── img/                              # College logo and assets
├── templates/
│   └── index.html                        # Single-page application templates for all 3 roles
├── test_permanent_assignments.py        # Permanent student question assignment 29-scenario test suite
├── test_anticheat_lifecycle.py           # Anti-cheat focus detection & leave confirmation test suite
├── test_individual_question_assignment.py# Question assignment & audit verification suite
└── test_all_buttons_and_backend.py       # Full system regression & buttons audit suite
```

---

## 📜 License & Acknowledgments

Developed for the **National Institute of Engineering and Technology (NIET)** Examination and Assessment Division. Designed to provide fair, tamper-resistant, and flexible offline examinations.
