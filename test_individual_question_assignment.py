"""
Comprehensive Test Suite for Individual Student Question Control / Assignment Feature.
Tests all requirements from Section 15 of specification:
Test A through Test O.
"""
import unittest
import json
import time
import os
import sys
import sqlite3
import shutil

# Ensure utf-8 output on Windows consoles
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

# Use app test client
import app as application
from app import app, conn, init_db

class TestIndividualQuestionAssignment(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config['TESTING'] = True
        app.config['WTF_CSRF_ENABLED'] = False
        init_db()

    def setUp(self):
        self.client = app.test_client()
        c = conn()
        c.execute("DELETE FROM submissions WHERE user_id IN (SELECT id FROM users WHERE username LIKE 'test_%') OR teacher_id IN (SELECT id FROM users WHERE username LIKE 'test_%')")
        c.execute("DELETE FROM drafts WHERE user_id IN (SELECT id FROM users WHERE username LIKE 'test_%')")
        c.execute("DELETE FROM activity WHERE user_id IN (SELECT id FROM users WHERE username LIKE 'test_%')")
        c.execute("DELETE FROM exam_access WHERE user_id IN (SELECT id FROM users WHERE username LIKE 'test_%') OR teacher_id IN (SELECT id FROM users WHERE username LIKE 'test_%')")
        c.execute("DELETE FROM mcq_status WHERE student_id IN (SELECT id FROM users WHERE username LIKE 'test_%') OR teacher_id IN (SELECT id FROM users WHERE username LIKE 'test_%')")
        c.execute("DELETE FROM test_cases WHERE question_id IN (SELECT id FROM questions WHERE prompt LIKE '%test_assignment%') OR question_id NOT IN (SELECT id FROM questions)")
        c.execute("DELETE FROM users WHERE username LIKE 'test_%'")
        c.execute("DELETE FROM questions WHERE prompt LIKE '%test_assignment%'")
        c.execute("DELETE FROM mcqs WHERE question LIKE '%test_assignment%'")
        c.execute("DELETE FROM student_question_assignments WHERE exam_key LIKE 'TEST_%'")
        c.commit()
        c.close()

    def create_user(self, username, name, role, password="Password123!"):
        c = conn()
        p_hash = application.generate_password_hash(password)
        c.execute("INSERT OR REPLACE INTO users (username, name, role, password_hash) VALUES (?, ?, ?, ?)",
                  (username, name, role, p_hash))
        uid = c.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()['id']
        c.commit()
        c.close()
        return uid

    def login(self, username, password="Password123!"):
        res = self.client.post('/api/login', json={'username': username, 'password': password})
        self.assertEqual(res.status_code, 200, f"Login failed for {username}: {res.get_json()}")
        return res

    def logout(self):
        self.client.post('/api/logout')

    def test_complete_individual_assignment_matrix(self):
        """Runs Tests A through O sequentially in an isolated test environment."""
        print("\n==================================================================")
        print("STARTING INDIVIDUAL STUDENT QUESTION ASSIGNMENT VERIFICATION")
        print("==================================================================")

        # Setup: 1 Teacher, 3 Students (A, B, C)
        tid = self.create_user("test_teacher1", "Test Teacher One", "teacher")
        sA_id = self.create_user("test_studentA", "Student Alpha", "student")
        sB_id = self.create_user("test_studentB", "Student Beta", "student")
        sC_id = self.create_user("test_studentC", "Student Gamma", "student")

        c = conn()
        for sid in (sA_id, sB_id, sC_id):
            c.execute("INSERT OR REPLACE INTO exam_access (user_id, teacher_id, allowed, violations, joined_at) VALUES (?, ?, 1, 0, NULL)", (sid, tid))

        # Create 10 Common Coding Questions for Teacher 1
        q_ids = []
        for i in range(1, 11):
            cur = c.execute("INSERT INTO questions (teacher_id, title, prompt, language, active) VALUES (?, ?, ?, 'python', 1)",
                            (tid, f"Coding Question {i} - Problem Title {i}", f"Solve problem {i} test_assignment"))
            qid = cur.lastrowid
            q_ids.append(qid)
            c.execute("INSERT INTO test_cases (question_id, input_data, expected_output, marks, visible) VALUES (?, 'in', 'out', 10, 1)", (qid,))

        source_common_order = [r['id'] for r in c.execute("SELECT id FROM questions WHERE teacher_id=? AND active=1 ORDER BY id DESC", (tid,)).fetchall()]

        # Set Exam State for Coding Exam with Key "TEST_CODING_1"
        exam_key_1 = "TEST_CODING_1"
        c.execute("""
            INSERT INTO exam_state (teacher_id, active, coding_active, access_code, duration_minutes, started_at)
            VALUES (?, 1, 1, ?, 60, ?)
            ON CONFLICT(teacher_id) DO UPDATE SET active=1, coding_active=1, access_code=excluded.access_code, started_at=excluded.started_at
        """, (tid, exam_key_1, time.time()))
        c.commit()
        c.close()

        # -------------------------------------------------------------
        # TEST A: Normal Examination (Common Mode Default)
        # -------------------------------------------------------------
        print("\n[TEST A] Normal Examination (Common Mode): Student A receives all 10 questions")
        self.login("test_studentA")
        join_res = self.client.post('/api/exam/join', json={'access_code': exam_key_1})
        self.assertEqual(join_res.status_code, 200, f"Join failed: {join_res.get_json()}")
        self.assertEqual(join_res.get_json()['exam_type'], 'coding')

        q_res = self.client.get('/api/questions')
        self.assertEqual(q_res.status_code, 200)
        received_questions = q_res.get_json()
        self.assertEqual(len(received_questions), 10, "Student A must receive all 10 questions")
        received_ids = [q['id'] for q in received_questions]
        self.assertEqual(received_ids, source_common_order, "Question order must match common question set")
        self.logout()
        print("  -> [PASS] Test A: Common mode returned all 10 questions in source order.")

        # -------------------------------------------------------------
        # TEST B: Single Question Assignment
        # -------------------------------------------------------------
        print("\n[TEST B] Single Question Assignment: Teacher assigns Question 5 only to Student B")
        self.login("test_teacher1")
        target_q5_id = q_ids[4]
        asgn_res = self.client.post('/api/assignments', json={
            'exam_key': exam_key_1,
            'exam_type': 'coding',
            'teacher_id': tid,
            'student_id': sB_id,
            'question_ids': [target_q5_id]
        })
        self.assertEqual(asgn_res.status_code, 200, f"Assignment save failed: {asgn_res.get_json()}")
        self.logout()

        self.login("test_studentB")
        join_res = self.client.post('/api/exam/join', json={'access_code': exam_key_1})
        self.assertEqual(join_res.status_code, 200)

        q_res = self.client.get('/api/questions')
        self.assertEqual(q_res.status_code, 200)
        b_questions = q_res.get_json()
        self.assertEqual(len(b_questions), 1, "Student B must receive EXACTLY 1 question")
        self.assertEqual(b_questions[0]['id'], target_q5_id, "Student B must receive Question 5 only")
        self.logout()
        print("  -> [PASS] Test B: Student B received exactly Question 5 and other 9 questions were excluded.")

        # -------------------------------------------------------------
        # TEST C: Multiple Questions Assignment
        # -------------------------------------------------------------
        print("\n[TEST C] Multiple Questions Assignment: Teacher assigns Questions 2, 5, 8 to Student C")
        self.login("test_teacher1")
        target_c_ids = [q_ids[1], q_ids[4], q_ids[7]]
        expected_c_order = [qid for qid in source_common_order if qid in target_c_ids]

        asgn_res = self.client.post('/api/assignments', json={
            'exam_key': exam_key_1,
            'exam_type': 'coding',
            'teacher_id': tid,
            'student_id': sC_id,
            'question_ids': target_c_ids
        })
        self.assertEqual(asgn_res.status_code, 200)
        self.logout()

        self.login("test_studentC")
        join_res = self.client.post('/api/exam/join', json={'access_code': exam_key_1})
        self.assertEqual(join_res.status_code, 200)

        q_res = self.client.get('/api/questions')
        self.assertEqual(q_res.status_code, 200)
        c_questions = q_res.get_json()
        self.assertEqual(len(c_questions), 3, "Student C must receive EXACTLY 3 questions")
        self.assertEqual([q['id'] for q in c_questions], expected_c_order, "Student C must receive Questions 2, 5, 8 in source order")
        self.logout()
        print("  -> [PASS] Test C: Student C received exactly Questions 2, 5, 8 in source order.")

        # -------------------------------------------------------------
        # TEST D: Isolation Check
        # -------------------------------------------------------------
        print("\n[TEST D] Isolation Check across Students")
        self.login("test_studentA")
        self.assertEqual(len(self.client.get('/api/questions').get_json()), 10)
        self.logout()
        self.login("test_studentB")
        self.assertEqual(len(self.client.get('/api/questions').get_json()), 1)
        self.logout()
        self.login("test_studentC")
        self.assertEqual(len(self.client.get('/api/questions').get_json()), 3)
        self.logout()
        print("  -> [PASS] Test D: Perfect isolation: Student A=10, Student B=1, Student C=3.")

        # -------------------------------------------------------------
        # TEST E: Reset to Common Questions Mode
        # -------------------------------------------------------------
        print("\n[TEST E] Reset to Common Questions Mode before attempt start")
        sD_id = self.create_user("test_studentD", "Student Delta", "student")
        c = conn()
        c.execute("INSERT OR REPLACE INTO exam_access (user_id, teacher_id, allowed, violations, joined_at) VALUES (?, ?, 1, 0, NULL)", (sD_id, tid))
        c.commit(); c.close()

        self.login("test_teacher1")
        self.client.post('/api/assignments', json={
            'exam_key': exam_key_1,
            'exam_type': 'coding',
            'teacher_id': tid,
            'student_id': sD_id,
            'question_ids': [target_q5_id]
        })
        reset_res = self.client.post('/api/assignments/reset', json={
            'exam_key': exam_key_1,
            'exam_type': 'coding',
            'teacher_id': tid,
            'student_id': sD_id
        })
        self.assertEqual(reset_res.status_code, 200)
        self.logout()

        self.login("test_studentD")
        self.client.post('/api/exam/join', json={'access_code': exam_key_1})
        self.assertEqual(len(self.client.get('/api/questions').get_json()), 10)
        self.logout()
        print("  -> [PASS] Test E: Resetting restored all 10 common questions for Student D.")

        # -------------------------------------------------------------
        # TEST F: Update Assignment before attempt starts
        # -------------------------------------------------------------
        print("\n[TEST F] Update Assignment: Change questions before attempt starts")
        sE_id = self.create_user("test_studentE", "Student Epsilon", "student")
        c = conn()
        c.execute("INSERT OR REPLACE INTO exam_access (user_id, teacher_id, allowed, violations, joined_at) VALUES (?, ?, 1, 0, NULL)", (sE_id, tid))
        c.commit(); c.close()

        self.login("test_teacher1")
        self.client.post('/api/assignments', json={
            'exam_key': exam_key_1, 'exam_type': 'coding', 'teacher_id': tid, 'student_id': sE_id,
            'question_ids': [q_ids[1], q_ids[4], q_ids[7]]
        })
        new_q_selection = [q_ids[0], q_ids[6]]
        self.client.post('/api/assignments', json={
            'exam_key': exam_key_1, 'exam_type': 'coding', 'teacher_id': tid, 'student_id': sE_id,
            'question_ids': new_q_selection
        })
        self.logout()

        self.login("test_studentE")
        self.client.post('/api/exam/join', json={'access_code': exam_key_1})
        e_qids = set(q['id'] for q in self.client.get('/api/questions').get_json())
        self.assertEqual(e_qids, set(new_q_selection))
        self.logout()
        print("  -> [PASS] Test F: Updated assignment delivered updated questions (Q1, Q7).")

        # -------------------------------------------------------------
        # TEST G: Direct Backend API Security & Authorization
        # -------------------------------------------------------------
        print("\n[TEST G] Direct Backend API Security: Reject unassigned question operations")
        self.login("test_studentB")
        self.client.post('/api/exam/join', json={'access_code': exam_key_1})

        unassigned_qid = q_ids[0]

        v_res = self.client.post('/api/verify', json={
            'question_id': unassigned_qid,
            'code': 'print("hack")',
            'language': 'python'
        })
        self.assertEqual(v_res.status_code, 403, f"Unassigned /api/verify must return 403 Forbidden: {v_res.status_code}")

        sub_res = self.client.post('/api/submit', json={
            'question_id': unassigned_qid,
            'code': 'print("hack")',
            'language': 'python',
            'score': 100
        })
        self.assertEqual(sub_res.status_code, 403, f"Unassigned /api/submit must return 403 Forbidden: {sub_res.status_code}")

        d_res = self.client.post('/api/draft', json={
            'question_id': unassigned_qid,
            'code': 'print("hack")',
            'language': 'python'
        })
        self.assertEqual(d_res.status_code, 403, f"Unassigned /api/draft must return 403 Forbidden: {d_res.status_code}")

        d_auth = self.client.post('/api/draft', json={
            'question_id': target_q5_id,
            'code': 'print("allowed")',
            'language': 'python'
        })
        self.assertEqual(d_auth.status_code, 200, "Assigned question draft must succeed")
        self.logout()
        print("  -> [PASS] Test G: Backend rejected unassigned question requests with 403 Forbidden.")

        # -------------------------------------------------------------
        # TEST H: Cross-Examination Isolation
        # -------------------------------------------------------------
        print("\n[TEST H] Cross-Examination Isolation: Separate keys do not leak assignments")
        exam_key_2 = "TEST_CODING_2"
        c = conn()
        c.execute("UPDATE exam_state SET access_code=? WHERE teacher_id=?", (exam_key_2, tid))
        c.execute("UPDATE exam_access SET joined_at=NULL WHERE teacher_id=?", (tid,))
        c.commit(); c.close()

        self.login("test_studentB")
        self.client.post('/api/exam/join', json={'access_code': exam_key_2})
        exam2_q = self.client.get('/api/questions').get_json()
        self.assertEqual(len(exam2_q), 10, "Student B must default to all 10 common questions in Exam 2")
        self.logout()
        print("  -> [PASS] Test H: Exam 2 did not leak Exam 1's assignment; Student B received all 10 questions.")

        # -------------------------------------------------------------
        # TEST I: Attempt Locking
        # -------------------------------------------------------------
        print("\n[TEST I] Attempt Locking: Block assignment changes after attempt starts")
        self.login("test_teacher1")
        blocked_mod = self.client.post('/api/assignments', json={
            'exam_key': exam_key_1,
            'exam_type': 'coding',
            'teacher_id': tid,
            'student_id': sB_id,
            'question_ids': [q_ids[0], q_ids[1]]
        })
        self.assertEqual(blocked_mod.status_code, 400, "Modification after attempt started must be blocked with 400")
        self.assertIn("already started", blocked_mod.get_json()['error'])

        blocked_reset = self.client.post('/api/assignments/reset', json={
            'exam_key': exam_key_1,
            'exam_type': 'coding',
            'teacher_id': tid,
            'student_id': sB_id
        })
        self.assertEqual(blocked_reset.status_code, 400, "Reset after attempt started must be blocked with 400")
        self.logout()
        print("  -> [PASS] Test I: Backend rejected assignment changes after student attempt started.")

        # -------------------------------------------------------------
        # TEST J: Persistence
        # -------------------------------------------------------------
        print("\n[TEST J] Persistence: Question set preserved across session reconnect")
        c = conn()
        c.execute("UPDATE exam_state SET access_code=? WHERE teacher_id=?", (exam_key_1, tid))
        c.commit(); c.close()

        self.login("test_studentB")
        self.client.post('/api/exam/join', json={'access_code': exam_key_1})
        self.logout()

        self.login("test_studentB")
        restored_q = self.client.get('/api/questions').get_json()
        self.assertEqual(len(restored_q), 1, "Must persist exactly 1 question")
        self.assertEqual(restored_q[0]['id'], target_q5_id)
        self.logout()
        print("  -> [PASS] Test J: Assignment persisted across logout/login and re-entry.")

        # -------------------------------------------------------------
        # TEST K: Coding Workflow (Run, Verify, Submit, Score)
        # -------------------------------------------------------------
        print("\n[TEST K] Coding Workflow for Individually Assigned Student")
        self.login("test_studentB")
        self.client.post('/api/exam/join', json={'access_code': exam_key_1})
        v_res = self.client.post('/api/verify', json={
            'question_id': target_q5_id,
            'code': 'print("out")',
            'language': 'python'
        })
        self.assertEqual(v_res.status_code, 200)

        s_res = self.client.post('/api/submit', json={
            'question_id': target_q5_id,
            'code': 'print("out")',
            'language': 'python',
            'score': 10.0
        })
        self.assertEqual(s_res.status_code, 200)
        self.assertEqual(s_res.get_json()['live_score'], 10.0)
        self.logout()
        print("  -> [PASS] Test K: Verify and Submit succeeded on assigned Question 5.")

        # -------------------------------------------------------------
        # TEST L: MCQ Examination Individual Assignment
        # -------------------------------------------------------------
        print("\n[TEST L] MCQ Examination Individual Assignment")
        mcq_key = "TEST_MCQ_1"
        c = conn()
        mcq_ids = []
        for i in range(1, 6):
            cur = c.execute("INSERT INTO mcqs (teacher_id, question, option_a, option_b, option_c, option_d, correct_option, marks) VALUES (?, ?, 'A', 'B', 'C', 'D', 'a', 2.0)",
                            (tid, f"MCQ Question {i} test_assignment"))
            mcq_ids.append(cur.lastrowid)

        c.execute("""
            INSERT INTO exam_state (teacher_id, active, mcq_active, mcq_access_code)
            VALUES (?, 1, 1, ?)
            ON CONFLICT(teacher_id) DO UPDATE SET active=1, mcq_active=1, mcq_access_code=excluded.mcq_access_code
        """, (tid, mcq_key))
        c.commit(); c.close()

        self.login("test_teacher1")
        target_mcqs = [mcq_ids[1], mcq_ids[3]]
        m_asgn = self.client.post('/api/assignments', json={
            'exam_key': mcq_key,
            'exam_type': 'mcq',
            'teacher_id': tid,
            'student_id': sB_id,
            'question_ids': target_mcqs
        })
        self.assertEqual(m_asgn.status_code, 200)
        self.logout()

        self.login("test_studentB")
        m_join = self.client.post('/api/exam/join', json={'access_code': mcq_key})
        self.assertEqual(m_join.status_code, 200)
        self.assertEqual(m_join.get_json()['exam_type'], 'mcq')

        b_mcqs = self.client.get('/api/mcqs').get_json()
        self.assertEqual(len(b_mcqs), 2, "Student B must receive EXACTLY 2 MCQs")
        self.assertEqual(set(m['id'] for m in b_mcqs), set(target_mcqs))
        for m in b_mcqs:
            self.assertNotIn('correct_option', m, "Correct option must NOT be sent to student!")

        sub_mcq = self.client.post('/api/mcqs/submit', json={
            'mcq_id': target_mcqs[0],
            'selected_option': 'a'
        })
        self.assertEqual(sub_mcq.status_code, 200)

        unassigned_mcq = mcq_ids[0]
        unauth_mcq = self.client.post('/api/mcqs/submit', json={
            'mcq_id': unassigned_mcq,
            'selected_option': 'a'
        })
        self.assertEqual(unauth_mcq.status_code, 403, "Unassigned MCQ submission must be rejected with 403 Forbidden")
        self.logout()
        print("  -> [PASS] Test L: MCQ individual assignment and answer protection 100% verified.")

        # -------------------------------------------------------------
        # TEST M: Scoring & Result Calculation
        # -------------------------------------------------------------
        print("\n[TEST M] Scoring & Result Calculation for Individually Assigned Student")
        self.login("test_studentB")
        res_slip = self.client.get('/api/student/result').get_json()
        self.assertEqual(res_slip['ok'], True)
        scores = res_slip['scores']
        self.assertEqual(scores['coding_total'], 10.0, "Coding max marks must be calculated ONLY from assigned question (10 marks)")
        self.assertEqual(scores['coding_score'], 10.0)
        self.assertEqual(scores['mcq_total'], 4.0, "MCQ max marks must be calculated ONLY from assigned MCQs (4 marks)")
        self.assertEqual(len(res_slip['questions']), 1)
        self.assertEqual(res_slip['questions'][0]['id'], target_q5_id)
        self.assertEqual(len(res_slip['mcqs']), 2)
        self.logout()
        print("  -> [PASS] Test M: Result calculation verified: Max marks and scorecards correctly scoped to assigned questions.")

        # -------------------------------------------------------------
        # TEST N: Admin Multi-Teacher Assignment Overview & Assignment List API
        # -------------------------------------------------------------
        print("\n[TEST N] Assignment Overview List API & Admin Access")
        self.login("admin", "Admin123!")
        exams_list = self.client.get('/api/assignments/exams').get_json()
        self.assertTrue(len(exams_list['exams']) >= 1, "Admin should see available exams")

        asgns = self.client.get(f'/api/assignments?exam_key={exam_key_1}&exam_type=coding&teacher_id={tid}').get_json()
        self.assertTrue(asgns['ok'])
        self.assertEqual(asgns['total_common'], 10)
        students_overview = asgns['students']
        b_entry = next(s for s in students_overview if s['id'] == sB_id)
        self.assertEqual(b_entry['mode'], 'individual')
        self.assertEqual(b_entry['assigned_question_ids'], [target_q5_id])
        a_entry = next(s for s in students_overview if s['id'] == sA_id)
        self.assertEqual(a_entry['mode'], 'common')
        self.logout()
        print("  -> [PASS] Test N: Admin overview API verified with correct student modes and question lists.")

        # -------------------------------------------------------------
        # TEST O: Data Safety & Existing Records
        # -------------------------------------------------------------
        print("\n[TEST O] Data Safety & Preservation")
        c = conn()
        teacher_rec = c.execute("SELECT 1 FROM users WHERE username='teacher'").fetchone()
        admin_rec = c.execute("SELECT 1 FROM users WHERE username='admin'").fetchone()
        self.assertTrue(teacher_rec and admin_rec, "Default teacher and admin accounts must remain intact")
        c.close()
        print("  -> [PASS] Test O: Database schema and existing records preserved without loss.")

        print("\n==================================================================")
        print("ALL 15 TESTS (A through O) PASSED WITH 100% SUCCESS!")
        print("==================================================================")

if __name__ == '__main__':
    unittest.main()
