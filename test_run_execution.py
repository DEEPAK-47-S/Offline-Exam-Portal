import json
import sqlite3
import sys
import time
import simple_websocket
from app import app, conn, get_db_path

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

print("=== TESTING /ws/run WITH QUESTION ID ===")

import urllib.request
import http.cookiejar

# Ensure teacher 14 has active exam
c = conn()
c.execute("UPDATE exam_state SET active=1, coding_active=1, duration_minutes=60, started_at=strftime('%s','now') WHERE teacher_id=14")
code_row = c.execute("SELECT access_code FROM exam_state WHERE teacher_id=14").fetchone()
access_code = code_row['access_code']
print("Active exam code:", access_code)

# Get a coding question for teacher 14
q_row = c.execute("SELECT id, title FROM questions WHERE teacher_id=14 AND active=1 LIMIT 1").fetchone()
qid = q_row['id']
qtitle = q_row['title']
print(f"Target Question: ID={qid}, Title='{qtitle}'")

# Ensure student 022 (id 530) has access
c.execute("INSERT OR REPLACE INTO exam_access(user_id, teacher_id, allowed, locked, joined_at) VALUES(530, 14, 1, 0, strftime('%s','now'))")
c.commit()
c.close()

# Setup HTTP cookie jar with standard library
cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

# Login as student 022
login_payload = json.dumps({'username': '022', 'password': '022'}).encode('utf-8')
req = urllib.request.Request('http://127.0.0.1:5000/api/login', data=login_payload, headers={'Content-Type': 'application/json'})
with opener.open(req) as resp:
    assert resp.status == 200, f"Login failed: {resp.status}"
print("Student 022 logged in successfully.")

# Join exam
join_payload = json.dumps({'access_code': access_code}).encode('utf-8')
req = urllib.request.Request('http://127.0.0.1:5000/api/exam/join', data=join_payload, headers={'Content-Type': 'application/json'})
with opener.open(req) as resp:
    assert resp.status == 200, f"Join failed: {resp.status}"
print("Student 022 joined exam successfully.")

# Get cookie string
cookie_header = "; ".join([f"{cookie.name}={cookie.value}" for cookie in cj])

# Now test WebSocket /ws/run through running server on port 5000 with session cookie
ws = simple_websocket.Client(f"ws://127.0.0.1:5000/ws/run", headers={"Cookie": cookie_header})

# First, send WITHOUT question_id to confirm safety error
ws.send(json.dumps({'action': 'start', 'language': 'python', 'code': 'print("Testing")'}))
resp = json.loads(ws.receive())
print("Response without QID:", resp)
assert resp.get('type') == 'compile_error', f"Expected compile_error, got {resp}"
assert 'Question ID is required' in resp.get('data', ''), f"Unexpected data: {resp}"
print("✓ Verified: Missing question_id is safely blocked.")

# Second, send with UNAUTHORIZED question_id (qid=10, which is not assigned to student 022)
ws.send(json.dumps({'action': 'start', 'question_id': 10, 'language': 'python', 'code': 'print("Testing Unauthorized")'}))
resp_unauth = json.loads(ws.receive())
print("Response with Unauthorized QID:", resp_unauth)
assert resp_unauth.get('type') == 'compile_error', f"Expected compile_error, got {resp_unauth}"
assert 'Unauthorized question access' in resp_unauth.get('data', ''), f"Unexpected data: {resp_unauth}"
print("✓ Verified: Unauthorized question access is blocked.")

# Third, send WITH AUTHORIZED question_id (qid=26, which is assigned to student 022)
ws.send(json.dumps({'action': 'start', 'question_id': 26, 'language': 'python', 'code': 'print("SUCCESS_HELLO_WORLD")'}))

outputs = []
while True:
    raw = ws.receive(timeout=5)
    if not raw:
        break
    msg = json.loads(raw)
    print("WS Message:", msg)
    if msg.get('type') == 'output':
        outputs.append(msg.get('data', ''))
    if msg.get('type') == 'exit':
        break

ws.close()

full_output = "".join(outputs)
print("Full Output Captured:", repr(full_output))
assert "SUCCESS_HELLO_WORLD" in full_output, f"Expected output missing: {full_output}"
print("✓ SUCCESS: Code ran cleanly and output was received!")
