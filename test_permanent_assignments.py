"""
Permanent Student-Based Question Assignment System - Verification Suite
Validates all 29 core scenarios from Phase 9 of the Permanent Assignment Specification.
Tests execute in an isolated temporary SQLite database without touching production exam_lab.db.
"""
import unittest
import json
import time
import os
import sys
import sqlite3
import tempfile

# Force utf-8 stdout for Windows consoles
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

import app as application
from app import app, conn, init_db, migrate_to_permanent_profiles, get_effective_assignment, is_question_authorized

class TestPermanentAssignmentsSuite(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_db_fd, cls.temp_db_path = tempfile.mkstemp(suffix=".db", prefix="test_perm_")
        os.close(cls.temp_db_fd)
        app.config['TESTING'] = True
        app.config['WTF_CSRF_ENABLED'] = False
        app.config['DATABASE_PATH'] = cls.temp_db_path
        init_db(cls.temp_db_path)

    @classmethod
    def tearDownClass(cls):
        app.config['DATABASE_PATH'] = None
        for p in (cls.temp_db_path, cls.temp_db_path + "-wal", cls.temp_db_path + "-shm"):
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass

    def setUp(self):
        self.client = app.test_client()
        c = conn()
        # Clean test records
        c.execute("DELETE FROM submissions WHERE user_id IN (SELECT id FROM users WHERE username LIKE 'p_test_%')")
        c.execute("DELETE FROM drafts WHERE user_id IN (SELECT id FROM users WHERE username LIKE 'p_test_%')")
        c.execute("DELETE FROM activity WHERE user_id IN (SELECT id FROM users WHERE username LIKE 'p_test_%')")
        c.execute("DELETE FROM exam_access WHERE user_id IN (SELECT id FROM users WHERE username LIKE 'p_test_%')")
        c.execute("DELETE FROM mcq_status WHERE student_id IN (SELECT id FROM users WHERE username LIKE 'p_test_%')")
        c.execute("DELETE FROM student_question_profile_items")
        c.execute("DELETE FROM student_question_profiles")
        c.execute("DELETE FROM exam_attempt_snapshots")
        c.execute("DELETE FROM student_question_assignments")
        c.execute("DELETE FROM test_cases WHERE question_id IN (SELECT id FROM questions WHERE prompt LIKE '%perm_test%')")
        c.execute("DELETE FROM questions WHERE prompt LIKE '%perm_test%'")
        c.execute("DELETE FROM mcqs WHERE question LIKE '%perm_test%'")
        c.execute("DELETE FROM users WHERE username LIKE 'p_test_%'")
        c.commit()
        c.close()

    def create_user(self, username, name, role, password="Password123!"):
        c = conn()
        p_hash = application.generate_password_hash(password)
        c.execute("INSERT OR REPLACE INTO users (username, name, role, password_hash) VALUES (?, ?, ?, ?)",
                  (username, name, role, p_hash))
        uid = c.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()['id']
        if role == 'student':
            c.execute("INSERT OR REPLACE INTO student_profiles (user_id, register_number, department, section, year) VALUES (?, ?, 'CSE', 'A', '3')",
                      (uid, username.upper()))
        c.commit()
        c.close()
        return uid

    def login(self, username, password="Password123!"):
        res = self.client.post('/api/login', json={'username': username, 'password': password})
        self.assertEqual(res.status_code, 200, f"Login failed for {username}: {res.get_json()}")
        return res

    def logout(self):
        self.client.post('/api/logout')

    def create_coding_question(self, teacher_id, title_num):
        c = conn()
        cur = c.execute(
            "INSERT INTO questions (teacher_id, title, prompt, language, active) VALUES (?, ?, ?, 'python', 1)",
            (teacher_id, f"Coding Title {title_num}", f"perm_test prompt {title_num}")
        )
        qid = cur.lastrowid
        c.execute("INSERT INTO test_cases (question_id, input_data, expected_output, marks, visible) VALUES (?, '1', '1', 10, 1)", (qid,))
        c.commit()
        c.close()
        return qid

    def create_mcq_question(self, teacher_id, title_num):
        c = conn()
        cur = c.execute(
            "INSERT INTO mcqs (teacher_id, question, option_a, option_b, option_c, option_d, correct_option, marks, time_limit) VALUES (?, ?, 'A', 'B', 'C', 'D', 'a', 2, 30)",
            (teacher_id, f"perm_test MCQ Question {title_num}")
        )
        mid = cur.lastrowid
        c.commit()
        c.close()
        return mid

    # =========================================================================
    # SCENARIOS 1, 2, 3: Q1 to Student 1, 10 room keys, persists every time
    # =========================================================================
    def test_scenarios_01_02_03_room_key_persistence(self):
        print("\n--- Testing Scenarios 1, 2, 3: Q1 to S1 across 10 room keys ---")
        t_id = self.create_user("p_test_t1", "Teacher One", "teacher")
        s1_id = self.create_user("p_test_s1", "Student One", "student")

        q1 = self.create_coding_question(t_id, 1)
        q2 = self.create_coding_question(t_id, 2)
        q3 = self.create_coding_question(t_id, 3)

        c = conn()
        c.execute("INSERT OR REPLACE INTO exam_access (user_id, teacher_id, allowed) VALUES (?, ?, 1)", (s1_id, t_id))
        c.commit(); c.close()

        # Scenario 1: Assign Q1 to Student 1
        self.login("p_test_t1")
        res = self.client.post('/api/permanent-assignments/bulk', json={
            'exam_type': 'coding',
            'student_ids': [s1_id],
            'question_ids': [q1]
        })
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.get_json().get('ok'))

        # Scenario 2 & 3: Create 10 different room keys and verify S1 receives Q1 every time
        for i in range(1, 11):
            key = f"ROOM_{i:04d}"
            res_start = self.client.post('/api/exam/start_coding', json={'access_code': key, 'minutes': 60})
            self.assertEqual(res_start.status_code, 200)
            self.assertEqual(res_start.get_json().get('access_code'), key)

            # Check effective assignment directly via get_effective_assignment
            c = conn()
            state, qids, _ = get_effective_assignment(c, s1_id, t_id, 'coding', key)
            c.close()
            self.assertEqual(state, 'INDIVIDUAL', f"Iteration {i}: state was {state}")
            self.assertEqual(qids, [q1], f"Iteration {i}: questions were {qids}")

            # Verify student join and question API
            self.login("p_test_s1")
            j_res = self.client.post('/api/exam/join', json={'access_code': key})
            self.assertEqual(j_res.status_code, 200)
            q_res = self.client.get('/api/questions')
            self.assertEqual(q_res.status_code, 200)
            q_data = q_res.get_json()
            self.assertEqual(len(q_data), 1)
            self.assertEqual(q_data[0]['id'], q1)
            self.login("p_test_t1")

        print("  ✓ Scenarios 1, 2, 3 passed successfully.")

    # =========================================================================
    # SCENARIOS 4, 5, 6: Assign Q2 to Student 2, create room key, S2 gets Q2
    # =========================================================================
    def test_scenarios_04_05_06_second_student_assignment(self):
        print("\n--- Testing Scenarios 4, 5, 6: Assign Q2 to S2 and verify ---")
        t_id = self.create_user("p_test_t2", "Teacher Two", "teacher")
        s1_id = self.create_user("p_test_s1b", "Student 1B", "student")
        s2_id = self.create_user("p_test_s2b", "Student 2B", "student")

        q1 = self.create_coding_question(t_id, 1)
        q2 = self.create_coding_question(t_id, 2)
        q3 = self.create_coding_question(t_id, 3)

        c = conn()
        c.execute("INSERT OR REPLACE INTO exam_access (user_id, teacher_id, allowed) VALUES (?, ?, 1)", (s1_id, t_id))
        c.execute("INSERT OR REPLACE INTO exam_access (user_id, teacher_id, allowed) VALUES (?, ?, 1)", (s2_id, t_id))
        c.commit(); c.close()

        self.login("p_test_t2")
        # Assign Q1 to S1, Q2 to S2
        self.client.post('/api/permanent-assignments/bulk', json={'exam_type': 'coding', 'student_ids': [s1_id], 'question_ids': [q1]})
        res = self.client.post('/api/permanent-assignments/bulk', json={'exam_type': 'coding', 'student_ids': [s2_id], 'question_ids': [q2]})
        self.assertEqual(res.status_code, 200)

        # Create another room key
        key = "ROOM_NEWKEY_456"
        self.client.post('/api/exam/start_coding', json={'access_code': key, 'minutes': 60})

        # S2 joins and gets Q2
        self.login("p_test_s2b")
        self.client.post('/api/exam/join', json={'access_code': key})
        q_res = self.client.get('/api/questions')
        self.assertEqual(q_res.status_code, 200)
        q_data = q_res.get_json()
        self.assertEqual([x['id'] for x in q_data], [q2])

        # S1 joins and still gets Q1
        self.login("p_test_s1b")
        self.client.post('/api/exam/join', json={'access_code': key})
        q_res1 = self.client.get('/api/questions')
        self.assertEqual(q_res1.status_code, 200)
        self.assertEqual([x['id'] for x in q_res1.get_json()], [q1])

        print("  ✓ Scenarios 4, 5, 6 passed successfully.")

    # =========================================================================
    # SCENARIOS 7 & 8: Assign different questions to 10 students; 10 remain independent
    # =========================================================================
    def test_scenarios_07_08_ten_independent_students(self):
        print("\n--- Testing Scenarios 7 & 8: 10 students with 10 independent questions ---")
        t_id = self.create_user("p_test_t3", "Teacher Three", "teacher")
        students = [self.create_user(f"p_test_stud_{i}", f"Student {i}", "student") for i in range(1, 11)]
        questions = [self.create_coding_question(t_id, i) for i in range(1, 11)]

        self.login("p_test_t3")
        # Distribute 1-to-1: each student gets their own question
        res = self.client.post('/api/permanent-assignments/distribute', json={
            'exam_type': 'coding',
            'student_ids': students,
            'question_ids': questions
        })
        self.assertEqual(res.status_code, 200)

        # Verify all 10 assignments remain strictly independent
        c = conn()
        for idx, sid in enumerate(students):
            expected_qid = questions[idx]
            state, qids, _ = get_effective_assignment(c, sid, t_id, 'coding')
            self.assertEqual(state, 'INDIVIDUAL')
            self.assertEqual(qids, [expected_qid], f"Student {idx+1} expected {expected_qid} but got {qids}")
        c.close()

        print("  ✓ Scenarios 7 & 8 passed successfully.")

    # =========================================================================
    # SCENARIOS 9 & 10: Assign Q1, Q2, Q3 to 10 students using bulk mode
    # =========================================================================
    def test_scenarios_09_10_bulk_assignment_same_set(self):
        print("\n--- Testing Scenarios 9 & 10: Bulk assign Q1, Q2, Q3 to 10 students ---")
        t_id = self.create_user("p_test_t4", "Teacher Four", "teacher")
        students = [self.create_user(f"p_test_bulk_{i}", f"Bulk Student {i}", "student") for i in range(1, 11)]
        q_ids = [self.create_coding_question(t_id, i) for i in range(1, 6)]
        selected_3 = [q_ids[0], q_ids[1], q_ids[2]]

        self.login("p_test_t4")
        res = self.client.post('/api/permanent-assignments/bulk', json={
            'exam_type': 'coding',
            'student_ids': students,
            'question_ids': selected_3
        })
        self.assertEqual(res.status_code, 200)

        # Scenario 10: Verify every selected student receives the exact same set in order
        c = conn()
        for sid in students:
            state, qids, _ = get_effective_assignment(c, sid, t_id, 'coding')
            self.assertEqual(state, 'INDIVIDUAL')
            self.assertEqual(qids, selected_3)
        c.close()

        print("  ✓ Scenarios 9 & 10 passed successfully.")

    # =========================================================================
    # SCENARIO 11: Verify different assignments coexist for different students
    # =========================================================================
    def test_scenario_11_coexistence_modes(self):
        print("\n--- Testing Scenario 11: Coexistence of different assignments ---")
        t_id = self.create_user("p_test_t5", "Teacher Five", "teacher")
        sA = self.create_user("p_test_coex_a", "Student A", "student")
        sB = self.create_user("p_test_coex_b", "Student B", "student")
        sC = self.create_user("p_test_coex_c", "Student C", "student")

        q1 = self.create_coding_question(t_id, 1)
        q2 = self.create_coding_question(t_id, 2)
        q3 = self.create_coding_question(t_id, 3)
        q4 = self.create_coding_question(t_id, 4)

        self.login("p_test_t5")
        # S_A gets [q1, q2]
        self.client.post('/api/permanent-assignments/bulk', json={'exam_type': 'coding', 'student_ids': [sA], 'question_ids': [q1, q2]})
        # S_B gets [q3, q4]
        self.client.post('/api/permanent-assignments/bulk', json={'exam_type': 'coding', 'student_ids': [sB], 'question_ids': [q3, q4]})
        # S_C remains untouched (COMMON)

        c = conn()
        stateA, qidsA, _ = get_effective_assignment(c, sA, t_id, 'coding')
        stateB, qidsB, _ = get_effective_assignment(c, sB, t_id, 'coding')
        stateC, qidsC, _ = get_effective_assignment(c, sC, t_id, 'coding')
        c.close()

        self.assertEqual((stateA, qidsA), ('INDIVIDUAL', [q1, q2]))
        self.assertEqual((stateB, qidsB), ('INDIVIDUAL', [q3, q4]))
        self.assertEqual((stateC, qidsC), ('COMMON', None))

        print("  ✓ Scenario 11 passed successfully.")

    # =========================================================================
    # SCENARIO 12: Verify Coding and MCQ assignments remain independent
    # =========================================================================
    def test_scenario_12_coding_and_mcq_independence(self):
        print("\n--- Testing Scenario 12: Coding and MCQ assignment independence ---")
        t_id = self.create_user("p_test_t6", "Teacher Six", "teacher")
        s1 = self.create_user("p_test_indep_s1", "Student Indep", "student")

        cq1 = self.create_coding_question(t_id, 1)
        cq2 = self.create_coding_question(t_id, 2)
        mq1 = self.create_mcq_question(t_id, 1)
        mq2 = self.create_mcq_question(t_id, 2)
        mq3 = self.create_mcq_question(t_id, 3)

        self.login("p_test_t6")
        # Coding gets [cq1], MCQ gets [mq2, mq3]
        self.client.post('/api/permanent-assignments/bulk', json={'exam_type': 'coding', 'student_ids': [s1], 'question_ids': [cq1]})
        self.client.post('/api/permanent-assignments/bulk', json={'exam_type': 'mcq', 'student_ids': [s1], 'question_ids': [mq2, mq3]})

        c = conn()
        c_state, c_qids, _ = get_effective_assignment(c, s1, t_id, 'coding')
        m_state, m_qids, _ = get_effective_assignment(c, s1, t_id, 'mcq')
        self.assertEqual((c_state, c_qids), ('INDIVIDUAL', [cq1]))
        self.assertEqual((m_state, m_qids), ('INDIVIDUAL', [mq2, mq3]))

        # Reset Coding to COMMON; verify MCQ remains untouched
        self.client.post('/api/permanent-assignments/bulk-reset', json={'exam_type': 'coding', 'student_ids': [s1]})
        c_state2, c_qids2, _ = get_effective_assignment(c, s1, t_id, 'coding')
        m_state2, m_qids2, _ = get_effective_assignment(c, s1, t_id, 'mcq')
        c.close()

        self.assertEqual(c_state2, 'COMMON')
        self.assertEqual((m_state2, m_qids2), ('INDIVIDUAL', [mq2, mq3]))

        print("  ✓ Scenario 12 passed successfully.")

    # =========================================================================
    # SCENARIOS 13, 14, 15: Edit profile Q1->Q5, active attempt uses snapshot, next exam receives Q5
    # =========================================================================
    def test_scenarios_13_14_15_active_attempt_snapshot_freeze(self):
        print("\n--- Testing Scenarios 13, 14, 15: Freeze active attempt snapshot & update next exam ---")
        t_id = self.create_user("p_test_t7", "Teacher Seven", "teacher")
        s1 = self.create_user("p_test_snap_s1", "Student Snap", "student")

        q1 = self.create_coding_question(t_id, 1)
        q2 = self.create_coding_question(t_id, 2)
        q5 = self.create_coding_question(t_id, 5)

        c = conn()
        c.execute("INSERT OR REPLACE INTO exam_access (user_id, teacher_id, allowed) VALUES (?, ?, 1)", (s1, t_id))
        c.commit(); c.close()

        self.login("p_test_t7")
        # Assign Q1
        self.client.post('/api/permanent-assignments/bulk', json={'exam_type': 'coding', 'student_ids': [s1], 'question_ids': [q1]})

        # Start exam 1
        key1 = "ROOM_SNAP_1"
        self.client.post('/api/exam/start_coding', json={'access_code': key1, 'minutes': 60})

        # S1 joins exam 1 -> snapshots Q1
        self.login("p_test_snap_s1")
        self.client.post('/api/exam/join', json={'access_code': key1})
        q_res = self.client.get('/api/questions')
        self.assertEqual([x['id'] for x in q_res.get_json()], [q1])

        # Scenario 13: Teacher updates S1 profile to Q5 mid-exam
        self.login("p_test_t7")
        self.client.post('/api/permanent-assignments/bulk', json={'exam_type': 'coding', 'student_ids': [s1], 'question_ids': [q5]})

        # Scenario 15: S1 is still inside active exam 1. Questions MUST remain Q1!
        self.login("p_test_snap_s1")
        q_active_res = self.client.get('/api/questions')
        self.assertEqual([x['id'] for x in q_active_res.get_json()], [q1], "Active exam must NOT be corrupted by mid-exam edits!")

        # Scenario 14: Finish exam 1, start exam 2. Next exam MUST receive Q5!
        self.client.post('/api/exam/leave', json={'reason': 'finished'})
        self.login("p_test_t7")
        key2 = "ROOM_SNAP_2"
        self.client.post('/api/exam/start_coding', json={'access_code': key2, 'minutes': 60})

        self.login("p_test_snap_s1")
        self.client.post('/api/exam/join', json={'access_code': key2})
        q_next_res = self.client.get('/api/questions')
        self.assertEqual([x['id'] for x in q_next_res.get_json()], [q5], "Next exam must snapshot the latest profile Q5!")

        print("  ✓ Scenarios 13, 14, 15 passed successfully.")

    # =========================================================================
    # SCENARIOS 16, 17, 18: Reset one student / selected group to COMMON mode
    # =========================================================================
    def test_scenarios_16_17_18_reset_isolation(self):
        print("\n--- Testing Scenarios 16, 17, 18: Reset isolation to COMMON mode ---")
        t_id = self.create_user("p_test_t8", "Teacher Eight", "teacher")
        s1 = self.create_user("p_test_rst_s1", "Student R1", "student")
        s2 = self.create_user("p_test_rst_s2", "Student R2", "student")
        s3 = self.create_user("p_test_rst_s3", "Student R3", "student")

        q1 = self.create_coding_question(t_id, 1)
        q2 = self.create_coding_question(t_id, 2)
        q3 = self.create_coding_question(t_id, 3)

        self.login("p_test_t8")
        self.client.post('/api/permanent-assignments/bulk', json={'exam_type': 'coding', 'student_ids': [s1], 'question_ids': [q1]})
        self.client.post('/api/permanent-assignments/bulk', json={'exam_type': 'coding', 'student_ids': [s2], 'question_ids': [q2]})
        self.client.post('/api/permanent-assignments/bulk', json={'exam_type': 'coding', 'student_ids': [s3], 'question_ids': [q3]})

        # Scenario 16 & 17: Reset S1 to COMMON. Verify S2 and S3 are untouched.
        res = self.client.post('/api/permanent-assignments/bulk-reset', json={'exam_type': 'coding', 'student_ids': [s1]})
        self.assertEqual(res.status_code, 200)

        c = conn()
        st1, _, _ = get_effective_assignment(c, s1, t_id, 'coding')
        st2, qids2, _ = get_effective_assignment(c, s2, t_id, 'coding')
        st3, qids3, _ = get_effective_assignment(c, s3, t_id, 'coding')
        self.assertEqual(st1, 'COMMON')
        self.assertEqual((st2, qids2), ('INDIVIDUAL', [q2]))
        self.assertEqual((st3, qids3), ('INDIVIDUAL', [q3]))

        # Scenario 18: Reset S2 only; verify S3 still remains INDIVIDUAL [q3]
        self.client.post('/api/permanent-assignments/bulk-reset', json={'exam_type': 'coding', 'student_ids': [s2]})
        st2_after, _, _ = get_effective_assignment(c, s2, t_id, 'coding')
        st3_after, qids3_after, _ = get_effective_assignment(c, s3, t_id, 'coding')
        c.close()

        self.assertEqual(st2_after, 'COMMON')
        self.assertEqual((st3_after, qids3_after), ('INDIVIDUAL', [q3]))

        print("  ✓ Scenarios 16, 17, 18 passed successfully.")

    # =========================================================================
    # SCENARIOS 19, 20, 21: Room key generation, exam shutdown, restart persistence
    # =========================================================================
    def test_scenarios_19_20_21_persistence_across_lifecycle_events(self):
        print("\n--- Testing Scenarios 19, 20, 21: Persistence across clear keys, shutdown, and restart ---")
        t_id = self.create_user("p_test_t9", "Teacher Nine", "teacher")
        s1 = self.create_user("p_test_perm_s1", "Student P1", "student")
        q1 = self.create_coding_question(t_id, 1)

        self.login("p_test_t9")
        self.client.post('/api/permanent-assignments/bulk', json={'exam_type': 'coding', 'student_ids': [s1], 'question_ids': [q1]})

        # Scenario 19: New room key generation and clear_keys
        self.client.post('/api/exam/start_coding', json={'access_code': 'K111', 'minutes': 30})
        self.client.post('/api/exam/clear_keys')
        self.client.post('/api/exam/start_coding', json={'access_code': 'K222', 'minutes': 30})

        c = conn()
        st, qids, _ = get_effective_assignment(c, s1, t_id, 'coding')
        c.close()
        self.assertEqual((st, qids), ('INDIVIDUAL', [q1]), "Clear keys must NEVER clear permanent profiles!")

        # Scenario 20: Exam shutdown
        self.client.post('/api/exam/stop')
        c = conn()
        st, qids, _ = get_effective_assignment(c, s1, t_id, 'coding')
        c.close()
        self.assertEqual((st, qids), ('INDIVIDUAL', [q1]), "Exam stop must NEVER clear permanent profiles!")

        # Scenario 21: Application restart simulation
        init_db(self.temp_db_path)
        c = conn()
        prof = c.execute("SELECT mode FROM student_question_profiles WHERE teacher_id=? AND student_id=? AND exam_type='coding'", (t_id, s1)).fetchone()
        items = c.execute("SELECT question_id FROM student_question_profile_items WHERE profile_id=(SELECT id FROM student_question_profiles WHERE teacher_id=? AND student_id=? AND exam_type='coding')", (t_id, s1)).fetchall()
        c.close()
        self.assertIsNotNone(prof)
        self.assertEqual(prof['mode'], 'INDIVIDUAL')
        self.assertEqual([r['question_id'] for r in items], [q1], "Application restart must preserve permanent profiles!")

        print("  ✓ Scenarios 19, 20, 21 passed successfully.")

    # =========================================================================
    # SCENARIOS 22 & 23: Invalid assignments never expose COMMON; access security
    # =========================================================================
    def test_scenarios_22_23_invalid_assignment_and_access_security(self):
        print("\n--- Testing Scenarios 22 & 23: Invalid assignment state & access authorization ---")
        t_id = self.create_user("p_test_t10", "Teacher Ten", "teacher")
        s1 = self.create_user("p_test_sec_s1", "Student Sec1", "student")
        s2 = self.create_user("p_test_sec_s2", "Student Sec2", "student")

        q1 = self.create_coding_question(t_id, 1)
        q2 = self.create_coding_question(t_id, 2)
        q3 = self.create_coding_question(t_id, 3)

        c = conn()
        c.execute("INSERT OR REPLACE INTO exam_access (user_id, teacher_id, allowed) VALUES (?, ?, 1)", (s1, t_id))
        c.execute("INSERT OR REPLACE INTO exam_access (user_id, teacher_id, allowed) VALUES (?, ?, 1)", (s2, t_id))
        c.commit(); c.close()

        self.login("p_test_t10")
        self.client.post('/api/permanent-assignments/bulk', json={'exam_type': 'coding', 'student_ids': [s1], 'question_ids': [q1]})
        self.client.post('/api/permanent-assignments/bulk', json={'exam_type': 'coding', 'student_ids': [s2], 'question_ids': [q2]})

        # Scenario 23: Verify student cannot access another student's assigned question
        c = conn()
        self.assertTrue(is_question_authorized(c, s1, t_id, 'coding', q1))
        self.assertFalse(is_question_authorized(c, s1, t_id, 'coding', q2), "S1 must NOT be authorized for Q2!")
        self.assertTrue(is_question_authorized(c, s2, t_id, 'coding', q2))
        self.assertFalse(is_question_authorized(c, s2, t_id, 'coding', q1), "S2 must NOT be authorized for Q1!")
        c.close()

        # Scenario 22: Invalidate S1's assignment by deactivating Q1
        c = conn()
        c.execute("UPDATE questions SET active=0 WHERE id=?", (q1,))
        c.commit()

        st_inv, qids_inv, _ = get_effective_assignment(c, s1, t_id, 'coding')
        c.close()
        self.assertEqual(st_inv, 'INVALID', "Inactive question must cause INVALID state!")
        self.assertIsNone(qids_inv)

        # In INVALID state, student must be refused access and NEVER receive common questions
        key = "ROOM_SEC_1"
        self.login("p_test_t10")
        self.client.post('/api/exam/start_coding', json={'access_code': key, 'minutes': 30})

        self.login("p_test_sec_s1")
        join_res = self.client.post('/api/exam/join', json={'access_code': key})
        self.assertEqual(join_res.status_code, 400)
        self.assertEqual(join_res.get_json().get('assignment_state'), 'INVALID')

        print("  ✓ Scenarios 22 & 23 passed successfully.")

    # =========================================================================
    # SCENARIO 24: Cross-teacher edit authorization
    # =========================================================================
    def test_scenario_24_cross_teacher_isolation(self):
        print("\n--- Testing Scenario 24: Teacher cross-ownership isolation ---")
        t1 = self.create_user("p_test_t11_a", "Teacher 11A", "teacher")
        t2 = self.create_user("p_test_t11_b", "Teacher 11B", "teacher")
        s1 = self.create_user("p_test_cross_s1", "Student Cross", "student")

        q_t1 = self.create_coding_question(t1, 1)

        # Teacher 2 attempts to assign Teacher 1's question
        self.login("p_test_t11_b")
        res = self.client.post('/api/permanent-assignments/bulk', json={
            'exam_type': 'coding',
            'student_ids': [s1],
            'question_ids': [q_t1]
        })
        self.assertEqual(res.status_code, 400, "Teacher 2 cannot assign Teacher 1's questions!")

        # Teacher 2 attempts to specify Teacher 1's ID
        res2 = self.client.post('/api/permanent-assignments/bulk', json={
            'teacher_id': t1,
            'exam_type': 'coding',
            'student_ids': [s1],
            'question_ids': [q_t1]
        })
        self.assertEqual(res2.status_code, 403, "Teacher 2 cannot impersonate Teacher 1!")

        print("  ✓ Scenario 24 passed successfully.")

    # =========================================================================
    # SCENARIOS 25 & 26: Migration preserves old assignments & is idempotent
    # =========================================================================
    def test_scenarios_25_26_migration_preservation_and_idempotency(self):
        print("\n--- Testing Scenarios 25 & 26: Migration preservation and idempotency ---")
        t_id = self.create_user("p_test_t12", "Teacher Twelve", "teacher")
        s1 = self.create_user("p_test_mig_s1", "Student Mig1", "student")
        q1 = self.create_coding_question(t_id, 1)
        q2 = self.create_coding_question(t_id, 2)

        c = conn()
        # Seed legacy assignment
        c.execute('''
            INSERT INTO student_question_assignments
                (exam_key, teacher_id, exam_type, student_id, assigned_question_ids, created_by)
            VALUES ('OLD_ROOM_KEY', ?, 'coding', ?, ?, ?)
        ''', (t_id, s1, json.dumps([q1, q2]), t_id))
        c.commit()

        # Run migration
        migrate_to_permanent_profiles(c)
        c.commit()

        # Verify Scenario 25: Old assignment preserved in permanent profiles
        prof = c.execute("SELECT id, mode, version FROM student_question_profiles WHERE teacher_id=? AND student_id=? AND exam_type='coding'", (t_id, s1)).fetchone()
        self.assertIsNotNone(prof)
        self.assertEqual(prof['mode'], 'INDIVIDUAL')
        items = c.execute("SELECT question_id FROM student_question_profile_items WHERE profile_id=? ORDER BY sort_order", (prof['id'],)).fetchall()
        self.assertEqual([r['question_id'] for r in items], [q1, q2])

        # Run migration again (Scenario 26: Idempotent and does not duplicate)
        migrate_to_permanent_profiles(c)
        c.commit()

        count_profs = c.execute("SELECT COUNT(*) FROM student_question_profiles WHERE teacher_id=? AND student_id=? AND exam_type='coding'", (t_id, s1)).fetchone()[0]
        count_items = c.execute("SELECT COUNT(*) FROM student_question_profile_items WHERE profile_id=?", (prof['id'],)).fetchone()[0]
        c.close()

        self.assertEqual(count_profs, 1, "Migration must not duplicate profiles!")
        self.assertEqual(count_items, 2, "Migration must not duplicate items!")

        print("  ✓ Scenarios 25 & 26 passed successfully.")

    # =========================================================================
    # SCENARIO 27: Bulk operation atomicity
    # =========================================================================
    def test_scenario_27_bulk_atomicity_on_failure(self):
        print("\n--- Testing Scenario 27: Bulk operation atomicity on failure ---")
        t_id = self.create_user("p_test_t13", "Teacher Thirteen", "teacher")
        s1 = self.create_user("p_test_atom_s1", "Student Atom1", "student")
        s2 = self.create_user("p_test_atom_s2", "Student Atom2", "student")
        q1 = self.create_coding_question(t_id, 1)

        self.login("p_test_t13")
        # Attempt bulk assign with an invalid question ID (999999)
        res = self.client.post('/api/permanent-assignments/bulk', json={
            'exam_type': 'coding',
            'student_ids': [s1, s2],
            'question_ids': [q1, 999999]
        })
        self.assertEqual(res.status_code, 400)

        # Verify nothing was saved for either student
        c = conn()
        p1 = c.execute("SELECT * FROM student_question_profiles WHERE teacher_id=? AND student_id=?", (t_id, s1)).fetchone()
        p2 = c.execute("SELECT * FROM student_question_profiles WHERE teacher_id=? AND student_id=?", (t_id, s2)).fetchone()
        c.close()
        self.assertIsNone(p1, "Student 1 profile must not be created when transaction fails!")
        self.assertIsNone(p2, "Student 2 profile must not be created when transaction fails!")

        print("  ✓ Scenario 27 passed successfully.")

    # =========================================================================
    # SCENARIOS 28 & 29: Snapshot reuse on reconnect & fresh snapshot on new exam
    # =========================================================================
    def test_scenarios_28_29_snapshot_lifecycle(self):
        print("\n--- Testing Scenarios 28 & 29: Snapshot reuse on reconnect and new exam update ---")
        t_id = self.create_user("p_test_t14", "Teacher Fourteen", "teacher")
        s1 = self.create_user("p_test_life_s1", "Student Life1", "student")

        q1 = self.create_coding_question(t_id, 1)
        q2 = self.create_coding_question(t_id, 2)

        c = conn()
        c.execute("INSERT OR REPLACE INTO exam_access (user_id, teacher_id, allowed) VALUES (?, ?, 1)", (s1, t_id))
        c.commit(); c.close()

        self.login("p_test_t14")
        self.client.post('/api/permanent-assignments/bulk', json={'exam_type': 'coding', 'student_ids': [s1], 'question_ids': [q1]})

        key = "ROOM_LIFE_1"
        self.client.post('/api/exam/start_coding', json={'access_code': key, 'minutes': 30})

        # S1 joins first time
        self.login("p_test_life_s1")
        self.client.post('/api/exam/join', json={'access_code': key})

        c = conn()
        snap_initial = c.execute("SELECT snapshot_question_ids, profile_version FROM exam_attempt_snapshots WHERE teacher_id=? AND student_id=?", (t_id, s1)).fetchone()
        c.close()
        self.assertIsNotNone(snap_initial)
        self.assertEqual(json.loads(snap_initial['snapshot_question_ids']), [q1])

        # Teacher updates profile to [q2] while student is in exam
        self.login("p_test_t14")
        self.client.post('/api/permanent-assignments/bulk', json={'exam_type': 'coding', 'student_ids': [s1], 'question_ids': [q2]})

        # Scenario 28: Student reconnects to ongoing exam
        self.login("p_test_life_s1")
        self.client.post('/api/exam/join', json={'access_code': key})
        c = conn()
        snap_reconn = c.execute("SELECT snapshot_question_ids FROM exam_attempt_snapshots WHERE teacher_id=? AND student_id=?", (t_id, s1)).fetchone()
        c.close()
        self.assertEqual(json.loads(snap_reconn['snapshot_question_ids']), [q1], "Snapshot must be reused on reconnect!")

        # Finish exam
        self.client.post('/api/exam/leave', json={'reason': 'finished'})

        # Scenario 29: Teacher starts a brand new exam
        self.login("p_test_t14")
        key_new = "ROOM_LIFE_2"
        self.client.post('/api/exam/start_coding', json={'access_code': key_new, 'minutes': 30})

        # Student joins new exam -> MUST snapshot latest profile [q2]
        self.login("p_test_life_s1")
        self.client.post('/api/exam/join', json={'access_code': key_new})
        c = conn()
        snap_new = c.execute("SELECT snapshot_question_ids, profile_version FROM exam_attempt_snapshots WHERE teacher_id=? AND student_id=?", (t_id, s1)).fetchone()
        c.close()
        self.assertIsNotNone(snap_new)
        self.assertEqual(json.loads(snap_new['snapshot_question_ids']), [q2], "New exam attempt must snapshot latest profile version!")

        print("  ✓ Scenarios 28 & 29 passed successfully.")

if __name__ == '__main__':
    unittest.main()

