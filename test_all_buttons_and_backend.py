import sys, time, json
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
import app as a

passed_count = 0
failed_tests = []

def run_test(name, fn):
    global passed_count
    try:
        fn()
        passed_count += 1
        print(f"  ✅ [PASS] {name}")
    except Exception as e:
        import traceback
        failed_tests.append((name, str(e)))
        print(f"  ❌ [FAIL] {name}: {e}")
        traceback.print_exc()

print("================================================================")
print("COMPREHENSIVE AUDIT: TESTING ALL BUTTONS, ROUTES & TAB SWITCHES")
print("================================================================")

# -------------------------------------------------------------
# 1. AUTH & SESSION API
# -------------------------------------------------------------
client = a.app.test_client()
def t_auth_routes():
    # Invalid login
    r = client.post('/api/login', json={'username': 'fake_user', 'password': 'wrong'})
    assert r.status_code == 401, "Invalid login should return 401"
    
    # Valid student login (reset password for clean test)
    c = a.conn()
    c.execute("UPDATE users SET password_hash=? WHERE username='28'", (a.generate_password_hash('28'),))
    c.commit(); c.close()
    r = client.post('/api/login', json={'username': '28', 'password': '28'})
    assert r.status_code == 200
    assert r.get_json()['role'] == 'student'
    
    # /api/me endpoint
    r = client.get('/api/me')
    assert r.status_code == 200
    assert r.get_json()['logged_in'] == True
    
    # Logout
    r = client.post('/api/logout')
    assert r.status_code == 200
    r = client.get('/api/me')
    assert r.get_json()['logged_in'] == False
run_test("1. Auth & Session (Login / Logout / /api/me)", t_auth_routes)

# -------------------------------------------------------------
# 2. TEACHER EXAM MANAGEMENT & QUESTION/TEST CASE CRUD
# -------------------------------------------------------------
teacher_client = a.app.test_client()
t_qid = None
t_case_id = None
active_exam_code = None
def t_teacher_crud():
    global t_qid, t_case_id, active_exam_code
    # Teacher login
    c = a.conn()
    c.execute("UPDATE users SET password_hash=? WHERE username='teacher'", (a.generate_password_hash('Teacher123!'),))
    c.commit(); c.close()
    
    r = teacher_client.post('/api/login', json={'username': 'teacher', 'password': 'Teacher123!'})
    assert r.status_code == 200, f"Teacher login: {r.get_json()}"
    
    # Add Question
    r = teacher_client.post('/api/questions', json={'title': 'Multiply Two Numbers', 'prompt': 'Calculate product of two numbers', 'language': 'python'})
    assert r.status_code == 200
    
    # Fetch questions
    r = teacher_client.get('/api/questions')
    assert r.status_code == 200
    questions = r.get_json()
    q = next((item for item in questions if item['title'] == 'Multiply Two Numbers'), None)
    assert q is not None, "Question should be found"
    t_qid = q['id']
    
    # Add Test Case
    r = teacher_client.post('/api/test-cases', json={'question_id': t_qid, 'input_data': '4 5', 'expected_output': '20', 'marks': 5.0, 'visible': True})
    assert r.status_code == 200
    
    # List Test Cases
    r = teacher_client.get(f'/api/test-cases/{t_qid}')
    assert r.status_code == 200
    cases = r.get_json()
    assert len(cases) > 0
    t_case_id = cases[0]['id']
    
    # Teacher toggle student access & password reset
    r = teacher_client.get('/api/students')
    assert r.status_code == 200
    students = r.get_json()
    s28 = next((s for s in students if s['username'] == '28'), None)
    assert s28 is not None
    r = teacher_client.post(f"/api/students/{s28['id']}/access", json={'allowed': True})
    assert r.status_code == 200
    r = teacher_client.post(f"/api/students/{s28['id']}/reset-password", json={'password': '2828'})
    assert r.status_code == 200
    
    # Start Coding Exam
    r = teacher_client.post('/api/exam/start_coding', json={'minutes': 45})
    assert r.status_code == 200
    active_exam_code = r.get_json().get('access_code')
    assert active_exam_code is not None, "Exam access code generated"
run_test("2. Teacher CRUD (Questions, Test Cases & Exam Start)", t_teacher_crud)

# -------------------------------------------------------------
# 3. STUDENT EXAM FLOW & CODE EXECUTION
# -------------------------------------------------------------
s_client = a.app.test_client()
def t_student_code_workflow():
    # Login student 28
    s_client.post('/api/login', json={'username': '28', 'password': '2828'})
    
    # Unlock student in DB for fresh test
    c = a.conn()
    c.execute("UPDATE exam_access SET locked=0, violations=0, last_violation_at=0, joined_at=NULL, final_score=NULL WHERE user_id=11")
    c.commit(); c.close()
    
    # Join with active exam key
    r = s_client.post('/api/exam/join', json={'access_code': active_exam_code})
    assert r.status_code == 200, f"Join failed: {r.get_json()}"
    
    # Save draft
    code_sol = "import sys\nnums = sys.stdin.read().split()\nif nums: print(int(nums[0]) * int(nums[1]))\n"
    r = s_client.post('/api/draft', json={'question_id': t_qid, 'language': 'python', 'code': code_sol})
    assert r.status_code == 200
    
    # Load draft
    r = s_client.get('/api/drafts')
    assert r.status_code == 200
    assert len(r.get_json()) > 0
    
    # Verify code
    r = s_client.post('/api/verify', json={'question_id': t_qid, 'language': 'python', 'code': code_sol})
    assert r.status_code == 200
    assert r.get_json()['score'] == 5.0
    
    # Submit code
    r = s_client.post('/api/submit', json={'question_id': t_qid, 'language': 'python', 'code': code_sol, 'score': 5.0})
    assert r.status_code == 200
    assert r.get_json()['live_score'] == 5.0
run_test("3. Student Workflow (Join, Drafts, Verify & Submit)", t_student_code_workflow)

# -------------------------------------------------------------
# 4. TAB SWITCH, ANTI-CHEAT & VIOLATIONS TO LOCK AT 5
# -------------------------------------------------------------
def t_tabswitch_and_lock_flow():
    # Strike 1: tab_hidden
    r1 = s_client.post('/api/activity', json={'event': 'tab_hidden', 'details': 'tab switch'})
    assert r1.status_code == 200
    assert r1.get_json()['illegal_count'] == 1
    assert r1.get_json()['locked'] == False
    
    time.sleep(0.85)
    # Strike 2: window_blur
    r2 = s_client.post('/api/activity', json={'event': 'window_blur', 'details': 'lost focus'})
    assert r2.get_json()['illegal_count'] == 2
    assert r2.get_json()['locked'] == False
    
    time.sleep(0.85)
    # Strike 3: blocked_key
    r3 = s_client.post('/api/activity', json={'event': 'blocked_key', 'details': 'Alt + Tab'})
    assert r3.get_json()['illegal_count'] == 3
    assert r3.get_json()['locked'] == False
    
    time.sleep(0.85)
    # Strike 4: extension_detected
    r4 = s_client.post('/api/activity', json={'event': 'extension_detected', 'details': 'Active Extension: Monica AI'})
    assert r4.get_json()['illegal_count'] == 4
    assert r4.get_json()['locked'] == False
    
    time.sleep(0.85)
    # Strike 5: fullscreen_exit -> Should LOCK the exam!
    r5 = s_client.post('/api/activity', json={'event': 'fullscreen_exit', 'details': 'exited fullscreen'})
    assert r5.get_json()['illegal_count'] == 5
    assert r5.get_json()['locked'] == True, "5th violation must lock the student!"
    
    # Verify further access is denied
    st = s_client.get('/api/exam-state').get_json()
    assert st['locked'] == True
run_test("4. Anti-Cheat (5 Strikes Tab Switch / Extension -> Automatic Lock)", t_tabswitch_and_lock_flow)

# -------------------------------------------------------------
# 5. TEACHER LEADERBOARD, DASHBOARD & HISTORY
# -------------------------------------------------------------
def t_teacher_dashboard_and_history():
    # Fetch dashboard
    r = teacher_client.get('/api/dashboard')
    assert r.status_code == 200
    dash_data = r.get_json()
    s28 = next((s for s in dash_data if s['username'] == '28'), None)
    assert s28 is not None
    assert s28['score'] == 5.0
    assert s28['illegal_count'] == 5
    assert s28['locked'] == 1
    
    # Stop exam (saves history)
    r = teacher_client.post('/api/exam/stop')
    assert r.status_code == 200
    
    # Check history
    r = teacher_client.get('/api/history')
    assert r.status_code == 200
    hist = r.get_json()
    assert len(hist) > 0, "History record should be saved after exam stop"
run_test("5. Teacher Dashboard & Exam History Archiving", t_teacher_dashboard_and_history)

# -------------------------------------------------------------
# 6. ADMIN USER MANAGEMENT & CONTROLS
# -------------------------------------------------------------
admin_client = a.app.test_client()
def t_admin_controls():
    admin_client.post('/api/login', json={'username': 'admin', 'password': 'Admin123!'})
    
    # Fetch students
    r = admin_client.get('/api/students')
    assert r.status_code == 200
    students = r.get_json()
    s28 = next((s for s in students if s['username'] == '28'), None)
    assert s28 is not None
    
    # Update student details via admin endpoint
    r = admin_client.put(f"/api/admin/students/{s28['id']}", json={'name': 'Student 28 Updated', 'username': '28', 'department': 'CSE', 'year': '3', 'class': 'CSE-A', 'section': 'A', 'semester': '6'})
    assert r.status_code == 200
    
    # Add new teacher
    r = admin_client.post('/api/admin/teachers', json={'name': 'Temp Prof', 'username': 'temp_prof', 'password': 'ProfPassword123!'})
    assert r.status_code == 200
    
    # List teachers from /api/admin/users
    r = admin_client.get('/api/admin/users')
    assert r.status_code == 200
    users = r.get_json()
    prof = next((t for t in users if t['username'] == 'temp_prof'), None)
    assert prof is not None
    
    # Delete temporary teacher
    r = admin_client.delete(f"/api/teachers/{prof['id']}")
    assert r.status_code == 200
run_test("6. Admin Management (Student Update & Teacher CRUD)", t_admin_controls)

# -------------------------------------------------------------
# 7. MULTI-LANGUAGE RUNTIME (C & JAVA COMPILATION & VERIFICATION)
# -------------------------------------------------------------
c_qid = None
java_qid = None
def t_c_and_java_execution():
    global c_qid, java_qid
    # Teacher starts coding exam
    r = teacher_client.post('/api/exam/start_coding', json={'minutes': 45})
    assert r.status_code == 200
    coding_code = r.get_json().get('access_code')
    assert coding_code is not None

    # Unlock student in DB for fresh test
    c = a.conn()
    c.execute("UPDATE exam_access SET locked=0, violations=0, last_violation_at=0, joined_at=NULL, final_score=NULL WHERE user_id=11")
    c.commit(); c.close()

    # Student joins coding exam
    r = s_client.post('/api/exam/join', json={'access_code': coding_code})
    assert r.status_code == 200

    # Student submits Python solution
    code_sol = "import sys\nnums = sys.stdin.read().split()\nif nums: print(int(nums[0]) * int(nums[1]))\n"
    r = s_client.post('/api/submit', json={'question_id': t_qid, 'language': 'python', 'code': code_sol, 'score': 5.0})
    assert r.status_code == 200

    # Teacher adds a C question
    r = teacher_client.post('/api/questions', json={'title': 'C Double Value', 'prompt': 'Print double of integer', 'language': 'c'})
    assert r.status_code == 200
    questions = teacher_client.get('/api/questions').get_json()
    q_c = next(q for q in questions if q['title'] == 'C Double Value')
    c_qid = q_c['id']
    teacher_client.post('/api/test-cases', json={'question_id': c_qid, 'input_data': '15', 'expected_output': '30', 'marks': 10.0, 'visible': True})

    # Student verifies C solution
    c_code = "#include <stdio.h>\nint main(){int n; if(scanf(\"%d\", &n)==1) printf(\"%d\", n*2); return 0;}"
    r = s_client.post('/api/verify', json={'question_id': c_qid, 'language': 'c', 'code': c_code})
    assert r.status_code == 200, f"C verify failed: {r.get_json()}"
    assert r.get_json()['score'] == 10.0, f"C test case should pass: {r.get_json()}"

    # Student submits C solution
    r = s_client.post('/api/submit', json={'question_id': c_qid, 'language': 'c', 'code': c_code, 'score': 10.0})
    assert r.status_code == 200

    # Teacher adds a Java question
    r = teacher_client.post('/api/questions', json={'title': 'Java Square Value', 'prompt': 'Print square of integer', 'language': 'java'})
    assert r.status_code == 200
    questions = teacher_client.get('/api/questions').get_json()
    q_java = next(q for q in questions if q['title'] == 'Java Square Value')
    java_qid = q_java['id']
    teacher_client.post('/api/test-cases', json={'question_id': java_qid, 'input_data': '7', 'expected_output': '49', 'marks': 10.0, 'visible': True})

    # Student verifies Java solution
    java_code = "import java.util.Scanner;\npublic class Solution { public static void main(String[] args){ Scanner sc=new Scanner(System.in); if(sc.hasNextInt()){ System.out.print(sc.nextInt()*7); } } }"
    r = s_client.post('/api/verify', json={'question_id': java_qid, 'language': 'java', 'code': java_code})
    assert r.status_code == 200, f"Java verify failed: {r.get_json()}"
    assert r.get_json()['score'] == 10.0, f"Java test case should pass: {r.get_json()}"

    # Student submits Java solution
    r = s_client.post('/api/submit', json={'question_id': java_qid, 'language': 'java', 'code': java_code, 'score': 10.0})
    assert r.status_code == 200
run_test("7. Multi-Language Runtime (C & Java Compilation & Verification)", t_c_and_java_execution)

# -------------------------------------------------------------
# 8. TEACHER LIVE CODE INSPECTION API & TIME-OVER EXIT
# -------------------------------------------------------------
def t_live_code_and_time_over():
    # Student exits with Time Over reason
    r = s_client.post('/api/exam/student-exit', json={'reason': 'Time Over - Exam Duration Ended'})
    assert r.status_code == 200

    # Teacher dashboard checks student and activity
    r = teacher_client.get('/api/dashboard')
    assert r.status_code == 200
    dash = r.get_json()
    s28 = next(s for s in dash if s['username'] == '28')
    assert s28['submissions'] >= 3, "Student should have submitted solutions"

    # Activity stream includes Time Over
    has_time_over = any('Time Over' in str(act.get('details', '')) for act in s28['activity'])
    assert has_time_over, "Time Over event must be recorded in student activity stream"

    # Teacher calls the live submissions inspection endpoint
    r = teacher_client.get(f"/api/teacher/student/{s28['id']}/submissions")
    assert r.status_code == 200
    subs_data = r.get_json()
    assert 'submissions' in subs_data
    assert len(subs_data['submissions']) >= 3, "Teacher must receive student submitted code"
    # Ensure titles and code content are present
    assert all('question_title' in sub and 'code' in sub for sub in subs_data['submissions'])
run_test("8. Teacher Live Code Inspection API & Time-Over Exit", t_live_code_and_time_over)

# -------------------------------------------------------------
# 9. MCQ EXAM LIFECYCLE (ADD, UPDATE, JOIN, SUBMIT & FINISH)
# -------------------------------------------------------------
mcq_id = None
mcq_access_code = None
def t_mcq_workflow():
    global mcq_id, mcq_access_code
    # Add MCQ
    r = teacher_client.post('/api/mcqs', json={
        'question': 'Which keyword is used to define a function in Python?',
        'option_a': 'func',
        'option_b': 'def',
        'option_c': 'function',
        'option_d': 'define',
        'correct_option': 'b',
        'marks': 2.0,
        'time_limit': 30
    })
    assert r.status_code == 200
    mcq_id = r.get_json()['id']

    # Update MCQ
    r = teacher_client.put(f'/api/mcqs/{mcq_id}', json={
        'question': 'Which keyword is used to define a function in Python?',
        'option_a': 'func',
        'option_b': 'def',
        'option_c': 'fn',
        'option_d': 'define',
        'correct_option': 'b',
        'marks': 3.0,
        'time_limit': 30
    })
    assert r.status_code == 200

    # Start MCQ Exam
    r = teacher_client.post('/api/exam/start_mcq', json={})
    assert r.status_code == 200
    mcq_access_code = r.get_json()['mcq_access_code']
    assert mcq_access_code is not None

    # Student joins MCQ exam
    c = a.conn()
    c.execute("UPDATE exam_access SET mcq_locked=0 WHERE user_id=11")
    c.commit(); c.close()

    r = s_client.post('/api/exam/join', json={'access_code': mcq_access_code})
    assert r.status_code == 200
    assert r.get_json()['exam_type'] == 'mcq'

    # Student lists MCQs
    r = s_client.get('/api/mcqs')
    assert r.status_code == 200
    mcqs = r.get_json()
    assert any(m['id'] == mcq_id for m in mcqs)

    # Student submits MCQ answer
    r = s_client.post('/api/mcqs/submit', json={'mcq_id': mcq_id, 'selected_option': 'b'})
    assert r.status_code == 200
    assert r.get_json()['is_correct'] == True

    # Student finishes MCQ
    r = s_client.post('/api/mcqs/finish')
    assert r.status_code == 200
run_test("9. MCQ Exam Lifecycle (Add, Update, Join, Submit & Finish)", t_mcq_workflow)

# -------------------------------------------------------------
# 10. CLEAR KEYS, LEADERBOARD EXPORT & PASSWORD CHANGE
# -------------------------------------------------------------
def t_clear_keys_and_extras():
    # Clear active exam keys
    r = teacher_client.post('/api/exam/clear_keys')
    assert r.status_code == 200
    st = teacher_client.get('/api/exam-state').get_json()
    assert st.get('access_code') is None
    assert st.get('mcq_access_code') is None

    # Change password
    r = teacher_client.post('/api/change_password', json={'password': 'NewTeacherPass123!'})
    assert r.status_code == 200
    # Reset back
    teacher_client.post('/api/change_password', json={'password': 'Teacher123!'})
# -------------------------------------------------------------
# 11. PLAGIARISM DETECTOR, SCORECARD & DUAL MONITOR ANTI-CHEAT
# -------------------------------------------------------------
def t_new_features():
    # 1. External Display detection in anti-cheat activity endpoint
    # Login as student 28 (password was reset to '2828' in test 2)
    s_client = a.app.test_client()
    login_res = s_client.post('/api/login', json={'username': '28', 'password': '2828'})
    assert login_res.status_code == 200, f"Student login failed: {login_res.get_json()}"
    r = s_client.post('/api/activity', json={
        'event': 'external_display_detected',
        'details': 'Secondary display / HDMI screen connected'
    })
    assert r.status_code == 200, f"External display activity logging failed: {r.status_code}"
    res = r.get_json()
    assert res.get('ok') is True, "Activity should return ok: True"

    # 2. Student Result / Scorecard endpoint
    r = s_client.get('/api/student/result')
    assert r.status_code == 200, f"Student scorecard endpoint failed: {r.status_code}"
    card = r.get_json()
    assert 'student' in card, "Scorecard must contain student profile"
    assert 'scores' in card, "Scorecard must contain scores breakdown"
    assert 'questions' in card, "Scorecard must contain questions breakdown"
    assert 'mcqs' in card, "Scorecard must contain mcq results"
    assert 'status' in card['scores'], "Scorecard scores must include status"

    # 3. Plagiarism API (Teacher access)
    r = teacher_client.get('/api/plagiarism')
    assert r.status_code == 200, f"Plagiarism API failed: {r.status_code}"
    plag = r.get_json()
    assert 'matches' in plag, "Plagiarism response must contain matches list"
    assert 'total_comparisons' in plag, "Plagiarism response must report total_comparisons"
    
    # Non-authorized role (student) should be blocked from plagiarism API
    r_blocked = s_client.get('/api/plagiarism')
    assert r_blocked.status_code in (401, 403), "Students must not access plagiarism reports"

run_test("11. Plagiarism Checker, Student Result Scorecard & Dual Monitor Anti-Cheat", t_new_features)

# -------------------------------------------------------------
# 12. CLEANUP TEMP TEST DATA
# -------------------------------------------------------------
def t_cleanup():
    c = a.conn()
    all_qids = [qid for qid in (t_qid, c_qid, java_qid) if qid]
    if all_qids:
        placeholders = ','.join('?' for _ in all_qids)
        c.execute(f"DELETE FROM test_cases WHERE question_id IN ({placeholders})", all_qids)
        c.execute(f"DELETE FROM submissions WHERE question_id IN ({placeholders})", all_qids)
        c.execute(f"DELETE FROM drafts WHERE question_id IN ({placeholders})", all_qids)
        c.execute(f"DELETE FROM questions WHERE id IN ({placeholders})", all_qids)
    if mcq_id:
        c.execute("DELETE FROM mcq_status WHERE mcq_id=?", (mcq_id,))
        c.execute("DELETE FROM mcqs WHERE id=?", (mcq_id,))
    c.commit(); c.close()
run_test("11. Cleanup Temporary Test Data", t_cleanup)

print("================================================================")
print(f"AUDIT COMPLETE: {passed_count} PASSED, {len(failed_tests)} FAILED")
print("================================================================")
if failed_tests:
    for name, err in failed_tests:
        print(f"  ❌ {name}: {err}")
    sys.exit(1)
else:
    print("ALL BUTTONS, ROUTES, ANTI-CHEAT & TAB SWITCH SYSTEMS 100% OPERATIONAL! 🚀")

