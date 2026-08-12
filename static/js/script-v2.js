const $ = (s) => document.querySelector(s);
let me = null;
let questions = [];
let ws = null;
let running = false;
let unlocked = false;
let exam_finished = false;
let lastScore = 0;
let timerId = null;
let securityBound = false;
let teacherStudents = [];

async function api(url, options = {}) {
  const headers = options.body instanceof FormData ? {} : {"Content-Type": "application/json"};
  const response = await fetch(url, {headers, ...options});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Request failed");
  return data;
}
function show(id) {
  ["#login", "#student", "#teacher", "#admin"].forEach((s) => $(s).classList.add("hidden"));
  $(id).classList.remove("hidden");
}
function esc(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"}[c]));
}

window.showTab = (tabId) => {
  document.querySelectorAll('.portal-tab-content').forEach(el => el.style.display = 'none');
  const target = document.getElementById(tabId);
  if (target) target.style.display = 'block';
  
  document.querySelectorAll('.tab-btn').forEach(el => {
    if (el.dataset.target === tabId) el.classList.add('active');
    else el.classList.remove('active');
  });
};

function fmt(seconds) {
  const m = Math.floor(seconds / 60);
  const s = String(seconds % 60).padStart(2, "0");
  return `${m}:${s}`;
}
$("#fsResumeBtn")?.addEventListener("click", async () => {
  try { await document.documentElement.requestFullscreen(); } catch (_) {}
  if (document.fullscreenElement) $("#fsPrompt")?.classList.add("hidden");
});
async function boot() {
  me = await api("/api/me");
  if (!me.logged_in) return show("#login");
  if (me.role === "admin") return admin();
  if (me.role === "teacher") return teacher();
  return student();
}
$("#loginForm").onsubmit = async (event) => {
  event.preventDefault();
  $("#loginError").textContent = "";
  try {
    me = await api("/api/login", {method: "POST", body: JSON.stringify({username: $("#username").value, password: $("#password").value})});
    if (me.role === "admin") return admin();
    if (me.role === "teacher") return teacher();
    return student();
  } catch (error) {
    $("#loginError").textContent = error.message;
  }
};
async function logout() {
  exam_finished = true;
  await api("/api/logout", {method: "POST"});
  location.reload();
}
function toggleTheme() {
  const root = document.documentElement;
  root.dataset.theme = root.dataset.theme === "light" ? "dark" : "light";
}

const code = $("#code");
const gutter = $("#gutter");
const highlight = $("#highlight");
// Two-layer syntax highlighting: a colored <pre> sits exactly behind the
// textarea, and the textarea's own text is made invisible so only its
// caret/selection shows. This is the standard technique used by most
// browser-based code editors (CodeMirror-style editors all use a variant
// of it). The earlier version of this file used the same idea but the two
// layers could drift out of pixel-perfect alignment on some browsers,
// which looked like duplicated characters. To prevent that:
//   1. #code and #highlight share the exact same font/padding/line-height/
//      white-space/box-sizing (enforced in style.css from one shared rule).
//   2. #highlight is pointer-events:none and user-select:none, so it can
//      NEVER receive clicks, focus, or typing - #code is the only element
//      the browser ever writes a character into.
//   3. #code's own glyphs are hidden with the standard, broadly supported
//      color:transparent (not a vendor-prefixed trick), so there is only
//      ever one visible rendering of any character at a time.
const KEYWORDS = {
  python: new Set("and as assert async await break class continue def del elif else except False finally for from global if import in is lambda None nonlocal not or pass raise return True try while with yield".split(" ")),
  java: new Set("abstract boolean break byte case catch char class const continue default do double else enum extends final finally float for goto if implements import instanceof int interface long native new package private protected public return short static strictfp super switch synchronized this throw throws transient try void volatile while true false null".split(" ")),
  c: new Set("auto break case char const continue default do double else enum extern float for goto if inline int long register restrict return short signed sizeof static struct switch typedef union unsigned void volatile while true false NULL".split(" "))
};
const BUILTINS = {
  python: new Set("print input int float str bool list dict set tuple range len super self type isinstance enumerate zip map filter sorted sum min max abs open".split(" ")),
  java: new Set("System String Integer Boolean Double Float Long Character Object Math Arrays Collections List ArrayList Map HashMap Set HashSet Scanner StringBuilder Exception Override out println print".split(" ")),
  c: new Set("printf scanf malloc calloc realloc free memset memcpy strlen strcpy strcmp strcat NULL size_t FILE".split(" "))
};
// Token order matters: well-formed strings/chars are tried before the
// "unterminated" fallback, so a properly closed literal is never
// mis-flagged as an error.
const TOKEN_PATTERN = /\/\/[^\n]*|#[^\n]*|\/\*[\s\S]*?\*\/|"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'|"(?:\\.|[^"\\\n])*$|'(?:\\.|[^'\\\n])*$|\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?[lLfFdD]?\b|\b[A-Za-z_]\w*\b|[+\-*/%=!<>|&~^]+/gm;
function classify(token, offsetEnd, source, language) {
  if (token.startsWith("//") || token.startsWith("#") || token.startsWith("/*")) return "tok-comment";
  if (token[0] === '"') return (token.length >= 2 && token.endsWith('"')) ? "tok-string" : "tok-error";
  if (token[0] === "'") return (token.length >= 2 && token.endsWith("'")) ? "tok-character" : "tok-error";
  if (/^\d/.test(token)) return "tok-number";
  if (/^[A-Za-z_]/.test(token)) {
    if ((KEYWORDS[language] || KEYWORDS.python).has(token)) return "tok-keyword";
    const after = source.slice(offsetEnd);
    if (/^\s*\(/.test(after)) return "tok-function";
    if ((BUILTINS[language] || BUILTINS.python).has(token)) return "tok-function";
    return "tok-identifier";
  }
  return "tok-operator";
}
function highlightCode() {
  if (!highlight) return;
  const language = $("#language")?.value || "python";
  const source = code.value;
  TOKEN_PATTERN.lastIndex = 0;
  let cursor = 0;
  let out = "";
  let m;
  while ((m = TOKEN_PATTERN.exec(source))) {
    out += esc(source.slice(cursor, m.index));
    const cls = classify(m[0], m.index + m[0].length, source, language);
    out += `<span class="${cls}">${esc(m[0])}</span>`;
    cursor = m.index + m[0].length;
    if (m[0].length === 0) TOKEN_PATTERN.lastIndex++;
  }
  out += esc(source.slice(cursor));
  // A trailing newline keeps the highlight layer's last line the same
  // height as the textarea's, so the two never drift apart vertically.
  highlight.innerHTML = out + "\n";
}
function lines() {
  gutter.innerHTML = Array.from({length: Math.max(1, code.value.split("\n").length)}, (_, i) => i + 1).join("<br>");
  highlightCode();
}
function replaceSelection(text, cursorOffset = text.length) {
  const start = code.selectionStart;
  const end = code.selectionEnd;
  code.setRangeText(text, start, end, "end");
  code.selectionStart = code.selectionEnd = start + cursorOffset;
  lines();
}
function currentLineIndent() {
  const lineStart = code.value.lastIndexOf("\n", code.selectionStart - 1) + 1;
  const line = code.value.slice(lineStart, code.selectionStart);
  return (line.match(/^\s*/) || [""])[0];
}
function setEditorLock(locked, reason = "") {
  code.readOnly = locked;
  $("#runButton").disabled = locked;
  $("#verifyButton").disabled = locked;
  $("#submitButton").disabled = locked;
  $("#saveState").textContent = locked ? (reason || "Locked") : "Ready";
}
code.addEventListener("input", lines);
code.addEventListener("scroll", () => { gutter.scrollTop = code.scrollTop; highlight.scrollTop = code.scrollTop; highlight.scrollLeft = code.scrollLeft; });
$("#language").addEventListener("change", highlightCode);
code.addEventListener("paste", (event) => {
  event.preventDefault();
});
// Character handling lives in exactly ONE listener (keydown), and nowhere else.
// Earlier this was split across a "beforeinput" listener (auto-pairing) and a
// separate "keydown" listener (Tab/Enter/closer-skip). On browsers where
// beforeinput's preventDefault() does not fully cancel native insertion
// (older embedded WebViews and some locked-down kiosk browsers used on
// school lab machines are the common offenders), BOTH listeners could act on
// the same keystroke: the browser inserted the raw character AND our code
// inserted its own pair, producing the duplicated "((", "{{", etc. that was
// reported. Keeping a single keydown+preventDefault handler is the
// universally reliable pattern (this is also what plain text editors use)
// and removes any chance of two handlers double-inserting the same key.
const OPEN_PAIRS = {"(": ")", "[": "]", "{": "}", "\"": "\"", "'": "'"};
const CLOSERS = new Set([")", "]", "}", "\"", "'"]);
code.addEventListener("keydown", (event) => {
  if (event.defaultPrevented) return;
  // Let IME composition (accented/CJK input) finish; do not intercept mid-composition.
  if (event.isComposing || event.keyCode === 229) return;
  const key = event.key.toLowerCase();
  if ((event.ctrlKey || event.metaKey) && key === "b") {
    event.preventDefault();
    return;
  }
  // The capture-phase security listener records Ctrl+B/Ctrl+V only once.
  // Copy is blocked during an exam but is not counted as a tab switch.
  if ((event.ctrlKey || event.metaKey) && ["v", "x", "t", "s", "p", "u", "r"].includes(key)) {
    event.preventDefault();
    return;
  }
  if (/^f[1-3]$/i.test(event.key)) {
    event.preventDefault();
    return;
  }
  if (event.key === "Tab") {
    event.preventDefault();
    replaceSelection("    ");
    return;
  }
  if (event.key === "Enter") {
    event.preventDefault();
    const beforeText = code.value.slice(0, code.selectionStart);
    const afterText = code.value.slice(code.selectionEnd);
    const before = beforeText.trimEnd();
    const language = $("#language")?.value || "python";
    let extra = "";
    if (language === "python" && /:\s*$/.test(before)) extra = "    ";
    if ((language === "java" || language === "c") && /\{\s*$/.test(before)) extra = "    ";
    
    if (beforeText.endsWith("{") && afterText.startsWith("}")) {
      const base = currentLineIndent();
      const insertExtra = extra || "    ";
      replaceSelection("\n" + base + insertExtra + "\n" + base, 1 + base.length + insertExtra.length);
      return;
    }
    
    replaceSelection("\n" + currentLineIndent() + extra);
    return;
  }
  // Typing a closing character right before an auto-inserted matching
  // closer just skips over it, instead of inserting a second one.
  if (event.key.length === 1 && CLOSERS.has(event.key) && !event.ctrlKey && !event.metaKey && !event.altKey &&
      code.selectionStart === code.selectionEnd && code.value[code.selectionStart] === event.key) {
    event.preventDefault();
    code.selectionStart = code.selectionEnd = code.selectionStart + 1;
    return;
  }
  // Auto-pair an opening bracket/quote. This is the ONLY place that inserts
  // a pair - exactly one open + one close character, never more.
  if (event.key.length === 1 && OPEN_PAIRS[event.key] && !event.ctrlKey && !event.metaKey && !event.altKey) {
    event.preventDefault();
    const a = code.selectionStart;
    const b = code.selectionEnd;
    const selected = code.value.slice(a, b);
    if (selected) {
      // Wrap the current selection instead of collapsing it.
      code.setRangeText(event.key + selected + OPEN_PAIRS[event.key], a, b, "end");
      code.selectionStart = a + 1;
      code.selectionEnd = a + 1 + selected.length;
      lines();
    } else {
      replaceSelection(event.key + OPEN_PAIRS[event.key], 1);
    }
    return;
  }
  // Every other key (letters, digits, @ # + = ; : etc.) is left completely
  // alone here and falls through to the browser's normal single-character
  // insertion and normal Backspace/Delete behavior.
});
lines();

async function student() {
  show("#student");
  $("#studentName").textContent = me.name;
  questions = await api("/api/questions");
  fillQuestionSelect();
  $("#questionSelect").onchange = loadQ;
  loadQ();
  $("#joinExam").onclick = joinExam;
  security();
  await refreshExam(true);
}
function fillQuestionSelect() {
  $("#questionSelect").innerHTML = questions.length ? questions.map((q) => `<option value="${q.id}">${esc(q.title)}</option>`).join("") : "<option>No question available</option>";
  $("#caseQuestion") && ($("#caseQuestion").innerHTML = questions.map((q) => `<option value="${q.id}">${esc(q.title)}</option>`).join(""));
}
function loadQ() {
  const q = questions.find((item) => String(item.id) === $("#questionSelect").value);
  if (!q) return;
  $("#questionText").textContent = q.prompt;
  $("#language").value = q.language;
}
async function joinExam() {
  try {
    const res = await api("/api/exam/join", {method: "POST", body: JSON.stringify({access_code: $("#examKey").value})});
    try { await document.documentElement.requestFullscreen(); } catch (_) {}
    $("#gateMessage").textContent = "Exam unlocked.";
    $("#warningModal")?.classList.remove("hidden");
    $("#warningOkBtn")?.addEventListener("click", () => {
      $("#warningModal")?.classList.add("hidden");
    });
    if (res.exam_type === 'mcq') {
      window.isMcqExam = true;
      show("#student-mcq");
      $("#studentMcqName").textContent = me.name;
      unlocked = true;
      await loadMcqQuestions();
      refreshMcqExam();
      return;
    }
    await refreshExam(true);
  } catch (error) {
    $("#gateMessage").textContent = error.message;
  }
}
async function refreshExam(force = false) {
  const state = await api("/api/exam-state");
  if (me.role === "student") {
    unlocked = Boolean(state.active && state.joined && !state.locked && state.remaining > 0);
    if (!unlocked) $("#fsPrompt")?.classList.add("hidden");
    $("#examTimer").textContent = state.active ? `Time left ${fmt(state.remaining)}` : "Waiting";
    $("#illegalCount").textContent = `Tab switches: ${state.illegal_count || 0}`;
    $("#examGate").style.display = unlocked ? "none" : "grid";
    $(".exam-shell").classList.toggle("exam-key-only", !unlocked);
    $("#student").classList.toggle("exam-running", unlocked);
    if (state.locked) {
      setEditorLock(true, "Locked by supervisor");
      $("#output").textContent = "Exam locked because illegal activity crossed the allowed limit.";
    } else if (!state.active) {
      setEditorLock(true, "Exam not started");
    } else if (!state.joined) {
      setEditorLock(true, state.allowed === false ? "Not selected for this exam" : "Enter exam key");
    } else if (state.remaining <= 0) {
      setEditorLock(true, "Time over");
      $("#output").textContent = "Exam time is over.";
    } else {
      setEditorLock(false);
    }
    clearTimeout(timerId);
    timerId = setTimeout(refreshExam, 1000);
  }
}
function security() {
  if (securityBound) return;
  securityBound = true;
  let lastSignalAt = 0;
  const record = (event, details = "") => {
    if (exam_finished || !unlocked) return;
    const now = Date.now();
    // A tab change may produce visibilitychange + blur + fullscreenchange.
    // Treat the group as a single violation.
    if (now - lastSignalAt < 900) return;
    lastSignalAt = now;
    sendActivity(event, details);
  };
  document.addEventListener("visibilitychange", () => { if (document.hidden) record("tab_hidden"); });
  window.addEventListener("blur", () => record("window_blur"));
  window.addEventListener("resize", () => { if (innerWidth < 900 || innerHeight < 600) record("screen_resize"); });
  document.addEventListener("fullscreenchange", () => {
    if (document.fullscreenElement) { $("#fsPrompt")?.classList.add("hidden"); return; }
    if (!unlocked) return;
    record("fullscreen_exit");
    // Browsers only allow requestFullscreen() from a direct user gesture, so we
    // cannot silently re-enter fullscreen here. Show a blocking prompt instead;
    // the student's click on the resume button is the required gesture.
    $("#fsPrompt")?.classList.remove("hidden");
  });
  document.addEventListener("contextmenu", (event) => { if (unlocked) event.preventDefault(); });
  document.addEventListener("copy", (event) => { if (unlocked) event.preventDefault(); });
  document.addEventListener("keydown", (event) => {
    if (!unlocked) return;
    const key = event.key.toLowerCase();
    const systemKey = event.key === "Meta" || event.key === "OS" || event.key === "Copilot";
    if (systemKey || ["f1", "f2", "f3"].includes(key) || ((event.ctrlKey || event.metaKey) && ["b", "v"].includes(key))) {
      event.preventDefault();
      event.stopPropagation();
      record(key === "v" ? "blocked_paste" : "blocked_key", systemKey ? event.key : ((event.ctrlKey || event.metaKey) ? `ctrl_${key}` : key));
    }
  }, true);
  window.addEventListener("beforeunload", () => { if (unlocked) navigator.sendBeacon("/api/activity", new Blob([JSON.stringify({event:"attempt_close"})], {type:"application/json"})); });
}
async function sendActivity(event, details = "") {
  if (!unlocked) return;
  try {
    const data = await api("/api/activity", {method: "POST", body: JSON.stringify({event, details})});
    $("#illegalCount").textContent = `Tab switches: ${data.illegal_count || 0}`;
    if (data.ignored) return;
    if (data.locked) {
      alert("Fourth violation: the exam has been submitted/locked by the security policy.");
      await refreshExam(true);
    } else {
      const warning = ["", "First warning", "Final warning", "Last warning"][data.illegal_count] || "Warning";
      alert(`${warning}: illegal activity detected (${data.illegal_count}/4).`);
    }
  } catch {}
}

let live = null;
function bindTerminal() {
  live = $("#termLive");
  live.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" || !ws || !running) return;
    event.preventDefault();
    const value = live.textContent;
    live.textContent = "";
    $("#output").insertBefore(document.createTextNode(value + "\n"), live);
    ws.send(JSON.stringify({action: "input", data: value}));
  });
  live.focus();
}
function writeTerminal(text) {
  $("#output").insertBefore(document.createTextNode(text), live);
  $("#output").scrollTop = $("#output").scrollHeight;
}
function runCode() {
  if (running || !unlocked) return;
  running = true;
  $("#status").textContent = "RUNNING";
  $("#output").innerHTML = '<span id="termLive" class="term-live" contenteditable="true" spellcheck="false"></span>';
  bindTerminal();
  const socket = new WebSocket((location.protocol === "https:" ? "wss" : "ws") + "://" + location.host + "/ws/run");
  ws = socket;
  socket.onopen = () => socket.send(JSON.stringify({action: "start", language: $("#language").value, code: code.value}));
  socket.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    if (msg.data) writeTerminal(msg.data);
    if (msg.type === "compile_error") {
      running = false;
      $("#status").textContent = "COMPILE ERROR";
      writeTerminal(msg.data);
      live.contentEditable = "false";
      ws = null;
      socket.close();
    }
    if (msg.type === "exit") {
      running = false;
      $("#status").textContent = `EXIT ${msg.code} - ${msg.time_ms} ms`;
      live.contentEditable = "false";
      ws = null;
      socket.close();
    }
  };
  socket.onerror = () => {
    if (ws !== socket || !running) return;
    running = false;
    $("#status").textContent = "RUNNER ERROR";
    writeTerminal("\nUnable to reach the local runner. Check that the server is running.");
    if (live) live.contentEditable = "false";
  };
}
async function verifyCode() {
  const q = questions.find((item) => String(item.id) === $("#questionSelect").value);
  if (!q || !unlocked) return false;
  lastScore = 0;
  try {
    const data = await api("/api/verify", {method: "POST", body: JSON.stringify({question_id: q.id, code: code.value, language: $("#language").value})});
    lastScore = data.score;
    $("#testResults").innerHTML = `<b>Score: ${data.score}</b>` + data.results.map((r) => `<div class="${r.passed ? "ok" : "bad"}"><span>${esc(r.name)}</span><span>${r.passed ? "Accepted" : "Failed"}</span><span>${r.visible ? `${esc(r.output)} / ${esc(r.expected)}` : "Hidden test case"}</span><span>${r.marks} marks</span></div>`).join("");
    return true;
  } catch (error) {
    $("#testResults").innerHTML = `<div class="bad">${esc(error.message)}</div>`;
    return false;
  }
}
async function submitCode() {
  const q = questions.find((item) => String(item.id) === $("#questionSelect").value);
  if (!q || !unlocked) return;
  const verified = await verifyCode();
  if (!verified) return;
  await api("/api/submit", {method: "POST", body: JSON.stringify({question_id: q.id, code: code.value, language: $("#language").value, score: lastScore})});
  $("#saveState").textContent = "Submitted";
}
async function leaveExam() {
  if (!unlocked) return;
  const promptEl = document.getElementById("leavePrompt");
  if (promptEl) promptEl.classList.remove("hidden");
}

document.getElementById("leaveNoBtn")?.addEventListener("click", () => {
  document.getElementById("leavePrompt")?.classList.add("hidden");
});

document.getElementById("leaveYesBtn")?.addEventListener("click", async () => {
  document.getElementById("leavePrompt")?.classList.add("hidden");
  exam_finished = true;
  try {
    await api("/api/exam/student-exit", {method: "POST"});
    unlocked = false;
    setEditorLock(true, "Exam ended");
    document.getElementById("output").textContent = "Your exam session has ended.";
    try { await document.exitFullscreen(); } catch (_) {}
    await refreshExam(true);
  } catch (error) {
    alert(error.message);
  }
});

async function teacher() {
  show("#teacher");
  $("#teacherName").textContent = me.name;
  await loadTeacherQuestions();
  $("#questionForm").onsubmit = async (event) => {
    event.preventDefault();
    const payload = {title: $("#qtitle").value, prompt: $("#qprompt").value, language: $("#qlang").value};
    const editId = $("#questionEditId").value;
    if (editId) {
      await api(`/api/questions/${editId}`, {method: "PUT", body: JSON.stringify(payload)});
    } else {
      await api("/api/questions", {method: "POST", body: JSON.stringify(payload)});
    }
    event.target.reset();
    $("#questionEditId").value = "";
    $("#questionSaveButton").textContent = "Publish / Update";
    await loadTeacherQuestions();
  };
  $("#questionCancelEdit").onclick = () => {
    $("#questionForm").reset();
    $("#questionEditId").value = "";
    $("#questionSaveButton").textContent = "Publish / Update";
  };
  $("#testCaseForm").onsubmit = async (event) => {
    event.preventDefault();
    const payload = {question_id: $("#caseQuestion").value, input_data: $("#caseInput").value, expected_output: $("#caseOutput").value, marks: $("#caseMarks").value, visible: $("#caseVisible").checked};
    const editId = $("#caseEditId").value;
    await api(editId ? `/api/test-cases/${editId}` : "/api/test-cases", {method: editId ? "PUT" : "POST", body: JSON.stringify(payload)});
    event.target.reset();
    $("#caseEditId").value = "";
    $("#caseSaveButton").textContent = "Add test case";
    $("#caseCancelEdit").classList.add("hidden");
    await loadCases();
  };
  $("#caseCancelEdit").onclick = () => { $("#testCaseForm").reset(); $("#caseEditId").value = ""; $("#caseSaveButton").textContent = "Add test case"; $("#caseCancelEdit").classList.add("hidden"); };
  $("#caseQuestion").onchange = loadCases;
  
  $("#mcqForm").onsubmit = async (event) => {
    event.preventDefault();
    const payload = {question: $("#mcqQuestion").value, option_a: $("#mcqOptionA").value, option_b: $("#mcqOptionB").value, option_c: $("#mcqOptionC").value, option_d: $("#mcqOptionD").value, correct_option: $("#mcqCorrectOption").value, marks: $("#mcqMarks").value, time_limit: $("#mcqTimeLimit").value};
    const editId = $("#mcqEditId").value;
    await api(editId ? `/api/mcqs/${editId}` : "/api/mcqs", {method: editId ? "PUT" : "POST", body: JSON.stringify(payload)});
    event.target.reset();
    $("#mcqEditId").value = "";
    $("#mcqSaveButton").textContent = "Add MCQ";
    $("#mcqCancelEdit").classList.add("hidden");
    await loadMCQs();
  };
  $("#mcqCancelEdit").onclick = () => { $("#mcqForm").reset(); $("#mcqEditId").value = ""; $("#mcqSaveButton").textContent = "Add MCQ"; $("#mcqCancelEdit").classList.add("hidden"); };
  await loadMCQs();

  $("#examForm").onsubmit = async (event) => {
    event.preventDefault();
    const data = await api("/api/exam/start", {method: "POST", body: JSON.stringify({minutes: $("#minutes").value})});
    $("#examCode").textContent = `Coding key: ${data.access_code} | MCQ key: ${data.mcq_access_code}`;
  };
  $("#stopExam").onclick = async () => {
    await api("/api/exam/stop", {method: "POST", body: JSON.stringify({})});
    $("#examCode").textContent = "Exam stopped.";
  };
  $("#importForm").onsubmit = async (event) => {
    event.preventDefault();
    const form = new FormData();
    form.append("file", $("#studentFile").files[0]);
    const response = await fetch("/api/students/import", {method: "POST", body: form});
    const data = await response.json();
    alert(response.ok ? `${data.imported} students imported.` : data.error);
    if (response.ok) { event.target.reset(); await loadStudents(); }
    dashboard();
  };
  if ($("#mcqImportForm")) {
    $("#mcqImportForm").onsubmit = async (event) => {
      event.preventDefault();
      const form = new FormData();
      form.append("file", $("#mcqFile").files[0]);
      const response = await fetch("/api/mcqs/import", {method: "POST", body: form});
      const data = await response.json();
      alert(response.ok ? `${data.imported} MCQs imported.` : data.error);
      if (response.ok) { event.target.reset(); await loadMCQs(); }
    };
  }
  $("#studentManualForm").onsubmit = async (event) => {
    event.preventDefault();
    await api("/api/students", {method: "POST", body: JSON.stringify({name: $("#studentFullName").value, username: $("#studentRegisterNumber").value, register_number: $("#studentRegisterNumber").value, department: $("#studentDepartment").value, year: $("#studentYear").value, semester: $("#studentSemester").value, section: $("#studentSection").value, password: $("#studentPassword").value})});
    event.target.reset();
    alert("Student saved.");
    await loadStudents();
    dashboard();
  };
  $("#studentSearch").oninput = renderStudents;
  await loadStudents();
  dashboard();
  setInterval(() => {
    if (!document.hidden && !document.getElementById("teacher").classList.contains("hidden")) {
      dashboard();
    }
  }, 3000);
  const state = await api("/api/exam-state");
  $("#examCode").textContent = state.active ? `Coding key: ${state.access_code} | MCQ key: ${state.mcq_access_code || "hidden"}` : "Exam is not active.";
}
async function loadTeacherQuestions() {
  questions = await api("/api/questions");
  fillQuestionSelect();
  renderTeacherQuestions();
  await loadCases();
}
function renderTeacherQuestions() {
  const target = $("#teacherQuestionList");
  if (!target) return;
  target.innerHTML = questions.length ? questions.map((q) => `<div class="question-row"><span><b>${esc(q.title)}</b><small>${esc(q.language)}</small></span><button type="button" onclick="editQuestion(${q.id})">Edit</button></div>`).join("") : "No questions published yet.";
}
function editQuestion(id) {
  const q = questions.find((item) => item.id === id);
  if (!q) return;
  $("#questionEditId").value = q.id;
  $("#qtitle").value = q.title;
  $("#qlang").value = q.language;
  $("#qprompt").value = q.prompt;
  $("#questionSaveButton").textContent = "Update question";
}
async function loadCases() {
  const id = $("#caseQuestion").value;
  if (!id) return $("#caseList").innerHTML = "No question selected.";
  const cases = await api(`/api/test-cases/${id}`);
  $("#caseList").innerHTML = cases.length ? cases.map((c, i) => `<div class="case-row"><b>Case ${i + 1}</b><span>${c.visible ? "Visible" : "Hidden"}</span><span>${c.marks} marks</span><button type="button" onclick="editCase(${c.id})">Edit</button></div>`).join("") : "No test cases yet.";
}
function editCase(id) {
  api(`/api/test-cases/${$("#caseQuestion").value}`).then((cases) => {
    const c = cases.find((item) => item.id === id); if (!c) return;
    $("#caseEditId").value = c.id; $("#caseInput").value = c.input_data || ""; $("#caseOutput").value = c.expected_output || ""; $("#caseMarks").value = c.marks; $("#caseVisible").checked = Boolean(c.visible); $("#caseSaveButton").textContent = "Update test case"; $("#caseCancelEdit").classList.remove("hidden");
  });
}
async function loadMCQs() {
  const mcqs = await api("/api/mcqs");
  $("#mcqList").innerHTML = mcqs.length ? mcqs.map(m => `<div class="case-row"><b>${esc(m.question)}</b><span>Answer: ${m.correct_option.toUpperCase()}</span><div><button type="button" onclick="editMCQ(${m.id})">Edit</button><button type="button" style="margin-left:6px; color:var(--danger);" onclick="deleteMCQ(${m.id})">Delete</button></div></div>`).join("") : "No MCQs added.";
}
window.deleteMCQ = async (id) => {
  if (!confirm("Are you sure you want to delete this MCQ?")) return;
  const res = await fetch(`/api/mcqs/${id}`, { method: "DELETE" });
  if (res.ok) { alert("MCQ deleted."); await loadMCQs(); }
  else { const data = await res.json(); alert(data.error || "Error deleting."); }
};
window.editMCQ = (id) => {
  api("/api/mcqs").then(mcqs => {
    const m = mcqs.find(item => item.id === id); if (!m) return;
    $("#mcqEditId").value = m.id;
    $("#mcqQuestion").value = m.question;
    $("#mcqOptionA").value = m.option_a; $("#mcqOptionB").value = m.option_b;
    $("#mcqOptionC").value = m.option_c; $("#mcqOptionD").value = m.option_d;
    $("#mcqCorrectOption").value = m.correct_option;
    $("#mcqMarks").value = m.marks || 1;
    $("#mcqTimeLimit").value = m.time_limit || 30;
    $("#mcqSaveButton").textContent = "Update MCQ";
    $("#mcqCancelEdit").classList.remove("hidden");
  });
};
let teacherDashboardData = [];
async function dashboard() {
  teacherDashboardData = await api("/api/dashboard");
  renderDashboard();
}
function renderDashboard() {
  const data = teacherDashboardData;
  $("#activity").innerHTML = `<table><tr><th>Student</th><th>Submissions</th><th>Score</th><th>Illegal</th><th>Latest activity</th></tr>${data.map((s) => `<tr><td>${esc(s.name)}<br><small>${esc(s.username)}</small></td><td>${s.submissions}</td><td>${s.score}</td><td>${s.illegal_count}</td><td>${s.activity.map((a) => `${esc(a.event)} - ${esc(a.created_at)}`).join("<br>") || "-"}</td></tr>`).join("")}</table>`;
  const cls = $("#leaderboardClassSelect")?.value;
  const filtered = cls ? data.filter(s => s.class_name === cls) : data;
  const sorted = [...filtered].sort((a,b) => {
    const sA = (a.score||0) + (a.mcq_score||0);
    const sB = (b.score||0) + (b.mcq_score||0);
    if (sB !== sA) return sB - sA;
    return (a.total_time||0) - (b.total_time||0);
  });
  const tbody = $("#leaderboardTableBody");
  if (tbody) tbody.innerHTML = sorted.map((s, i) => `<tr><td>${i+1}</td><td>${esc(s.name)}</td><td>${esc(s.register_number || s.username)}</td><td>${esc(s.class_name || '-')}</td><td>${s.total_time||0}</td><td>${s.pending ? 'Pending' : s.score}</td><td>${s.pending ? 'Pending' : (s.mcq_score||0)}</td></tr>`).join("");
}
if ($("#leaderboardClassSelect")) $("#leaderboardClassSelect").addEventListener("change", renderDashboard);

async function loadStudents() {
  const target = $("#studentList");
  if (!target) return;
  teacherStudents = await api("/api/students");
  renderStudents();
}
function renderStudents() {
  const target = $("#studentList");
  if (!target) return;
  const query = ($("#studentSearch")?.value || "").toLowerCase();
  const list = teacherStudents.filter((s) => `${s.name} ${s.username} ${s.register_number} ${s.department}`.toLowerCase().includes(query));
  target.innerHTML = list.length ? list.map((s) => `<div class="student-row"><div><b>${esc(s.name)}</b><small>${esc(s.register_number || s.username)} · ${esc(s.department || "No department")}</small><small>${esc(s.year || "")} ${esc(s.semester || "")} ${esc(s.section || "")}</small></div><div class="row-actions"><button data-edit="${s.id}">Edit</button><button data-access="${s.id}">${s.allowed ? "Allowed" : "Blocked"}</button><button data-reset="${s.id}">Reset</button><button data-delete="${s.id}">Delete</button></div></div>`).join("") : "No matching students.";
  target.querySelectorAll("button[data-edit]").forEach((b) => b.onclick = () => editStudent(Number(b.dataset.edit)));
  target.querySelectorAll("button[data-access]").forEach((b) => b.onclick = () => toggleStudentAccess(Number(b.dataset.access)));
  target.querySelectorAll("button[data-reset]").forEach((b) => b.onclick = () => resetStudentPassword(Number(b.dataset.reset)));
  target.querySelectorAll("button[data-delete]").forEach((b) => b.onclick = () => removeStudent(Number(b.dataset.delete)));
}
async function editStudent(id) {
  const s = teacherStudents.find((item) => item.id === id); if (!s) return;
  const name = prompt("Student name", s.name); if (name === null) return;
  const username = prompt("Username / register number", s.username); if (username === null) return;
  const department = prompt("Department", s.department || ""); if (department === null) return;
  const year = prompt("Year", s.year || ""); if (year === null) return;
  const semester = prompt("Semester", s.semester || ""); if (semester === null) return;
  const section = prompt("Section", s.section || ""); if (section === null) return;
  await api(`/api/students/${id}`, {method:"PUT", body:JSON.stringify({name,username,register_number:username,department,year,semester,section})});
  await loadStudents();
}
async function toggleStudentAccess(id) {
  const s = teacherStudents.find((item) => item.id === id); if (!s) return;
  await api(`/api/students/${id}/access`, {method:"POST",body:JSON.stringify({allowed:!s.allowed})}); await loadStudents();
}
async function resetStudentPassword(id) {
  const password = prompt("New password (minimum 4 characters)"); if (!password) return;
  await api(`/api/students/${id}/reset-password`, {method:"POST",body:JSON.stringify({password})}); alert("Password reset.");
}
async function removeStudent(id) {
  if (!confirm("Delete this student account?")) return;
  await api(`/api/students/${id}`, {method:"DELETE"}); await loadStudents();
}

async function admin() {
  show("#admin");
  $("#adminName").textContent = me.name;
  $("#teacherAccessForm").onsubmit = async (event) => {
    event.preventDefault();
    await api("/api/admin/teachers", {method: "POST", body: JSON.stringify({name: $("#teacherFullName").value, username: $("#teacherUserName").value, password: $("#teacherPassword").value})});
    event.target.reset();
    await loadAdminUsers();
  };
  $("#teacherImportForm").onsubmit = async (event) => {
    event.preventDefault();
    const file = $("#teacherFile").files[0];
    if (!file) return;
    const form = new FormData(); form.append("file", file);
    const response = await fetch("/api/admin/teachers/import", {method: "POST", body: form});
    const data = await response.json();
    alert(response.ok ? `${data.imported} teachers imported or updated.` : data.error);
    if (response.ok) { event.target.reset(); await loadAdminUsers(); }
  };
  await loadAdminUsers();
}
async function loadAdminUsers() {
  const users = await api("/api/admin/users");
  $("#adminUsers").innerHTML = `<table><tr><th>Name</th><th>Username</th><th>Role</th><th>Actions</th></tr>${users.map((u) => `<tr><td>${esc(u.name)}</td><td>${esc(u.username)}</td><td>${esc(u.role)}</td><td>${u.role==='teacher' ? `<button onclick="editTeacher(${u.id}, '${esc(u.name).replace(/'/g, "\\'")}', '${esc(u.username).replace(/'/g, "\\'")}')">Edit</button>` : ''}</td></tr>`).join("")}</table>`;
}
window.editTeacher = async (id, oldName, oldUsername) => {
  const name = prompt("Teacher name", oldName); if (!name) return;
  const username = prompt("Teacher username", oldUsername); if (!username) return;
  const password = prompt("New password (leave blank to keep current)");
  await api(`/api/admin/teachers/${id}`, {method: "POST", body: JSON.stringify({name, username, password: password || ""})});
  await loadAdminUsers();
};

setTimeout(() => $("#intro")?.remove(), 3000);

function initSlider() {
  const slider = $("#collegeSlider");
  if (!slider) return;
  let images = [];
  for (let i = 1; i <= 7; i++) {
    let img = document.createElement("img");
    img.src = `/static/img/college/campus-${i}.jpg`;
    img.onerror = () => { img.remove(); };
    img.onload = () => {
      images.push(img);
      slider.appendChild(img);
      if (images.length === 1) img.classList.add("active");
    };
  }
  let currentIdx = 0;
  setInterval(() => {
    if (images.length <= 1) return;
    images[currentIdx].classList.remove("active");
    currentIdx = (currentIdx + 1) % images.length;
    images[currentIdx].classList.add("active");
  }, 5000);
}

(function particles() {
  const canvas = $("#bg-canvas");
  const ctx = canvas.getContext("2d");
  let stars = [];
  let mx = -999, my = -999;
  function resize() {
    canvas.width = innerWidth;
    canvas.height = innerHeight;
    stars = Array.from({length: 150}, () => ({
      x: (Math.random() - 0.5) * canvas.width * 2,
      y: (Math.random() - 0.5) * canvas.height * 2,
      z: Math.random() * 1000
    }));
  }
  addEventListener("resize", resize);
  addEventListener("pointermove", (event) => { mx = event.clientX; my = event.clientY; });
  function tick() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    const cx = canvas.width / 2;
    const cy = canvas.height / 2;
    stars.forEach((star) => {
      star.z -= 1.5;
      if (star.z <= 0) {
        star.z = 1000;
        star.x = (Math.random() - 0.5) * canvas.width * 2;
        star.y = (Math.random() - 0.5) * canvas.height * 2;
      }
      const scale = 500 / star.z;
      const px = cx + (star.x - (mx - cx) * 0.5) * scale;
      const py = cy + (star.y - (my - cy) * 0.5) * scale;
      if (px >= 0 && px <= canvas.width && py >= 0 && py <= canvas.height) {
        const size = Math.max(0.1, 2 * scale);
        const alpha = Math.min(1, Math.max(0.1, 1 - star.z / 1000));
        ctx.fillStyle = `rgba(100, 200, 255, ${alpha})`;
        ctx.beginPath();
        ctx.arc(px, py, size, 0, 7);
        ctx.fill();
      }
    });
    requestAnimationFrame(tick);
  }
  resize();
  tick();
})();
let adminStudents = [];
async function loadAdminStudents() {
  try { adminStudents = await api("/api/students"); } catch (e) {}
}
function renderAdminStudents() {
  const query = $("#adminStudentSearch").value.toLowerCase();
  if (query.length < 2) return;
  const s = adminStudents.find(x => x.username.toLowerCase() === query || (x.register_number && x.register_number.toLowerCase() === query));
  if (s) {
    $("#adminStudentEditFields").style.display = "block";
    $("#adminEditId").value = s.id;
    $("#adminEditName").value = s.name;
    $("#adminEditUsername").value = s.username;
    $("#adminEditRegNo").value = s.register_number || s.username;
    $("#adminEditDept").value = s.department || "";
    $("#adminEditYear").value = s.year || "";
    $("#adminEditSem").value = s.semester || "";
    $("#adminEditSec").value = s.section || "";
    $("#adminEditPassword").value = "";
  } else {
    $("#adminStudentEditFields").style.display = "none";
  }
}

if ($("#adminStudentSearch")) {
    $("#adminStudentSearch").oninput = renderAdminStudents;
    $("#adminCancelEditBtn").onclick = () => { $("#adminStudentEditFields").style.display = "none"; $("#adminStudentSearch").value = ""; };
    $("#adminSaveStudentBtn").onclick = async () => {
        const id = $("#adminEditId").value;
        const data = {
            name: $("#adminEditName").value,
            username: $("#adminEditUsername").value,
            register_number: $("#adminEditRegNo").value,
            department: $("#adminEditDept").value,
            year: $("#adminEditYear").value,
            semester: $("#adminEditSem").value,
            section: $("#adminEditSec").value,
            password: $("#adminEditPassword").value
        };
        await api(`/api/admin/students/${id}`, {method: "PUT", body: JSON.stringify(data)});
        alert("Student updated.");
        $("#adminStudentEditFields").style.display = "none";
        $("#adminStudentSearch").value = "";
        await loadAdminStudents();
    };
}

let mcqQuestions = [];
let currentMcqIndex = 0;
let mcqTimeRemaining = 0;
let mcqInterval = null;

async function loadMcqQuestions() {
    mcqQuestions = await api("/api/mcqs");
    currentMcqIndex = 0;
    if (mcqQuestions.length > 0) {
        $("#mcqContainer").style.display = "block";
        $("#mcqFinished").style.display = "none";
        renderMcqQuestion();
    } else {
        $("#mcqContainer").style.display = "none";
        $("#mcqFinished").style.display = "block";
    }
}

function renderMcqQuestion() {
    if (currentMcqIndex >= mcqQuestions.length) {
        $("#mcqContainer").style.display = "none";
        $("#mcqFinished").style.display = "block";
        clearInterval(mcqInterval);
        return;
    }
    const q = mcqQuestions[currentMcqIndex];
    $("#mcqQuestionText").textContent = q.question;
    $("#mcqTextA").textContent = q.option_a;
    $("#mcqTextB").textContent = q.option_b;
    $("#mcqTextC").textContent = q.option_c;
    $("#mcqTextD").textContent = q.option_d;
    mcqTimeRemaining = q.time_limit || 30;
    
    clearInterval(mcqInterval);
    updateMcqTimerDisplay();
    mcqInterval = setInterval(() => {
        mcqTimeRemaining--;
        updateMcqTimerDisplay();
        if (mcqTimeRemaining <= 0) {
            clearInterval(mcqInterval);
            submitMcqAnswer(''); // Timeout
        }
    }, 1000);
}

function updateMcqTimerDisplay() {
    $("#mcqTimer").textContent = `Time left: ${mcqTimeRemaining}s`;
}

window.submitMcqAnswer = async (selected) => {
    clearInterval(mcqInterval);
    const q = mcqQuestions[currentMcqIndex];
    try {
        await api("/api/mcqs/submit", {method: "POST", body: JSON.stringify({mcq_id: q.id, selected_option: selected})});
    } catch(e) {}
    currentMcqIndex++;
    renderMcqQuestion();
};

function refreshMcqExam() {
    // Only basic liveness check if needed, logic is mostly client side per question
    api("/api/exam-state").then(state => {
        if (!state.active || state.locked) {
            unlocked = false;
            clearInterval(mcqInterval);
            alert(state.locked ? "Exam locked due to violation." : "Exam ended.");
            leaveExam();
        }
    }).catch(e => {});
    if (unlocked && window.isMcqExam) setTimeout(refreshMcqExam, 5000);
}

const origAdmin = admin;
admin = async function() {
    await origAdmin();
    await loadAdminStudents();
};

initSlider();
boot().catch(() => show("#login"));
