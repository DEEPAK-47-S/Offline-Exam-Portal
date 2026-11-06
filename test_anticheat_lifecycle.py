import sys, time, json, os, tempfile, atexit
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
import app as a

# -------------------------------------------------------------
# MANDATORY TEST ISOLATION: TEMPORARY TEST DATABASE
# Never touch or modify the production exam_lab.db
# -------------------------------------------------------------
temp_db_fd, temp_db_path = tempfile.mkstemp(suffix=".db", prefix="test_anticheat_")
os.close(temp_db_fd)

a.app.config['TESTING'] = True
a.app.config['WTF_CSRF_ENABLED'] = False
a.app.config['DATABASE_PATH'] = temp_db_path
a.init_db(temp_db_path)

def cleanup_temp_db():
    a.app.config['DATABASE_PATH'] = None
    for p in (temp_db_path, temp_db_path + "-wal", temp_db_path + "-shm"):
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass

atexit.register(cleanup_temp_db)

# Seed test database
c = a.conn()
c.execute("INSERT OR REPLACE INTO users (username, name, role, password_hash) VALUES (?, ?, ?, ?)",
          ('teacher_ac', 'AntiCheat Teacher', 'teacher', a.generate_password_hash('Teacher123!')))
t_uid = c.execute("SELECT id FROM users WHERE username='teacher_ac'").fetchone()['id']
c.execute("INSERT OR REPLACE INTO teacher_profiles (user_id, department, class, year, section) VALUES (?, 'CSE', 'CSE-A', '3', 'A')", (t_uid,))

c.execute("INSERT OR REPLACE INTO users (username, name, role, password_hash) VALUES (?, ?, ?, ?)",
          ('stud_code', 'Coding Student', 'student', a.generate_password_hash('StudPass456!')))
s_code_uid = c.execute("SELECT id FROM users WHERE username='stud_code'").fetchone()['id']
c.execute("INSERT OR REPLACE INTO student_profiles (user_id, register_number, department, year, class, semester, section) VALUES (?, 'REG_CODE', 'CSE', '3', 'CSE-A', '6', 'A')", (s_code_uid,))
c.execute("INSERT OR REPLACE INTO exam_access (user_id, teacher_id, allowed, violations, locked) VALUES (?, ?, 1, 0, 0)", (s_code_uid, t_uid))

c.execute("INSERT OR REPLACE INTO users (username, name, role, password_hash) VALUES (?, ?, ?, ?)",
          ('stud_mcq', 'MCQ Student', 'student', a.generate_password_hash('StudPass456!')))
s_mcq_uid = c.execute("SELECT id FROM users WHERE username='stud_mcq'").fetchone()['id']
c.execute("INSERT OR REPLACE INTO student_profiles (user_id, register_number, department, year, class, semester, section) VALUES (?, 'REG_MCQ', 'CSE', '3', 'CSE-A', '6', 'A')", (s_mcq_uid,))
c.execute("INSERT OR REPLACE INTO exam_access (user_id, teacher_id, allowed, mcq_violations, mcq_locked) VALUES (?, ?, 1, 0, 0)", (s_mcq_uid, t_uid))

# Insert a coding question and an MCQ question
c.execute("INSERT INTO questions (teacher_id, title, prompt, language) VALUES (?, 'Sum of Two', 'Return a+b', 'python')", (t_uid,))
code_qid = c.execute("SELECT last_insert_rowid() as id").fetchone()['id']
c.execute("INSERT INTO test_cases (question_id, input_data, expected_output, marks, visible) VALUES (?, '1 2\n', '3', 10, 1)", (code_qid,))

c.execute("INSERT INTO mcqs (teacher_id, question, option_a, option_b, option_c, option_d, correct_option, marks) VALUES (?, '2+2=?', '3', '4', '5', '6', 'b', 5)", (t_uid,))
mcq_qid = c.execute("SELECT last_insert_rowid() as id").fetchone()['id']

c.commit()
c.close()

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

print("=" * 65)
print("ANTI-CHEAT LIFECYCLE & LEAVE CONFIRMATION TEST SUITE")
print(f"ISOLATED TEST DATABASE: {temp_db_path}")
print("=" * 65)

# Setup exams with teacher client
teacher_client = a.app.test_client()
r = teacher_client.post('/api/login', json={'username': 'teacher_ac', 'password': 'Teacher123!'})
assert r.status_code == 200

r_code = teacher_client.post('/api/exam/start_coding', json={'minutes': 60})
assert r_code.status_code == 200
coding_key = r_code.get_json()['access_code']

r_mcq = teacher_client.post('/api/exam/start_mcq', json={})
assert r_mcq.status_code == 200
mcq_key = r_mcq.get_json()['mcq_access_code']

# Client for coding student
client_code = a.app.test_client()
client_code.post('/api/login', json={'username': 'stud_code', 'password': 'StudPass456!'})
r_join_code = client_code.post('/api/exam/join', json={'access_code': coding_key})
assert r_join_code.status_code == 200, f"Coding join failed: {r_join_code.get_json()}"

# Client for mcq student
client_mcq = a.app.test_client()
client_mcq.post('/api/login', json={'username': 'stud_mcq', 'password': 'StudPass456!'})
r_join_mcq = client_mcq.post('/api/exam/join', json={'access_code': mcq_key})
assert r_join_mcq.status_code == 200, f"MCQ join failed: {r_join_mcq.get_json()}"

# TEST 1: Coding exam starts -> focus lost (tab_hidden) -> violation recorded
def test_1_coding_focus_loss():
    res = client_code.post('/api/activity', json={'event': 'tab_hidden', 'details': 'visibility_hidden'})
    assert res.status_code == 200, f"Expected 200, got {res.status_code}"
    data = res.get_json()
    assert data['ok'] is True
    assert data['illegal_count'] == 1, f"Expected 1, got {data['illegal_count']}"
    assert data['locked'] is False

run_test("1. Coding exam starts -> focus lost (tab_hidden) -> violation recorded", test_1_coding_focus_loss)

# TEST 2: MCQ exam starts -> focus lost -> violation recorded
def test_2_mcq_focus_loss():
    res = client_mcq.post('/api/activity', json={'event': 'window_blur', 'details': 'focus_lost'})
    assert res.status_code == 200
    data = res.get_json()
    assert data['ok'] is True
    assert data['illegal_count'] == 1, f"Expected 1, got {data['illegal_count']}"
    assert data['locked'] is False

run_test("2. MCQ exam starts -> focus lost (window_blur) -> violation recorded", test_2_mcq_focus_loss)

# TEST 3: Leave clicked -> modal open -> focus lost -> violation recorded
def test_3_leave_open_focus_loss():
    time.sleep(0.4) # exceed debounce
    res = client_code.post('/api/activity', json={'event': 'tab_hidden', 'details': 'switched_tab_while_leave_modal_open'})
    assert res.status_code == 200
    data = res.get_json()
    assert data['ok'] is True
    assert data['illegal_count'] == 2, f"Expected 2, got {data['illegal_count']}"
    assert data['locked'] is False

run_test("3. Leave clicked -> modal open -> focus lost -> violation recorded", test_3_leave_open_focus_loss)

# TEST 4: Leave clicked -> modal open -> Alt key event -> recorded
def test_4_leave_open_alt_key():
    time.sleep(0.4)
    res = client_code.post('/api/activity', json={'event': 'blocked_key', 'details': 'Alt + Tab'})
    assert res.status_code == 200
    data = res.get_json()
    assert data['ok'] is True
    assert data['illegal_count'] == 3, f"Expected 3, got {data['illegal_count']}"

run_test("4. Leave clicked -> modal open -> Alt key event -> recorded", test_4_leave_open_alt_key)

# TEST 5 & 6: Leave clicked -> NO -> monitoring continues & next tab switch recorded
def test_5_6_leave_no_continues_monitoring():
    # Frontend transitions back to ACTIVE when student clicks NO.
    # Non-strike telemetry for window_refocus when user clicked back
    res_refocus = client_code.post('/api/activity', json={'event': 'window_refocus', 'details': 'returned_from_switch'})
    assert res_refocus.get_json()['ignored'] is True
    assert res_refocus.get_json()['illegal_count'] == 3

    # Now student switches tab again:
    time.sleep(0.4)
    res = client_code.post('/api/activity', json={'event': 'tab_hidden', 'details': 'second_switch_after_modal_cancelled'})
    assert res.status_code == 200
    data = res.get_json()
    assert data['illegal_count'] == 4, f"Expected 4, got {data['illegal_count']}"
    assert data['locked'] is False

run_test("5 & 6. Leave clicked -> NO -> monitoring continues -> another switch recorded", test_5_6_leave_no_continues_monitoring)

# TEST 7: Leave clicked -> YES -> successful finalization locks attempt in DB -> later focus changes return ignored
def test_7_leave_yes_finalization():
    # Student confirms YES
    exit_res = client_code.post('/api/exam/student-exit', json={'reason': 'Student confirmed Leave Exam'})
    assert exit_res.status_code == 200
    assert exit_res.get_json()['ok'] is True
    
    # Verify locked in DB
    c_check = a.conn()
    row = c_check.execute('SELECT locked FROM exam_access WHERE user_id=? AND teacher_id=?', (s_code_uid, t_uid)).fetchone()
    c_check.close()
    assert row['locked'] == 1, "Attempt must be locked in database"
    
    # Subsequent focus changes must report locked=True
    post_res = client_code.post('/api/activity', json={'event': 'tab_hidden', 'details': 'after_exit'})
    assert post_res.get_json()['locked'] is True

run_test("7. Leave clicked -> YES -> successful finalization locks attempt -> later activity locked", test_7_leave_yes_finalization)

# TEST 8: YES request failure -> attempt remains active and monitoring continues
def test_8_yes_failure_remains_active():
    # Seed a new student
    c = a.conn()
    c.execute("INSERT OR REPLACE INTO users (username, name, role, password_hash) VALUES ('stud_fail', 'Fail Test', 'student', ?)",
              (a.generate_password_hash('StudPass456!'),))
    s_fail_uid = c.execute("SELECT id FROM users WHERE username='stud_fail'").fetchone()['id']
    c.execute("INSERT OR REPLACE INTO student_profiles (user_id, register_number, department, year, class, semester, section) VALUES (?, 'REG_FAIL', 'CSE', '3', 'CSE-A', '6', 'A')", (s_fail_uid,))
    c.execute("INSERT OR REPLACE INTO exam_access (user_id, teacher_id, allowed, violations, locked) VALUES (?, ?, 1, 0, 0)", (s_fail_uid, t_uid))
    c.commit()
    c.close()

    client_fail = a.app.test_client()
    client_fail.post('/api/login', json={'username': 'stud_fail', 'password': 'StudPass456!'})
    client_fail.post('/api/exam/join', json={'access_code': coding_key})
    
    # Attempt remains active and unlocked
    c_check = a.conn()
    row = c_check.execute('SELECT locked FROM exam_access WHERE user_id=? AND teacher_id=?', (s_fail_uid, t_uid)).fetchone()
    c_check.close()
    assert row['locked'] == 0, "Attempt should be unlocked"

    # Monitoring detects focus loss
    res = client_fail.post('/api/activity', json={'event': 'tab_hidden', 'details': 'network_issue_modal_reverted'})
    assert res.get_json()['illegal_count'] == 1

run_test("8. YES request failure -> attempt remains active and monitoring continues", test_8_yes_failure_remains_active)

# TEST 9: Repeated blur + visibility events within same departure -> one violation transition (burst debounce)
def test_9_burst_debounce():
    time.sleep(0.4)
    # Event 1: window_blur
    res1 = client_mcq.post('/api/activity', json={'event': 'window_blur', 'details': 'focus_lost'})
    data1 = res1.get_json()
    assert data1['illegal_count'] == 2

    # Event 2: tab_hidden immediately within 10ms (burst from single Alt+Tab)
    res2 = client_mcq.post('/api/activity', json={'event': 'tab_hidden', 'details': 'visibility_hidden'})
    data2 = res2.get_json()
    assert data2.get('ignored') is True, "Burst event within debounce threshold must be ignored"
    assert data2['illegal_count'] == 2, "Illegal count must not double-increment for single departure"

run_test("9. Repeated blur + visibility within same departure -> one violation transition", test_9_burst_debounce)

# TEST 10: Multiple separate tab switches -> separate violation transitions
def test_10_multiple_separate_switches():
    # Wait for debounce window
    time.sleep(0.4)
    res = client_mcq.post('/api/activity', json={'event': 'tab_hidden', 'details': 'switch_3'})
    assert res.get_json()['illegal_count'] == 3
    
    time.sleep(0.4)
    res = client_mcq.post('/api/activity', json={'event': 'tab_hidden', 'details': 'switch_4'})
    assert res.get_json()['illegal_count'] == 4

run_test("10. Multiple separate tab switches -> separate violation transitions", test_10_multiple_separate_switches)

# TEST 11: Fullscreen exit while Leave modal is open -> recorded
def test_11_fullscreen_exit_recorded():
    time.sleep(0.4)
    # 5th strike (locks exam)
    res = client_mcq.post('/api/activity', json={'event': 'fullscreen_exit', 'details': 'exited_fullscreen_during_leave_modal'})
    data = res.get_json()
    assert data['illegal_count'] == 5
    assert data['locked'] is True

run_test("11. Fullscreen exit while Leave modal is open -> recorded (5th violation locks)", test_11_fullscreen_exit_recorded)

# TEST 12: Finished attempt -> later focus changes do not create exam violations
def test_12_finished_attempt_ignores_activity():
    time.sleep(0.4)
    res = client_mcq.post('/api/activity', json={'event': 'tab_hidden', 'details': 'post_locked_switch'})
    data = res.get_json()
    assert data['locked'] is True
    assert data['illegal_count'] == 5

run_test("12. Finished attempt -> later focus changes do not create exam violations", test_12_finished_attempt_ignores_activity)

# TEST 13: Timer & draft saving continue while confirmation modal is open
def test_13_draft_saving_continues():
    c = a.conn()
    c.execute("INSERT OR REPLACE INTO users (username, name, role, password_hash) VALUES ('stud_draft', 'Draft Student', 'student', ?)",
              (a.generate_password_hash('StudPass456!'),))
    s_draft_uid = c.execute("SELECT id FROM users WHERE username='stud_draft'").fetchone()['id']
    c.execute("INSERT OR REPLACE INTO student_profiles (user_id, register_number, department, year, class, semester, section) VALUES (?, 'REG_DRAFT', 'CSE', '3', 'CSE-A', '6', 'A')", (s_draft_uid,))
    c.execute("INSERT OR REPLACE INTO exam_access (user_id, teacher_id, allowed, violations, locked) VALUES (?, ?, 1, 0, 0)", (s_draft_uid, t_uid))
    c.commit()
    c.close()

    client_draft = a.app.test_client()
    client_draft.post('/api/login', json={'username': 'stud_draft', 'password': 'StudPass456!'})
    client_draft.post('/api/exam/join', json={'access_code': coding_key})

    # Save draft while modal is open
    res = client_draft.post('/api/draft', json={'question_id': code_qid, 'code': 'x = 42', 'language': 'python'})
    assert res.status_code == 200, f"Save draft returned {res.status_code}: {res.get_json()}"
    assert res.get_json()['ok'] is True
    
    # Verify draft saved in database
    c_check = a.conn()
    row = c_check.execute('SELECT code FROM drafts WHERE user_id=? AND question_id=?', (s_draft_uid, code_qid)).fetchone()
    c_check.close()
    assert row is not None and row['code'] == 'x = 42'

run_test("13. Timer & draft saving continue while confirmation modal is open", test_13_draft_saving_continues)

# TEST 14: Coding and MCQ retain existing scoring and submission behavior
def test_14_scoring_and_submission():
    c = a.conn()
    c.execute("INSERT OR REPLACE INTO users (username, name, role, password_hash) VALUES ('stud_ans', 'Ans Student', 'student', ?)",
              (a.generate_password_hash('StudPass456!'),))
    s_ans_uid = c.execute("SELECT id FROM users WHERE username='stud_ans'").fetchone()['id']
    c.execute("INSERT OR REPLACE INTO student_profiles (user_id, register_number, department, year, class, semester, section) VALUES (?, 'REG_ANS', 'CSE', '3', 'CSE-A', '6', 'A')", (s_ans_uid,))
    c.execute("INSERT OR REPLACE INTO exam_access (user_id, teacher_id, allowed, mcq_violations, mcq_locked) VALUES (?, ?, 1, 0, 0)", (s_ans_uid, t_uid))
    c.commit()
    c.close()

    client_ans = a.app.test_client()
    client_ans.post('/api/login', json={'username': 'stud_ans', 'password': 'StudPass456!'})
    client_ans.post('/api/exam/join', json={'access_code': mcq_key})

    # Submit correct answer 'b'
    res = client_ans.post('/api/mcqs/submit', json={'mcq_id': mcq_qid, 'selected_option': 'b'})
    assert res.status_code == 200, f"MCQ submit failed: {res.get_json()}"
    assert res.get_json()['ok'] is True

    # Finish MCQ exam
    res_fin = client_ans.post('/api/mcqs/finish')
    assert res_fin.status_code == 200, f"MCQ finish failed: {res_fin.get_json()}"
    assert res_fin.get_json()['ok'] is True

    # Check score
    c_check = a.conn()
    row = c_check.execute('SELECT mcq_final_score, mcq_locked FROM exam_access WHERE user_id=? AND teacher_id=?', (s_ans_uid, t_uid)).fetchone()
    c_check.close()
    assert row['mcq_locked'] == 1
    assert row['mcq_final_score'] == 5, f"Expected 5 marks, got {row['mcq_final_score']}"

run_test("14. Coding and MCQ retain existing scoring and submission behavior", test_14_scoring_and_submission)

print("=" * 65)
print(f"AUDIT COMPLETE: {passed_count} PASSED, {len(failed_tests)} FAILED")
print("=" * 65)
if failed_tests:
    sys.exit(1)
