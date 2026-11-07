"""Offline NIT Exam. Run locally: python app.py"""
import json, os, queue, shutil, sqlite3, subprocess, sys, tempfile, threading, time, random, csv, secrets, difflib
from datetime import datetime, timezone, timedelta
# Task 7: Add common Java/GCC install paths for offline Windows lab machines
_extra_paths = [
    "C:\\msys64\\ucrt64\\bin",
    "C:\\msys64\\mingw64\\bin",
    "C:\\MinGW\\bin",
    "C:\\TDM-GCC-64\\bin",
    "C:\\Program Files\\Java\\jdk-21\\bin",
    "C:\\Program Files\\Java\\jdk-17\\bin",
    "C:\\Program Files\\Eclipse Adoptium\\jdk-21.0.0.35-hotspot\\bin",
    "C:\\Program Files\\Eclipse Adoptium\\jdk-17.0.0.35-hotspot\\bin",
    "C:\\Program Files\\OpenJDK\\jdk-17\\bin",
    "C:\\Program Files\\Amazon Corretto\\jdk17.0.0_0\\bin",
]
for _p in _extra_paths:
    if os.path.isdir(_p) and _p not in os.environ.get("PATH",""):
        os.environ["PATH"] += os.pathsep + _p

from functools import wraps
from flask import Flask, jsonify, render_template, request, session, has_request_context
from flask_sock import Sock
from werkzeug.security import check_password_hash, generate_password_hash as _werkzeug_gen_hash
def generate_password_hash(password, method='pbkdf2:sha256'):
    return _werkzeug_gen_hash(password, method=method)

# Task 3: India Standard Time (Asia/Kolkata / Tamil Nadu Time)
try:
    import zoneinfo
    _IST = zoneinfo.ZoneInfo("Asia/Kolkata")
except ImportError:
    import pytz
    _IST = pytz.timezone("Asia/Kolkata")

def to_ist(ts):
    """Convert any timestamp to Indian Standard Time (IST) display string."""
    try:
        if isinstance(ts, (int, float)):
            dt = datetime.fromtimestamp(float(ts), tz=timezone.utc).astimezone(_IST)
            return dt.strftime('%d-%m-%Y %I:%M:%S %p IST')
        s = str(ts).strip()
        if not s or s in ('None', 'null', ''):
            return '-'
        if 'IST' in s:
            return s
        for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%d-%m-%Y %H:%M:%S', '%Y-%m-%d %I:%M:%S %p', '%d-%m-%Y %I:%M:%S %p'):
            try:
                dt = datetime.strptime(s, fmt).replace(tzinfo=timezone.utc).astimezone(_IST)
                return dt.strftime('%d-%m-%Y %I:%M:%S %p IST')
            except ValueError:
                pass
        try:
            dt = datetime.fromtimestamp(float(s), tz=timezone.utc).astimezone(_IST)
            return dt.strftime('%d-%m-%Y %I:%M:%S %p IST')
        except (ValueError, OSError):
            pass
        return s
    except Exception:
        return str(ts)

def now_ist_str():
    """Return current Indian Standard Time (IST) as DD-MM-YYYY hh:mm:ss AM/PM IST."""
    return datetime.now(tz=_IST).strftime('%d-%m-%Y %I:%M:%S %p IST')

from dotenv import load_dotenv
load_dotenv()

ROOT = os.path.dirname(__file__)
DB = os.environ.get("DATABASE_PATH", os.path.join(ROOT, "exam_lab.db"))
app = Flask(__name__)
secret_key = os.environ.get("NIT_EXAM_SECRET")
if not secret_key:
    sys.exit("CRITICAL ERROR: NIT_EXAM_SECRET environment variable is not set. Please create a .env file and set it.")

EXEC_TIMEOUT = int(os.environ.get("CODE_EXECUTION_TIMEOUT", 5))
MAX_OUTPUT = int(os.environ.get("CODE_MAX_OUTPUT", 102400))
MAX_SOURCE = int(os.environ.get("CODE_MAX_SOURCE_SIZE", 102400))

app.config.update(SECRET_KEY=secret_key, SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")
sock = Sock(app)

def get_db_path():
    return app.config.get('DATABASE_PATH') or os.environ.get("DATABASE_PATH") or DB

def conn():
    c=sqlite3.connect(get_db_path(), timeout=60.0)
    c.row_factory=sqlite3.Row
    try:
        c.execute('PRAGMA journal_mode=WAL;')
        c.execute('PRAGMA synchronous=NORMAL;')
        c.execute('PRAGMA busy_timeout=60000;')
    except Exception:
        pass
    return c

def migrate_to_permanent_profiles(c):
    """
    Non-destructive migration from student_question_assignments into:
    - student_question_profiles
    - student_question_profile_items
    - exam_attempt_snapshots
    Groups by (teacher_id, student_id, exam_type), picks most recently updated record,
    and preserves historical records in student_question_assignments untouched.
    """
    tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    if 'student_question_assignments' not in tables:
        return
    groups = c.execute("SELECT DISTINCT teacher_id, student_id, exam_type FROM student_question_assignments").fetchall()
    for g in groups:
        tid, sid, etype = g['teacher_id'], g['student_id'], g['exam_type']
        exists = c.execute("SELECT id FROM student_question_profiles WHERE teacher_id=? AND student_id=? AND exam_type=?", (tid, sid, etype)).fetchone()
        if exists:
            continue
        latest = c.execute("SELECT * FROM student_question_assignments WHERE teacher_id=? AND student_id=? AND exam_type=? ORDER BY updated_at DESC, id DESC LIMIT 1", (tid, sid, etype)).fetchone()
        if not latest:
            continue
        raw_asgn = str(latest['assigned_question_ids'] or '').strip()
        if raw_asgn in ('COMMON', '', 'None'):
            continue
        try:
            qids = json.loads(raw_asgn)
            if isinstance(qids, list) and len(qids) > 0:
                cur = c.execute('''
                    INSERT INTO student_question_profiles
                        (teacher_id, student_id, exam_type, mode, version, created_by, updated_by, created_at, updated_at)
                    VALUES (?, ?, ?, 'INDIVIDUAL', 1, ?, ?, ?, ?)
                ''', (tid, sid, etype, latest['created_by'], latest['created_by'], latest['created_at'], latest['updated_at']))
                prof_id = cur.lastrowid
                for order, qid in enumerate(qids):
                    c.execute('''
                        INSERT OR IGNORE INTO student_question_profile_items
                            (profile_id, question_id, sort_order, created_at)
                        VALUES (?, ?, ?, ?)
                    ''', (prof_id, int(qid), order, latest['created_at']))
                if latest['attempt_locked']:
                    snap_raw = latest['snapshot_question_ids'] or raw_asgn
                    c.execute('''
                        INSERT OR IGNORE INTO exam_attempt_snapshots
                            (exam_instance_id, teacher_id, student_id, exam_type, profile_version, mode, snapshot_question_ids, created_at)
                        VALUES (?, ?, ?, ?, 1, 'INDIVIDUAL', ?, ?)
                    ''', (latest['exam_key'], tid, sid, etype, snap_raw, latest['created_at']))
        except Exception:
            pass

def init_db(db_path=None):
    if db_path:
        app.config['DATABASE_PATH'] = db_path
    c=conn(); c.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, name TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('student','teacher','admin')), password_hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS questions(id INTEGER PRIMARY KEY, teacher_id INTEGER NOT NULL, title TEXT NOT NULL, prompt TEXT NOT NULL, language TEXT NOT NULL DEFAULT 'python', active INTEGER NOT NULL DEFAULT 1, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS submissions(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, teacher_id INTEGER NOT NULL, question_id INTEGER, code TEXT NOT NULL, language TEXT NOT NULL, score REAL DEFAULT 0, time_seconds INTEGER DEFAULT 0, submitted_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS activity(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, teacher_id INTEGER NOT NULL, event TEXT NOT NULL, details TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS courses(id INTEGER PRIMARY KEY, title TEXT NOT NULL, description TEXT DEFAULT '', active INTEGER DEFAULT 1, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS topics(id INTEGER PRIMARY KEY, course_id INTEGER NOT NULL, title TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS test_cases(id INTEGER PRIMARY KEY, question_id INTEGER NOT NULL, input_data TEXT DEFAULT '', expected_output TEXT DEFAULT '', marks REAL DEFAULT 1, visible INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS exam_access(user_id INTEGER NOT NULL, teacher_id INTEGER NOT NULL DEFAULT 1, joined_at REAL, locked INTEGER DEFAULT 0, allowed INTEGER DEFAULT 1, violations INTEGER DEFAULT 0, last_violation_at REAL DEFAULT 0, final_score REAL, mcq_final_score REAL, PRIMARY KEY(user_id, teacher_id));
CREATE TABLE IF NOT EXISTS student_profiles(user_id INTEGER PRIMARY KEY, register_number TEXT DEFAULT '', department TEXT DEFAULT '', year TEXT DEFAULT '', semester TEXT DEFAULT '', section TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS mcqs(id INTEGER PRIMARY KEY, teacher_id INTEGER NOT NULL, question TEXT NOT NULL, option_a TEXT NOT NULL, option_b TEXT NOT NULL, option_c TEXT NOT NULL, option_d TEXT NOT NULL, correct_option TEXT NOT NULL, marks REAL DEFAULT 1, time_limit INTEGER DEFAULT 30);
CREATE TABLE IF NOT EXISTS mcq_status(id INTEGER PRIMARY KEY, student_id INTEGER NOT NULL, teacher_id INTEGER NOT NULL, student_name TEXT NOT NULL, mcq_id INTEGER NOT NULL, selected_option TEXT, is_correct INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS exam_history(id INTEGER PRIMARY KEY, teacher_id INTEGER NOT NULL, teacher_name TEXT, exam_key TEXT, exam_date TEXT DEFAULT CURRENT_TIMESTAMP, total_students INTEGER, result_data TEXT, db_file TEXT);
CREATE TABLE IF NOT EXISTS student_question_assignments(id INTEGER PRIMARY KEY AUTOINCREMENT, exam_key TEXT NOT NULL, teacher_id INTEGER NOT NULL, exam_type TEXT NOT NULL CHECK(exam_type IN ('coding', 'mcq')), student_id INTEGER NOT NULL, assigned_question_ids TEXT NOT NULL, created_by INTEGER NOT NULL, attempt_locked INTEGER DEFAULT 0, snapshot_question_ids TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(exam_key, teacher_id, exam_type, student_id));
CREATE INDEX IF NOT EXISTS idx_sqa_lookup ON student_question_assignments(exam_key, teacher_id, exam_type, student_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_mcq_status_single ON mcq_status(student_id, teacher_id, mcq_id);
CREATE TABLE IF NOT EXISTS student_question_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    teacher_id INTEGER NOT NULL,
    student_id INTEGER NOT NULL,
    exam_type TEXT NOT NULL CHECK(exam_type IN ('coding', 'mcq')),
    mode TEXT NOT NULL CHECK(mode IN ('COMMON', 'INDIVIDUAL')) DEFAULT 'COMMON',
    version INTEGER NOT NULL DEFAULT 1,
    created_by INTEGER NOT NULL,
    updated_by INTEGER NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(teacher_id, student_id, exam_type)
);
CREATE INDEX IF NOT EXISTS idx_sqp_lookup ON student_question_profiles(teacher_id, student_id, exam_type);
CREATE TABLE IF NOT EXISTS student_question_profile_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER NOT NULL REFERENCES student_question_profiles(id) ON DELETE CASCADE,
    question_id INTEGER NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(profile_id, question_id)
);
CREATE INDEX IF NOT EXISTS idx_sqpi_lookup ON student_question_profile_items(profile_id, sort_order);
CREATE TABLE IF NOT EXISTS exam_attempt_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exam_instance_id TEXT NOT NULL,
    teacher_id INTEGER NOT NULL,
    student_id INTEGER NOT NULL,
    exam_type TEXT NOT NULL CHECK(exam_type IN ('coding', 'mcq')),
    profile_version INTEGER NOT NULL DEFAULT 1,
    mode TEXT NOT NULL CHECK(mode IN ('COMMON', 'INDIVIDUAL', 'INVALID')),
    snapshot_question_ids TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(exam_instance_id, teacher_id, exam_type, student_id)
);
CREATE INDEX IF NOT EXISTS idx_eas_lookup ON exam_attempt_snapshots(exam_instance_id, teacher_id, exam_type, student_id);
''')
    for stmt in ("ALTER TABLE submissions ADD COLUMN score REAL DEFAULT 0", "ALTER TABLE submissions ADD COLUMN time_seconds INTEGER DEFAULT 0", "ALTER TABLE exam_access ADD COLUMN allowed INTEGER DEFAULT 1", "ALTER TABLE exam_access ADD COLUMN violations INTEGER DEFAULT 0", "ALTER TABLE exam_access ADD COLUMN last_violation_at REAL DEFAULT 0", "ALTER TABLE exam_access ADD COLUMN final_score REAL", "ALTER TABLE exam_access ADD COLUMN mcq_final_score REAL", "ALTER TABLE exam_access ADD COLUMN total_time INTEGER", "ALTER TABLE exam_access ADD COLUMN mcq_locked INTEGER DEFAULT 0", "ALTER TABLE exam_access ADD COLUMN mcq_violations INTEGER DEFAULT 0", "ALTER TABLE mcqs ADD COLUMN marks REAL DEFAULT 1", "ALTER TABLE mcqs ADD COLUMN time_limit INTEGER DEFAULT 30", "ALTER TABLE exam_state ADD COLUMN mcq_access_code TEXT", "ALTER TABLE exam_state ADD COLUMN coding_active INTEGER DEFAULT 1", "ALTER TABLE exam_state ADD COLUMN mcq_active INTEGER DEFAULT 0", "ALTER TABLE users ADD COLUMN department TEXT", "ALTER TABLE users ADD COLUMN session_token TEXT", "ALTER TABLE student_profiles ADD COLUMN class TEXT", "ALTER TABLE teacher_profiles ADD COLUMN section TEXT", "ALTER TABLE exam_history ADD COLUMN exam_key TEXT", "ALTER TABLE exam_history ADD COLUMN db_file TEXT", "ALTER TABLE student_question_assignments ADD COLUMN attempt_locked INTEGER DEFAULT 0", "ALTER TABLE student_question_assignments ADD COLUMN snapshot_question_ids TEXT"):
        try: c.execute(stmt)
        except sqlite3.OperationalError: pass
    c.execute('CREATE TABLE IF NOT EXISTS exam_state(teacher_id INTEGER PRIMARY KEY, active INTEGER DEFAULT 0, access_code TEXT, mcq_access_code TEXT, duration_minutes INTEGER DEFAULT 60, started_at REAL, coding_active INTEGER DEFAULT 1, mcq_active INTEGER DEFAULT 0)')
    c.execute('CREATE TABLE IF NOT EXISTS drafts(user_id INTEGER, question_id INTEGER, language TEXT DEFAULT "python", code TEXT, updated_at TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(user_id, question_id, language))')
    c.execute('CREATE TABLE IF NOT EXISTS teacher_profiles(user_id INTEGER PRIMARY KEY, department TEXT DEFAULT "", class TEXT DEFAULT "", year TEXT DEFAULT "", section TEXT DEFAULT "")')
    if not c.execute("SELECT 1 FROM users WHERE username='teacher'").fetchone():
        c.execute("INSERT INTO users(username,name,role,password_hash) VALUES(?,?,?,?)", ('teacher','Exam Teacher','teacher',generate_password_hash('ChangeMe123!')))
        c.execute("INSERT INTO users(username,name,role,password_hash) VALUES(?,?,?,?)", ('student001','Demo Student','student',generate_password_hash('Student123!')))
    if not c.execute("SELECT 1 FROM users WHERE username='admin'").fetchone():
        c.execute("INSERT INTO users(username,name,role,password_hash) VALUES(?,?,?,?)", ('admin','Exam Admin','admin',generate_password_hash('Admin123!')))
    # Existing students remain allowed by default. Teachers can later revoke access per student.
    # Do NOT auto-insert students into exam_access — teacher must add them
    migrate_to_permanent_profiles(c)
    c.commit(); c.close()

init_db()

def auth(role=None):
    def deco(fn):
        @wraps(fn)
        def w(*a, **k):
            if 'user_id' not in session or (role and session.get('role') != role): return jsonify(error='Unauthorized'),401
            if session.get('force_password_change') and request.endpoint not in ('change_password', 'logout'):
                return jsonify(error='Must change default password', force_password_change=True),403
            # Task 4: Single-session enforcement for students
            if session.get('role') == 'student':
                c2=conn()
                try:
                    srow=c2.execute('SELECT session_token FROM users WHERE id=?',(session['user_id'],)).fetchone()
                    if not srow or srow['session_token'] != session.get('session_token'):
                        session.clear()
                        return jsonify(error='Session expired. You were logged in from another device.',session_expired=True),401
                finally:
                    c2.close()
            return fn(*a, **k)
        return w
    return deco

@app.errorhandler(Exception)
def handle_exception(e):
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException): return e
    return jsonify(error="An unexpected internal server error occurred."), 500

@app.after_request
def apply_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    return response

@app.before_request
def validate_csrf():
    if request.method in ["POST", "PUT", "DELETE"]:
        # Modern browser same-origin guarantee
        sec_fetch_site = request.headers.get("Sec-Fetch-Site", "").lower()
        if sec_fetch_site in ("same-origin", "same-site", "none"):
            return None

        origin = request.headers.get("Origin") or request.headers.get("Referer")
        if not origin:
            return None

        from urllib.parse import urlparse
        parsed_origin = urlparse(origin)
        origin_netloc = (parsed_origin.netloc or '').lower()
        origin_host = origin_netloc.split(':')[0].lower()

        curr_host_header = (request.headers.get('X-Forwarded-Host') or request.headers.get('Host') or request.host or '').lower()
        curr_host = curr_host_header.split(':')[0].lower()

        # Exact match (with or without port)
        if origin_netloc == curr_host_header or origin_host == curr_host or origin_host in curr_host_header or curr_host in origin_host:
            return None

        # Both are local loopback (e.g. 127.0.0.1 vs localhost)
        if origin_host in ('127.0.0.1', 'localhost') and curr_host in ('127.0.0.1', 'localhost'):
            return None

        # Allow LAN / Intranet / Private requests (10.x.x.x, 192.168.x.x, 172.x.x.x, 127.x.x.x)
        if origin_host.startswith(('10.', '192.168.', '172.', '127.')) or origin_host == 'localhost':
            return None

        # Allow Cloud deployments & college domains (Azure, Cloudflare, Ngrok, etc.)
        if origin_host.endswith((
            '.azurewebsites.net', '.trycloudflare.com', '.ngrok-free.app',
            '.ngrok.io', '.onrender.com', '.vercel.app', '.loca.lt',
            '.niet.co.in', '.edu', '.ac.in'
        )):
            return None

        # Allow requests with authenticated session token
        if session.get('user_id') and session.get('session_token'):
            return None

        return jsonify(error="CSRF Origin check failed"), 403

@app.route('/')
def home(): return render_template('index.html')
@app.post('/api/login')
def login():
    d=request.get_json() or {}; u=(d.get('username') or '').strip(); p=d.get('password') or ''
    c=conn(); row=c.execute('SELECT * FROM users WHERE username=?',(u,)).fetchone()
    if not row or not check_password_hash(row['password_hash'],p): c.close(); return jsonify(error='Invalid login'),401
    force_change = row['role'] != 'admin' and p in ('ChangeMe123!', 'Student123!')
    session.clear(); session.update(user_id=row['id'], username=row['username'], name=row['name'], role=row['role'])
    if force_change: session['force_password_change'] = True
    # Task 4: Generate new session_token for students, invalidating any previous session
    if row['role'] == 'student':
        tok = secrets.token_hex(32)
        c.execute('UPDATE users SET session_token=? WHERE id=?',(tok, row['id'])); c.commit()
        session['session_token'] = tok
    c.close()
    log(row['id'],'login',''); return jsonify(name=row['name'],role=row['role'], force_password_change=force_change)
@app.post('/api/logout')
def logout(): session.clear(); return jsonify(ok=True)
@app.get('/api/me')
def me(): return jsonify(logged_in='user_id' in session, name=session.get('name'), role=session.get('role'))
def log(uid,event,details=''):
    # Task 3: Store activity timestamps in IST
    c=conn(); c.execute('INSERT INTO activity(user_id,teacher_id,event,details,created_at) VALUES(?,?,?,?,?)',(uid,session.get('teacher_id') or session.get('user_id') or 0,event,str(details)[:500],now_ist_str())); c.commit(); c.close()
def illegal_count(uid, tid=None):
    c=conn()
    teacher_id = tid or session.get('teacher_id') or session.get('user_id')
    row = None
    if teacher_id:
        row = c.execute('SELECT violations, mcq_violations FROM exam_access WHERE user_id=? AND teacher_id=?',(uid, teacher_id)).fetchone()
    if not row:
        row = c.execute('SELECT violations, mcq_violations FROM exam_access WHERE user_id=? LIMIT 1',(uid,)).fetchone()
    c.close()
    if not row: return 0
    return int((row['violations'] or 0) + (row['mcq_violations'] or 0))
def current_exam(c=None):
    """Return (exam_state_dict, remaining_seconds_for_coding).
    Task 2: MCQ-only exam (coding_active=0, no started_at) stays active indefinitely.
    Only auto-deactivate when a CODING exam's timer runs out.
    """
    close=False
    if c is None: c=conn(); close=True
    s=c.execute('SELECT * FROM exam_state WHERE teacher_id=?', (session.get('teacher_id') or session.get('user_id'),)).fetchone()
    if not s: s={'active':0, 'coding_active':0, 'mcq_active':0}
    else: s=dict(s)
    coding_active = bool(s.get('coding_active', 0))
    remaining = 0
    if s['active'] and s['started_at'] and coding_active:
        remaining = max(0, int(s['duration_minutes']*60 - (time.time()-s['started_at'])))
        if remaining <= 0:
            # Coding timer expired — deactivate coding; keep active only if MCQ still running
            if s.get('mcq_active'):
                c.execute('UPDATE exam_state SET coding_active=0 WHERE teacher_id=?', (session.get('teacher_id') or session.get('user_id'),)); c.commit(); s['coding_active']=0
            else:
                c.execute('UPDATE exam_state SET active=0,coding_active=0 WHERE teacher_id=?', (session.get('teacher_id') or session.get('user_id'),)); c.commit(); s['active']=0
    if close: c.close()
    return s, remaining

def get_effective_assignment(c, student_id, teacher_id, exam_type, exam_key=None, exam_instance_id=None):
    """
    Returns (state, question_ids, attempt_locked).
    state is one of: 'COMMON', 'INDIVIDUAL', 'INVALID'.
    - 'COMMON': student takes common question set (if attempt is locked, question_ids is the locked snapshot).
    - 'INDIVIDUAL': student has an individual assignment (question_ids is the list of assigned/snapshot question IDs).
    - 'INVALID': individual assignment is corrupted, empty, or references non-existent/inactive questions.
                 NEVER silently falls back to Common Mode.
    """
    if not exam_instance_id:
        sess_key = session.get('exam_key') if has_request_context() else None
        exam_instance_id = exam_key or sess_key

    # Case 1: Check locked attempt snapshot
    snap = None
    if exam_instance_id:
        snap = c.execute('''
            SELECT mode, snapshot_question_ids, profile_version
            FROM exam_attempt_snapshots
            WHERE exam_instance_id=? AND teacher_id=? AND exam_type=? AND student_id=?
        ''', (str(exam_instance_id), teacher_id, exam_type, student_id)).fetchone()

    if not snap:
        ea = c.execute('SELECT joined_at FROM exam_access WHERE user_id=? AND teacher_id=?', (student_id, teacher_id)).fetchone()
        if ea and ea['joined_at']:
            snap = c.execute('''
                SELECT exam_instance_id, mode, snapshot_question_ids, profile_version
                FROM exam_attempt_snapshots
                WHERE teacher_id=? AND exam_type=? AND student_id=?
                ORDER BY id DESC LIMIT 1
            ''', (teacher_id, exam_type, student_id)).fetchone()

    if snap:
        s_mode = snap['mode']
        s_raw = str(snap['snapshot_question_ids'] or '').strip()
        if s_mode == 'COMMON':
            try:
                s_ids = json.loads(s_raw)
                if isinstance(s_ids, list) and len(s_ids) > 0:
                    return 'COMMON', [int(x) for x in s_ids], True
            except Exception:
                pass
            return 'COMMON', None, True
        elif s_mode == 'INDIVIDUAL':
            try:
                qids = json.loads(s_raw)
                if not isinstance(qids, list) or len(qids) == 0:
                    return 'INVALID', None, True
                int_qids = [int(x) for x in qids]
                if exam_type == 'coding':
                    existing = set(r[0] for r in c.execute('SELECT id FROM questions WHERE teacher_id=?', (teacher_id,)).fetchall())
                else:
                    existing = set(r[0] for r in c.execute('SELECT id FROM mcqs WHERE teacher_id=?', (teacher_id,)).fetchall())
                valid_ordered = [qid for qid in int_qids if qid in existing]
                if len(valid_ordered) != len(int_qids):
                    return 'INVALID', None, True
                return 'INDIVIDUAL', valid_ordered, True
            except Exception:
                return 'INVALID', None, True
        elif s_mode == 'INVALID':
            return 'INVALID', None, True

    # Case 2: Permanent Student Profile (Independent of Room Keys)
    prof = c.execute('''
        SELECT id, mode, version
        FROM student_question_profiles
        WHERE teacher_id=? AND student_id=? AND exam_type=?
    ''', (teacher_id, student_id, exam_type)).fetchone()

    if prof:
        if prof['mode'] == 'INDIVIDUAL':
            items = c.execute('''
                SELECT question_id
                FROM student_question_profile_items
                WHERE profile_id=?
                ORDER BY sort_order ASC, id ASC
            ''', (prof['id'],)).fetchall()
            if not items:
                return 'INVALID', None, False
            int_qids = [it['question_id'] for it in items]
            if exam_type == 'coding':
                active_src = set(r[0] for r in c.execute('SELECT id FROM questions WHERE teacher_id=? AND active=1', (teacher_id,)).fetchall())
            else:
                active_src = set(r[0] for r in c.execute('SELECT id FROM mcqs WHERE teacher_id=?', (teacher_id,)).fetchall())
            valid_ordered = [qid for qid in int_qids if qid in active_src]
            if len(valid_ordered) != len(int_qids):
                return 'INVALID', None, False
            return 'INDIVIDUAL', valid_ordered, False
        elif prof['mode'] == 'COMMON':
            return 'COMMON', None, False

    # Case 3: Legacy student_question_assignments fallback (for backwards compat)
    if exam_instance_id:
        leg = c.execute('''
            SELECT assigned_question_ids, attempt_locked, snapshot_question_ids
            FROM student_question_assignments
            WHERE exam_key=? AND teacher_id=? AND exam_type=? AND student_id=?
        ''', (str(exam_instance_id), teacher_id, exam_type, student_id)).fetchone()
    else:
        leg = c.execute('''
            SELECT assigned_question_ids, attempt_locked, snapshot_question_ids
            FROM student_question_assignments
            WHERE teacher_id=? AND student_id=? AND exam_type=?
            ORDER BY id DESC LIMIT 1
        ''', (teacher_id, student_id, exam_type)).fetchone()

    if leg:
        is_locked = bool(leg['attempt_locked'])
        assigned_raw = str(leg['assigned_question_ids'] or '').strip()
        snapshot_raw = str(leg['snapshot_question_ids'] or '').strip()
        if assigned_raw == 'COMMON' or assigned_raw == '' or assigned_raw == 'None':
            if is_locked and snapshot_raw:
                try:
                    s_ids = json.loads(snapshot_raw)
                    if isinstance(s_ids, list) and len(s_ids) > 0:
                        return 'COMMON', [int(x) for x in s_ids], True
                except Exception:
                    pass
                return 'INVALID', None, True
            return 'COMMON', None, is_locked
        try:
            raw_to_parse = snapshot_raw if (is_locked and snapshot_raw) else assigned_raw
            qids = json.loads(raw_to_parse)
            if not isinstance(qids, list) or len(qids) == 0:
                return 'INVALID', None, is_locked
            int_qids = [int(x) for x in qids]
            if exam_type == 'coding':
                active_src = set(r[0] for r in c.execute('SELECT id FROM questions WHERE teacher_id=? AND active=1', (teacher_id,)).fetchall())
            else:
                active_src = set(r[0] for r in c.execute('SELECT id FROM mcqs WHERE teacher_id=?', (teacher_id,)).fetchall())
            valid_ordered = [qid for qid in int_qids if qid in active_src]
            if len(valid_ordered) != len(int_qids):
                return 'INVALID', None, is_locked
            return 'INDIVIDUAL', valid_ordered, is_locked
        except Exception:
            return 'INVALID', None, is_locked

    return 'COMMON', None, False

def is_question_authorized(c, student_id, teacher_id, exam_type, question_id, exam_key=None):
    """True if question_id is authorized for student_id in this exam instance."""
    try:
        qid = int(question_id)
    except (TypeError, ValueError):
        return False
    state, qids, _ = get_effective_assignment(c, student_id, teacher_id, exam_type, exam_key)
    if state == 'INVALID':
        return False
    if state == 'INDIVIDUAL':
        return qids is not None and qid in qids
    if state == 'COMMON':
        if qids is not None:
            return qid in qids
        if exam_type == 'coding':
            row = c.execute('SELECT 1 FROM questions WHERE id=? AND teacher_id=? AND active=1', (qid, teacher_id)).fetchone()
            return bool(row)
        else:
            row = c.execute('SELECT 1 FROM mcqs WHERE id=? AND teacher_id=?', (qid, teacher_id)).fetchone()
            return bool(row)
    return False

def get_student_teacher_id(c, uid):
    tid = session.get('teacher_id') if has_request_context() else None
    if tid: return tid
    acc = c.execute('SELECT teacher_id FROM exam_access WHERE user_id=? AND teacher_id IS NOT NULL ORDER BY rowid DESC LIMIT 1', (uid,)).fetchone()
    if acc and acc['teacher_id']:
        if has_request_context():
            session['teacher_id'] = acc['teacher_id']
        return acc['teacher_id']
    st = c.execute('SELECT teacher_id FROM exam_state WHERE active=1 LIMIT 1').fetchone()
    if st and st['teacher_id']:
        if has_request_context():
            session['teacher_id'] = st['teacher_id']
        return st['teacher_id']
    return session.get('user_id') if has_request_context() else None

def has_exam_access(uid):
    """True if student can access ANY active exam (coding OR MCQ)."""
    c=conn()
    try:
        s,remaining=current_exam(c)
        tid = get_student_teacher_id(c, uid)
        row=c.execute('SELECT locked,allowed,joined_at,mcq_locked FROM exam_access WHERE user_id=? AND teacher_id=?',(uid,tid)).fetchone()
        if not s or not s.get('active') or not row or not row['allowed'] or not row['joined_at']: return False
        coding_ok = bool(s.get('coding_active') and remaining>0 and not row['locked'])
        mcq_ok    = bool(s.get('mcq_active') and not row['mcq_locked'])
        return coding_ok or mcq_ok
    finally:
        c.close()

def has_mcq_access(uid):
    """True only if MCQ exam is active and student is eligible."""
    c=conn()
    try:
        s,_=current_exam(c)
        tid = get_student_teacher_id(c, uid)
        row=c.execute('SELECT mcq_locked,allowed,joined_at FROM exam_access WHERE user_id=? AND teacher_id=?',(uid,tid)).fetchone()
        return bool(s and s.get('active') and s.get('mcq_active') and row and row['allowed'] and row['joined_at'] and not row['mcq_locked'])
    finally:
        c.close()
def require_exam_access():
    if session.get('role')!='student' or not has_exam_access(session['user_id']):
        return jsonify(error='Exam is locked. Enter the current exam key after the teacher starts the exam.'),403
    return None
def require_mcq_access():
    if session.get('role')!='student' or not has_mcq_access(session['user_id']):
        return jsonify(error='MCQ exam access denied.'),403
    return None
@app.post('/api/activity')
@auth('student')
def activity():
    d=request.get_json() or {}; event=str(d.get('event',''))
    allowed_events={'tab_hidden','window_blur','fullscreen_exit','blocked_paste','blocked_key','screen_resize','attempt_close','three_finger_gesture','mouse_left_viewport','window_refocus','extension_detected','external_display_detected'}
    if event not in allowed_events or not has_exam_access(session['user_id']):
        return jsonify(ok=True,ignored=True,illegal_count=illegal_count(session['user_id']),locked=False)
    now=time.time(); c=conn()
    try:
        # Use exam_type from session to track violations separately
        exam_type = session.get('exam_type', 'coding')
        tid = get_student_teacher_id(c, session['user_id'])
        if exam_type == 'mcq':
            row=c.execute('SELECT mcq_violations,mcq_locked,last_violation_at FROM exam_access WHERE user_id=? AND teacher_id=?',(session['user_id'], tid)).fetchone()
            curr_cnt = int(row['mcq_violations'] or 0) if row else 0
            if row and (row['mcq_locked'] or curr_cnt >= 5):
                return jsonify(ok=True,illegal_count=5,locked=True)
                
            if event in ('window_refocus', 'attempt_close'):
                log(session['user_id'], event, d.get('details',''))
                return jsonify(ok=True, ignored=True, illegal_count=min(5, curr_cnt), locked=False)

            last_v = float(row['last_violation_at'] or 0) if row else 0
            if last_v and (now - last_v < 0.35):
                return jsonify(ok=True,ignored=True,illegal_count=min(5, curr_cnt),locked=False)

            new_count = min(5, curr_cnt + 1)
            locked = (new_count >= 5)
            c.execute('UPDATE exam_access SET mcq_violations=?,mcq_locked=?,last_violation_at=? WHERE user_id=? AND teacher_id=?',
                      (new_count, 1 if locked else 0, now, session['user_id'], tid))
            c.commit()
            log(session['user_id'], event, d.get('details',''))
            if locked:
                log(session['user_id'], 'mcq_exam_locked', 'mcq violation limit exceeded (5/5)')
            return jsonify(ok=True, illegal_count=new_count, locked=locked)
        else:
            row=c.execute('SELECT violations,locked,last_violation_at FROM exam_access WHERE user_id=? AND teacher_id=?',(session['user_id'], tid)).fetchone()
            curr_cnt = int(row['violations'] or 0) if row else 0
            if row and (row['locked'] or curr_cnt >= 5):
                return jsonify(ok=True,illegal_count=5,locked=True)

            if event in ('window_refocus', 'attempt_close'):
                log(session['user_id'], event, d.get('details',''))
                return jsonify(ok=True, ignored=True, illegal_count=min(5, curr_cnt), locked=False)

            last_v = float(row['last_violation_at'] or 0) if row else 0
            if last_v and (now - last_v < 0.35):
                return jsonify(ok=True,ignored=True,illegal_count=min(5, curr_cnt),locked=False)

            new_count = min(5, curr_cnt + 1)
            locked = (new_count >= 5)
            c.execute('UPDATE exam_access SET violations=?,locked=?,last_violation_at=? WHERE user_id=? AND teacher_id=?',
                      (new_count, 1 if locked else 0, now, session['user_id'], tid))
            c.commit()
            log(session['user_id'], event, d.get('details',''))
            if locked:
                log(session['user_id'], 'exam_locked', 'coding violation limit exceeded (5/5)')
            return jsonify(ok=True, illegal_count=new_count, locked=locked)
    finally:
        c.close()
@app.get('/api/questions')
@auth()
def questions():
    c=conn()
    tid = session.get('teacher_id') or session.get('user_id')
    if session.get('role') == 'student':
        tid = get_student_teacher_id(c, session['user_id'])
        state, qids, locked = get_effective_assignment(c, session['user_id'], tid, 'coding', session.get('exam_key'))
        if state == 'INVALID':
            c.close()
            return jsonify(error="Invalid question assignment. Please contact your examination proctor.", assignment_state="INVALID"), 400
        if (state == 'INDIVIDUAL' or (state == 'COMMON' and locked)) and qids:
            placeholders = ','.join('?' * len(qids))
            rows = c.execute(f'SELECT * FROM questions WHERE teacher_id=? AND id IN ({placeholders})', [tid] + qids).fetchall()
            row_map = {r['id']: dict(r) for r in rows}
            c.close()
            return jsonify([row_map[qid] for qid in qids if qid in row_map])
    rows=c.execute('SELECT * FROM questions WHERE active=1 AND teacher_id=? ORDER BY id DESC', (tid,)).fetchall(); c.close(); return jsonify([dict(x) for x in rows])
@app.post('/api/questions')
@auth('teacher')
def add_question():
    d=request.get_json() or {}; c=conn(); cur=c.execute('INSERT INTO questions(teacher_id,title,prompt,language) VALUES(?,?,?,?)',(session['user_id'],d.get('title','Untitled'),d.get('prompt',''),d.get('language','python'))); c.commit(); qid=cur.lastrowid; c.close(); return jsonify(ok=True,id=qid)
@app.put('/api/questions/<int:question_id>')
@auth('teacher')
def update_question(question_id):
    d=request.get_json() or {}
    title=(d.get('title') or '').strip()
    prompt=d.get('prompt') or ''
    language=d.get('language') or 'python'
    if not title:
        return jsonify(error='Question title is required.'),400
    c=conn(); c.execute('UPDATE questions SET title=?, prompt=?, language=? WHERE id=? AND teacher_id=?',(title,prompt,language,question_id, session['user_id'])); c.commit(); c.close()
    return jsonify(ok=True)

@app.delete('/api/questions/<int:question_id>')
@auth('teacher')
def delete_question(question_id):
    c=conn(); c.execute('UPDATE questions SET active=0 WHERE id=? AND teacher_id=?',(question_id, session['user_id'])); c.execute('DELETE FROM test_cases WHERE question_id=?',(question_id,)); c.commit(); c.close(); return jsonify(ok=True)

@app.post('/api/questions/import')
@auth('teacher')
def import_questions():
    """Import coding questions from CSV/Excel. Flexible column matching.
    Columns: question_name/title, instruction/prompt/description, language (optional),
    test_case_N_input, test_case_N_output, test_case_N_mark (N=1,2,...)
    """
    f=request.files.get('file')
    if not f: return jsonify(error='Choose a CSV or XLSX file.'),400
    try: rows=read_import_file(f)
    except ValueError as e: return jsonify(error=str(e)),400
    import re as _re2
    c=conn(); count=0
    try:
        def fuzzy(row, *keys):
            for k in keys:
                if k in row and row[k].strip(): return row[k].strip()
            # partial match
            for rk in row:
                for k in keys:
                    if k in rk and row[rk].strip(): return row[rk].strip()
            return ''
        for r in rows:
            title=fuzzy(r,'question_name','title','question','name')
            prompt=fuzzy(r,'instruction','instructions','prompt','description','problem','problem_statement')
            language=fuzzy(r,'language','lang') or 'python'
            langs = [l.strip() for l in language.split(',')]
            valid_langs = [l for l in langs if l in ('python', 'java', 'c')]
            language = ','.join(valid_langs) if valid_langs else 'python'
            if not title: continue
            cur=c.execute('INSERT INTO questions(teacher_id,title,prompt,language) VALUES(?,?,?,?)',(session['user_id'],title,prompt,language))
            qid=cur.lastrowid; count+=1
            # collect test cases: test_case_1_input, test_case_1_output, test_case_1_mark etc.
            tc_nums=set()
            for k in r:
                m=_re2.search(r'(\d+)',k)
                if m and ('input' in k or 'output' in k or 'mark' in k or 'expected' in k): tc_nums.add(m.group(1))
            for n in sorted(tc_nums, key=lambda x:int(x)):
                inp=fuzzy(r,f'test_case_{n}_input',f'input_{n}',f'testcase{n}input',f'tc{n}input',f'case{n}input')
                out=fuzzy(r,f'test_case_{n}_output',f'test_case_{n}_expected',f'output_{n}',f'expected_{n}',f'testcase{n}output',f'tc{n}output')
                mark=fuzzy(r,f'test_case_{n}_mark',f'test_case_{n}_marks',f'mark_{n}',f'marks_{n}',f'tc{n}mark')
                try: mark=float(mark) if mark else 1.0
                except ValueError: mark=1.0
                c.execute('INSERT INTO test_cases(question_id,input_data,expected_output,marks,visible) VALUES(?,?,?,?,0)',(qid,inp,out,mark))
        c.commit()
        return jsonify(ok=True,imported=count)
    finally:
        c.close()

@app.get('/api/mcqs')
@auth()
def list_mcqs():
    c=conn()
    teacher_id = session.get('teacher_id') or session.get('user_id')
    if session.get('role') == 'student':
        teacher_id = get_student_teacher_id(c, session['user_id'])
        state, qids, locked = get_effective_assignment(c, session['user_id'], teacher_id, 'mcq', session.get('exam_key'))
        if state == 'INVALID':
            c.close()
            return jsonify(error="Invalid question assignment. Please contact your examination proctor.", assignment_state="INVALID"), 400
        if (state == 'INDIVIDUAL' or (state == 'COMMON' and locked)) and qids:
            placeholders = ','.join('?' * len(qids))
            rows = c.execute(f'SELECT * FROM mcqs WHERE teacher_id=? AND id IN ({placeholders})', [teacher_id] + qids).fetchall()
            row_map = {r['id']: dict(r) for r in rows}
            c.close()
            res = []
            for qid in qids:
                if qid in row_map:
                    d = row_map[qid]
                    d.pop('correct_option', None)
                    res.append(d)
            return jsonify(res)
        rows = c.execute('SELECT * FROM mcqs WHERE teacher_id=? ORDER BY id', (teacher_id,)).fetchall()
        c.close()
        res = []
        for x in rows:
            d = dict(x)
            d.pop('correct_option', None)  # Protect answers from student
            res.append(d)
        return jsonify(res)
    rows=c.execute('SELECT * FROM mcqs WHERE teacher_id=? ORDER BY id', (teacher_id,)).fetchall()
    c.close()
    return jsonify([dict(x) for x in rows])
@app.post('/api/mcqs')
@auth('teacher')
def add_mcq():
    d=request.get_json() or {}; c=conn(); cur=c.execute('INSERT INTO mcqs(teacher_id,question,option_a,option_b,option_c,option_d,correct_option,marks,time_limit) VALUES(?,?,?,?,?,?,?,?,?)',(session['user_id'],d.get('question',''),d.get('option_a',''),d.get('option_b',''),d.get('option_c',''),d.get('option_d',''),d.get('correct_option','a'),float(d.get('marks', 1)),int(d.get('time_limit', 30)))); c.commit(); qid=cur.lastrowid; c.close(); return jsonify(ok=True,id=qid)
@app.put('/api/mcqs/<int:mcq_id>')
@auth('teacher')
def update_mcq(mcq_id):
    d=request.get_json() or {}; c=conn(); c.execute('UPDATE mcqs SET question=?, option_a=?, option_b=?, option_c=?, option_d=?, correct_option=?, marks=?, time_limit=? WHERE id=? AND teacher_id=?',(d.get('question',''),d.get('option_a',''),d.get('option_b',''),d.get('option_c',''),d.get('option_d',''),d.get('correct_option','a'),float(d.get('marks', 1)),int(d.get('time_limit', 30)),mcq_id,session['user_id'])); c.commit(); c.close(); return jsonify(ok=True)
@app.delete('/api/mcqs/<int:mcq_id>')
@auth('teacher')
def delete_mcq(mcq_id):
    c=conn(); c.execute('DELETE FROM mcqs WHERE id=? AND teacher_id=?',(mcq_id, session['user_id'])); c.commit(); c.close(); return jsonify(ok=True)
@app.get('/api/mcqs/status')
@auth('teacher')
def list_mcq_status():
    c=conn(); rows=c.execute('SELECT * FROM mcq_status WHERE teacher_id=? ORDER BY id DESC', (session['user_id'],)).fetchall(); c.close(); return jsonify([dict(x) for x in rows])
@app.post('/api/mcqs/submit')
@auth('student')
def submit_mcq():
    # Task 2: Use MCQ-specific access check (works even without coding exam)
    locked=require_mcq_access()
    if locked: return locked
    d=request.get_json() or {}; mcq_id=d.get('mcq_id'); selected=d.get('selected_option','')
    c=conn()
    tid = get_student_teacher_id(c, session['user_id'])
    exam_key = session.get('exam_key')
    
    acc = c.execute('SELECT mcq_locked, allowed, joined_at FROM exam_access WHERE user_id=? AND teacher_id=?', (session['user_id'], tid)).fetchone()
    if acc and acc['mcq_locked']:
        c.close(); return jsonify(error='MCQ examination has been finished and locked.'), 403
        
    if not is_question_authorized(c, session['user_id'], tid, 'mcq', mcq_id, exam_key):
        c.close(); return jsonify(error='Unauthorized MCQ submission.'), 403
    mcq=c.execute('SELECT correct_option FROM mcqs WHERE id=? AND teacher_id=?',(mcq_id, tid)).fetchone()
    if not mcq: c.close(); return jsonify(error='MCQ not found.'),404
    is_correct=int(str(mcq['correct_option']).lower()==str(selected).lower())
    
    # Idempotent answer upsert: one answer per student, teacher, and mcq
    existing = c.execute('SELECT id FROM mcq_status WHERE student_id=? AND teacher_id=? AND mcq_id=?', (session['user_id'], tid, mcq_id)).fetchone()
    if existing:
        c.execute('UPDATE mcq_status SET selected_option=?, is_correct=? WHERE id=?', (selected, is_correct, existing['id']))
    else:
        c.execute('INSERT INTO mcq_status(student_id,teacher_id,student_name,mcq_id,selected_option,is_correct) VALUES(?,?,?,?,?,?)',(session['user_id'],tid,session['name'],mcq_id,selected,is_correct))
    c.commit()
    
    state, qids, _ = get_effective_assignment(c, session['user_id'], tid, 'mcq', exam_key)
    if qids:
        placeholders = ','.join('?' * len(qids))
        mcq_row=c.execute(f'''
            SELECT COALESCE(SUM(m.marks), 0) as total
            FROM (
                SELECT mcq_id, MAX(is_correct) as is_correct
                FROM mcq_status
                WHERE student_id=? AND teacher_id=? AND mcq_id IN ({placeholders})
                GROUP BY mcq_id
            ) s
            JOIN mcqs m ON s.mcq_id=m.id
            WHERE s.is_correct=1
        ''', [session['user_id'], tid] + qids).fetchone()
    else:
        mcq_row=c.execute('''
            SELECT COALESCE(SUM(m.marks), 0) as total
            FROM (
                SELECT mcq_id, MAX(is_correct) as is_correct
                FROM mcq_status
                WHERE student_id=? AND teacher_id=?
                GROUP BY mcq_id
            ) s
            JOIN mcqs m ON s.mcq_id=m.id
            WHERE s.is_correct=1
        ''', (session['user_id'],tid)).fetchone()
    running_mcq_score=float(mcq_row['total']) if mcq_row and mcq_row['total'] else 0.0
    c.execute('INSERT INTO exam_access(user_id,teacher_id,mcq_final_score) VALUES(?,?,?) ON CONFLICT(user_id,teacher_id) DO UPDATE SET mcq_final_score=excluded.mcq_final_score',(session['user_id'],tid,running_mcq_score))
    c.commit(); c.close()
    return jsonify(ok=True,is_correct=bool(is_correct))

@app.post('/api/mcqs/finish')
@auth('student')
def finish_mcq():
    c = conn()
    tid = get_student_teacher_id(c, session['user_id'])
    exam_key = session.get('exam_key')
    row = c.execute('SELECT joined_at, total_time, mcq_locked FROM exam_access WHERE user_id=? AND teacher_id=?', 
                    (session['user_id'], tid)).fetchone()
    if row and row['mcq_locked']:
        c.close()
        return jsonify(ok=True)
    elapsed = max(1, int(time.time() - row['joined_at'])) if (row and row['joined_at']) else 0
    curr_time = (row['total_time'] if row else 0) or 0
    new_time = max(curr_time, elapsed)
    state, qids_f, _ = get_effective_assignment(c, session['user_id'], tid, 'mcq', exam_key)
    if qids_f:
        pl_f = ','.join('?' * len(qids_f))
        mcq_row = c.execute(f'''
            SELECT COALESCE(SUM(m.marks), 0) as total
            FROM (
                SELECT mcq_id, MAX(is_correct) as is_correct
                FROM mcq_status
                WHERE student_id=? AND teacher_id=? AND mcq_id IN ({pl_f})
                GROUP BY mcq_id
            ) s
            JOIN mcqs m ON s.mcq_id=m.id
            WHERE s.is_correct=1
        ''', [session['user_id'], tid] + qids_f).fetchone()
    else:
        mcq_row = c.execute('''
            SELECT COALESCE(SUM(m.marks), 0) as total
            FROM (
                SELECT mcq_id, MAX(is_correct) as is_correct
                FROM mcq_status
                WHERE student_id=? AND teacher_id=?
                GROUP BY mcq_id
            ) s
            JOIN mcqs m ON s.mcq_id=m.id
            WHERE s.is_correct=1
        ''', (session['user_id'], tid)).fetchone()
    mcq_final = float(mcq_row['total']) if mcq_row and mcq_row['total'] else 0.0
    c.execute('UPDATE exam_access SET mcq_locked=1, mcq_final_score=?, total_time=? WHERE user_id=? AND teacher_id=?', 
              (mcq_final, new_time, session['user_id'], tid))
    c.commit(); c.close()
    return jsonify(ok=True)

@app.post('/api/submit')
@auth('student')
def submit():
    locked=require_exam_access()
    if locked: return locked
    d=request.get_json() or {}
    code=d.get('code', '')
    language=d.get('language', 'python')
    qid=d.get('question_id')
    if not qid:
        return jsonify(error='question_id is required.'), 400
        
    c=conn()
    s, remaining = current_exam(c)
    if not s or not s.get('active') or remaining <= 0:
        c.close()
        return jsonify(error='Examination time has expired or exam is not active.'), 403
        
    tid = get_student_teacher_id(c, session['user_id'])
    exam_key = session.get('exam_key')
    
    acc = c.execute('SELECT locked, allowed, joined_at FROM exam_access WHERE user_id=? AND teacher_id=?', (session['user_id'], tid)).fetchone()
    if not acc or not acc['allowed'] or acc['locked'] or not acc['joined_at']:
        c.close()
        return jsonify(error='Examination access denied or locked.'), 403
        
    if not is_question_authorized(c, session['user_id'], tid, 'coding', qid, exam_key):
        c.close()
        return jsonify(error='Unauthorized question submission.'), 403
        
    elapsed = max(0, int(s['duration_minutes']*60 - remaining)) if s.get('duration_minutes') else 0
    
    # AUTHORITATIVE SERVER-SIDE SCORE CALCULATION
    cases = c.execute('SELECT * FROM test_cases WHERE question_id=? ORDER BY id', (qid,)).fetchall()
    calculated_score = 0.0
    passed_cases = 0
    total_cases = len(cases)
    
    if cases:
        for t in cases:
            passed, out, _ = run_once(language, code, t['input_data'])
            expected = normalize_output(t['expected_output'])
            norm_out = normalize_output(out)
            if passed and norm_out == expected:
                calculated_score += float(t['marks'] or 0)
                passed_cases += 1
    calculated_score = round(calculated_score, 2)
    
    c.execute('''
        INSERT INTO submissions(user_id, teacher_id, question_id, code, language, score, time_seconds, submitted_at)
        VALUES(?, ?, ?, ?, ?, ?, ?, ?)
    ''', (session['user_id'], tid, qid, code, language, calculated_score, elapsed, now_ist_str()))
    
    state, qids, _ = get_effective_assignment(c, session['user_id'], tid, 'coding', exam_key)
    if qids:
        placeholders = ','.join('?' * len(qids))
        best_row = c.execute(f'''
            SELECT COALESCE(SUM(max_score),0) as total, COALESCE(SUM(min_time),0) as t
            FROM (
                SELECT MAX(score) as max_score, MIN(time_seconds) as min_time
                FROM submissions
                WHERE user_id=? AND teacher_id=? AND question_id IN ({placeholders})
                GROUP BY question_id
            )
        ''', [session['user_id'], tid] + qids).fetchone()
    else:
        best_row = c.execute('''
            SELECT COALESCE(SUM(max_score),0) as total, COALESCE(SUM(min_time),0) as t
            FROM (
                SELECT MAX(score) as max_score, MIN(time_seconds) as min_time
                FROM submissions
                WHERE user_id=? AND teacher_id=?
                GROUP BY question_id
            )
        ''', (session['user_id'], tid)).fetchone()
        
    live_score = float(best_row['total']) if best_row and best_row['total'] else 0.0
    live_time = int(best_row['t']) if best_row and best_row['t'] else 0
    c.execute('''
        INSERT INTO exam_access(user_id, teacher_id, final_score, total_time)
        VALUES(?, ?, ?, ?)
        ON CONFLICT(user_id, teacher_id) DO UPDATE SET final_score=excluded.final_score, total_time=excluded.total_time
    ''', (session['user_id'], tid, live_score, live_time))
    c.commit()
    c.close()
    log(session['user_id'], 'submission', f'saved score={calculated_score} live={live_score}')
    return jsonify(ok=True, elapsed=elapsed, live_score=live_score, score=calculated_score, passed_cases=passed_cases, total_cases=total_cases)

@app.post('/api/draft')
@auth('student')
def save_draft():
    locked=require_exam_access()
    if locked: return locked
    d=request.get_json() or {}; qid=d.get('question_id'); code=d.get('code',''); lang=d.get('language','python')
    c=conn()
    try:
        tid = get_student_teacher_id(c, session['user_id'])
        if not is_question_authorized(c, session['user_id'], tid, 'coding', qid, session.get('exam_key')):
            return jsonify(error='Unauthorized question draft.'), 403
        c.execute('INSERT INTO drafts(user_id,question_id,language,code,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(user_id,question_id,language) DO UPDATE SET code=excluded.code, updated_at=excluded.updated_at',(session['user_id'],qid,lang,code,now_ist_str()))
        c.commit()
        return jsonify(ok=True)
    finally:
        c.close()

@app.get('/api/drafts')
@auth('student')
def get_drafts():
    c=conn()
    try:
        tid = get_student_teacher_id(c, session['user_id'])
        state, qids, locked = get_effective_assignment(c, session['user_id'], tid, 'coding', session.get('exam_key'))
        if state == 'INVALID':
            return jsonify(error="Invalid question assignment."), 400
        if (state == 'INDIVIDUAL' or (state == 'COMMON' and locked)) and qids:
            placeholders = ','.join('?' * len(qids))
            rows=c.execute(f'SELECT question_id, language, code FROM drafts WHERE user_id=? AND question_id IN ({placeholders})', [session['user_id']] + qids).fetchall()
        else:
            rows=c.execute('SELECT question_id, language, code FROM drafts WHERE user_id=?',(session['user_id'],)).fetchall()
        return jsonify([dict(x) for x in rows])
    finally:
        c.close()

@app.get('/api/leaderboard/download')
@auth()
def leaderboard_download():
    if session.get('role') not in ('teacher', 'admin'): return jsonify(error='Unauthorized'),401
    import io
    class_filter=request.args.get('class','').strip()  # partial match supported
    sort_order=request.args.get('sort','desc')
    fmt=request.args.get('format','csv').lower()  # 'csv' or 'xlsx'
    c=conn()
    try:
        teacher_id = session.get('teacher_id') or session.get('user_id')
        mcq_total=c.execute('SELECT COALESCE(SUM(marks),0) FROM mcqs WHERE teacher_id=?', (teacher_id,)).fetchone()[0] or 0
        coding_total=c.execute('SELECT COALESCE(SUM(marks),0) FROM test_cases t JOIN questions q ON t.question_id=q.id WHERE q.teacher_id=?', (teacher_id,)).fetchone()[0] or 0
        students=c.execute("SELECT u.id,u.name,u.username,p.department,p.semester,p.section,p.register_number,e.final_score,e.mcq_final_score,e.total_time FROM users u LEFT JOIN student_profiles p ON p.user_id=u.id LEFT JOIN exam_access e ON e.user_id=u.id AND e.teacher_id=? WHERE u.role='student' ORDER BY u.name", (teacher_id,)).fetchall()
        rows=[]
        for s in students:
            dept=s['department'] or ''; sec=s['section'] or ''
            cls=f"{dept}-{sec}".strip('-') if (dept or sec) else dept
            if class_filter:
                cf=class_filter.lower()
                if cf not in cls.lower() and cf not in dept.lower(): continue
            cs=round(s['final_score'] or 0,2); ms=round(s['mcq_final_score'] or 0,2)
            state_c, c_qids, _ = get_effective_assignment(c, s['id'], teacher_id, 'coding')
            state_m, m_qids, _ = get_effective_assignment(c, s['id'], teacher_id, 'mcq')
            is_ind_c = (state_c == 'INDIVIDUAL')
            is_ind_m = (state_m == 'INDIVIDUAL')
            s_c_tot = coding_total
            s_m_tot = mcq_total
            if is_ind_c and c_qids:
                pl_c = ','.join('?' * len(c_qids))
                s_c_tot = round(c.execute(f'SELECT COALESCE(SUM(marks),0) FROM test_cases WHERE question_id IN ({pl_c})', c_qids).fetchone()[0] or 0, 2)
            if is_ind_m and m_qids:
                pl_m = ','.join('?' * len(m_qids))
                s_m_tot = round(c.execute(f'SELECT COALESCE(SUM(marks),0) FROM mcqs WHERE id IN ({pl_m})', m_qids).fetchone()[0] or 0, 2)
            rows.append({'name':s['name'],'reg':s['register_number'] or s['username'],'dept':dept,'section':sec,'class':cls,'semester':s['semester'] or '','coding':cs,'coding_total':round(s_c_tot,2),'mcq':ms,'mcq_total':round(s_m_tot,2),'total':round(cs+ms,2),'grand_total':round(s_c_tot+s_m_tot,2),'time':s['total_time'] or 0})
    finally:
        c.close()
    rows.sort(key=lambda x:x['total'],reverse=(sort_order=='desc'))
    # Task 6: filename = class name or 'overall_leaderboard'
    safe_class=class_filter.replace(' ','_') if class_filter else 'overall'
    headers_row=['Rank','Name','Register No','Department','Section','Class','Semester','Coding Score','Coding Total','MCQ Score','MCQ Total','Total Score','Grand Total','Time (s)']
    from flask import Response
    if fmt=='xlsx':
        try:
            from openpyxl import Workbook
            wb=Workbook(); ws=wb.active; ws.title='Leaderboard'
            ws.append(headers_row)
            for i,r in enumerate(rows,1): ws.append([i,r['name'],r['reg'],r['dept'],r['section'],r['class'],r['semester'],r['coding'],r['coding_total'],r['mcq'],r['mcq_total'],r['total'],r['grand_total'],r['time']])
            buf=io.BytesIO(); wb.save(buf); buf.seek(0)
            return Response(buf.read(),mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':f'attachment;filename={safe_class}_leaderboard.xlsx'})
        except ImportError: pass  # fallback to csv if openpyxl missing
    buf=io.StringIO()
    buf.write(','.join(headers_row)+'\n')
    for i,r in enumerate(rows,1):
        buf.write(f"{i},{r['name']},{r['reg']},{r['dept']},{r['section']},{r['class']},{r['semester']},{r['coding']},{r['coding_total']},{r['mcq']},{r['mcq_total']},{r['total']},{r['grand_total']},{r['time']}\n")
    return Response(buf.getvalue(),mimetype='text/csv',headers={'Content-Disposition':f'attachment;filename={safe_class}_leaderboard.csv'})

@app.get('/api/dashboard')
@auth()
def dashboard():
    if session.get('role') not in ('teacher', 'admin'): return jsonify(error='Unauthorized'),401
    c=conn()
    teacher_id = session.get('teacher_id') or session.get('user_id')
    if session.get('role') == 'admin':
        st = c.execute("SELECT teacher_id FROM exam_state WHERE active=1 LIMIT 1").fetchone()
        if st and st['teacher_id']:
            teacher_id = st['teacher_id']
    mcq_total=round(c.execute('SELECT COALESCE(SUM(marks),0) FROM mcqs WHERE teacher_id=?', (teacher_id,)).fetchone()[0] or 0,2)
    coding_total=round(c.execute('SELECT COALESCE(SUM(marks),0) FROM test_cases t JOIN questions q ON t.question_id=q.id WHERE q.teacher_id=?', (teacher_id,)).fetchone()[0] or 0,2)
    students=c.execute("SELECT u.id,u.name,u.username,p.department,p.section,p.register_number,e.final_score,e.mcq_final_score,e.total_time,e.locked FROM users u LEFT JOIN student_profiles p ON p.user_id=u.id JOIN exam_access e ON e.user_id=u.id AND e.teacher_id=? WHERE u.role='student' AND e.joined_at IS NOT NULL ORDER BY u.name", (teacher_id,)).fetchall(); out=[]
    for s in students:
        a=c.execute('SELECT event,details,created_at FROM activity WHERE user_id=? AND teacher_id=? ORDER BY id DESC',(s['id'],teacher_id)).fetchall()
        n=c.execute('SELECT count(*) n FROM submissions WHERE user_id=? AND teacher_id=?',(s['id'],teacher_id)).fetchone()['n']
        score = s['final_score'] if s['final_score'] is not None else 0
        mcq_score = s['mcq_final_score'] if s['mcq_final_score'] is not None else 0
        dept=s['department'] or ''; sec=s['section'] or ''
        cls=f"{dept}-{sec}".strip('-') if (dept or sec) else dept
        truly_pending = (n == 0 and s['final_score'] is None)
        # Task 3: Convert activity timestamps to IST for display
        # to_ist() handles both UTC strings (from old records) and IST strings (new records)
        activity_list = [{'event':x['event'],'details':x['details'],'created_at':to_ist(x['created_at'])} for x in a]
        state_c, c_qids, _ = get_effective_assignment(c, s['id'], teacher_id, 'coding')
        state_m, m_qids, _ = get_effective_assignment(c, s['id'], teacher_id, 'mcq')
        is_ind_c = (state_c == 'INDIVIDUAL')
        is_ind_m = (state_m == 'INDIVIDUAL')
        s_coding_total = coding_total
        s_mcq_total = mcq_total
        if is_ind_c and c_qids:
            pl_c = ','.join('?' * len(c_qids))
            s_coding_total = round(c.execute(f'SELECT COALESCE(SUM(marks),0) FROM test_cases WHERE question_id IN ({pl_c})', c_qids).fetchone()[0] or 0, 2)
        if is_ind_m and m_qids:
            pl_m = ','.join('?' * len(m_qids))
            s_mcq_total = round(c.execute(f'SELECT COALESCE(SUM(marks),0) FROM mcqs WHERE id IN ({pl_m})', m_qids).fetchone()[0] or 0, 2)
        out.append({'id':s['id'],'name':s['name'],'username':s['username'],'department':dept,'section':sec,'class_name':cls,'register_number':s['register_number'],'submissions':n,'score':round(score or 0,2),'mcq_score':round(mcq_score or 0,2),'coding_total':s_coding_total,'mcq_total':s_mcq_total,'total_time':s['total_time'] or 0,'locked':s['locked'],'illegal_count':illegal_count(s['id']),'activity':activity_list,'pending':truly_pending})
    c.close(); return jsonify(out)

@app.get('/api/teacher/student/<int:student_id>/submissions')
@auth()
def teacher_student_submissions(student_id):
    if session.get('role') not in ('teacher', 'admin'): return jsonify(error='Unauthorized'), 401
    c = conn()
    subs = c.execute('''SELECT q.id as question_id, q.title as question_title, s.code, s.language, s.score, s.submitted_at
                        FROM submissions s
                        JOIN questions q ON s.question_id = q.id
                        WHERE s.user_id = ?
                        ORDER BY s.id DESC''', (student_id,)).fetchall()
    best = {}
    for sub in subs:
        qid = sub['question_id']
        if qid not in best or (sub['score'] or 0) > (best[qid]['score'] or 0):
            best[qid] = dict(sub)
    st = c.execute('SELECT u.id, u.name, u.username, p.register_number, p.department FROM users u LEFT JOIN student_profiles p ON p.user_id=u.id WHERE u.id=?', (student_id,)).fetchone()
    c.close()
    return jsonify(student=dict(st) if st else {}, submissions=list(best.values()))

def normalize_code_for_plagiarism(code, language='python'):
    if not code: return ""
    lines = []
    in_m = False
    for l in (code or '').splitlines():
        l = l.strip()
        if not l: continue
        if language in ('c', 'java'):
            if '/*' in l and '*/' in l: l = re.sub(r'/\*.*?\*/', '', l).strip()
            elif '/*' in l: in_m = True; l = l.split('/*')[0].strip()
            elif '*/' in l: in_m = False; l = l.split('*/')[1].strip()
            elif in_m: continue
            if '//' in l: l = l.split('//')[0].strip()
        else:
            if l.startswith('#'): continue
            if '#' in l: l = l.split('#')[0].strip()
        if l: lines.append(l)
    return re.sub(r'\s+', ' ', ' '.join(lines)).strip()

def compute_similarity(code1, code2, language='python'):
    norm1 = normalize_code_for_plagiarism(code1, language)
    norm2 = normalize_code_for_plagiarism(code2, language)
    if not norm1 or not norm2: return 0.0
    if norm1 == norm2: return 100.0
    ratio = difflib.SequenceMatcher(None, norm1, norm2).ratio()
    w1 = set(re.findall(r'[a-zA-Z_]\w+|[^\w\s]', norm1))
    w2 = set(re.findall(r'[a-zA-Z_]\w+|[^\w\s]', norm2))
    jac = (len(w1 & w2) / len(w1 | w2)) if (w1 or w2) else 0.0
    return round(min(100.0, max(0.0, (ratio * 0.65 + jac * 0.35) * 100.0)), 1)

@app.get('/api/plagiarism')
@auth()
def check_plagiarism():
    if session.get('role') not in ('teacher', 'admin'): return jsonify(error='Unauthorized'), 401
    c = conn()
    teacher_id = session.get('teacher_id') or session.get('user_id')
    if session.get('role') == 'admin':
        st = c.execute("SELECT teacher_id FROM exam_state WHERE active=1 LIMIT 1").fetchone()
        if st and st['teacher_id']: teacher_id = st['teacher_id']
            
    history_id = request.args.get('history_id', type=int)
    question_filter = request.args.get('question_id', type=int)
    results = []; high_risk_count = 0; total_comparisons = 0
    
    if history_id:
        row = c.execute("SELECT * FROM exam_history WHERE id=?", (history_id,)).fetchone()
        if row and row['result_data']:
            try:
                res_data = json.loads(row['result_data'])
                by_q = {}
                for stud in res_data:
                    for sub in stud.get('submissions_list', []):
                        q_t = sub.get('question_title') or f"Question #{sub.get('question_id')}"
                        if question_filter and sub.get('question_id') != question_filter: continue
                        by_q.setdefault(q_t, []).append({
                            'student': {'id': stud.get('user_id'), 'name': stud.get('name'), 'username': stud.get('username'), 'department': stud.get('department', ''), 'section': stud.get('section', '')},
                            'code': sub.get('code', ''), 'language': sub.get('language', 'python'), 'question_id': sub.get('question_id', 0), 'question_title': q_t
                        })
                for q_t, sub_list in by_q.items():
                    n = len(sub_list)
                    for i in range(n):
                        for j in range(i + 1, n):
                            total_comparisons += 1
                            s1 = sub_list[i]; s2 = sub_list[j]
                            sim = compute_similarity(s1['code'], s2['code'], s1['language'])
                            risk = "HIGH" if sim >= 75.0 else ("MODERATE" if sim >= 50.0 else "LOW")
                            if risk == "HIGH": high_risk_count += 1
                            results.append({'question_id': s1['question_id'], 'question_title': q_t, 'language': s1['language'], 'similarity': sim, 'risk': risk, 'student_1': s1['student'], 'student_2': s2['student'], 'code_1': s1['code'], 'code_2': s2['code']})
            except Exception: pass
        c.close(); results.sort(key=lambda x: x['similarity'], reverse=True)
        return jsonify(ok=True, history_id=history_id, total_comparisons=total_comparisons, high_risk_count=high_risk_count, matches=results)
        
    q_query = "SELECT id, title, language FROM questions WHERE teacher_id=?"
    q_params = [teacher_id]
    if question_filter: q_query += " AND id=?"; q_params.append(question_filter)
    questions = c.execute(q_query, q_params).fetchall()
    
    for q in questions:
        qid = q['id']; q_title = q['title']; q_lang = q['language']
        subs = c.execute("""
            SELECT s.user_id, s.code, s.language, s.score, u.name, u.username, p.department, p.section
            FROM submissions s JOIN users u ON s.user_id = u.id LEFT JOIN student_profiles p ON p.user_id = u.id
            WHERE s.question_id = ? AND s.teacher_id = ? AND length(trim(s.code)) > 0
            GROUP BY s.user_id ORDER BY s.id DESC
        """, (qid, teacher_id)).fetchall()
        n = len(subs)
        for i in range(n):
            for j in range(i + 1, n):
                total_comparisons += 1
                s1 = subs[i]; s2 = subs[j]
                sim = compute_similarity(s1['code'], s2['code'], q_lang)
                risk = "HIGH" if sim >= 75.0 else ("MODERATE" if sim >= 50.0 else "LOW")
                if risk == "HIGH": high_risk_count += 1
                results.append({
                    'question_id': qid, 'question_title': q_title, 'language': q_lang, 'similarity': sim, 'risk': risk,
                    'student_1': {'id': s1['user_id'], 'name': s1['name'], 'username': s1['username'], 'department': s1['department'] or '', 'section': s1['section'] or ''},
                    'student_2': {'id': s2['user_id'], 'name': s2['name'], 'username': s2['username'], 'department': s2['department'] or '', 'section': s2['section'] or ''},
                    'code_1': s1['code'], 'code_2': s2['code']
                })
    results.sort(key=lambda x: x['similarity'], reverse=True)
    c.close()
    return jsonify(ok=True, total_comparisons=total_comparisons, high_risk_count=high_risk_count, matches=results)

@app.get('/api/student/result')
@auth('student')
def student_result():
    uid = session['user_id']; c = conn()
    try:
        tid = get_student_teacher_id(c, uid)
        user_row = c.execute("SELECT u.id, u.name, u.username, p.department, p.year, p.class, p.section, p.register_number FROM users u LEFT JOIN student_profiles p ON p.user_id=u.id WHERE u.id=?", (uid,)).fetchone()
        if not user_row: return jsonify(error="Student not found"), 404
        acc = c.execute("SELECT * FROM exam_access WHERE user_id=? AND teacher_id=?", (uid, tid)).fetchone()
        state_c, assigned_c_ids, _ = get_effective_assignment(c, uid, tid, 'coding')
        state_m, assigned_m_ids, _ = get_effective_assignment(c, uid, tid, 'mcq')
        is_ind_c = (state_c == 'INDIVIDUAL')
        is_ind_m = (state_m == 'INDIVIDUAL')
        if is_ind_c and assigned_c_ids:
            pl_c = ','.join('?' * len(assigned_c_ids))
            raw_coding_total = round(c.execute(f'SELECT COALESCE(SUM(marks),0) FROM test_cases t JOIN questions q ON t.question_id=q.id WHERE q.teacher_id=? AND q.id IN ({pl_c})', [tid] + assigned_c_ids).fetchone()[0] or 0, 2)
        else:
            raw_coding_total = round(c.execute('SELECT COALESCE(SUM(marks),0) FROM test_cases t JOIN questions q ON t.question_id=q.id WHERE q.teacher_id=?', (tid,)).fetchone()[0] or 0, 2)
        if is_ind_m and assigned_m_ids:
            pl_m = ','.join('?' * len(assigned_m_ids))
            raw_mcq_total = round(c.execute(f'SELECT COALESCE(SUM(marks),0) FROM mcqs WHERE teacher_id=? AND id IN ({pl_m})', [tid] + assigned_m_ids).fetchone()[0] or 0, 2)
        else:
            raw_mcq_total = round(c.execute('SELECT COALESCE(SUM(marks),0) FROM mcqs WHERE teacher_id=?', (tid,)).fetchone()[0] or 0, 2)
        raw_coding_score = float(acc['final_score'] or 0.0) if acc and acc['final_score'] is not None else 0.0
        raw_mcq_score = float(acc['mcq_final_score'] or 0.0) if acc and acc['mcq_final_score'] is not None else 0.0

        # Determine what exam(s) the student actually attended
        has_coding_subs = c.execute("SELECT count(*) FROM submissions WHERE user_id=? AND teacher_id=?", (uid, tid)).fetchone()[0] > 0
        has_coding_final = acc is not None and acc['final_score'] is not None
        attended_coding = bool(has_coding_subs or has_coding_final or (session.get('exam_type') == 'coding' and acc and acc['joined_at']))

        has_mcq_subs = c.execute("SELECT count(*) FROM mcq_status WHERE student_id=? AND teacher_id=?", (uid, tid)).fetchone()[0] > 0
        has_mcq_final = acc is not None and acc['mcq_final_score'] is not None and (acc['mcq_final_score'] > 0 or acc['mcq_locked'])
        attended_mcq = bool(has_mcq_subs or has_mcq_final or (session.get('exam_type') == 'mcq' and acc and acc['joined_at']))

        # Fallback to current session exam_type or active exam if neither registered yet
        if not attended_coding and not attended_mcq:
            if session.get('exam_type') == 'mcq':
                attended_mcq = True
            else:
                attended_coding = True

        if attended_coding and not attended_mcq:
            exam_mode = 'coding'
            coding_score = raw_coding_score
            coding_total = raw_coding_total
            mcq_score = 0.0
            mcq_total = 0.0
            total_score = coding_score
            grand_total = coding_total
        elif attended_mcq and not attended_coding:
            exam_mode = 'mcq'
            coding_score = 0.0
            coding_total = 0.0
            mcq_score = raw_mcq_score
            mcq_total = raw_mcq_total
            total_score = mcq_score
            grand_total = mcq_total
        else:
            exam_mode = 'both'
            coding_score = raw_coding_score
            coding_total = raw_coding_total
            mcq_score = raw_mcq_score
            mcq_total = raw_mcq_total
            total_score = round(coding_score + mcq_score, 2)
            grand_total = round(coding_total + mcq_total, 2)

        percentage = round((total_score / grand_total * 100), 1) if grand_total > 0 else 0.0
        status = "PASS" if percentage >= 50.0 else "NEEDS IMPROVEMENT"

        questions_list = []
        if attended_coding:
            q_filter = (" AND q.id IN (" + ",".join(["?"] * len(assigned_c_ids)) + ") ") if (is_ind_c and assigned_c_ids) else ""
            q_params = [uid, tid, uid, tid, uid, tid, uid, tid, tid] + (assigned_c_ids if (is_ind_c and assigned_c_ids) else [])
            q_rows = c.execute("""
                SELECT q.id, q.title, q.prompt, q.language,
                       COALESCE((SELECT SUM(marks) FROM test_cases WHERE question_id=q.id), 0) as max_marks,
                       (SELECT MAX(score) FROM submissions WHERE user_id=? AND question_id=q.id AND teacher_id=?) as scored_marks,
                       (SELECT language FROM submissions WHERE user_id=? AND question_id=q.id AND teacher_id=? ORDER BY id DESC LIMIT 1) as submitted_lang,
                       (SELECT time_seconds FROM submissions WHERE user_id=? AND question_id=q.id AND teacher_id=? ORDER BY id DESC LIMIT 1) as time_taken,
                       (SELECT code FROM submissions WHERE user_id=? AND question_id=q.id AND teacher_id=? ORDER BY id DESC LIMIT 1) as submitted_code
                FROM questions q WHERE q.teacher_id=? AND q.active=1 """ + q_filter + """ORDER BY q.id
            """, q_params).fetchall()
            for q in q_rows:
                scored = float(q['scored_marks'] or 0.0) if q['scored_marks'] is not None else 0.0
                max_m = float(q['max_marks'] or 0.0)
                q_status = "Full Marks" if (scored >= max_m and max_m > 0) else ("Partial" if scored > 0 else "Not Solved")
                questions_list.append({"id": q['id'], "title": q['title'], "language": q['submitted_lang'] or q['language'], "max_marks": max_m, "scored_marks": scored, "status": q_status, "time_seconds": q['time_taken'] or 0, "has_code": bool(q['submitted_code'])})

        mcq_list = []
        if attended_mcq:
            m_filter = (" AND m.id IN (" + ",".join(["?"] * len(assigned_m_ids)) + ") ") if (is_ind_m and assigned_m_ids) else ""
            m_params = [uid, tid, tid] + (assigned_m_ids if (is_ind_m and assigned_m_ids) else [])
            mcq_rows = c.execute("""
                SELECT m.id, m.question, m.marks, s.selected_option, s.is_correct, m.correct_option
                FROM mcqs m LEFT JOIN mcq_status s ON s.mcq_id=m.id AND s.student_id=? AND s.teacher_id=?
                WHERE m.teacher_id=? """ + m_filter + """ORDER BY m.id
            """, m_params).fetchall()
            for m in mcq_rows:
                mcq_list.append({"id": m['id'], "question": m['question'], "marks": float(m['marks'] or 1.0), "selected_option": m['selected_option'] or '-', "is_correct": bool(m['is_correct']) if m['is_correct'] is not None else False})

        return jsonify(
            ok=True,
            student={
                "name": user_row['name'],
                "username": user_row['username'],
                "department": user_row['department'] or '',
            "year": user_row['year'] or '',
            "class": user_row['class'] or '',
            "section": user_row['section'] or '',
            "register_number": user_row['register_number'] or user_row['username']
        },
        scores={
            "exam_mode": exam_mode,
            "attended_coding": attended_coding,
            "attended_mcq": attended_mcq,
            "coding_score": coding_score,
            "coding_total": coding_total,
            "mcq_score": mcq_score,
            "mcq_total": mcq_total,
            "total_score": total_score,
            "grand_total": grand_total,
            "percentage": percentage,
            "status": status,
            "total_time": (acc['total_time'] if acc else 0) or 0,
            "violations": (acc['violations'] if acc else 0) or 0,
            "locked": bool(acc['locked'] if acc else False)
        },
            questions=questions_list,
            mcqs=mcq_list,
            exam_date=now_ist_str()
        )
    finally:
        c.close()

@app.get('/api/courses')
@auth()
def courses():
    c=conn(); rows=c.execute('SELECT * FROM courses WHERE active=1 ORDER BY id DESC').fetchall(); c.close(); return jsonify([dict(x) for x in rows])
@app.post('/api/courses')
@auth('teacher')
def add_course():
    d=request.get_json() or {}; c=conn(); c.execute('INSERT INTO courses(title,description) VALUES(?,?)',(d.get('title','Untitled course'),d.get('description',''))); c.commit(); c.close(); return jsonify(ok=True)
@app.post('/api/topics')
@auth('teacher')
def add_topic():
    d=request.get_json() or {}; c=conn(); c.execute('INSERT INTO topics(course_id,title) VALUES(?,?)',(d.get('course_id'),d.get('title','Untitled topic'))); c.commit(); c.close(); return jsonify(ok=True)
@app.post('/api/test-cases')
@auth('teacher')
def add_test_case():
    d=request.get_json() or {}; c=conn()
    try:
        q = c.execute('SELECT id FROM questions WHERE id=? AND teacher_id=?', (d.get('question_id'), session['user_id'])).fetchone()
        if not q: return jsonify(error='Question not found or access denied.'), 403
        c.execute('INSERT INTO test_cases(question_id,input_data,expected_output,marks,visible) VALUES(?,?,?,?,?)',(d.get('question_id'),d.get('input_data',''),d.get('expected_output',''),d.get('marks',1),bool(d.get('visible'))))
        c.commit()
        return jsonify(ok=True)
    finally:
        c.close()

@app.get('/api/test-cases/<int:question_id>')
@auth('teacher')
def list_test_cases(question_id):
    c=conn()
    try:
        rows=c.execute('SELECT t.* FROM test_cases t JOIN questions q ON t.question_id = q.id WHERE t.question_id=? AND q.teacher_id=? ORDER BY t.id',(question_id, session['user_id'])).fetchall()
        return jsonify([dict(x) for x in rows])
    finally:
        c.close()

@app.put('/api/test-cases/<int:case_id>')
@auth('teacher')
def update_test_case(case_id):
    d=request.get_json() or {}
    try: marks=float(d.get('marks', 1))
    except (TypeError, ValueError): return jsonify(error='Marks must be numeric.'),400
    c=conn()
    try:
        q = c.execute('SELECT t.id FROM test_cases t JOIN questions q ON t.question_id = q.id WHERE t.id=? AND q.teacher_id=?', (case_id, session['user_id'])).fetchone()
        if not q: return jsonify(error='Test case not found or access denied.'), 403
        cur=c.execute('UPDATE test_cases SET input_data=?,expected_output=?,marks=?,visible=? WHERE id=?',(d.get('input_data',''),d.get('expected_output',''),marks,int(bool(d.get('visible'))),case_id))
        c.commit()
        if not cur.rowcount: return jsonify(error='Test case not found.'),404
        return jsonify(ok=True)
    finally:
        c.close()

@app.delete('/api/test-cases/<int:case_id>')
@auth('teacher')
def delete_test_case(case_id):
    c=conn()
    try:
        q = c.execute('SELECT t.id FROM test_cases t JOIN questions q ON t.question_id = q.id WHERE t.id=? AND q.teacher_id=?', (case_id, session['user_id'])).fetchone()
        if not q: return jsonify(error='Test case not found or access denied.'), 403
        cur=c.execute('DELETE FROM test_cases WHERE id=?',(case_id,))
        c.commit()
        if not cur.rowcount: return jsonify(error='Test case not found.'),404
        return jsonify(ok=True)
    finally:
        c.close()

@app.get('/api/exam-state')
@auth()
def exam_state():
    c=conn()
    try:
        s,remaining=current_exam(c); joined=False; locked=False; row=None; has_result=False
        if session.get('role')=='student':
            tid = get_student_teacher_id(c, session['user_id'])
            row=c.execute('SELECT locked,allowed,joined_at,violations,final_score,mcq_final_score FROM exam_access WHERE user_id=? AND teacher_id=?',(session['user_id'],tid)).fetchone()
            joined=bool(row and row['joined_at']); locked=bool(row and row['locked'])
            has_result=bool(row and (row['final_score'] is not None or row['mcq_final_score'] is not None or row['locked']))
        is_staff = session.get('role') in ('teacher','admin')
        return jsonify(active=bool(s['active']),remaining=remaining,joined=joined,locked=locked,allowed=bool(row and row['allowed']) if session.get('role')=='student' else True,illegal_count=illegal_count(session['user_id']) if session.get('role')=='student' else 0,access_code=s['access_code'] if is_staff else None,mcq_access_code=s.get('mcq_access_code') if is_staff else None,has_result=has_result)
    finally:
        c.close()
@app.post('/api/change_password')
@auth()
def change_password():
    pw = (request.get_json() or {}).get("password")
    if not pw: return jsonify(error="Password required"),400
    if pw in ('Admin123!', 'ChangeMe123!', 'Student123!'): return jsonify(error="Please choose a different secure password."),400
    from werkzeug.security import generate_password_hash
    c=conn(); c.execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(pw), session["user_id"]))
    c.commit(); c.close()
    session.pop('force_password_change', None)
    return jsonify(ok=True)


def save_exam_history(teacher_id, c):
    import json
    # Get total students who joined or submitted answers
    students = c.execute('SELECT count(*) as c FROM exam_access WHERE teacher_id=? AND (joined_at IS NOT NULL OR final_score IS NOT NULL OR mcq_final_score IS NOT NULL)', (teacher_id,)).fetchone()
    total_students = students['c'] if students else 0
    has_subs = c.execute('SELECT count(*) FROM submissions WHERE teacher_id=?', (teacher_id,)).fetchone()[0]
    has_mcq = c.execute('SELECT count(*) FROM mcq_status WHERE teacher_id=?', (teacher_id,)).fetchone()[0]
    if total_students == 0 and has_subs == 0 and has_mcq == 0:
        return # don't save empty exam
    
    teacher = c.execute('SELECT name FROM users WHERE id=?', (teacher_id,)).fetchone()
    teacher_name = teacher['name'] if teacher else 'Unknown'
    
    # Get active exam key for this session
    st = c.execute('SELECT access_code, mcq_access_code FROM exam_state WHERE teacher_id=?', (teacher_id,)).fetchone()
    exam_key = (st['access_code'] if st and st['access_code'] else (st['mcq_access_code'] if st and st['mcq_access_code'] else 'EXAM'))
    
    # Save snapshot of leaderboard
    rows = c.execute('''SELECT u.id as user_id, u.name, u.username, 
                               COALESCE(p.department, u.department, '-') as department,
                               COALESCE(p.section, '-') as section,
                               COALESCE(p.register_number, u.username) as register_number,
                               COALESCE(a.final_score, 0) as final_score, 
                               COALESCE(a.mcq_final_score, 0) as mcq_final_score, 
                               COALESCE(a.total_time, 0) as total_time,
                               a.joined_at
                        FROM exam_access a 
                        JOIN users u ON a.user_id=u.id 
                        LEFT JOIN student_profiles p ON p.user_id=u.id
                        WHERE a.teacher_id=? AND (a.joined_at IS NOT NULL OR a.final_score IS NOT NULL OR a.mcq_final_score IS NOT NULL)
                        ORDER BY (COALESCE(a.final_score,0) + COALESCE(a.mcq_final_score,0)) DESC''', (teacher_id,)).fetchall()
    
    data = []
    all_question_submissions = []
    for r in rows:
        d = dict(r)
        # If student never had total_time set but joined, calculate elapsed seconds from joined_at
        if (not d.get('total_time') or d['total_time'] == 0) and d.get('joined_at'):
            d['total_time'] = max(1, int(time.time() - d['joined_at']))
        # Fetch code submissions
        subs = c.execute('''SELECT q.id as question_id, q.title, s.code, s.language, s.score 
                            FROM submissions s 
                            JOIN questions q ON s.question_id=q.id 
                            WHERE s.user_id=? AND (s.teacher_id=? OR q.teacher_id=?)''', (d['user_id'], teacher_id, teacher_id)).fetchall()
        
        best_subs = {}
        for s in subs:
            title = s['title']
            if title not in best_subs or (s['score'] or 0) > (best_subs[title]['score'] or 0):
                best_subs[title] = dict(s)
        
        # Also check drafts for any questions where student typed code
        drafts = c.execute('''SELECT q.id as question_id, q.title, d.code, d.language, 0 as score 
                              FROM drafts d 
                              JOIN questions q ON d.question_id=q.id 
                              WHERE d.user_id=?''', (d['user_id'],)).fetchall()
        for dr in drafts:
            title = dr['title']
            if title not in best_subs and dr['code'] and dr['code'].strip():
                best_subs[title] = dict(dr)
        
        submissions_list = []
        code_str = ""
        for title, sub in best_subs.items():
            sub_item = {
                'question_title': title,
                'question_id': sub.get('question_id'),
                'language': sub.get('language') or 'python',
                'score': sub.get('score', 0),
                'code': sub.get('code', '')
            }
            submissions_list.append(sub_item)
            code_str += f"\n--- Question: {title} ({sub.get('language','python')} | Score: {sub.get('score',0)}) ---\n{sub.get('code','')}\n"
            all_question_submissions.append({
                'user_id': d['user_id'],
                'student_name': d['name'],
                'register_number': d['register_number'],
                'question_id': sub.get('question_id'),
                'question_title': title,
                'language': sub.get('language') or 'python',
                'score': sub.get('score', 0),
                'code': sub.get('code', '')
            })
        
        d['violations'] = illegal_count(d['user_id'], teacher_id)
        d['code_submitted'] = code_str.strip()
        d['submissions_list'] = submissions_list
        data.append(d)
        
    json_data = json.dumps(data)
    # Check if the latest exam history entry for this teacher is identical
    last_hist = c.execute('SELECT result_data FROM exam_history WHERE teacher_id=? ORDER BY id DESC LIMIT 1', (teacher_id,)).fetchone()
    if last_hist and last_hist['result_data'] == json_data:
        return # Skip duplicate saving

    # Create standalone SQLite database file dedicated to this exam key
    exam_dbs_dir = os.path.join(ROOT, 'exam_databases')
    os.makedirs(exam_dbs_dir, exist_ok=True)
    ts_slug = datetime.now(tz=_IST).strftime('%Y%m%d_%H%M%S')
    db_filename = f"exam_{exam_key}_{ts_slug}.db"
    db_filepath = os.path.join(exam_dbs_dir, db_filename)
    exam_date_ist = now_ist_str()

    try:
        sess_db = sqlite3.connect(db_filepath)
        sess_cur = sess_db.cursor()
        sess_cur.executescript('''
            CREATE TABLE IF NOT EXISTS exam_info (
                exam_key TEXT,
                teacher_id INTEGER,
                teacher_name TEXT,
                exam_date TEXT,
                total_students INTEGER
            );
            CREATE TABLE IF NOT EXISTS student_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exam_key TEXT,
                user_id INTEGER,
                student_name TEXT,
                register_number TEXT,
                department TEXT,
                section TEXT,
                coding_score REAL,
                mcq_score REAL,
                total_score REAL,
                total_time INTEGER,
                violations INTEGER,
                code_submitted TEXT,
                submissions_json TEXT
            );
            CREATE TABLE IF NOT EXISTS question_submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exam_key TEXT,
                user_id INTEGER,
                student_name TEXT,
                register_number TEXT,
                question_id INTEGER,
                question_title TEXT,
                language TEXT,
                score REAL,
                code TEXT
            );
            CREATE TABLE IF NOT EXISTS activity_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exam_key TEXT,
                user_id INTEGER,
                event TEXT,
                details TEXT,
                created_at TEXT
            );
        ''')
        sess_cur.execute('INSERT INTO exam_info VALUES (?,?,?,?,?)', (str(exam_key), teacher_id, teacher_name, exam_date_ist, total_students))
        for item in data:
            tot = (float(item.get('final_score') or 0)) + (float(item.get('mcq_final_score') or 0))
            sess_cur.execute('''INSERT INTO student_results 
                (exam_key, user_id, student_name, register_number, department, section, coding_score, mcq_score, total_score, total_time, violations, code_submitted, submissions_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (str(exam_key), item['user_id'], item['name'], item['register_number'], item['department'], item['section'], item['final_score'], item['mcq_final_score'], tot, item['total_time'], item['violations'], item['code_submitted'], json.dumps(item['submissions_list'])))
        for qsub in all_question_submissions:
            sess_cur.execute('''INSERT INTO question_submissions
                (exam_key, user_id, student_name, register_number, question_id, question_title, language, score, code)
                VALUES (?,?,?,?,?,?,?,?,?)''',
                (str(exam_key), qsub['user_id'], qsub['student_name'], qsub['register_number'], qsub['question_id'], qsub['question_title'], qsub['language'], qsub['score'], qsub['code']))
        sess_cur.executescript('''CREATE TABLE IF NOT EXISTS student_question_assignments (id INTEGER PRIMARY KEY AUTOINCREMENT, exam_key TEXT, teacher_id INTEGER, exam_type TEXT, student_id INTEGER, assigned_question_ids TEXT, created_by INTEGER, created_at TEXT, updated_at TEXT);''')
        asgn_rows = c.execute("SELECT exam_key, teacher_id, exam_type, student_id, assigned_question_ids, created_by, created_at, updated_at FROM student_question_assignments WHERE teacher_id=? AND exam_key=?", (teacher_id, str(exam_key))).fetchall()
        for ar in asgn_rows:
            sess_cur.execute("INSERT INTO student_question_assignments (exam_key, teacher_id, exam_type, student_id, assigned_question_ids, created_by, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)", (ar['exam_key'], ar['teacher_id'], ar['exam_type'], ar['student_id'], ar['assigned_question_ids'], ar['created_by'], ar['created_at'], ar['updated_at']))
        act_rows = c.execute('SELECT user_id, event, details, created_at FROM activity WHERE teacher_id=?', (teacher_id,)).fetchall()
        for a in act_rows:
            sess_cur.execute('INSERT INTO activity_logs (exam_key, user_id, event, details, created_at) VALUES (?,?,?,?,?)',
                (str(exam_key), a['user_id'], a['event'], a['details'], to_ist(a['created_at'])))
        sess_db.commit()
        sess_db.close()
    except Exception as ex:
        print("Error saving standalone exam database:", ex)
        db_filename = None

    c.execute('INSERT INTO exam_history(teacher_id, teacher_name, exam_key, exam_date, total_students, result_data, db_file) VALUES(?,?,?,?,?,?,?)',
              (teacher_id, teacher_name, str(exam_key), exam_date_ist, total_students, json_data, db_filename))

@app.post('/api/exam/start_coding')
@auth('teacher')
def exam_start_coding():
    d=request.get_json() or {}; minutes=max(1,int(d.get("minutes",60)))
    c_pre=conn(); st_pre=c_pre.execute('SELECT access_code FROM exam_state WHERE teacher_id=?',(session['user_id'],)).fetchone(); c_pre.close()
    exam_code=str(d.get("access_code") or (st_pre['access_code'] if st_pre and st_pre['access_code'] else random.randint(100000,999999)))
    c=conn()
    save_exam_history(session['user_id'], c)
    # Clear all active session data and drafts for this teacher so the new exam key is 100% fresh!
    c.execute("DELETE FROM submissions WHERE teacher_id=?",(session["user_id"],))
    c.execute("DELETE FROM activity WHERE teacher_id=?",(session["user_id"],))
    c.execute("DELETE FROM mcq_status WHERE teacher_id=?",(session["user_id"],))
    c.execute("DELETE FROM drafts WHERE question_id IN (SELECT id FROM questions WHERE teacher_id=?)", (session["user_id"],))
    c.execute("DELETE FROM exam_attempt_snapshots WHERE teacher_id=? AND exam_type='coding'", (session["user_id"],))
    c.execute("UPDATE exam_access SET joined_at=NULL,locked=0,violations=0,last_violation_at=0,final_score=NULL,mcq_final_score=NULL,total_time=NULL,mcq_locked=0,mcq_violations=0 WHERE teacher_id=?", (session['user_id'],))
    c.execute("INSERT INTO exam_state (teacher_id, active, coding_active, access_code, duration_minutes, started_at) VALUES (?, 1, 1, ?, ?, ?) ON CONFLICT(teacher_id) DO UPDATE SET active=1, coding_active=1, access_code=excluded.access_code, duration_minutes=excluded.duration_minutes, started_at=excluded.started_at", (session['user_id'], exam_code, minutes, time.time()))
    c.commit(); c.close(); return jsonify(ok=True,access_code=exam_code)

@app.post('/api/exam/start_mcq')
@auth('teacher')
def exam_start_mcq():
    d=request.get_json() or {}
    c_pre=conn(); st_pre=c_pre.execute('SELECT mcq_access_code FROM exam_state WHERE teacher_id=?',(session['user_id'],)).fetchone(); c_pre.close()
    mcq_code=str(d.get("mcq_access_code") or (st_pre['mcq_access_code'] if st_pre and st_pre['mcq_access_code'] else random.randint(100000,999999)))
    c=conn()
    save_exam_history(session['user_id'], c)
    c.execute("DELETE FROM mcq_status WHERE teacher_id=?",(session["user_id"],))
    c.execute("DELETE FROM submissions WHERE teacher_id=?",(session["user_id"],))
    c.execute("DELETE FROM activity WHERE teacher_id=?",(session["user_id"],))
    c.execute("DELETE FROM drafts WHERE question_id IN (SELECT id FROM questions WHERE teacher_id=?)", (session["user_id"],))
    c.execute("DELETE FROM exam_attempt_snapshots WHERE teacher_id=? AND exam_type='mcq'", (session["user_id"],))
    c.execute("UPDATE exam_access SET joined_at=NULL,locked=0,violations=0,last_violation_at=0,final_score=NULL,mcq_final_score=NULL,total_time=NULL,mcq_locked=0,mcq_violations=0 WHERE teacher_id=?", (session['user_id'],))
    c.execute("INSERT INTO exam_state (teacher_id, active, mcq_active, mcq_access_code) VALUES (?, 1, 1, ?) ON CONFLICT(teacher_id) DO UPDATE SET active=1, mcq_active=1, mcq_access_code=excluded.mcq_access_code", (session['user_id'], mcq_code))
    c.commit(); c.close(); return jsonify(ok=True,mcq_access_code=mcq_code)

@app.post('/api/exam/stop')
@auth('teacher')
def exam_stop():
    c=conn()
    tid = session.get('teacher_id') or session['user_id']
    save_exam_history(tid, c)
    c.execute('UPDATE exam_state SET active=0, coding_active=0, mcq_active=0 WHERE teacher_id=?', (tid,))
    c.execute('UPDATE exam_access SET joined_at=NULL WHERE teacher_id=?', (tid,))
    c.commit(); c.close(); return jsonify(ok=True)
@app.post('/api/exam/clear_keys')
@auth('teacher')
def exam_clear_keys():
    c=conn()
    tid = session.get('teacher_id') or session.get('user_id')
    save_exam_history(tid, c)
    c.execute("DELETE FROM submissions WHERE teacher_id=?", (tid,))
    c.execute("DELETE FROM drafts WHERE question_id IN (SELECT id FROM questions WHERE teacher_id=?)", (tid,))
    c.execute("DELETE FROM mcq_status WHERE teacher_id=?", (tid,))
    c.execute("DELETE FROM activity WHERE teacher_id=?", (tid,))
    c.execute("DELETE FROM exam_attempt_snapshots WHERE teacher_id=?", (tid,))
    c.execute("UPDATE exam_access SET joined_at=NULL,locked=0,violations=0,last_violation_at=0,final_score=NULL,mcq_final_score=NULL,total_time=NULL,mcq_locked=0,mcq_violations=0 WHERE teacher_id=?", (tid,))
    c.execute('UPDATE exam_state SET access_code=NULL, mcq_access_code=NULL, active=0, coding_active=0, mcq_active=0 WHERE teacher_id=?', (tid,))
    c.commit(); c.close(); return jsonify(ok=True)

@app.post('/api/exam/student-exit')
@auth('student')
def exam_student_exit():
    c=conn()
    try:
        tid = session.get('teacher_id')
        if not tid:
            acc = c.execute('SELECT teacher_id FROM exam_access WHERE user_id=? LIMIT 1', (session['user_id'],)).fetchone()
            if acc and acc['teacher_id']:
                tid = acc['teacher_id']
        if not tid:
            st = c.execute('SELECT teacher_id FROM exam_state WHERE active=1 LIMIT 1').fetchone()
            if st:
                tid = st['teacher_id']
                
        etype = session.get('exam_type') or 'coding'
        if etype == 'coding':
            score_row = c.execute('SELECT COALESCE(SUM(max_score),0) as total, COALESCE(SUM(min_time),0) as time_total FROM (SELECT MAX(score) as max_score, MIN(time_seconds) as min_time FROM submissions WHERE user_id=? AND teacher_id=? GROUP BY question_id)', (session['user_id'], tid)).fetchone()
            final_score = score_row['total'] if score_row else 0
            total_time = score_row['time_total'] if score_row else 0
            if tid:
                c.execute('UPDATE exam_access SET locked=1, final_score=?, total_time=? WHERE user_id=? AND teacher_id=?',(final_score, total_time, session['user_id'], tid))
            else:
                c.execute('UPDATE exam_access SET locked=1, final_score=?, total_time=? WHERE user_id=?',(final_score, total_time, session['user_id']))
        elif etype == 'mcq':
            mcq_row = c.execute('SELECT SUM(m.marks) as total FROM mcq_status s JOIN mcqs m ON s.mcq_id=m.id WHERE s.student_id=? AND s.teacher_id=? AND s.is_correct=1', (session['user_id'], tid)).fetchone()
            mcq_final = mcq_row['total'] if mcq_row and mcq_row['total'] else 0
            if tid:
                c.execute('UPDATE exam_access SET mcq_locked=1, mcq_final_score=? WHERE user_id=? AND teacher_id=?',(mcq_final, session['user_id'], tid))
            else:
                c.execute('UPDATE exam_access SET mcq_locked=1, mcq_final_score=? WHERE user_id=?',(mcq_final, session['user_id']))
        else:
            score_row = c.execute('SELECT COALESCE(SUM(max_score),0) as total, COALESCE(SUM(min_time),0) as time_total FROM (SELECT MAX(score) as max_score, MIN(time_seconds) as min_time FROM submissions WHERE user_id=? AND teacher_id=? GROUP BY question_id)', (session['user_id'], tid)).fetchone()
            final_score = score_row['total'] if score_row else 0
            total_time = score_row['time_total'] if score_row else 0
            mcq_row = c.execute('SELECT SUM(m.marks) as total FROM mcq_status s JOIN mcqs m ON s.mcq_id=m.id WHERE s.student_id=? AND s.teacher_id=? AND s.is_correct=1', (session['user_id'], tid)).fetchone()
            mcq_final = mcq_row['total'] if mcq_row and mcq_row['total'] else 0
            if tid:
                c.execute('UPDATE exam_access SET locked=1, mcq_locked=1, final_score=?, mcq_final_score=?, total_time=? WHERE user_id=? AND teacher_id=?',(final_score, mcq_final, total_time, session['user_id'], tid))
            else:
                c.execute('UPDATE exam_access SET locked=1, mcq_locked=1, final_score=?, mcq_final_score=?, total_time=? WHERE user_id=?',(final_score, mcq_final, total_time, session['user_id']))

        c.commit()
        d = request.get_json(silent=True) or {}
        reason = d.get('reason') or 'student exited voluntarily'
        log(session['user_id'], 'exam_left', reason)
        return jsonify(ok=True)
    finally:
        c.close()
@app.post('/api/exam/join')
@auth('student')
def exam_join():
    d=request.get_json() or {}; key=str(d.get('access_code','')).strip()
    if not key:
        return jsonify(error='Please enter an exam key.'), 400
    c=conn()

    # Find the active exam that matches this key (any teacher's exam)
    found_state = c.execute(
        "SELECT * FROM exam_state WHERE (access_code=? AND coding_active=1 AND active=1) OR (mcq_access_code=? AND mcq_active=1 AND active=1)",
        (key, key)
    ).fetchone()

    if not found_state:
        c.close(); log(session['user_id'],'invalid_exam_key','')
        return jsonify(error='Invalid or expired exam key. Ask your teacher to verify the key.'), 403

    teacher_id = found_state['teacher_id']
    s = dict(found_state)

    # Determine exam type from the key
    exam_type = None
    remaining = 0

    if key == str(s.get('access_code') or '') and s.get('coding_active'):
        # Compute remaining time for coding exam
        if s.get('started_at') and s.get('duration_minutes'):
            remaining = max(0, int(s['duration_minutes']*60 - (time.time()-s['started_at'])))
        if remaining <= 0:
            c.close(); return jsonify(error='Coding exam time has expired.'), 403
        exam_type = 'coding'

    elif key == str(s.get('mcq_access_code') or '') and s.get('mcq_active'):
        exam_type = 'mcq'
        remaining = 0  # MCQ has no countdown in join

    else:
        c.close(); log(session['user_id'],'invalid_exam_key','')
        return jsonify(error='Invalid exam key.'), 403

    # Auto-register student under this teacher's exam room (if not already registered)
    c.execute(
        'INSERT OR IGNORE INTO exam_access(user_id, teacher_id, allowed, violations, last_violation_at, mcq_locked) VALUES (?, ?, 1, 0, 0, 0)',
        (session['user_id'], teacher_id)
    )
    c.commit()

    # Now check student's access status
    row = c.execute(
        'SELECT allowed, locked, mcq_locked, joined_at FROM exam_access WHERE user_id=? AND teacher_id=?',
        (session['user_id'], teacher_id)
    ).fetchone()

    if not row or not row['allowed']:
        c.close(); return jsonify(error='You are blocked from this exam by the teacher.'), 403

    if exam_type == 'coding' and row['locked']:
        c.close(); return jsonify(error='Your coding exam is locked due to violations.'), 403

    if exam_type == 'mcq' and row['mcq_locked']:
        c.close(); return jsonify(error='Your MCQ exam is locked due to violations.'), 403

    # Set session to associate student with this teacher's exam room
    session['teacher_id'] = teacher_id
    session['exam_type'] = exam_type
    session['exam_key'] = key
    
    # Check if this student is reconnecting to an ongoing attempt
    is_reconnect = bool(row and row['joined_at'])
    snap = None
    if is_reconnect:
        snap = c.execute('''
            SELECT mode, snapshot_question_ids FROM exam_attempt_snapshots
            WHERE exam_instance_id=? AND teacher_id=? AND exam_type=? AND student_id=?
        ''', (str(key), teacher_id, exam_type, session['user_id'])).fetchone()
    
    if snap:
        state = snap['mode']
        if state == 'INVALID':
            c.close()
            return jsonify(error="Invalid question assignment. Please contact your examination proctor.", assignment_state="INVALID"), 400
    else:
        state, qids_join, locked = get_effective_assignment(c, session['user_id'], teacher_id, exam_type, key)
        if state == 'INVALID':
            c.close()
            return jsonify(error="Invalid question assignment. Please contact your examination proctor.", assignment_state="INVALID"), 400
            
        if state == 'INDIVIDUAL':
            prof = c.execute("SELECT version FROM student_question_profiles WHERE teacher_id=? AND student_id=? AND exam_type=?", (teacher_id, session['user_id'], exam_type)).fetchone()
            p_ver = prof['version'] if prof else 1
            c.execute('''
                INSERT INTO exam_attempt_snapshots
                    (exam_instance_id, teacher_id, student_id, exam_type, profile_version, mode, snapshot_question_ids, created_at)
                VALUES (?, ?, ?, ?, ?, 'INDIVIDUAL', ?, CURRENT_TIMESTAMP)
                ON CONFLICT(exam_instance_id, teacher_id, exam_type, student_id) DO UPDATE SET
                    snapshot_question_ids=excluded.snapshot_question_ids,
                    profile_version=excluded.profile_version,
                    mode='INDIVIDUAL'
            ''', (str(key), teacher_id, session['user_id'], exam_type, p_ver, json.dumps(qids_join)))
            c.execute('''
                INSERT INTO student_question_assignments
                    (exam_key, teacher_id, exam_type, student_id, assigned_question_ids, created_by, attempt_locked, snapshot_question_ids, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(exam_key, teacher_id, exam_type, student_id) DO UPDATE SET
                    attempt_locked=1,
                    snapshot_question_ids=excluded.snapshot_question_ids,
                    updated_at=CURRENT_TIMESTAMP
            ''', (str(key), teacher_id, exam_type, session['user_id'], json.dumps(qids_join), teacher_id, json.dumps(qids_join)))
        elif state == 'COMMON' and not locked:
            # Snapshot the common question set at attempt start!
            if exam_type == 'coding':
                c_rows = c.execute("SELECT id FROM questions WHERE teacher_id=? AND active=1 ORDER BY id DESC", (teacher_id,)).fetchall()
            else:
                c_rows = c.execute("SELECT id FROM mcqs WHERE teacher_id=? ORDER BY id", (teacher_id,)).fetchall()
            common_qids = [r['id'] for r in c_rows]
            c.execute('''
                INSERT INTO exam_attempt_snapshots
                    (exam_instance_id, teacher_id, student_id, exam_type, profile_version, mode, snapshot_question_ids, created_at)
                VALUES (?, ?, ?, ?, 1, 'COMMON', ?, CURRENT_TIMESTAMP)
                ON CONFLICT(exam_instance_id, teacher_id, exam_type, student_id) DO UPDATE SET
                    snapshot_question_ids=excluded.snapshot_question_ids,
                    mode='COMMON'
            ''', (str(key), teacher_id, session['user_id'], exam_type, json.dumps(common_qids)))
            c.execute('''
                INSERT INTO student_question_assignments
                    (exam_key, teacher_id, exam_type, student_id, assigned_question_ids, created_by, attempt_locked, snapshot_question_ids, updated_at)
                VALUES (?, ?, ?, ?, 'COMMON', ?, 1, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(exam_key, teacher_id, exam_type, student_id) DO UPDATE SET
                    attempt_locked=1,
                    snapshot_question_ids=excluded.snapshot_question_ids,
                    updated_at=CURRENT_TIMESTAMP
                WHERE assigned_question_ids='COMMON' OR assigned_question_ids IS NULL
            ''', (str(key), teacher_id, exam_type, session['user_id'], session['user_id'], json.dumps(common_qids)))

    c.execute(
        'UPDATE exam_access SET joined_at=COALESCE(joined_at,?) WHERE user_id=? AND teacher_id=?',
        (time.time(), session['user_id'], teacher_id)
    )
    # Clear drafts so the student has a fresh slate for this exam session
    c.execute('DELETE FROM drafts WHERE user_id=?', (session['user_id'],))
    c.commit(); c.close()
    log(session['user_id'], 'exam_join', exam_type)
    return jsonify(ok=True, remaining=remaining, exam_type=exam_type)
@app.post('/api/students/import')
@auth('teacher')
def import_students():
    return import_people('student')
@app.post('/api/students')
@auth('teacher')
def add_student():
    d=request.get_json() or {}
    name=(d.get('name') or '').strip()
    username=(d.get('username') or d.get('registration_number') or '').strip()
    password=d.get('password') or ''
    if not name or not username or not password:
        return jsonify(error='Student name, register number/username, and password are required.'),400
    c=conn(); c.execute('INSERT OR REPLACE INTO users(username,name,role,password_hash) VALUES(?,?,?,?)',(username,name,'student',generate_password_hash(password))); uid=c.execute('SELECT id FROM users WHERE username=?',(username,)).fetchone()['id']; c.execute('INSERT OR IGNORE INTO exam_access(user_id,teacher_id,allowed,violations) VALUES(?,?,1,0)',(uid,session['user_id'])); c.execute('INSERT OR REPLACE INTO student_profiles(user_id,register_number,department,year,class,semester,section) VALUES(?,?,?,?,?,?,?)',(uid,d.get('register_number') or username,d.get('department') or '',d.get('year') or '',d.get('class') or '',d.get('semester') or '',d.get('section') or '')); c.commit(); c.close()
    return jsonify(ok=True)

@app.get('/api/students')
@auth()
def list_students():
    if session.get('role') not in ('teacher', 'admin'): return jsonify(error='Unauthorized'),401
    c=conn()
    if session.get('role') == 'admin':
        rows=c.execute("SELECT u.id,u.name,u.username,p.register_number,p.department,p.year,p.class,p.semester,p.section,COALESCE(e.allowed,1) allowed FROM users u LEFT JOIN student_profiles p ON p.user_id=u.id LEFT JOIN exam_access e ON e.user_id=u.id WHERE u.role='student' ORDER BY u.name").fetchall()
    else:
        rows=c.execute("SELECT u.id,u.name,u.username,p.register_number,p.department,p.year,p.class,p.semester,p.section,COALESCE(e.allowed,1) allowed FROM users u JOIN exam_access e ON u.id=e.user_id AND e.teacher_id=? LEFT JOIN student_profiles p ON p.user_id=u.id WHERE u.role='student' ORDER BY u.name", (session['user_id'],)).fetchall()
    c.close()
    return jsonify([dict(row) for row in rows])

@app.route('/api/teacher/profile', methods=['GET', 'PUT'])
@auth('teacher')
def teacher_profile():
    c = conn()
    if request.method == 'GET':
        row = c.execute("SELECT department, class, year, section FROM teacher_profiles WHERE user_id=?", (session['user_id'],)).fetchone()
        c.close()
        return jsonify(dict(row) if row else {"department": "", "class": "", "year": "", "section": ""})
    else:
        d = request.get_json() or {}
        c.execute("INSERT OR REPLACE INTO teacher_profiles(user_id, department, class, year, section) VALUES(?,?,?,?,?)", (session['user_id'], d.get('department',''), d.get('class',''), d.get('year',''), d.get('section','')))
        c.commit()
        c.close()
        return jsonify(ok=True)

@app.put('/api/students/<int:user_id>')
@auth('teacher')
def update_student(user_id):
    d=request.get_json() or {}; name=(d.get('name') or '').strip(); username=(d.get('username') or '').strip()
    if not name or not username: return jsonify(error='Student name and username are required.'),400
    c=conn(); c.execute("UPDATE users SET name=?,username=? WHERE id=? AND role='student'",(name,username,user_id)); c.execute('INSERT OR REPLACE INTO student_profiles(user_id,register_number,department,year,class,semester,section) VALUES(?,?,?,?,?,?,?)',(user_id,d.get('register_number') or username,d.get('department') or '',d.get('year') or '',d.get('class') or '',d.get('semester') or '',d.get('section') or '')); c.execute('INSERT OR IGNORE INTO exam_access(user_id,teacher_id,allowed,violations) VALUES(?,?,1,0)',(user_id,session['user_id'])); c.commit(); c.close(); return jsonify(ok=True)

@app.put('/api/admin/students/<int:user_id>')
@auth('admin')
def admin_update_student(user_id):
    d=request.get_json() or {}; name=(d.get('name') or '').strip(); username=(d.get('username') or '').strip(); password=d.get('password') or ''
    if not name or not username: return jsonify(error='Student name and username are required.'),400
    c=conn()
    if password: c.execute("UPDATE users SET name=?,username=?,password_hash=? WHERE id=? AND role='student'",(name,username,generate_password_hash(password),user_id))
    else: c.execute("UPDATE users SET name=?,username=? WHERE id=? AND role='student'",(name,username,user_id))
    c.execute('INSERT OR REPLACE INTO student_profiles(user_id,register_number,department,year,class,semester,section) VALUES(?,?,?,?,?,?,?)',(user_id,d.get('register_number') or username,d.get('department') or '',d.get('year') or '',d.get('class') or '',d.get('semester') or '',d.get('section') or ''))
    c.commit(); c.close(); return jsonify(ok=True)

@app.delete('/api/students/<int:user_id>')
@auth('teacher')
def delete_student(user_id):
    c=conn(); c.execute("DELETE FROM users WHERE id=? AND role='student'",(user_id,)); c.execute('DELETE FROM student_profiles WHERE user_id=?',(user_id,)); c.execute('DELETE FROM exam_access WHERE user_id=?',(user_id,)); c.commit(); c.close(); return jsonify(ok=True)
@app.delete('/api/teachers/<int:user_id>')
@auth('admin')
def delete_teacher(user_id):
    c=conn(); c.execute("DELETE FROM users WHERE id=? AND role='teacher'",(user_id,)); c.commit(); c.close(); return jsonify(ok=True)

@app.post('/api/students/<int:user_id>/reset-password')
@auth('teacher')
def reset_student_password(user_id):
    password=(request.get_json() or {}).get('password') or ''
    if len(password)<4: return jsonify(error='Password must have at least 4 characters.'),400
    c=conn(); c.execute("UPDATE users SET password_hash=? WHERE id=? AND role='student'",(generate_password_hash(password),user_id)); c.commit(); c.close(); return jsonify(ok=True)

@app.post('/api/students/<int:user_id>/access')
@auth('teacher')
def set_student_access(user_id):
    allowed=bool((request.get_json() or {}).get('allowed'))
    c=conn(); c.execute('INSERT INTO exam_access(user_id,teacher_id,allowed,violations) VALUES(?,?,?,0) ON CONFLICT(user_id,teacher_id) DO UPDATE SET allowed=excluded.allowed',(user_id,session['user_id'],int(allowed))); c.commit(); c.close(); return jsonify(ok=True,allowed=allowed)

def read_import_file(f):
    """Read the shared CSV/XLSX import format used by admin and teachers.
    Column headers are normalized: stripped, lowercased, and spaces/hyphens replaced with underscores.
    """
    rows=[]
    def norm_heads(heads):
        """Normalize a list of column header strings for flexible matching."""
        result = []
        for h in heads:
            h = str(h).strip().lower()
            # Replace spaces, hyphens, and dots with underscore for uniform matching
            import re as _re3
            h = _re3.sub(r'[\s\-\.]+', '_', h)
            h = _re3.sub(r'_+', '_', h).strip('_')
            result.append(h)
        return result
    if f.filename.lower().endswith('.csv'):
        raw = f.read()
        try:
            content = raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            try:
                content = raw.decode('cp1252')
            except UnicodeDecodeError:
                content = raw.decode('latin-1', errors='replace')
        lines = content.splitlines()
        if lines:
            reader = csv.reader(lines)
            try:
                heads = norm_heads(next(reader))
                rows = [dict(zip(heads, [str(x).strip() for x in r])) for r in reader]
            except StopIteration:
                rows = []
    else:
        try:
            from openpyxl import load_workbook
            ws=load_workbook(f,read_only=True,data_only=True).active
            rows_iter=ws.iter_rows()
            heads=norm_heads([str(x.value or '') for x in next(rows_iter)])
            rows=[dict(zip(heads,[str(x.value or '') for x in r])) for r in rows_iter]
        except Exception:
            raise ValueError('Invalid Excel file. Use columns: name, username, password.')
    return rows

def import_people(role):
    f=request.files.get('file')
    if not f: return jsonify(error='Choose a CSV or XLSX file.'),400
    try: rows=read_import_file(f)
    except ValueError as error: return jsonify(error=str(error)),400
    c=conn(); count=0
    try:
        for r in rows:
            name=r.get('name','').strip()
            username=(r.get('username') or r.get('registration_number') or r.get('registernumber') or r.get('register_number') or r.get('staff_number') or '').strip()
            password=r.get('password','').strip()
            dept=r.get('department','').strip()
            if name and username and password:
                c.execute('INSERT OR REPLACE INTO users(username,name,role,password_hash,department) VALUES(?,?,?,?,?)',(username,name,role,generate_password_hash(password),dept))
                if role=='student':
                    uid=c.execute('SELECT id FROM users WHERE username=?',(username,)).fetchone()['id']
                    c.execute('INSERT OR IGNORE INTO exam_access(user_id,teacher_id,allowed,violations) VALUES(?,?,1,0)',(uid,session['user_id']))
                    section=r.get('section') or r.get('session') or ''
                    c.execute('INSERT OR REPLACE INTO student_profiles(user_id,register_number,department,year,semester,section) VALUES(?,?,?,?,?,?)',(uid,r.get('registernumber') or r.get('register_number') or username,dept,r.get('year') or '',r.get('semester') or '',section))
                count+=1
        c.commit()
        return jsonify(ok=True,imported=count)
    finally:
        c.close()

@app.post('/api/mcqs/import')
@auth('teacher')
def import_mcqs():
    f=request.files.get('file')
    if not f: return jsonify(error='Choose a CSV or XLSX file.'),400
    try: rows=read_import_file(f)
    except ValueError as error: return jsonify(error=str(error)),400
    c=conn(); count=0
    try:
        for r in rows:
            q=r.get('question') or r.get('questions')
            a=r.get('option_a') or r.get('option a')
            b=r.get('option_b') or r.get('option b')
            opt_c=r.get('option_c') or r.get('option c')
            d=r.get('option_d') or r.get('option d')
            correct=r.get('correct_option') or r.get('correct option')
            if q and a and b and opt_c and d and correct:
                try: marks = float(r.get('mark') or r.get('marks') or 1)
                except ValueError: marks = 1.0
                try: time_limit = int(r.get('time') or r.get('time_limit') or 30)
                except ValueError: time_limit = 30
                c.execute('INSERT INTO mcqs(teacher_id,question,option_a,option_b,option_c,option_d,correct_option,marks,time_limit) VALUES(?,?,?,?,?,?,?,?,?)', (session['user_id'], str(q).strip(), str(a).strip(), str(b).strip(), str(opt_c).strip(), str(d).strip(), str(correct).strip().lower(), marks, time_limit))
                count+=1
        c.commit()
        return jsonify(ok=True,imported=count)
    finally:
        c.close()
@app.get('/api/admin/users')
@auth('admin')
def admin_users():
    c=conn(); rows=c.execute("""
        SELECT u.id, u.username, u.name, u.role, 
               COALESCE(p.department, tp.department) as department,
               COALESCE(p.class, tp.class) as class,
               COALESCE(p.section, tp.section) as section,
               p.year as student_year, tp.year as teacher_year,
               p.register_number
        FROM users u 
        LEFT JOIN student_profiles p ON u.id = p.user_id AND u.role='student'
        LEFT JOIN teacher_profiles tp ON u.id = tp.user_id AND u.role='teacher'
        WHERE u.role IN ('teacher','student') ORDER BY u.role, u.name
    """).fetchall(); c.close(); return jsonify([dict(x) for x in rows])
@app.post('/api/admin/teachers')
@auth('admin')
def admin_add_teacher():
    d=request.get_json() or {}; username=(d.get('username') or '').strip(); name=(d.get('name') or '').strip(); password=d.get('password') or ''
    if not username or not name or not password: return jsonify(error='Name, username and password are required.'),400
    c=conn(); c.execute('INSERT OR REPLACE INTO users(username,name,role,password_hash) VALUES(?,?,?,?)',(username,name,'teacher',generate_password_hash(password))); c.commit(); c.close(); return jsonify(ok=True)
@app.post('/api/admin/teachers/import')
@auth('admin')
def admin_import_teachers():
    return import_people('teacher')
@app.post('/api/admin/teachers/<int:user_id>')
@auth('admin')
def admin_update_teacher(user_id):
    d=request.get_json() or {}; name=(d.get('name') or '').strip(); username=(d.get('username') or '').strip(); password=d.get('password') or ''
    if not name or not username: return jsonify(error='Teacher name and username are required.'),400
    c=conn()
    if password:
        c.execute("UPDATE users SET name=?,username=?,password_hash=? WHERE id=? AND role='teacher'",(name,username,generate_password_hash(password),user_id))
    else:
        c.execute("UPDATE users SET name=?,username=? WHERE id=? AND role='teacher'",(name,username,user_id))
    c.execute("INSERT OR REPLACE INTO teacher_profiles(user_id, department, class, year, section) VALUES(?,?,?,?,?)", (user_id, d.get('department',''), d.get('class',''), d.get('year',''), d.get('section','')))
    c.commit(); c.close(); return jsonify(ok=True)

@app.post('/api/admin/backup')
@auth('admin')
def create_backup():
    backup_dir = os.path.join(ROOT, 'backups')
    os.makedirs(backup_dir, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_path = os.path.join(backup_dir, f'exam_lab_backup_{ts}.db')
    try:
        shutil.copy2(get_db_path(), backup_path)
        return jsonify(ok=True, file=f'exam_lab_backup_{ts}.db')
    except Exception as e:
        return jsonify(error=str(e)), 500

def normalize_output(s):
    if not s: return ""
    lines = [line.strip() for line in str(s).replace('\r\n', '\n').split('\n')]
    while lines and not lines[-1]: lines.pop()
    while lines and not lines[0]: lines.pop(0)
    return '\n'.join(lines)

@app.post('/api/verify')
@auth('student')
def verify():
    locked=require_exam_access()
    if locked: return locked
    d=request.get_json() or {}; qid=d.get('question_id'); code=d.get('code',''); language=d.get('language','python')
    c=conn()
    tid = get_student_teacher_id(c, session['user_id'])
    if not is_question_authorized(c, session['user_id'], tid, 'coding', qid):
        c.close(); return jsonify(error='Unauthorized question access.'), 403
    cases=c.execute('SELECT * FROM test_cases WHERE question_id=? ORDER BY id',(qid,)).fetchall(); c.close()
    if not cases: return jsonify(error='Teacher has not added test cases for this question.'),400
    results=[]; total=0
    for i,t in enumerate(cases,1):
        passed,out,ms=run_once(language,code,t['input_data'])
        expected=normalize_output(t['expected_output'])
        norm_out=normalize_output(out)
        ok=passed and norm_out==expected
        marks=float(t['marks'] or 0) if ok else 0
        total+=marks
        results.append({'name':f'Test Case {i}','visible':bool(t['visible']),'passed':ok,'time_ms':ms,'marks':marks,'output':out if t['visible'] else '', 'expected':expected if t['visible'] else ''})
    return jsonify(score=round(total,2),results=results)

import re as _re
_JAVA_PUBLIC_CLASS = _re.compile(r'public\s+(?:final\s+|abstract\s+|strictfp\s+)*class\s+([A-Za-z_$][A-Za-z0-9_$]*)')
_JAVA_ANY_CLASS = _re.compile(r'\bclass\s+([A-Za-z_$][A-Za-z0-9_$]*)')

def get_sanitized_env(temp_dir):
    """Minimal, sanitized environment for untrusted student code execution.
    Strips all server secrets, database credentials, and session tokens."""
    return {
        'SYSTEMROOT': os.environ.get('SYSTEMROOT', 'C:\\Windows'),
        'WINDIR': os.environ.get('WINDIR', 'C:\\Windows'),
        'PATH': os.environ.get('PATH', ''),
        'PATHEXT': os.environ.get('PATHEXT', '.COM;.EXE;.BAT;.CMD'),
        'TEMP': temp_dir,
        'TMP': temp_dir,
        'PYTHONPATH': '',
        'PYTHONUNBUFFERED': '1',
        'PYTHONHASHSEED': 'random',
        'LANG': 'en_US.UTF-8',
    }

def create_sandbox_job():
    """Create a Windows Job Object with memory limit and kill-on-close policy."""
    if os.name != 'nt':
        return None
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.windll.kernel32
        job = k32.CreateJobObjectW(None, None)
        if not job:
            return None
            
        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [('r_op', ctypes.c_uint64), ('w_op', ctypes.c_uint64), ('o_op', ctypes.c_uint64), ('r_tr', ctypes.c_uint64), ('w_tr', ctypes.c_uint64), ('o_tr', ctypes.c_uint64)]

        class BASIC_LIMIT(ctypes.Structure):
            _fields_ = [('p_time', ctypes.c_int64), ('j_time', ctypes.c_int64), ('flags', wintypes.DWORD), ('min_ws', ctypes.c_size_t), ('max_ws', ctypes.c_size_t), ('active_proc', wintypes.DWORD), ('affinity', ctypes.c_size_t), ('priority', wintypes.DWORD), ('sched', wintypes.DWORD)]

        class EXT_LIMIT(ctypes.Structure):
            _fields_ = [('basic', BASIC_LIMIT), ('io', IO_COUNTERS), ('proc_mem', ctypes.c_size_t), ('job_mem', ctypes.c_size_t), ('peak_proc', ctypes.c_size_t), ('peak_job', ctypes.c_size_t)]

        info = EXT_LIMIT()
        info.basic.flags = 0x2000 | 0x0200 # KILL_ON_JOB_CLOSE | JOB_MEMORY
        info.job_mem = 512 * 1024 * 1024 # 512 MB memory limit
        k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
        return job
    except Exception:
        return None

def assign_process_to_sandbox(job, process):
    """Assign process to Windows Job Object sandbox."""
    if os.name != 'nt' or not job or not process:
        return False
    try:
        import ctypes
        return bool(ctypes.windll.kernel32.AssignProcessToJobObject(job, process._handle))
    except Exception:
        return False

def terminate_process_tree(proc):
    """Forcefully terminate a process and all its children to prevent runaway processes."""
    if not proc:
        return
    try:
        if os.name == 'nt':
            subprocess.run(['taskkill', '/F', '/T', '/PID', str(proc.pid)], capture_output=True, timeout=2)
        else:
            proc.kill()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass

def start_process(language, code):
    if len(code.encode('utf-8', errors='ignore')) > MAX_SOURCE:
        return None, None, f"Compilation Error: Source code exceeds size limit of {MAX_SOURCE} bytes."
    td=tempfile.mkdtemp(prefix='nit_exam_')
    safe_env = get_sanitized_env(td)
    proc = None
    try:
        if language=='java':
            m = _JAVA_PUBLIC_CLASS.search(code) or _JAVA_ANY_CLASS.search(code)
            class_name = m.group(1) if m else 'Main'
            path=os.path.join(td,f'{class_name}.java')
            with open(path,'w',encoding='utf8') as f:
                f.write(code)
            r=subprocess.run(['javac','-d',td,path],capture_output=True,text=True,timeout=EXEC_TIMEOUT,cwd=td,env=safe_env)
            if r.returncode:
                shutil.rmtree(td,ignore_errors=True)
                return None,None,r.stderr or r.stdout
            proc = subprocess.Popen(['java','-XX:+UseSerialGC','-Xmx128m','-Xms16m','-cp',td,class_name],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,bufsize=0,cwd=td,env=safe_env)
        elif language=='c':
            path=os.path.join(td,'main.c')
            executable=os.path.join(td,'main.exe')
            with open(path,'w',encoding='utf8') as f:
                f.write(code)
            r=subprocess.run(['gcc',path,'-O2','-std=c11','-lm','-o',executable],capture_output=True,text=True,timeout=EXEC_TIMEOUT,cwd=td,env=safe_env)
            if r.returncode:
                shutil.rmtree(td,ignore_errors=True)
                return None,None,r.stderr or r.stdout
            proc = subprocess.Popen([executable],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,bufsize=0,cwd=td,env=safe_env)
        elif language=='python':
            path=os.path.join(td,'main.py')
            with open(path,'w',encoding='utf8') as f:
                f.write(code)
            proc = subprocess.Popen([sys.executable,'-u',path],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,bufsize=0,cwd=td,env=safe_env)
        else:
            shutil.rmtree(td,ignore_errors=True)
            return None,None,'Unsupported language. Select Python, Java, or C.'

        job = create_sandbox_job()
        if job and proc:
            assign_process_to_sandbox(job, proc)
            proc._sandbox_job = job
        return proc, td, None
    except FileNotFoundError:
        shutil.rmtree(td,ignore_errors=True)
        return None,None,'Compiler could not start. Install Python, a JDK for Java, and MinGW-w64/GCC for C; add them to PATH.'
    except subprocess.TimeoutExpired:
        shutil.rmtree(td,ignore_errors=True)
        return None,None,f'Compilation timed out ({EXEC_TIMEOUT}s).'

def run_once(language, code, input_data):
    proc,td,err=start_process(language,code); started=time.time()
    if err: return False,err,0
    
    out_data = bytearray()
    def reader():
        try:
            while True:
                chunk = proc.stdout.read(4096)
                if not chunk: break
                out_data.extend(chunk)
                if len(out_data) > MAX_OUTPUT:
                    terminate_process_tree(proc)
                    break
        except Exception:
            pass

    try:
        if input_data: proc.stdin.write(input_data.encode())
        proc.stdin.close()
    except Exception:
        pass
        
    t = threading.Thread(target=reader)
    t.start()
    t.join(timeout=EXEC_TIMEOUT)
    if t.is_alive():
        terminate_process_tree(proc)
        t.join(timeout=1.0)
        if td: shutil.rmtree(td,ignore_errors=True)
        return False, f'Time Limit Exceeded ({EXEC_TIMEOUT}s).', EXEC_TIMEOUT*1000
        
    try:
        proc.wait(timeout=1.0)
    except Exception:
        terminate_process_tree(proc)
    finally:
        try:
            if proc.stdout: proc.stdout.close()
        except Exception:
            pass
        try:
            if proc.stdin and not proc.stdin.closed: proc.stdin.close()
        except Exception:
            pass
        
    if td: shutil.rmtree(td,ignore_errors=True)
    if len(out_data) > MAX_OUTPUT:
        return False, out_data.decode(errors='replace') + '\n[Output Limit Exceeded]', round((time.time()-started)*1000)
    
    return proc.returncode==0, out_data.decode(errors='replace'), round((time.time()-started)*1000)

@sock.route('/ws/run')
def run(ws):
    role = session.get('role')
    if role not in ('student', 'teacher', 'admin'): return
    if role == 'student' and not has_exam_access(session.get('user_id')): return
    proc=td=None; q=queue.Queue(); started=0; out_len=0
    def reader(stream):
        nonlocal out_len
        try:
            while True:
                data=os.read(stream.fileno(),4096)
                if not data: break
                out_len += len(data)
                if out_len > MAX_OUTPUT:
                    q.put(b"\n[Output Limit Exceeded]".decode(errors='replace'))
                    if proc: terminate_process_tree(proc)
                    break
                q.put(data.decode(errors='replace'))
        except Exception:
            pass
    try:
        while True:
            raw=ws.receive(timeout=.05 if proc else None)
            while not q.empty(): ws.send(json.dumps({'type':'output','data':q.get()}))
            if proc and proc.poll() is not None:
                ws.send(json.dumps({'type':'exit','code':proc.returncode,'time_ms':round((time.time()-started)*1000)})); shutil.rmtree(td,ignore_errors=True); proc=td=None
            if not raw: continue
            try: m=json.loads(raw)
            except (TypeError, ValueError): continue
            if m.get('action')=='start':
                qid = m.get('question_id')
                user_role = session.get('role')
                if user_role == 'student':
                    if not qid:
                        ws.send(json.dumps({'type':'compile_error','data':'Question ID is required.'}))
                        continue
                    c_run = conn()
                    tid = get_student_teacher_id(c_run, session.get('user_id'))
                    is_auth = is_question_authorized(c_run, session.get('user_id'), tid, 'coding', qid, session.get('exam_key'))
                    c_run.close()
                    if not is_auth:
                        ws.send(json.dumps({'type':'compile_error','data':'Unauthorized question access.'}))
                        continue
                elif user_role in ('teacher', 'admin'):
                    pass
                if proc: terminate_process_tree(proc); shutil.rmtree(td,ignore_errors=True)
                proc,td,err=start_process(m.get('language'),m.get('code','')); started=time.time()
                if err: ws.send(json.dumps({'type':'compile_error','data':err})); proc=td=None
                elif proc: threading.Thread(target=reader,args=(proc.stdout,),daemon=True).start()
            elif m.get('action')=='input' and proc:
                try:
                    proc.stdin.write((m.get('data','')+'\n').encode()); proc.stdin.flush()
                except Exception:
                    pass
            elif m.get('action')=='stop' and proc:
                terminate_process_tree(proc)
    finally:
        if proc: proc.kill()
        if td: shutil.rmtree(td,ignore_errors=True)


@app.get('/api/history')
@auth()
def get_history():
    c=conn()
    if session.get('role') == 'admin':
        rows = c.execute('SELECT h.*, u.name as teacher_name FROM exam_history h JOIN users u ON h.teacher_id=u.id ORDER BY h.id DESC').fetchall()
    else:
        rows = c.execute('SELECT * FROM exam_history WHERE teacher_id=? ORDER BY id DESC', (session['user_id'],)).fetchall()
    c.close()
    out = []
    for r in rows:
        d = dict(r)
        d['exam_date'] = to_ist(d.get('exam_date'))
        out.append(d)
    return jsonify(out)

@app.get('/api/history/<int:hid>/details')
@auth()
def get_history_details(hid):
    c=conn()
    try:
        if session.get('role') == 'admin':
            row = c.execute('SELECT h.*, u.name as teacher_name FROM exam_history h JOIN users u ON h.teacher_id=u.id WHERE h.id=?', (hid,)).fetchone()
        else:
            row = c.execute('SELECT * FROM exam_history WHERE id=? AND teacher_id=?', (hid, session['user_id'])).fetchone()
        if not row:
            return jsonify(error="History record not found"), 404
        d = dict(row)
        d['exam_date'] = to_ist(d.get('exam_date'))
        try:
            data = json.loads(d.get('result_data') or '[]')
            for item in data:
                # If submissions_list or code_submitted is empty, dynamically query from db
                if (not item.get('submissions_list') or not item.get('code_submitted')) and item.get('user_id'):
                    subs = c.execute('''SELECT q.id as question_id, q.title, s.code, s.language, s.score 
                                        FROM submissions s 
                                        JOIN questions q ON s.question_id=q.id 
                                        WHERE s.user_id=?''', (item['user_id'],)).fetchall()
                    best = {}
                    for s in subs:
                        title = s['title']
                        if title not in best or (s['score'] or 0) > (best[title]['score'] or 0):
                            best[title] = dict(s)
                    drafts = c.execute('''SELECT q.id as question_id, q.title, d.code, d.language, 0 as score 
                                          FROM drafts d 
                                          JOIN questions q ON d.question_id=q.id 
                                          WHERE d.user_id=?''', (item['user_id'],)).fetchall()
                    for dr in drafts:
                        title = dr['title']
                        if title not in best and dr['code'] and dr['code'].strip():
                            best[title] = dict(dr)
                    if best:
                        submissions_list = []
                        code_str = ""
                        for title, sub in best.items():
                            submissions_list.append({
                                'question_title': title,
                                'question_id': sub.get('question_id'),
                                'language': sub.get('language') or 'python',
                                'score': sub.get('score', 0),
                                'code': sub.get('code', '')
                            })
                            code_str += f"\n--- Question: {title} ({sub.get('language','python')} | Score: {sub.get('score',0)}) ---\n{sub.get('code','')}\n"
                        item['code_submitted'] = code_str.strip()
                        item['submissions_list'] = submissions_list
            d['result_data'] = data
        except Exception as e:
            print("Error parsing history result_data:", e)
            d['result_data'] = []
        return jsonify(d)
    finally:
        c.close()

@app.delete('/api/history/<int:hid>')
@auth()
def delete_history(hid):
    c=conn()
    if session.get('role') == 'admin':
        c.execute('DELETE FROM exam_history WHERE id=?', (hid,))
    else:
        c.execute('DELETE FROM exam_history WHERE id=? AND teacher_id=?', (hid, session['user_id']))
    c.commit(); c.close()
    return jsonify(ok=True)


@app.route('/api/history/<int:hid>/download')
@auth()
def download_history_csv(hid):
    c=conn()
    if session.get('role') == 'admin':
        row = c.execute('SELECT * FROM exam_history WHERE id=?', (hid,)).fetchone()
    else:
        row = c.execute('SELECT * FROM exam_history WHERE id=? AND teacher_id=?', (hid, session['user_id'])).fetchone()
    c.close()
    if not row:
        return "Not found", 404
        
    import json, io, csv
    from flask import Response
    
    data = json.loads(row['result_data'] or '[]')
    
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Header: includes Teacher Name and Date in IST
    writer.writerow(['Exam History Report'])
    writer.writerow(['Teacher:', row.get('teacher_name', 'Unknown')])
    writer.writerow(['Date (IST):', to_ist(row['exam_date'])])
    writer.writerow(['Total Students:', row['total_students']])
    writer.writerow([])
    
    # Leaderboard Columns
    writer.writerow(['Name', 'Username', 'Department', 'Coding Score', 'MCQ Score', 'Violations (Tab Switches)', 'Total Time (s)', 'Code Submitted'])
    
    for d in data:
        writer.writerow([
            d.get('name', ''), 
            d.get('username', ''), 
            d.get('department', ''), 
            d.get('final_score', 0), 
            d.get('mcq_final_score', 0), 
            d.get('violations', 0),
            d.get('total_time', 0),
            d.get('code_submitted', '')
        ])
        
    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition": f"attachment;filename=exam_history_{hid}.csv"})

@app.route('/api/history/<int:hid>/database')
@auth()
def download_history_database(hid):
    c=conn()
    if session.get('role') == 'admin':
        row = c.execute('SELECT * FROM exam_history WHERE id=?', (hid,)).fetchone()
    else:
        row = c.execute('SELECT * FROM exam_history WHERE id=? AND teacher_id=?', (hid, session['user_id'])).fetchone()
    c.close()
    if not row:
        return "Not found", 404
    db_file = row['db_file']
    exam_dbs_dir = os.path.join(ROOT, 'exam_databases')
    if db_file and os.path.isfile(os.path.join(exam_dbs_dir, db_file)):
        from flask import send_file
        return send_file(os.path.join(exam_dbs_dir, db_file), as_attachment=True, download_name=db_file)
    return "Database file not available for this record", 404




# ==================== INDIVIDUAL STUDENT QUESTION ASSIGNMENT APIS ====================

@app.get('/api/assignments/exams')
@auth()
def list_assignment_exams():
    if session.get('role') not in ('teacher', 'admin'):
        return jsonify(error='Unauthorized'), 401
    c = conn()
    exams = []
    
    if session.get('role') == 'admin':
        teachers = c.execute("SELECT id, name FROM users WHERE role='teacher' ORDER BY name").fetchall()
    else:
        teacher = c.execute("SELECT id, name FROM users WHERE id=?", (session['user_id'],)).fetchone()
        teachers = [teacher] if teacher else []
        
    for t in teachers:
        tid = t['id']
        tname = t['name']
        st = c.execute("SELECT * FROM exam_state WHERE teacher_id=?", (tid,)).fetchone()
        coding_key = st['access_code'] if st and st['access_code'] else None
        mcq_key = st['mcq_access_code'] if st and st['mcq_access_code'] else None
        
        coding_count = c.execute("SELECT COUNT(*) FROM questions WHERE teacher_id=? AND active=1", (tid,)).fetchone()[0]
        mcq_count = c.execute("SELECT COUNT(*) FROM mcqs WHERE teacher_id=?", (tid,)).fetchone()[0]
        
        if coding_count > 0 or (st and st['active'] and st['coding_active']):
            if not coding_key:
                coding_key = str(random.randint(100000, 999999))
                c.execute("INSERT INTO exam_state (teacher_id, active, coding_active, access_code) VALUES (?, 0, 0, ?) ON CONFLICT(teacher_id) DO UPDATE SET access_code=excluded.access_code", (tid, coding_key))
                c.commit()
            exams.append({
                'id': f"{tid}_coding_{coding_key}",
                'teacher_id': tid,
                'teacher_name': tname,
                'exam_type': 'coding',
                'exam_key': coding_key,
                'title': f"{tname} — Coding Exam (Key: {coding_key})" if session.get('role') == 'admin' else f"Coding Exam (Key: {coding_key})",
                'active': bool(st and st['active'] and st['coding_active']),
                'total_questions': coding_count
            })
            
        if mcq_count > 0 or (st and st['active'] and st['mcq_active']):
            if not mcq_key:
                mcq_key = str(random.randint(100000, 999999))
                c.execute("INSERT INTO exam_state (teacher_id, active, mcq_active, mcq_access_code) VALUES (?, 0, 0, ?) ON CONFLICT(teacher_id) DO UPDATE SET mcq_access_code=excluded.mcq_access_code", (tid, mcq_key))
                c.commit()
            exams.append({
                'id': f"{tid}_mcq_{mcq_key}",
                'teacher_id': tid,
                'teacher_name': tname,
                'exam_type': 'mcq',
                'exam_key': mcq_key,
                'title': f"{tname} — MCQ Exam (Key: {mcq_key})" if session.get('role') == 'admin' else f"MCQ Exam (Key: {mcq_key})",
                'active': bool(st and st['active'] and st['mcq_active']),
                'total_questions': mcq_count
            })
            
    c.close()
    return jsonify(exams=exams)

@app.get('/api/assignments')
@auth()
def get_assignments():
    if session.get('role') not in ('teacher', 'admin'):
        return jsonify(error='Unauthorized'), 401
    exam_key = str(request.args.get('exam_key', '')).strip()
    exam_type = str(request.args.get('exam_type', '')).strip().lower()
    teacher_id = request.args.get('teacher_id', type=int)
    
    if not exam_key or exam_type not in ('coding', 'mcq'):
        return jsonify(error='exam_key and valid exam_type (coding/mcq) are required.'), 400
        
    c = conn()
    try:
        if session.get('role') == 'teacher':
            current_teacher_id = session['user_id']
            if teacher_id and teacher_id != current_teacher_id:
                return jsonify(error='You do not own this examination.'), 403
            teacher_id = current_teacher_id
            is_owner = False
            st = c.execute("SELECT access_code, mcq_access_code FROM exam_state WHERE teacher_id=?", (teacher_id,)).fetchone()
            if st:
                curr_key = st['access_code'] if exam_type == 'coding' else st['mcq_access_code']
                if curr_key and str(curr_key).strip() == exam_key:
                    is_owner = True
            if not is_owner:
                past = c.execute("SELECT 1 FROM student_question_assignments WHERE exam_key=? AND teacher_id=? AND exam_type=?", (exam_key, teacher_id, exam_type)).fetchone()
                if past:
                    is_owner = True
                else:
                    hist = c.execute("SELECT 1 FROM exam_history WHERE exam_key=? AND teacher_id=?", (exam_key, teacher_id)).fetchone()
                    if hist:
                        is_owner = True
            if not is_owner:
                return jsonify(error='Exam key does not belong to your examination.'), 403
        else:
            if not teacher_id:
                st = c.execute("SELECT teacher_id FROM exam_state WHERE access_code=? OR mcq_access_code=? LIMIT 1", (exam_key, exam_key)).fetchone()
                teacher_id = st['teacher_id'] if st else None
            t_user = c.execute("SELECT id FROM users WHERE id=? AND role='teacher'", (teacher_id,)).fetchone()
            if not t_user:
                return jsonify(error='Invalid or non-existent teacher specified.'), 400
            is_valid_key = False
            st = c.execute("SELECT access_code, mcq_access_code FROM exam_state WHERE teacher_id=?", (teacher_id,)).fetchone()
            if st:
                curr_key = st['access_code'] if exam_type == 'coding' else st['mcq_access_code']
                if curr_key and str(curr_key).strip() == exam_key:
                    is_valid_key = True
            if not is_valid_key:
                past = c.execute("SELECT 1 FROM student_question_assignments WHERE exam_key=? AND teacher_id=? AND exam_type=?", (exam_key, teacher_id, exam_type)).fetchone()
                if past:
                    is_valid_key = True
                else:
                    hist = c.execute("SELECT 1 FROM exam_history WHERE exam_key=? AND teacher_id=?", (exam_key, teacher_id)).fetchone()
                    if hist:
                        is_valid_key = True
            if not is_valid_key:
                return jsonify(error='Exam key does not belong to the selected teacher or exam type.'), 400
        
        if exam_type == 'coding':
            q_rows = c.execute('''
                SELECT q.id, q.title, q.prompt, q.language,
                       COALESCE((SELECT SUM(marks) FROM test_cases WHERE question_id=q.id), 0) as marks
                FROM questions q
                WHERE q.teacher_id=? AND q.active=1
                ORDER BY q.id DESC
            ''', (teacher_id,)).fetchall()
            common_questions = [{'id': r['id'], 'title': r['title'], 'prompt': r['prompt'], 'language': r['language'], 'marks': float(r['marks'] or 0)} for r in q_rows]
        else:
            m_rows = c.execute('''
                SELECT id, question, marks, time_limit
                FROM mcqs
                WHERE teacher_id=?
                ORDER BY id
            ''', (teacher_id,)).fetchall()
            common_questions = [{'id': r['id'], 'title': r['question'], 'marks': float(r['marks'] or 1.0), 'time_limit': r['time_limit']} for r in m_rows]
            
        if session.get('role') == 'admin':
            stud_rows = c.execute('''
                SELECT u.id, u.name, u.username, p.register_number, p.department, p.section,
                       e.joined_at, e.locked, e.mcq_locked
                FROM users u
                LEFT JOIN student_profiles p ON p.user_id=u.id
                LEFT JOIN exam_access e ON e.user_id=u.id AND e.teacher_id=?
                WHERE u.role='student'
                ORDER BY u.name
            ''', (teacher_id,)).fetchall()
        else:
            stud_rows = c.execute('''
                SELECT u.id, u.name, u.username, p.register_number, p.department, p.section,
                       e.joined_at, e.locked, e.mcq_locked
                FROM users u
                JOIN exam_access e ON u.id=e.user_id AND e.teacher_id=?
                LEFT JOIN student_profiles p ON p.user_id=u.id
                WHERE u.role='student'
                ORDER BY u.name
            ''', (teacher_id,)).fetchall()
            
        assignments_map = {}
        rows = c.execute('''
            SELECT student_id, assigned_question_ids, attempt_locked, snapshot_question_ids, updated_at
            FROM student_question_assignments
            WHERE exam_key=? AND teacher_id=? AND exam_type=?
        ''', (exam_key, teacher_id, exam_type)).fetchall()
        for r in rows:
            assigned_raw = str(r['assigned_question_ids'] or '').strip()
            if assigned_raw == 'COMMON' or assigned_raw == '' or assigned_raw == 'None':
                continue
            try:
                qids = json.loads(assigned_raw)
                if isinstance(qids, list) and len(qids) > 0:
                    assignments_map[r['student_id']] = {
                        'assigned_question_ids': qids,
                        'attempt_locked': bool(r['attempt_locked']),
                        'updated_at': r['updated_at']
                    }
            except Exception:
                pass
                
        students = []
        for s in stud_rows:
            sid = s['id']
            asgn = assignments_map.get(sid)
            attempt_started = bool(s['joined_at'])
            if asgn and asgn['attempt_locked']:
                attempt_started = True
                
            students.append({
                'id': sid,
                'name': s['name'],
                'username': s['username'],
                'register_number': s['register_number'] or s['username'],
                'department': s['department'] or '',
                'section': s['section'] or '',
                'mode': 'individual' if asgn else 'common',
                'assigned_question_ids': asgn['assigned_question_ids'] if asgn else [q['id'] for q in common_questions],
                'attempt_started': attempt_started,
                'attempt_locked': bool(asgn and asgn['attempt_locked'])
            })
            
        return jsonify(
            ok=True,
            exam_key=exam_key,
            exam_type=exam_type,
            teacher_id=teacher_id,
            common_questions=common_questions,
            total_common=len(common_questions),
            students=students
        )
    finally:
        c.close()

@app.post('/api/assignments')
@auth()
def save_assignment():
    if session.get('role') not in ('teacher', 'admin'):
        return jsonify(error='Unauthorized'), 401
    d = request.get_json() or {}
    exam_key = str(d.get('exam_key', '')).strip()
    exam_type = str(d.get('exam_type', '')).strip().lower()
    student_id = d.get('student_id')
    question_ids = d.get('question_ids')
    requested_teacher_id = d.get('teacher_id')
    
    if not exam_key or exam_type not in ('coding', 'mcq') or not student_id:
        return jsonify(error='exam_key, exam_type, and student_id are required.'), 400
        
    if not question_ids or not isinstance(question_ids, list) or len(question_ids) == 0:
        return jsonify(error='Please select at least one question for individual assignment. To use all questions, use Reset to Common Questions.'), 400
        
    c = conn()
    try:
        if session.get('role') == 'teacher':
            teacher_id = session['user_id']
            if requested_teacher_id and int(requested_teacher_id) != teacher_id:
                return jsonify(error='You do not own this examination.'), 403
            is_owner = False
            st = c.execute("SELECT access_code, mcq_access_code FROM exam_state WHERE teacher_id=?", (teacher_id,)).fetchone()
            if st:
                curr_key = st['access_code'] if exam_type == 'coding' else st['mcq_access_code']
                if curr_key and str(curr_key).strip() == exam_key:
                    is_owner = True
            if not is_owner:
                past = c.execute("SELECT 1 FROM student_question_assignments WHERE exam_key=? AND teacher_id=? AND exam_type=?", (exam_key, teacher_id, exam_type)).fetchone()
                if past:
                    is_owner = True
                else:
                    hist = c.execute("SELECT 1 FROM exam_history WHERE exam_key=? AND teacher_id=?", (exam_key, teacher_id)).fetchone()
                    if hist:
                        is_owner = True
            if not is_owner:
                return jsonify(error='Exam key does not belong to your examination.'), 403
        else:
            if not requested_teacher_id:
                st = c.execute("SELECT teacher_id FROM exam_state WHERE access_code=? OR mcq_access_code=? LIMIT 1", (exam_key, exam_key)).fetchone()
                teacher_id = st['teacher_id'] if st else None
            else:
                teacher_id = requested_teacher_id
            t_user = c.execute("SELECT id FROM users WHERE id=? AND role='teacher'", (teacher_id,)).fetchone()
            if not t_user:
                return jsonify(error='Invalid or non-existent teacher specified.'), 400
            is_valid_key = False
            st = c.execute("SELECT access_code, mcq_access_code FROM exam_state WHERE teacher_id=?", (teacher_id,)).fetchone()
            if st:
                curr_key = st['access_code'] if exam_type == 'coding' else st['mcq_access_code']
                if curr_key and str(curr_key).strip() == exam_key:
                    is_valid_key = True
            if not is_valid_key:
                past = c.execute("SELECT 1 FROM student_question_assignments WHERE exam_key=? AND teacher_id=? AND exam_type=?", (exam_key, teacher_id, exam_type)).fetchone()
                if past:
                    is_valid_key = True
                else:
                    hist = c.execute("SELECT 1 FROM exam_history WHERE exam_key=? AND teacher_id=?", (exam_key, teacher_id)).fetchone()
                    if hist:
                        is_valid_key = True
            if not is_valid_key:
                return jsonify(error='Exam key does not belong to the selected teacher or exam type.'), 400

        s_user = c.execute("SELECT id FROM users WHERE id=? AND role='student'", (student_id,)).fetchone()
        if not s_user:
            return jsonify(error='Invalid student account.'), 400

        ea = c.execute("SELECT allowed FROM exam_access WHERE user_id=? AND teacher_id=?", (student_id, teacher_id)).fetchone()
        if not ea or not ea['allowed']:
            return jsonify(error='Student is not authorized to participate in this examination.'), 403

        # Check attempt locking (Rule I / Section 8)
        asgn = c.execute("SELECT attempt_locked FROM student_question_assignments WHERE exam_key=? AND teacher_id=? AND exam_type=? AND student_id=?", (exam_key, teacher_id, exam_type, student_id)).fetchone()
        if asgn and asgn['attempt_locked']:
            return jsonify(error='Cannot modify assignment: Student has already started the examination attempt.'), 400
            
        selected_set = set(int(x) for x in question_ids)
        if exam_type == 'coding':
            src = c.execute("SELECT id FROM questions WHERE teacher_id=? AND active=1 ORDER BY id DESC", (teacher_id,)).fetchall()
        else:
            src = c.execute("SELECT id FROM mcqs WHERE teacher_id=? ORDER BY id", (teacher_id,)).fetchall()
            
        src_ids = [r['id'] for r in src]
        src_set = set(src_ids)
        if not selected_set.issubset(src_set):
            return jsonify(error='One or more selected questions do not belong to this examination.'), 400
            
        final_ordered = [qid for qid in src_ids if qid in selected_set]
        
        c.execute('''
            INSERT INTO student_question_assignments
                (exam_key, teacher_id, exam_type, student_id, assigned_question_ids, created_by, attempt_locked, snapshot_question_ids, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 0, NULL, CURRENT_TIMESTAMP)
            ON CONFLICT(exam_key, teacher_id, exam_type, student_id) DO UPDATE SET
                assigned_question_ids=excluded.assigned_question_ids,
                created_by=excluded.created_by,
                attempt_locked=0,
                snapshot_question_ids=NULL,
                updated_at=CURRENT_TIMESTAMP
        ''', (exam_key, teacher_id, exam_type, student_id, json.dumps(final_ordered), session['user_id']))

        # Also persist to Permanent Student Question Profiles
        c.execute('''
            INSERT INTO student_question_profiles
                (teacher_id, student_id, exam_type, mode, version, created_by, updated_by, created_at, updated_at)
            VALUES (?, ?, ?, 'INDIVIDUAL', 1, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(teacher_id, student_id, exam_type) DO UPDATE SET
                mode='INDIVIDUAL',
                version=version + 1,
                updated_by=excluded.updated_by,
                updated_at=CURRENT_TIMESTAMP
        ''', (teacher_id, student_id, exam_type, session['user_id'], session['user_id']))
        prof_row = c.execute("SELECT id FROM student_question_profiles WHERE teacher_id=? AND student_id=? AND exam_type=?", (teacher_id, student_id, exam_type)).fetchone()
        if prof_row:
            prof_id = prof_row['id']
            c.execute("DELETE FROM student_question_profile_items WHERE profile_id=?", (prof_id,))
            for sort_order, qid in enumerate(final_ordered):
                c.execute("INSERT INTO student_question_profile_items (profile_id, question_id, sort_order) VALUES (?, ?, ?)", (prof_id, qid, sort_order))

        c.commit()
        return jsonify(ok=True, message=f'Successfully assigned {len(final_ordered)} question(s) to student.', assigned_question_ids=final_ordered)
    finally:
        c.close()

@app.post('/api/assignments/reset')
@auth()
def reset_assignment():
    if session.get('role') not in ('teacher', 'admin'):
        return jsonify(error='Unauthorized'), 401
    d = request.get_json() or {}
    exam_key = str(d.get('exam_key', '')).strip()
    exam_type = str(d.get('exam_type', '')).strip().lower()
    student_id = d.get('student_id')
    requested_teacher_id = d.get('teacher_id')
    
    if not exam_key or exam_type not in ('coding', 'mcq') or not student_id:
        return jsonify(error='exam_key, exam_type, and student_id are required.'), 400
        
    c = conn()
    try:
        if session.get('role') == 'teacher':
            teacher_id = session['user_id']
            if requested_teacher_id and int(requested_teacher_id) != teacher_id:
                return jsonify(error='You do not own this examination.'), 403
            is_owner = False
            st = c.execute("SELECT access_code, mcq_access_code FROM exam_state WHERE teacher_id=?", (teacher_id,)).fetchone()
            if st:
                curr_key = st['access_code'] if exam_type == 'coding' else st['mcq_access_code']
                if curr_key and str(curr_key).strip() == exam_key:
                    is_owner = True
            if not is_owner:
                past = c.execute("SELECT 1 FROM student_question_assignments WHERE exam_key=? AND teacher_id=? AND exam_type=?", (exam_key, teacher_id, exam_type)).fetchone()
                if past:
                    is_owner = True
                else:
                    hist = c.execute("SELECT 1 FROM exam_history WHERE exam_key=? AND teacher_id=?", (exam_key, teacher_id)).fetchone()
                    if hist:
                        is_owner = True
            if not is_owner:
                return jsonify(error='Exam key does not belong to your examination.'), 403
        else:
            if not requested_teacher_id:
                st = c.execute("SELECT teacher_id FROM exam_state WHERE access_code=? OR mcq_access_code=? LIMIT 1", (exam_key, exam_key)).fetchone()
                teacher_id = st['teacher_id'] if st else None
            else:
                teacher_id = requested_teacher_id
            t_user = c.execute("SELECT id FROM users WHERE id=? AND role='teacher'", (teacher_id,)).fetchone()
            if not t_user:
                return jsonify(error='Invalid or non-existent teacher specified.'), 400
            is_valid_key = False
            st = c.execute("SELECT access_code, mcq_access_code FROM exam_state WHERE teacher_id=?", (teacher_id,)).fetchone()
            if st:
                curr_key = st['access_code'] if exam_type == 'coding' else st['mcq_access_code']
                if curr_key and str(curr_key).strip() == exam_key:
                    is_valid_key = True
            if not is_valid_key:
                past = c.execute("SELECT 1 FROM student_question_assignments WHERE exam_key=? AND teacher_id=? AND exam_type=?", (exam_key, teacher_id, exam_type)).fetchone()
                if past:
                    is_valid_key = True
                else:
                    hist = c.execute("SELECT 1 FROM exam_history WHERE exam_key=? AND teacher_id=?", (exam_key, teacher_id)).fetchone()
                    if hist:
                        is_valid_key = True
            if not is_valid_key:
                return jsonify(error='Exam key does not belong to the selected teacher or exam type.'), 400

        asgn = c.execute("SELECT attempt_locked FROM student_question_assignments WHERE exam_key=? AND teacher_id=? AND exam_type=? AND student_id=?", (exam_key, teacher_id, exam_type, student_id)).fetchone()
        if asgn and asgn['attempt_locked']:
            return jsonify(error='Cannot reset assignment: Student has already started the examination attempt.'), 400
            
        prof = c.execute("SELECT id FROM student_question_profiles WHERE teacher_id=? AND student_id=? AND exam_type=?", (teacher_id, student_id, exam_type)).fetchone()
        if prof:
            c.execute("DELETE FROM student_question_profile_items WHERE profile_id=?", (prof['id'],))
            c.execute('''
                UPDATE student_question_profiles
                SET mode='COMMON', version=version + 1, updated_by=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=?
            ''', (session['user_id'], prof['id']))

        c.execute('''
            DELETE FROM student_question_assignments
            WHERE exam_key=? AND teacher_id=? AND exam_type=? AND student_id=?
        ''', (exam_key, teacher_id, exam_type, student_id))
        c.commit()
        return jsonify(ok=True, message='Student reset to Common Questions mode.')
    finally:
        c.close()

# ==================== PERMANENT STUDENT QUESTION ASSIGNMENT APIS ====================

@app.get('/api/permanent-assignments')
@auth()
def get_permanent_assignments():
    if session.get('role') not in ('teacher', 'admin'):
        return jsonify(error='Unauthorized'), 401
    
    exam_type = str(request.args.get('exam_type', 'coding')).strip().lower()
    if exam_type not in ('coding', 'mcq'):
        exam_type = 'coding'
        
    c = conn()
    try:
        if session.get('role') == 'teacher':
            teacher_id = session['user_id']
        else:
            teacher_id = request.args.get('teacher_id', type=int)
            if not teacher_id:
                first_t = c.execute("SELECT id FROM users WHERE role='teacher' ORDER BY name LIMIT 1").fetchone()
                teacher_id = first_t['id'] if first_t else session['user_id']
                
        t_user = c.execute("SELECT id, name FROM users WHERE id=?", (teacher_id,)).fetchone()
        teacher_name = t_user['name'] if t_user else 'Teacher'

        # Fetch teacher's questions
        if exam_type == 'coding':
            q_rows = c.execute('''
                SELECT q.id, q.title, q.prompt, q.language,
                       COALESCE((SELECT SUM(marks) FROM test_cases WHERE question_id=q.id), 0) as marks
                FROM questions q
                WHERE q.teacher_id=? AND q.active=1
                ORDER BY q.id DESC
            ''', (teacher_id,)).fetchall()
            questions = [{'id': r['id'], 'title': r['title'], 'prompt': r['prompt'], 'language': r['language'], 'marks': float(r['marks'] or 0)} for r in q_rows]
        else:
            m_rows = c.execute('''
                SELECT id, question, marks, time_limit
                FROM mcqs
                WHERE teacher_id=?
                ORDER BY id
            ''', (teacher_id,)).fetchall()
            questions = [{'id': r['id'], 'title': r['question'], 'marks': float(r['marks'] or 1.0), 'time_limit': r['time_limit']} for r in m_rows]

        # Fetch students
        if session.get('role') == 'admin':
            stud_rows = c.execute('''
                SELECT u.id, u.name, u.username, p.register_number, p.department, p.section, p.year,
                       e.joined_at, e.locked, e.mcq_locked
                FROM users u
                LEFT JOIN student_profiles p ON p.user_id=u.id
                LEFT JOIN exam_access e ON e.user_id=u.id AND e.teacher_id=?
                WHERE u.role='student'
                ORDER BY u.name
            ''', (teacher_id,)).fetchall()
        else:
            stud_rows = c.execute('''
                SELECT u.id, u.name, u.username, p.register_number, p.department, p.section, p.year,
                       e.joined_at, e.locked, e.mcq_locked
                FROM users u
                JOIN exam_access e ON u.id=e.user_id AND e.teacher_id=?
                LEFT JOIN student_profiles p ON p.user_id=u.id
                WHERE u.role='student'
                ORDER BY u.name
            ''', (teacher_id,)).fetchall()

        # Load permanent profiles
        prof_rows = c.execute('''
            SELECT id, student_id, mode, version, updated_at
            FROM student_question_profiles
            WHERE teacher_id=? AND exam_type=?
        ''', (teacher_id, exam_type)).fetchall()
        
        prof_map = {}
        for pr in prof_rows:
            items = c.execute('''
                SELECT question_id FROM student_question_profile_items
                WHERE profile_id=?
                ORDER BY sort_order ASC, id ASC
            ''', (pr['id'],)).fetchall()
            prof_map[pr['student_id']] = {
                'id': pr['id'],
                'mode': pr['mode'],
                'version': pr['version'],
                'updated_at': pr['updated_at'],
                'assigned_question_ids': [it['question_id'] for it in items]
            }

        departments = set()
        sections = set()
        years = set()
        students = []

        all_q_ids = [q['id'] for q in questions]

        for s in stud_rows:
            sid = s['id']
            dept = (s['department'] or '').strip()
            sec = (s['section'] or '').strip()
            yr = (s['year'] or '').strip()
            if dept: departments.add(dept)
            if sec: sections.add(sec)
            if yr: years.add(yr)

            prof = prof_map.get(sid)
            is_indiv = bool(prof and prof['mode'] == 'INDIVIDUAL')
            attempt_active = bool(s['joined_at'])

            assigned_ids = prof['assigned_question_ids'] if (prof and is_indiv) else all_q_ids

            students.append({
                'id': sid,
                'name': s['name'],
                'username': s['username'],
                'register_number': s['register_number'] or s['username'],
                'department': dept,
                'section': sec,
                'year': yr,
                'mode': 'INDIVIDUAL' if is_indiv else 'COMMON',
                'assigned_question_ids': assigned_ids,
                'version': prof['version'] if prof else 1,
                'updated_at': prof['updated_at'] if prof else None,
                'attempt_active': attempt_active
            })

        teachers_list = []
        if session.get('role') == 'admin':
            teachers_list = [{'id': t['id'], 'name': t['name']} for t in c.execute("SELECT id, name FROM users WHERE role='teacher' ORDER BY name").fetchall()]

        return jsonify(
            ok=True,
            exam_type=exam_type,
            teacher_id=teacher_id,
            teacher_name=teacher_name,
            teachers=teachers_list,
            questions=questions,
            total_questions=len(questions),
            students=students,
            total_students=len(students),
            departments=sorted(list(departments)),
            sections=sorted(list(sections)),
            years=sorted(list(years))
        )
    finally:
        c.close()

@app.post('/api/permanent-assignments/bulk')
@auth()
def bulk_save_permanent_assignments():
    if session.get('role') not in ('teacher', 'admin'):
        return jsonify(error='Unauthorized'), 401
    d = request.get_json() or {}
    exam_type = str(d.get('exam_type', '')).strip().lower()
    student_ids = d.get('student_ids')
    question_ids = d.get('question_ids')
    requested_teacher_id = d.get('teacher_id')
    
    if exam_type not in ('coding', 'mcq'):
        return jsonify(error='Valid exam_type (coding/mcq) is required.'), 400
    if not student_ids or not isinstance(student_ids, list) or len(student_ids) == 0:
        return jsonify(error='Please select at least one student.'), 400
    if not question_ids or not isinstance(question_ids, list) or len(question_ids) == 0:
        return jsonify(error='Please select at least one question for individual assignment.'), 400

    c = conn()
    try:
        if session.get('role') == 'teacher':
            teacher_id = session['user_id']
            if requested_teacher_id and int(requested_teacher_id) != teacher_id:
                return jsonify(error='You do not own this examination.'), 403
        else:
            teacher_id = requested_teacher_id
            if not teacher_id:
                return jsonify(error='Teacher ID is required for admin.'), 400
            t_user = c.execute("SELECT id FROM users WHERE id=? AND role='teacher'", (teacher_id,)).fetchone()
            if not t_user:
                return jsonify(error='Invalid or non-existent teacher specified.'), 400

        selected_set = set(int(x) for x in question_ids)
        if exam_type == 'coding':
            src = c.execute("SELECT id FROM questions WHERE teacher_id=? AND active=1 ORDER BY id DESC", (teacher_id,)).fetchall()
        else:
            src = c.execute("SELECT id FROM mcqs WHERE teacher_id=? ORDER BY id", (teacher_id,)).fetchall()
        src_ids = [r['id'] for r in src]
        src_set = set(src_ids)
        if not selected_set.issubset(src_set):
            return jsonify(error='One or more selected questions do not belong to this teacher or are inactive.'), 400

        # Preserve order without duplicates
        final_ordered = []
        for q in question_ids:
            iq = int(q)
            if iq in selected_set and iq not in final_ordered:
                final_ordered.append(iq)

        with c:
            for s_id in student_ids:
                sid = int(s_id)
                s_user = c.execute("SELECT id FROM users WHERE id=? AND role='student'", (sid,)).fetchone()
                if not s_user:
                    continue
                c.execute("INSERT OR IGNORE INTO exam_access (user_id, teacher_id, allowed) VALUES (?, ?, 1)", (sid, teacher_id))
                
                c.execute('''
                    INSERT INTO student_question_profiles
                        (teacher_id, student_id, exam_type, mode, version, created_by, updated_by, created_at, updated_at)
                    VALUES (?, ?, ?, 'INDIVIDUAL', 1, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    ON CONFLICT(teacher_id, student_id, exam_type) DO UPDATE SET
                        mode='INDIVIDUAL',
                        version=version + 1,
                        updated_by=excluded.updated_by,
                        updated_at=CURRENT_TIMESTAMP
                ''', (teacher_id, sid, exam_type, session['user_id'], session['user_id']))
                
                prof_row = c.execute("SELECT id FROM student_question_profiles WHERE teacher_id=? AND student_id=? AND exam_type=?", (teacher_id, sid, exam_type)).fetchone()
                prof_id = prof_row['id']
                
                c.execute("DELETE FROM student_question_profile_items WHERE profile_id=?", (prof_id,))
                for sort_order, qid in enumerate(final_ordered):
                    c.execute('''
                        INSERT INTO student_question_profile_items (profile_id, question_id, sort_order)
                        VALUES (?, ?, ?)
                    ''', (prof_id, qid, sort_order))

                # Also sync legacy table for backward compat
                st = c.execute("SELECT access_code, mcq_access_code FROM exam_state WHERE teacher_id=?", (teacher_id,)).fetchone()
                exam_key = (st['access_code'] if exam_type == 'coding' else st['mcq_access_code']) if st else 'PERMANENT'
                if exam_key:
                    c.execute('''
                        INSERT INTO student_question_assignments
                            (exam_key, teacher_id, exam_type, student_id, assigned_question_ids, created_by, attempt_locked, snapshot_question_ids, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, 0, NULL, CURRENT_TIMESTAMP)
                        ON CONFLICT(exam_key, teacher_id, exam_type, student_id) DO UPDATE SET
                            assigned_question_ids=excluded.assigned_question_ids,
                            updated_at=CURRENT_TIMESTAMP
                        WHERE attempt_locked=0
                    ''', (str(exam_key), teacher_id, exam_type, sid, json.dumps(final_ordered), session['user_id']))

        log(session['user_id'], 'permanent_assignment_bulk_saved', f"{len(student_ids)} students, {len(final_ordered)} questions, {exam_type}")
        return jsonify(
            ok=True,
            count=len(student_ids),
            message=f"Successfully assigned {len(final_ordered)} question(s) to {len(student_ids)} student(s)."
        )
    finally:
        c.close()

@app.post('/api/permanent-assignments/distribute')
@auth()
def distribute_permanent_assignments():
    if session.get('role') not in ('teacher', 'admin'):
        return jsonify(error='Unauthorized'), 401
    d = request.get_json() or {}
    exam_type = str(d.get('exam_type', '')).strip().lower()
    student_ids = d.get('student_ids')
    question_ids = d.get('question_ids')
    requested_teacher_id = d.get('teacher_id')
    
    if exam_type not in ('coding', 'mcq'):
        return jsonify(error='Valid exam_type (coding/mcq) is required.'), 400
    if not student_ids or not isinstance(student_ids, list) or len(student_ids) == 0:
        return jsonify(error='Please select at least one student.'), 400
    if not question_ids or not isinstance(question_ids, list) or len(question_ids) == 0:
        return jsonify(error='Please select at least one question.'), 400
    if len(student_ids) != len(question_ids):
        return jsonify(error=f'Student count ({len(student_ids)}) and question count ({len(question_ids)}) must match for 1-to-1 distribution.'), 400

    c = conn()
    try:
        if session.get('role') == 'teacher':
            teacher_id = session['user_id']
            if requested_teacher_id and int(requested_teacher_id) != teacher_id:
                return jsonify(error='You do not own this examination.'), 403
        else:
            teacher_id = requested_teacher_id
            if not teacher_id:
                return jsonify(error='Teacher ID is required for admin.'), 400
            t_user = c.execute("SELECT id FROM users WHERE id=? AND role='teacher'", (teacher_id,)).fetchone()
            if not t_user:
                return jsonify(error='Invalid or non-existent teacher specified.'), 400

        selected_set = set(int(x) for x in question_ids)
        if exam_type == 'coding':
            src = c.execute("SELECT id FROM questions WHERE teacher_id=? AND active=1 ORDER BY id DESC", (teacher_id,)).fetchall()
        else:
            src = c.execute("SELECT id FROM mcqs WHERE teacher_id=? ORDER BY id", (teacher_id,)).fetchall()
        src_set = set(r['id'] for r in src)
        if not selected_set.issubset(src_set):
            return jsonify(error='One or more selected questions do not belong to this teacher or are inactive.'), 400

        with c:
            for i, sid in enumerate(student_ids):
                s_int = int(sid)
                qid = int(question_ids[i])
                s_user = c.execute("SELECT id FROM users WHERE id=? AND role='student'", (s_int,)).fetchone()
                if not s_user:
                    continue
                c.execute("INSERT OR IGNORE INTO exam_access (user_id, teacher_id, allowed) VALUES (?, ?, 1)", (s_int, teacher_id))
                
                c.execute('''
                    INSERT INTO student_question_profiles
                        (teacher_id, student_id, exam_type, mode, version, created_by, updated_by, created_at, updated_at)
                    VALUES (?, ?, ?, 'INDIVIDUAL', 1, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    ON CONFLICT(teacher_id, student_id, exam_type) DO UPDATE SET
                        mode='INDIVIDUAL',
                        version=version + 1,
                        updated_by=excluded.updated_by,
                        updated_at=CURRENT_TIMESTAMP
                ''', (teacher_id, s_int, exam_type, session['user_id'], session['user_id']))
                
                prof_row = c.execute("SELECT id FROM student_question_profiles WHERE teacher_id=? AND student_id=? AND exam_type=?", (teacher_id, s_int, exam_type)).fetchone()
                prof_id = prof_row['id']
                c.execute("DELETE FROM student_question_profile_items WHERE profile_id=?", (prof_id,))
                c.execute("INSERT INTO student_question_profile_items (profile_id, question_id, sort_order) VALUES (?, ?, 0)", (prof_id, qid))

        log(session['user_id'], 'permanent_assignment_distributed', f"{len(student_ids)} students distributed 1-to-1, {exam_type}")
        return jsonify(
            ok=True,
            count=len(student_ids),
            message=f"Successfully distributed 1 question per student to {len(student_ids)} student(s)."
        )
    finally:
        c.close()

@app.post('/api/permanent-assignments/bulk-reset')
@auth()
def bulk_reset_permanent_assignments():
    if session.get('role') not in ('teacher', 'admin'):
        return jsonify(error='Unauthorized'), 401
    d = request.get_json() or {}
    exam_type = str(d.get('exam_type', '')).strip().lower()
    student_ids = d.get('student_ids')
    requested_teacher_id = d.get('teacher_id')

    if exam_type not in ('coding', 'mcq'):
        return jsonify(error='Valid exam_type (coding/mcq) is required.'), 400
    if not student_ids or not isinstance(student_ids, list) or len(student_ids) == 0:
        return jsonify(error='Please select at least one student to reset.'), 400

    c = conn()
    try:
        if session.get('role') == 'teacher':
            teacher_id = session['user_id']
            if requested_teacher_id and int(requested_teacher_id) != teacher_id:
                return jsonify(error='You do not own this examination.'), 403
        else:
            teacher_id = requested_teacher_id
            if not teacher_id:
                return jsonify(error='Teacher ID is required for admin.'), 400
            t_user = c.execute("SELECT id FROM users WHERE id=? AND role='teacher'", (teacher_id,)).fetchone()
            if not t_user:
                return jsonify(error='Invalid or non-existent teacher specified.'), 400

        with c:
            for sid in student_ids:
                s_int = int(sid)
                prof = c.execute("SELECT id FROM student_question_profiles WHERE teacher_id=? AND student_id=? AND exam_type=?", (teacher_id, s_int, exam_type)).fetchone()
                if prof:
                    c.execute("DELETE FROM student_question_profile_items WHERE profile_id=?", (prof['id'],))
                    c.execute('''
                        UPDATE student_question_profiles
                        SET mode='COMMON', version=version + 1, updated_by=?, updated_at=CURRENT_TIMESTAMP
                        WHERE id=?
                    ''', (session['user_id'], prof['id']))
                c.execute("DELETE FROM student_question_assignments WHERE teacher_id=? AND student_id=? AND exam_type=?", (teacher_id, s_int, exam_type))

        log(session['user_id'], 'permanent_assignment_bulk_reset', f"{len(student_ids)} students reset, {exam_type}")
        return jsonify(
            ok=True,
            count=len(student_ids),
            message=f"Successfully reset {len(student_ids)} student(s) to Common Questions mode."
        )
    finally:
        c.close()

@app.post('/api/permanent-assignments/save')
@auth()
def save_single_permanent_assignment():
    d = request.get_json() or {}
    sid = d.get('student_id')
    if not sid:
        return jsonify(error='student_id is required'), 400
    d['student_ids'] = [sid]
    return bulk_save_permanent_assignments()

@app.put('/api/permanent-assignments/<int:student_id>')
@auth()
def put_single_permanent_assignment(student_id):
    d = request.get_json() or {}
    d['student_ids'] = [student_id]
    return bulk_save_permanent_assignments()

@app.post('/api/permanent-assignments/<int:student_id>/reset')
@auth()
def reset_single_permanent_assignment(student_id):
    d = request.get_json() or {}
    d['student_ids'] = [student_id]
    return bulk_reset_permanent_assignments()

if __name__ == '__main__':
    import sys
    if '--check-html' in sys.argv:
        # Utility: validate key HTML elements exist exactly once in templates/index.html
        html_path = os.path.join(ROOT, 'templates', 'index.html')
        f_content = open(html_path, encoding='utf-8').read()
        print('HTML validator — templates/index.html')
        print('Total lines:', f_content.count('\n'))
        print('Total chars:', len(f_content))
        checks = [
            'id="teacher"', 'id="admin"', 'id="student"', 'id="login"',
            'id="tab-questions"', 'id="tab-mcqs"', 'id="tab-students"', 'id="tab-activity"',
            'id="teacherQuestionList"', 'id="activity"', 'id="adminActivity"',
            'id="leaderboardTableBody"', 'id="adminLeaderboardTableBody"',
            'id="leaderboardClassSearch"', 'id="adminLeaderboardClassSearch"',
            'id="questionImportForm"', 'script src', '</html>',
        ]
        all_ok = True
        for k in checks:
            cnt = f_content.count(k)
            status = 'OK' if cnt == 1 else ('MISSING' if cnt == 0 else f'DUPLICATE x{cnt}')
            if status != 'OK': all_ok = False
            print(f'  {status}: {k}')

    # Health Checks
    print("Performing Startup Health Checks...")
    compilers_ok = True
    for cmd, lang in [('gcc', 'C'), ('java', 'Java Runtime'), ('javac', 'Java Compiler')]:
        if not shutil.which(cmd):
            print(f"WARNING: '{cmd}' not found in PATH. {lang} code execution will fail.")
            compilers_ok = False
    if compilers_ok:
        print("All required compilers found.")
    
    import sys
    port = 5000
    host = '0.0.0.0'
    for arg in sys.argv[1:]:
        if arg.startswith('--port='):
            port = int(arg.split('=')[1])
        elif arg.startswith('--host='):
            host = arg.split('=')[1]
            
    # When running as internal worker on 5001-5005, bind strictly to 127.0.0.1
    # so all incoming students MUST go through the Port 5000 Load Balancer.
    if port != 5000 and not any(a.startswith('--host=') for a in sys.argv[1:]):
        host = '127.0.0.1'

    print(f"Starting server on http://{host}:{port}")
    app.run(host=host, port=port, debug=False)
