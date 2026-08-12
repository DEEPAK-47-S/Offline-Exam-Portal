"""Offline NIT Exam. Run locally: python app.py"""
import json, os, queue, shutil, sqlite3, subprocess, sys, tempfile, threading, time, random, csv
os.environ["PATH"] += os.pathsep + "C:\\msys64\\ucrt64\\bin"
from functools import wraps
from flask import Flask, jsonify, render_template, request, session
from flask_sock import Sock
from werkzeug.security import check_password_hash, generate_password_hash

ROOT = os.path.dirname(__file__)
DB = os.path.join(ROOT, "exam_lab.db")
app = Flask(__name__)
app.config.update(SECRET_KEY=os.environ.get("NIT_EXAM_SECRET", "change-this-local-secret"), SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")
sock = Sock(app)

def conn():
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; return c
def init_db():
    c=conn(); c.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, name TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('student','teacher','admin')), password_hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS questions(id INTEGER PRIMARY KEY, title TEXT NOT NULL, prompt TEXT NOT NULL, language TEXT NOT NULL DEFAULT 'python', active INTEGER NOT NULL DEFAULT 1, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS submissions(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, question_id INTEGER, code TEXT NOT NULL, language TEXT NOT NULL, score REAL DEFAULT 0, time_seconds INTEGER DEFAULT 0, submitted_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS activity(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, event TEXT NOT NULL, details TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS courses(id INTEGER PRIMARY KEY, title TEXT NOT NULL, description TEXT DEFAULT '', active INTEGER DEFAULT 1, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS topics(id INTEGER PRIMARY KEY, course_id INTEGER NOT NULL, title TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS test_cases(id INTEGER PRIMARY KEY, question_id INTEGER NOT NULL, input_data TEXT DEFAULT '', expected_output TEXT DEFAULT '', marks REAL DEFAULT 1, visible INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS exam_access(user_id INTEGER PRIMARY KEY, joined_at REAL, locked INTEGER DEFAULT 0, allowed INTEGER DEFAULT 1, violations INTEGER DEFAULT 0, last_violation_at REAL DEFAULT 0, final_score REAL, mcq_final_score REAL);
CREATE TABLE IF NOT EXISTS student_profiles(user_id INTEGER PRIMARY KEY, register_number TEXT DEFAULT '', department TEXT DEFAULT '', year TEXT DEFAULT '', semester TEXT DEFAULT '', section TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS mcqs(id INTEGER PRIMARY KEY, question TEXT NOT NULL, option_a TEXT NOT NULL, option_b TEXT NOT NULL, option_c TEXT NOT NULL, option_d TEXT NOT NULL, correct_option TEXT NOT NULL, marks REAL DEFAULT 1, time_limit INTEGER DEFAULT 30);
CREATE TABLE IF NOT EXISTS mcq_status(id INTEGER PRIMARY KEY, student_id INTEGER NOT NULL, student_name TEXT NOT NULL, mcq_id INTEGER NOT NULL, selected_option TEXT, is_correct INTEGER DEFAULT 0);''')
    for stmt in ("ALTER TABLE submissions ADD COLUMN score REAL DEFAULT 0", "ALTER TABLE submissions ADD COLUMN time_seconds INTEGER DEFAULT 0", "ALTER TABLE exam_access ADD COLUMN allowed INTEGER DEFAULT 1", "ALTER TABLE exam_access ADD COLUMN violations INTEGER DEFAULT 0", "ALTER TABLE exam_access ADD COLUMN last_violation_at REAL DEFAULT 0", "ALTER TABLE exam_access ADD COLUMN final_score REAL", "ALTER TABLE exam_access ADD COLUMN mcq_final_score REAL", "ALTER TABLE exam_access ADD COLUMN total_time INTEGER", "ALTER TABLE mcqs ADD COLUMN marks REAL DEFAULT 1", "ALTER TABLE mcqs ADD COLUMN time_limit INTEGER DEFAULT 30", "ALTER TABLE exam_state ADD COLUMN mcq_access_code TEXT", "ALTER TABLE users ADD COLUMN department TEXT"):
        try: c.execute(stmt)
        except sqlite3.OperationalError: pass
    c.execute('CREATE TABLE IF NOT EXISTS exam_state(id INTEGER PRIMARY KEY CHECK(id=1), active INTEGER DEFAULT 0, access_code TEXT, mcq_access_code TEXT, duration_minutes INTEGER DEFAULT 60, started_at REAL)')
    c.execute('INSERT OR IGNORE INTO exam_state(id,active,duration_minutes) VALUES(1,0,60)')
    if not c.execute("SELECT 1 FROM users WHERE username='teacher'").fetchone():
        c.execute("INSERT INTO users(username,name,role,password_hash) VALUES(?,?,?,?)", ('teacher','Exam Teacher','teacher',generate_password_hash('ChangeMe123!')))
        c.execute("INSERT INTO users(username,name,role,password_hash) VALUES(?,?,?,?)", ('student001','Demo Student','student',generate_password_hash('Student123!')))
    if not c.execute("SELECT 1 FROM users WHERE username='admin'").fetchone():
        c.execute("INSERT INTO users(username,name,role,password_hash) VALUES(?,?,?,?)", ('admin','Exam Admin','admin',generate_password_hash('Admin123!')))
    # Existing students remain allowed by default. Teachers can later revoke access per student.
    c.execute("INSERT OR IGNORE INTO exam_access(user_id,allowed,violations) SELECT id,1,0 FROM users WHERE role='student'")
    c.commit(); c.close()

init_db()

def auth(role=None):
    def deco(fn):
        @wraps(fn)
        def w(*a, **k):
            if 'user_id' not in session or (role and session.get('role') != role): return jsonify(error='Unauthorized'),401
            return fn(*a, **k)
        return w
    return deco
@app.route('/')
def home(): return render_template('index.html')
@app.post('/api/login')
def login():
    d=request.get_json() or {}; u=(d.get('username') or '').strip(); p=d.get('password') or ''
    c=conn(); row=c.execute('SELECT * FROM users WHERE username=?',(u,)).fetchone(); c.close()
    if not row or not check_password_hash(row['password_hash'],p): return jsonify(error='Invalid login'),401
    session.clear(); session.update(user_id=row['id'], username=row['username'], name=row['name'], role=row['role'])
    log(row['id'],'login',''); return jsonify(name=row['name'],role=row['role'])
@app.post('/api/logout')
def logout(): session.clear(); return jsonify(ok=True)
@app.get('/api/me')
def me(): return jsonify(logged_in='user_id' in session, name=session.get('name'), role=session.get('role'))
def log(uid,event,details=''):
    c=conn(); c.execute('INSERT INTO activity(user_id,event,details) VALUES(?,?,?)',(uid,event,str(details)[:500])); c.commit(); c.close()
def illegal_count(uid):
    c=conn(); row=c.execute('SELECT violations FROM exam_access WHERE user_id=?',(uid,)).fetchone(); c.close()
    return int(row['violations']) if row else 0
def current_exam(c=None):
    close=False
    if c is None: c=conn(); close=True
    s=dict(c.execute('SELECT * FROM exam_state WHERE id=1').fetchone())
    remaining=max(0,int(s['duration_minutes']*60-(time.time()-s['started_at']))) if s['active'] and s['started_at'] else 0
    if s['active'] and remaining<=0:
        c.execute('UPDATE exam_state SET active=0 WHERE id=1'); c.commit(); s['active']=0
    if close: c.close()
    return s, remaining
def has_exam_access(uid):
    c=conn(); s,remaining=current_exam(c); row=c.execute('SELECT locked,allowed,joined_at FROM exam_access WHERE user_id=?',(uid,)).fetchone(); c.close()
    return bool(s['active'] and remaining>0 and row and row['allowed'] and row['joined_at'] and not row['locked'])
def require_exam_access():
    if session.get('role')!='student' or not has_exam_access(session['user_id']):
        return jsonify(error='Exam is locked. Enter the current exam key after the teacher starts the exam.'),403
    return None
@app.post('/api/activity')
@auth('student')
def activity():
    d=request.get_json() or {}; event=str(d.get('event',''))
    allowed_events={'tab_hidden','window_blur','fullscreen_exit','blocked_paste','blocked_key','screen_resize','attempt_close'}
    # Do not monitor while the student is still at the exam-key screen.
    if event not in allowed_events or not has_exam_access(session['user_id']):
        return jsonify(ok=True,ignored=True,illegal_count=illegal_count(session['user_id']),locked=False)
    # A single application switch can fire blur, visibility, and fullscreen events.
    # Count that action once, not three times.
    now=time.time(); c=conn(); row=c.execute('SELECT violations,last_violation_at FROM exam_access WHERE user_id=?',(session['user_id'],)).fetchone()
    if row and now-float(row['last_violation_at'] or 0)<1.25:
        c.close(); return jsonify(ok=True,ignored=True,illegal_count=int(row['violations'] or 0),locked=False)
    c.execute('UPDATE exam_access SET violations=violations+1,last_violation_at=? WHERE user_id=?',(now,session['user_id'])); c.commit(); c.close()
    log(session['user_id'],event,d.get('details',''))
    count=illegal_count(session['user_id'])
    locked=count >= 4
    if locked:
        c=conn(); c.execute('UPDATE exam_access SET locked=1 WHERE user_id=?',(session['user_id'],)); c.commit(); c.close(); log(session['user_id'],'exam_locked','illegal activity limit exceeded')
    return jsonify(ok=True,illegal_count=count,locked=locked)
@app.get('/api/questions')
@auth()
def questions():
    c=conn(); rows=c.execute('SELECT * FROM questions WHERE active=1 ORDER BY id DESC').fetchall(); c.close(); return jsonify([dict(x) for x in rows])
@app.post('/api/questions')
@auth('teacher')
def add_question():
    d=request.get_json() or {}; c=conn(); cur=c.execute('INSERT INTO questions(title,prompt,language) VALUES(?,?,?)',(d.get('title','Untitled'),d.get('prompt',''),d.get('language','python'))); c.commit(); qid=cur.lastrowid; c.close(); return jsonify(ok=True,id=qid)
@app.put('/api/questions/<int:question_id>')
@auth('teacher')
def update_question(question_id):
    d=request.get_json() or {}
    title=(d.get('title') or '').strip()
    prompt=d.get('prompt') or ''
    language=d.get('language') or 'python'
    if not title or language not in ('python','java','c'):
        return jsonify(error='Question title and valid language are required.'),400
    c=conn(); c.execute('UPDATE questions SET title=?, prompt=?, language=? WHERE id=?',(title,prompt,language,question_id)); c.commit(); c.close()
    return jsonify(ok=True)

@app.get('/api/mcqs')
@auth()
def list_mcqs():
    c=conn(); rows=c.execute('SELECT * FROM mcqs ORDER BY id').fetchall(); c.close(); return jsonify([dict(x) for x in rows])
@app.post('/api/mcqs')
@auth('teacher')
def add_mcq():
    d=request.get_json() or {}; c=conn(); cur=c.execute('INSERT INTO mcqs(question,option_a,option_b,option_c,option_d,correct_option,marks,time_limit) VALUES(?,?,?,?,?,?,?,?)',(d.get('question',''),d.get('option_a',''),d.get('option_b',''),d.get('option_c',''),d.get('option_d',''),d.get('correct_option','a'),float(d.get('marks', 1)),int(d.get('time_limit', 30)))); c.commit(); qid=cur.lastrowid; c.close(); return jsonify(ok=True,id=qid)
@app.put('/api/mcqs/<int:mcq_id>')
@auth('teacher')
def update_mcq(mcq_id):
    d=request.get_json() or {}; c=conn(); c.execute('UPDATE mcqs SET question=?, option_a=?, option_b=?, option_c=?, option_d=?, correct_option=?, marks=?, time_limit=? WHERE id=?',(d.get('question',''),d.get('option_a',''),d.get('option_b',''),d.get('option_c',''),d.get('option_d',''),d.get('correct_option','a'),float(d.get('marks', 1)),int(d.get('time_limit', 30)),mcq_id)); c.commit(); c.close(); return jsonify(ok=True)
@app.delete('/api/mcqs/<int:mcq_id>')
@auth('teacher')
def delete_mcq(mcq_id):
    c=conn(); c.execute('DELETE FROM mcqs WHERE id=?',(mcq_id,)); c.commit(); c.close(); return jsonify(ok=True)
@app.get('/api/mcqs/status')
@auth('teacher')
def list_mcq_status():
    c=conn(); rows=c.execute('SELECT * FROM mcq_status ORDER BY id DESC').fetchall(); c.close(); return jsonify([dict(x) for x in rows])
@app.post('/api/mcqs/submit')
@auth('student')
def submit_mcq():
    locked=require_exam_access()
    if locked: return locked
    d=request.get_json() or {}; mcq_id=d.get('mcq_id'); selected=d.get('selected_option','')
    c=conn(); mcq=c.execute('SELECT correct_option FROM mcqs WHERE id=?',(mcq_id,)).fetchone()
    if not mcq: return jsonify(error='MCQ not found.'),404
    is_correct=int(str(mcq['correct_option']).lower()==str(selected).lower())
    c.execute('INSERT INTO mcq_status(student_id,student_name,mcq_id,selected_option,is_correct) VALUES(?,?,?,?,?)',(session['user_id'],session['name'],mcq_id,selected,is_correct)); c.commit(); c.close()
    return jsonify(ok=True,is_correct=bool(is_correct))
@app.post('/api/submit')
@auth('student')
def submit():
    locked=require_exam_access()
    if locked: return locked
    d=request.get_json() or {}; score=float(d.get('score') or 0); c=conn(); s,remaining=current_exam(c); elapsed=max(0,int(s['duration_minutes']*60-remaining)) if s.get('duration_minutes') else 0
    c.execute('INSERT INTO submissions(user_id,question_id,code,language,score,time_seconds) VALUES(?,?,?,?,?,?)',(session['user_id'],d.get('question_id'),d.get('code',''),d.get('language','python'),score,elapsed)); c.commit(); c.close(); log(session['user_id'],'submission',f'saved score={score}'); return jsonify(ok=True,elapsed=elapsed)
@app.get('/api/dashboard')
@auth('teacher')
def dashboard():
    c=conn(); students=c.execute("SELECT u.id,u.name,u.username,p.department,p.section,p.register_number,e.final_score,e.mcq_final_score,e.total_time,e.locked FROM users u LEFT JOIN student_profiles p ON p.user_id=u.id LEFT JOIN exam_access e ON e.user_id=u.id WHERE u.role='student' ORDER BY u.name").fetchall(); out=[]
    for s in students:
        a=c.execute('SELECT event,details,created_at FROM activity WHERE user_id=? ORDER BY id DESC LIMIT 5',(s['id'],)).fetchall()
        n=c.execute('SELECT count(*) n FROM submissions WHERE user_id=?',(s['id'],)).fetchone()['n']
        score = s['final_score'] if s['final_score'] is not None else 0
        mcq_score = s['mcq_final_score'] if s['mcq_final_score'] is not None else 0
        out.append({'name':s['name'],'username':s['username'],'class_name':f"{s['department'] or ''}-{s['section'] or ''}".strip('-'),'register_number':s['register_number'],'submissions':n,'score':round(score or 0,2),'mcq_score':round(mcq_score or 0,2),'total_time':s['total_time'] or 0,'locked':s['locked'],'illegal_count':illegal_count(s['id']),'activity':[dict(x) for x in a],'pending':s['final_score'] is None})
    c.close(); return jsonify(out)
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
    d=request.get_json() or {}; c=conn(); c.execute('INSERT INTO test_cases(question_id,input_data,expected_output,marks,visible) VALUES(?,?,?,?,?)',(d.get('question_id'),d.get('input_data',''),d.get('expected_output',''),d.get('marks',1),bool(d.get('visible')))); c.commit(); c.close(); return jsonify(ok=True)
@app.get('/api/test-cases/<int:question_id>')
@auth('teacher')
def list_test_cases(question_id):
    c=conn(); rows=c.execute('SELECT * FROM test_cases WHERE question_id=? ORDER BY id',(question_id,)).fetchall(); c.close(); return jsonify([dict(x) for x in rows])
@app.put('/api/test-cases/<int:case_id>')
@auth('teacher')
def update_test_case(case_id):
    d=request.get_json() or {}
    try: marks=float(d.get('marks', 1))
    except (TypeError, ValueError): return jsonify(error='Marks must be numeric.'),400
    c=conn(); cur=c.execute('UPDATE test_cases SET input_data=?,expected_output=?,marks=?,visible=? WHERE id=?',(d.get('input_data',''),d.get('expected_output',''),marks,int(bool(d.get('visible'))),case_id)); c.commit(); c.close()
    if not cur.rowcount: return jsonify(error='Test case not found.'),404
    return jsonify(ok=True)
@app.get('/api/exam-state')
@auth()
def exam_state():
    c=conn(); s,remaining=current_exam(c); joined=False; locked=False
    if session.get('role')=='student':
        row=c.execute('SELECT locked,allowed,joined_at,violations FROM exam_access WHERE user_id=?',(session['user_id'],)).fetchone(); joined=bool(row and row['joined_at']); locked=bool(row and row['locked'])
    c.close()
    return jsonify(active=bool(s['active']),remaining=remaining,joined=joined,locked=locked,allowed=bool(row and row['allowed']) if session.get('role')=='student' else True,illegal_count=illegal_count(session['user_id']) if session.get('role')=='student' else 0,access_code=s['access_code'] if session.get('role')=='teacher' else None,mcq_access_code=s.get('mcq_access_code') if session.get('role')=='teacher' else None)
@app.post('/api/exam/start')
@auth('teacher')
def exam_start():
    d=request.get_json() or {}; code=str(random.randint(100000,999999)); mcq_code=str(random.randint(100000,999999)); minutes=max(1,int(d.get('minutes',60))); c=conn(); c.execute('DELETE FROM submissions'); c.execute('DELETE FROM mcq_status'); c.execute('DELETE FROM activity'); c.execute('UPDATE exam_access SET joined_at=NULL,locked=0,violations=0,last_violation_at=0,final_score=NULL,mcq_final_score=NULL,total_time=NULL'); c.execute('UPDATE exam_state SET active=1,access_code=?,mcq_access_code=?,duration_minutes=?,started_at=? WHERE id=1',(code,mcq_code,minutes,time.time())); c.commit(); c.close(); return jsonify(ok=True,access_code=code,mcq_access_code=mcq_code)
@app.post('/api/exam/stop')
@auth('teacher')
def exam_stop():
    c=conn(); c.execute('UPDATE exam_state SET active=0 WHERE id=1'); c.commit(); c.close(); return jsonify(ok=True)
@app.post('/api/exam/student-exit')
@auth('student')
def exam_student_exit():
    c=conn(); state=c.execute('SELECT active FROM exam_state WHERE id=1').fetchone()
    if not state or not state['active']:
        c.close(); return jsonify(error='Exam is not active.'),403
    score_row = c.execute('SELECT COALESCE(SUM(max_score),0) as total, COALESCE(SUM(min_time),0) as time_total FROM (SELECT MAX(score) as max_score, MIN(time_seconds) as min_time FROM submissions WHERE user_id=? GROUP BY question_id)', (session['user_id'],)).fetchone()
    final_score = score_row['total'] if score_row else 0
    total_time = score_row['time_total'] if score_row else 0
    mcq_row = c.execute('SELECT SUM(m.marks) as total FROM mcq_status s JOIN mcqs m ON s.mcq_id=m.id WHERE s.student_id=? AND s.is_correct=1', (session['user_id'],)).fetchone()
    mcq_final = mcq_row['total'] if mcq_row and mcq_row['total'] else 0
    c.execute('UPDATE exam_access SET locked=1, final_score=?, mcq_final_score=?, total_time=? WHERE user_id=?',(final_score, mcq_final, total_time, session['user_id'])); c.commit(); c.close()
    log(session['user_id'],'exam_left','student exited voluntarily')
    return jsonify(ok=True)
@app.post('/api/exam/join')
@auth('student')
def exam_join():
    d=request.get_json() or {}; key=str(d.get('access_code','')).strip(); c=conn(); s,remaining=current_exam(c)
    if not s['active'] or remaining<=0:
        c.close(); return jsonify(error='Exam has not started yet.'),403
    row=c.execute('SELECT allowed,locked FROM exam_access WHERE user_id=?',(session['user_id'],)).fetchone()
    if not row or not row['allowed']:
        c.close(); return jsonify(error='You are not selected for this exam.'),403
    exam_type = None
    if key == str(s.get('access_code') or ''):
        exam_type = 'coding'
    elif key == str(s.get('mcq_access_code') or ''):
        exam_type = 'mcq'
    else:
        c.close(); log(session['user_id'],'invalid_exam_key',''); return jsonify(error='Invalid exam key.'),403
    if row['locked']:
        c.close(); return jsonify(error='This student session is locked for this exam.'),403
    c.execute('UPDATE exam_access SET joined_at=COALESCE(joined_at,?) WHERE user_id=?',(time.time(),session['user_id'])); c.commit(); c.close(); log(session['user_id'],'exam_join',exam_type); return jsonify(ok=True,remaining=remaining,exam_type=exam_type)
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
    c=conn(); c.execute('INSERT OR REPLACE INTO users(username,name,role,password_hash) VALUES(?,?,?,?)',(username,name,'student',generate_password_hash(password))); uid=c.execute('SELECT id FROM users WHERE username=?',(username,)).fetchone()['id']; c.execute('INSERT OR IGNORE INTO exam_access(user_id,allowed,violations) VALUES(?,1,0)',(uid,)); c.execute('INSERT OR REPLACE INTO student_profiles(user_id,register_number,department,year,semester,section) VALUES(?,?,?,?,?,?)',(uid,d.get('register_number') or username,d.get('department') or '',d.get('year') or '',d.get('semester') or '',d.get('section') or '')); c.commit(); c.close()
    return jsonify(ok=True)

@app.get('/api/students')
@auth()
def list_students():
    if session.get('role') not in ('teacher', 'admin'): return jsonify(error='Unauthorized'),401
    c=conn(); rows=c.execute("SELECT u.id,u.name,u.username,p.register_number,p.department,p.year,p.semester,p.section,COALESCE(e.allowed,1) allowed FROM users u LEFT JOIN student_profiles p ON p.user_id=u.id LEFT JOIN exam_access e ON e.user_id=u.id WHERE u.role='student' ORDER BY u.name").fetchall(); c.close()
    return jsonify([dict(row) for row in rows])

@app.put('/api/students/<int:user_id>')
@auth('teacher')
def update_student(user_id):
    d=request.get_json() or {}; name=(d.get('name') or '').strip(); username=(d.get('username') or '').strip()
    if not name or not username: return jsonify(error='Student name and username are required.'),400
    c=conn(); c.execute("UPDATE users SET name=?,username=? WHERE id=? AND role='student'",(name,username,user_id)); c.execute('INSERT OR REPLACE INTO student_profiles(user_id,register_number,department,year,semester,section) VALUES(?,?,?,?,?,?)',(user_id,d.get('register_number') or username,d.get('department') or '',d.get('year') or '',d.get('semester') or '',d.get('section') or '')); c.execute('INSERT OR IGNORE INTO exam_access(user_id,allowed,violations) VALUES(?,1,0)',(user_id,)); c.commit(); c.close(); return jsonify(ok=True)

@app.put('/api/admin/students/<int:user_id>')
@auth('admin')
def admin_update_student(user_id):
    d=request.get_json() or {}; name=(d.get('name') or '').strip(); username=(d.get('username') or '').strip(); password=d.get('password') or ''
    if not name or not username: return jsonify(error='Student name and username are required.'),400
    c=conn()
    if password: c.execute("UPDATE users SET name=?,username=?,password_hash=? WHERE id=? AND role='student'",(name,username,generate_password_hash(password),user_id))
    else: c.execute("UPDATE users SET name=?,username=? WHERE id=? AND role='student'",(name,username,user_id))
    c.execute('INSERT OR REPLACE INTO student_profiles(user_id,register_number,department,year,semester,section) VALUES(?,?,?,?,?,?)',(user_id,d.get('register_number') or username,d.get('department') or '',d.get('year') or '',d.get('semester') or '',d.get('section') or ''))
    c.commit(); c.close(); return jsonify(ok=True)

@app.delete('/api/students/<int:user_id>')
@auth('teacher')
def delete_student(user_id):
    c=conn(); c.execute("DELETE FROM users WHERE id=? AND role='student'",(user_id,)); c.execute('DELETE FROM student_profiles WHERE user_id=?',(user_id,)); c.execute('DELETE FROM exam_access WHERE user_id=?',(user_id,)); c.commit(); c.close(); return jsonify(ok=True)

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
    c=conn(); c.execute('INSERT INTO exam_access(user_id,allowed,violations) VALUES(?,?,0) ON CONFLICT(user_id) DO UPDATE SET allowed=excluded.allowed',(user_id,int(allowed))); c.commit(); c.close(); return jsonify(ok=True,allowed=allowed)

def read_import_file(f):
    """Read the shared CSV/XLSX import format used by admin and teachers."""
    rows=[]
    if f.filename.lower().endswith('.csv'):
        lines = f.read().decode('utf-8-sig').splitlines()
        if lines:
            reader = csv.reader(lines)
            heads = [str(x).strip().lower() for x in next(reader)]
            rows = [dict(zip(heads, [str(x).strip() for x in r])) for r in reader]
    else:
        try:
            from openpyxl import load_workbook
            ws=load_workbook(f,read_only=True,data_only=True).active
            rows_iter=ws.iter_rows()
            heads=[str(x.value or '').strip().lower() for x in next(rows_iter)]
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
    for r in rows:
        name=r.get('name','').strip()
        username=(r.get('username') or r.get('registration_number') or r.get('registernumber') or r.get('register_number') or r.get('staff_number') or '').strip()
        password=r.get('password','').strip()
        dept=r.get('department','').strip()
        if name and username and password:
            c.execute('INSERT OR REPLACE INTO users(username,name,role,password_hash,department) VALUES(?,?,?,?,?)',(username,name,role,generate_password_hash(password),dept))
            if role=='student':
                uid=c.execute('SELECT id FROM users WHERE username=?',(username,)).fetchone()['id']
                c.execute('INSERT OR IGNORE INTO exam_access(user_id,allowed,violations) VALUES(?,1,0)',(uid,))
                section=r.get('section') or r.get('session') or ''
                c.execute('INSERT OR REPLACE INTO student_profiles(user_id,register_number,department,year,semester,section) VALUES(?,?,?,?,?,?)',(uid,r.get('registernumber') or r.get('register_number') or username,dept,r.get('year') or '',r.get('semester') or '',section))
            count+=1
    c.commit(); c.close(); return jsonify(ok=True,imported=count)

@app.post('/api/mcqs/import')
@auth('teacher')
def import_mcqs():
    f=request.files.get('file')
    if not f: return jsonify(error='Choose a CSV or XLSX file.'),400
    try: rows=read_import_file(f)
    except ValueError as error: return jsonify(error=str(error)),400
    c=conn(); count=0
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
            c.execute('INSERT INTO mcqs(question,option_a,option_b,option_c,option_d,correct_option,marks,time_limit) VALUES(?,?,?,?,?,?,?,?)', (str(q).strip(), str(a).strip(), str(b).strip(), str(opt_c).strip(), str(d).strip(), str(correct).strip().lower(), marks, time_limit))
            count+=1
    c.commit(); c.close(); return jsonify(ok=True,imported=count)
@app.get('/api/admin/users')
@auth('admin')
def admin_users():
    c=conn(); rows=c.execute("SELECT id,username,name,role FROM users WHERE role IN ('teacher','student') ORDER BY role,name").fetchall(); c.close(); return jsonify([dict(x) for x in rows])
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
    c.commit(); c.close(); return jsonify(ok=True)
@app.post('/api/verify')
@auth('student')
def verify():
    locked=require_exam_access()
    if locked: return locked
    d=request.get_json() or {}; qid=d.get('question_id'); code=d.get('code',''); language=d.get('language','python')
    c=conn(); cases=c.execute('SELECT * FROM test_cases WHERE question_id=? ORDER BY id',(qid,)).fetchall(); c.close()
    if not cases: return jsonify(error='Teacher has not added test cases for this question.'),400
    results=[]; total=0
    for i,t in enumerate(cases,1):
        passed,out,ms=run_once(language,code,t['input_data'])
        expected=(t['expected_output'] or '').strip()
        ok=passed and out.strip()==expected
        marks=float(t['marks'] or 0) if ok else 0
        total+=marks
        results.append({'name':f'Test Case {i}','visible':bool(t['visible']),'passed':ok,'time_ms':ms,'marks':marks,'output':out if t['visible'] else '', 'expected':expected if t['visible'] else ''})
    return jsonify(score=round(total,2),results=results)

import re as _re
_JAVA_PUBLIC_CLASS = _re.compile(r'public\s+(?:final\s+|abstract\s+|strictfp\s+)*class\s+([A-Za-z_$][A-Za-z0-9_$]*)')
_JAVA_ANY_CLASS = _re.compile(r'\bclass\s+([A-Za-z_$][A-Za-z0-9_$]*)')

def start_process(language, code):
    td=tempfile.mkdtemp(prefix='nit_exam_')
    try:
        if language=='java':
            # Java requires the source filename to match its PUBLIC top-level
            # class exactly (e.g. "public class Solution" MUST live in
            # Solution.java). The old code always wrote Main.java and always
            # ran class "Main", so any normal DSA-style submission (public
            # class Solution / BinarySearch / etc., which is the overwhelming
            # majority of real submissions) failed to compile with a cryptic
            # "class X is public, should be declared in a file named X.java"
            # error - which looked like "Java just doesn't work". Detect the
            # real class name from the submitted code instead of assuming Main.
            m = _JAVA_PUBLIC_CLASS.search(code) or _JAVA_ANY_CLASS.search(code)
            class_name = m.group(1) if m else 'Main'
            path=os.path.join(td,f'{class_name}.java'); open(path,'w',encoding='utf8').write(code)
            r=subprocess.run(['javac','-d',td,path],capture_output=True,text=True,timeout=5,cwd=td)
            if r.returncode: shutil.rmtree(td,ignore_errors=True); return None,None,r.stderr or r.stdout
            return subprocess.Popen(['java','-cp',td,class_name],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,bufsize=0,cwd=td),td,None
        if language=='c':
            path=os.path.join(td,'main.c'); executable=os.path.join(td,'main.exe'); open(path,'w',encoding='utf8').write(code)
            # -std=c11 (rather than c17) matches what older MinGW/GCC builds
            # still commonly installed on offline Windows exam-lab machines
            # support; c17 is functionally almost identical for student code
            # but is only available on newer GCC and would otherwise fail to
            # even start compiling on an older toolchain.
            r=subprocess.run(['gcc',path,'-O2','-std=c11','-lm','-o',executable],capture_output=True,text=True,timeout=15,cwd=td)
            if r.returncode: shutil.rmtree(td,ignore_errors=True); return None,None,r.stderr or r.stdout
            return subprocess.Popen([executable],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,bufsize=0,cwd=td),td,None
        if language!='python': return None,None,'Unsupported language. Select Python, Java, or C.'
        path=os.path.join(td,'main.py'); open(path,'w',encoding='utf8').write(code)
        return subprocess.Popen([sys.executable,'-u',path],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,bufsize=0,cwd=td),td,None
    except FileNotFoundError:
        shutil.rmtree(td,ignore_errors=True)
        return None,None,'Compiler could not start. Install Python, a JDK for Java, and MinGW-w64/GCC for C; add them to PATH.'
    except subprocess.TimeoutExpired:
        shutil.rmtree(td,ignore_errors=True)
        return None,None,'Compilation timed out (5s).'
def run_once(language, code, input_data):
    proc,td,err=start_process(language,code); started=time.time()
    if err: return False,err,0
    try:
        out,_=proc.communicate((input_data or '').encode(),timeout=5)
        return proc.returncode==0,out.decode(errors='replace'),round((time.time()-started)*1000)
    except subprocess.TimeoutExpired:
        proc.kill(); return False,'Execution timed out (5s).',5000
    finally:
        if td: shutil.rmtree(td,ignore_errors=True)
@sock.route('/ws/run')
def run(ws):
    if session.get('role')!='student' or not has_exam_access(session.get('user_id')): return
    proc=td=None; q=queue.Queue(); started=0
    def reader(stream):
        while True:
            data=os.read(stream.fileno(),4096)
            if not data: break
            q.put(data.decode(errors='replace'))
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
                if proc: proc.kill(); shutil.rmtree(td,ignore_errors=True)
                proc,td,err=start_process(m.get('language'),m.get('code','')); started=time.time()
                if err: ws.send(json.dumps({'type':'compile_error','data':err})); proc=td=None
                elif proc: threading.Thread(target=reader,args=(proc.stdout,),daemon=True).start()
            elif m.get('action')=='input' and proc: proc.stdin.write((m.get('data','')+'\n').encode()); proc.stdin.flush()
            elif m.get('action')=='stop' and proc: proc.kill()
    finally:
        if proc: proc.kill()
        if td: shutil.rmtree(td,ignore_errors=True)
if __name__=='__main__':
    # 0.0.0.0 allows other laptops on the same offline Wi-Fi/LAN to connect.
    app.run(host='0.0.0.0',port=5000,debug=False)
