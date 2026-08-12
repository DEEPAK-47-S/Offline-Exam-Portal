# NIT Exam

Offline Flask + SQLite code-writing exam application. It opens with a short original intro then role-based login.

## Run

```powershell
pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:5000.

Initial accounts: `teacher` / `ChangeMe123!`; `student001` / `Student123!`. Change these before an exam (the accounts are stored in `exam_lab.db`).

Teachers publish questions and view locally stored student activity/submissions. Students have only the editor, question panel, console and submit action. The editor auto-pairs brackets/quotes, supports tabs, has a black/white toggle, and blocks ordinary clipboard shortcuts/paste.

## Important limits

This is a local/offline exam tool. It cannot send email while offline, and it deliberately never stores or sends passwords. Browser tab switching/fullscreen exits are detected and logged locally for the teacher, but a web browser cannot make cheating prevention absolute (users can still use operating-system or developer tools). For supervised exams, run it on controlled lab computers with kiosk/browser policies.
