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

// ==================== EXAM LIFECYCLE STATE MODEL ====================
const EXAM_STATES = {
  NOT_STARTED: 'NOT_STARTED',
  ACTIVE: 'ACTIVE',
  LEAVE_CONFIRMATION_OPEN: 'LEAVE_CONFIRMATION_OPEN',
  FINALIZING: 'FINALIZING',
  FINISHED: 'FINISHED'
};
let examLifecycleState = EXAM_STATES.NOT_STARTED;
window.EXAM_STATES = EXAM_STATES;
window.getExamLifecycleState = () => examLifecycleState;

function setExamLifecycleState(newState) {
  const oldState = examLifecycleState;
  examLifecycleState = newState;
  window._examLifecycleState = newState;
  // Clear legacy flag to ensure full backward compatibility without suppressing violations
  window._leavingExam = false;
}

function isMonitoringActive() {
  return (
    unlocked &&
    !exam_finished &&
    (examLifecycleState === EXAM_STATES.ACTIVE ||
     examLifecycleState === EXAM_STATES.LEAVE_CONFIRMATION_OPEN ||
     examLifecycleState === EXAM_STATES.FINALIZING)
  );
}
window.isMonitoringActive = isMonitoringActive;

// Task 1: Per-(questionId_language) code store
// Code typed for each question+language combo is saved and restored when switching.
// Keys: "qid_lang", e.g. "5_python", "5_java", "12_c"
const codeStore = {};

async function api(url, options = {}) {
  const headers = options.body instanceof FormData ? {} : {"Content-Type": "application/json"};
  const response = await fetch(url, {headers, ...options});
  const data = await response.json();
  if (!response.ok) {
    // Task 4: Handle session expiry (another device logged in)
    if (response.status === 401 && data.session_expired) {
      exam_finished = true;
      unlocked = false;
      alert('You have been logged out because this account was logged in on another device.');
      location.reload();
      throw new Error(data.error);
    }
    throw new Error(data.error || "Request failed");
  }
  return data;
}
function show(id) {
  ["#login", "#student", "#student-home", "#teacher", "#admin", "#student-mcq"].forEach((s) => $(s)?.classList.add("hidden"));
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
    if (me.force_password_change) {
      showPasswordModal(true);
      return;
    }
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
$("#language").addEventListener("change", (e) => {
  // Task 1: Language select is disabled during exam (locked to question language by loadQ).
  // This listener only fires when NOT in an exam (teacher preview / non-exam view).
  // Save old code, restore new language's code for the same question.
  if (_currentQuestionId !== null && _currentLang !== null) {
    const oldKey = _currentQuestionId + '_' + _currentLang;
    codeStore[oldKey] = code.value;
    _currentLang = e.target.value;
    const newKey = _currentQuestionId + '_' + _currentLang;
    code.value = codeStore[newKey] || '';
    lines();
  }
  highlightCode();
});
// Initialize _prevValue on load
$("#language")._prevValue = $("#language").value;
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
  show("#student-home");
  $("#studentHomeName").textContent = me.name;
  $("#studentName").textContent = me.name;
  questions = await api("/api/questions");
  // Clean slate: do not pre-populate codeStore with stale drafts on home page
  fillQuestionSelect();
  $("#questionSelect").onchange = loadQ;
  $("#homeJoinExam").onclick = joinExam;
  $("#homeExamKey").addEventListener("keydown", (e) => {
    if (e.key === "Enter") joinExam();
  });
  security();
  await refreshExam(true);

  // Pre-exam extension check banner on student home page
  const updateHomeExtBanner = () => {
    const banner = $("#homeExtensionBanner");
    if (!banner) return;
    const ext = typeof window.checkActiveExtensions === 'function' ? window.checkActiveExtensions() : null;
    if (ext) {
      banner.style.display = "block";
      const nameEl = banner.querySelector("b");
      if (nameEl) {
        nameEl.textContent = `UNAUTHORIZED BROWSER EXTENSIONS DETECTED (${ext})!`;
      }
    } else {
      banner.style.display = "none";
    }
  };
  updateHomeExtBanner();
  setInterval(() => {
    if (!unlocked && !exam_finished && $("#student-home") && !$("#student-home").classList.contains("hidden")) {
      updateHomeExtBanner();
    }
  }, 1000);

  setInterval(async () => {
    if (unlocked && !exam_finished && _currentQuestionId) {
      try {
        await api('/api/draft', {
          method: 'POST',
          body: JSON.stringify({ question_id: _currentQuestionId, language: $("#language").value, code: code.value })
        });
      } catch (e) {}
    }
  }, 30000);
}

const markedForReview = new Set();
const submittedQuestions = new Set();

function renderQuestionPalette() {
  const container = $("#questionPalette");
  if (!container || !questions || !questions.length) return;
  container.innerHTML = questions.map((q, idx) => {
    const qid = String(q.id);
    const isActive = (qid === String(_currentQuestionId));
    const isSubmitted = submittedQuestions.has(qid);
    const isReview = markedForReview.has(qid);
    let cls = "palette-badge";
    if (isActive) cls += " active";
    if (isReview) cls += " review";
    if (isSubmitted) cls += " submitted";
    if (!isSubmitted && !isReview && !isActive) cls += " unsolved";
    return `<div class="${cls}" onclick="selectQuestionById('${qid}')" title="${esc(q.title)}">${idx + 1}</div>`;
  }).join("");
  const countEl = $("#paletteCount");
  if (countEl) countEl.textContent = `${submittedQuestions.size} / ${questions.length} Solved`;
}
window.renderQuestionPalette = renderQuestionPalette;

window.selectQuestionById = (qid) => {
  const sel = $("#questionSelect");
  if (sel) {
    sel.value = String(qid);
    loadQ();
  }
};

function updateMarkReviewButton() {
  const btn = $("#markReviewButton");
  if (!btn) return;
  const isMarked = markedForReview.has(String(_currentQuestionId));
  if (isMarked) {
    btn.innerHTML = "✓ Marked for Review";
    btn.classList.add("active");
  } else {
    btn.innerHTML = "📌 Mark for Review";
    btn.classList.remove("active");
  }
}

window.toggleMarkForReview = () => {
  if (!_currentQuestionId) return;
  const qid = String(_currentQuestionId);
  if (markedForReview.has(qid)) {
    markedForReview.delete(qid);
  } else {
    markedForReview.add(qid);
  }
  updateMarkReviewButton();
  renderQuestionPalette();
};

function fillQuestionSelect() {
  $("#questionSelect").innerHTML = questions.length ? questions.map((q) => `<option value="${q.id}">${esc(q.title)}</option>`).join("") : "<option>No question available</option>";
  $("#caseQuestion") && ($("#caseQuestion").innerHTML = questions.map((q) => `<option value="${q.id}">${esc(q.title)}</option>`).join(""));
  renderQuestionPalette();
}
let _currentQuestionId = null;
let _currentLang = null;
function loadQ() {
  const q = questions.find((item) => String(item.id) === $("#questionSelect").value);
  if (!q) return;
  const lang = $("#language");

  // Task 1+8: Determine the "new" question language (locked to question's language)
  const newQid = String(q.id);
  const allowedLangs = (q.language || "python").split(",").map(s => s.trim());
  const currentVal = _currentLang || lang.value;
  lang.innerHTML = allowedLangs.map(l => `<option value="${l}">${l.charAt(0).toUpperCase() + l.slice(1)}</option>`).join('');
  let newLang = allowedLangs.includes(currentVal) ? currentVal : allowedLangs[0];
  lang.value = newLang;

  // Task 1: Save current editor code into codeStore under old (question+language) key
  if (_currentQuestionId !== null && _currentLang !== null) {
    const oldKey = _currentQuestionId + '_' + _currentLang;
    codeStore[oldKey] = code.value;
  }

  // Task 8: Lock language only if 1 language available
  lang.value = newLang;
  lang._prevValue = newLang;
  lang.disabled = (allowedLangs.length <= 1);

  // Task 1: Restore code for new question+language key
  const newKey = newQid + '_' + newLang;
  const isDifferentQuestion = newQid !== _currentQuestionId;
  const isDifferentLang = newLang !== _currentLang;

  if (isDifferentQuestion || isDifferentLang) {
    // Switching question or language — restore saved code (or empty if first visit)
    code.value = codeStore[newKey] || '';
    if (isDifferentQuestion) {
      // Clear scores/results only when switching questions, not language
      lastScore = 0;
      $("#testResults").innerHTML = '';
      $("#saveState").textContent = 'Ready';
    }
    lines();
  }

  _currentQuestionId = newQid;
  _currentLang = newLang;
  $("#questionText").textContent = q.prompt;
  highlightCode();
  renderQuestionPalette();
  updateMarkReviewButton();
}
async function joinExam() {
  // Check for extensions BEFORE allowing entry into exam
  const ext = typeof window.checkActiveExtensions === 'function' ? window.checkActiveExtensions() : null;
  if (ext) {
    try {
      await api("/api/activity", {
        method: "POST",
        body: JSON.stringify({ event: "extension_detected", details: `Blocked on entry: ${ext}` })
      });
    } catch(_) {}
    if (typeof window.showExtensionPrompt === 'function') {
      window.showExtensionPrompt(ext);
    }
    return;
  }
  try {
    const key = $("#homeExamKey").value;
    const res = await api("/api/exam/join", {method: "POST", body: JSON.stringify({access_code: key})});
    try { await document.documentElement.requestFullscreen(); } catch (_) {}
    $("#homeGateMessage").textContent = "Exam unlocked.";
    // Task 4: Warning modal OK button - suppress violations and re-enter fullscreen
    const warnModal = $("#warningModal");
    const warnOkBtn = $("#warningOkBtn");
    if (warnModal) warnModal.classList.remove("hidden");
    if (warnOkBtn) {
      const handler = async () => {
        warnOkBtn.removeEventListener("click", handler);
        if (warnModal) warnModal.classList.add("hidden");
        // Suppress violations while re-entering fullscreen from the warning modal
        window._suppressViolations = true;
        try { await document.documentElement.requestFullscreen(); } catch (_) {}
        setTimeout(() => { window._suppressViolations = false; }, 2000);
      };
      warnOkBtn.addEventListener("click", handler);
    }
    if (res.exam_type === 'mcq') {
      window.isMcqExam = true;
      window._examType = 'mcq';
      show("#student-mcq");
      $("#studentMcqName").textContent = me.name;
      unlocked = true;
      exam_finished = false;
      setExamLifecycleState(EXAM_STATES.ACTIVE);
      await loadMcqQuestions();
      refreshMcqExam();
      return;
    }
    window._examType = 'coding';
    unlocked = true;
    exam_finished = false;
    setExamLifecycleState(EXAM_STATES.ACTIVE);
    // Task 1+8: Reset question tracking, codeStore, editor, and palette for 100% clean slate in this exam key
    _currentQuestionId = null;
    _currentLang = null;
    Object.keys(codeStore).forEach(k => delete codeStore[k]);
    if (code) code.value = "";
    if (highlight) highlight.textContent = "";
    lines();
    submittedQuestions.clear();
    markedForReview.clear();
    lastScore = 0;
    if ($("#testResults")) $("#testResults").innerHTML = "";
    if ($("#saveState")) $("#saveState").textContent = "Ready";
    if ($("#output")) $("#output").textContent = "";
    if ($("#termLive")) $("#termLive").textContent = "";

    // Unlock language select (will be re-locked per question by loadQ)
    $("#language").disabled = false;
    show("#student");
    fillQuestionSelect();
    loadQ();
    await refreshExam(true);
  } catch (error) {
    $("#homeGateMessage").textContent = error.message;
  }
}

async function exitExamToHome(reasonMessage, isSuccess = false) {
  setExamLifecycleState(EXAM_STATES.FINISHED);
  unlocked = false;
  exam_finished = true;
  window._leavingExam = false;
  window._suppressViolations = true;
  window.isMcqExam = false;
  window._examType = null;

  // Clear in-memory codeStore & editor state on exit
  Object.keys(codeStore).forEach(k => delete codeStore[k]);
  if (code) code.value = "";
  if (highlight) highlight.textContent = "";
  lines();
  submittedQuestions.clear();
  markedForReview.clear();
  lastScore = 0;
  _currentQuestionId = null;
  _currentLang = null;

  if (mcqInterval) {
    clearInterval(mcqInterval);
    mcqInterval = null;
  }

  // Close all modal dialogs
  ["#leavePrompt", "#fsPrompt", "#extPrompt", "#warningModal"].forEach((sel) => {
    const el = document.querySelector(sel);
    if (el) el.classList.add("hidden");
  });

  if (typeof setEditorLock === "function") {
    setEditorLock(true, "Exam ended");
  }

  // Safely exit fullscreen
  try {
    if (document.fullscreenElement) {
      await document.exitFullscreen();
    }
  } catch (_) {}

  // Transition to student-home
  show("#student-home");

  const scBtn = document.getElementById("viewScorecardBtn");
  if (scBtn) scBtn.style.display = "block";
  if (isSuccess) {
    setTimeout(() => {
      if (typeof window.openStudentResultModal === 'function') {
        window.openStudentResultModal();
      }
    }, 600);
  }

  const gateMsg = document.getElementById("homeGateMessage");
  if (gateMsg && reasonMessage) {
    const color = isSuccess ? "#10b981" : "#f59e0b";
    const icon = isSuccess ? "✅" : "⚠️";
    gateMsg.innerHTML = `<span style="color:${color}; font-weight:700;">${icon} ${reasonMessage}</span>`;
  }

  const homeKey = document.getElementById("homeExamKey");
  if (homeKey) homeKey.value = "";

  const langSel = document.getElementById("language");
  if (langSel) langSel.disabled = false;
}
window.exitExamToHome = exitExamToHome;

async function refreshExam(force = false) {
  const state = await api("/api/exam-state");
  if (me.role === "student") {
    // Task 2: For coding exam, require remaining>0. For MCQ-only, active is enough.
    const isCodingExam = (window._examType === 'coding');
    const isMcqExam = (window._examType === 'mcq' || window.isMcqExam);
    const currentlyInExam = !$("#student")?.classList.contains("hidden") || !$("#student-mcq")?.classList.contains("hidden");

    unlocked = isCodingExam
      ? Boolean(state.active && state.joined && !state.locked && state.remaining > 0)
      : (isMcqExam ? Boolean(state.active && state.joined && !state.locked) : false);

    if (unlocked && examLifecycleState === EXAM_STATES.NOT_STARTED) {
      setExamLifecycleState(EXAM_STATES.ACTIVE);
    }

    if (!unlocked) $("#fsPrompt")?.classList.add("hidden");
    // Task 2: Show timer only for coding exam
    $("#examTimer").textContent = (state.active && isCodingExam) ? `Time left ${fmt(state.remaining)}` : (state.active ? "Exam active" : "Waiting");
    $("#illegalCount").textContent = `Tab switches: ${state.illegal_count || 0}`;

    // If student is currently inside any exam workspace and the exam has ended / timed out / stopped:
    if (currentlyInExam) {
      if (!state.active) {
        await exitExamToHome("Exam ended. You have been returned to home.", false);
        return;
      }
      if (state.locked) {
        await exitExamToHome("Exam locked due to security policy violations.", false);
        return;
      }
      if (isCodingExam && state.remaining <= 0) {
        try {
          await api("/api/exam/student-exit", {
            method: "POST",
            body: JSON.stringify({ reason: "Time Over - Exam Duration Ended" })
          });
        } catch(_) {}
        await exitExamToHome("⏰ Exam time is over. Your solutions have been submitted.", true);
        return;
      }
      if (!state.joined) {
        await exitExamToHome(state.allowed === false ? "Not selected for this exam." : "Exam session ended. Please enter exam key.", false);
        return;
      }
    }

    if (!unlocked && !currentlyInExam) {
      show("#student-home");
      const scBtn = $("#viewScorecardBtn");
      if (scBtn) scBtn.style.display = state.has_result ? "block" : "none";
    } else if (unlocked && isCodingExam) {
      show("#student");
      if (typeof checkExternalMonitors === 'function') checkExternalMonitors();
    }

    if (state.locked) {
      setEditorLock(true, "Locked by supervisor");
      $("#output").textContent = "Exam locked because illegal activity crossed the allowed limit.";
    } else if (!state.active) {
      setEditorLock(true, "Exam not started");
    } else if (!state.joined) {
      setEditorLock(true, state.allowed === false ? "Not selected for this exam" : "Enter exam key");
    } else if (isCodingExam && state.remaining <= 0) {
      setEditorLock(true, "Time over");
      $("#output").textContent = "Exam time is over.";
    } else {
      setEditorLock(false);
      // Task 8: Re-lock language to current question
      if (_currentQuestionId) loadQ();
    }
    if (!refreshExam._questionTick) refreshExam._questionTick = 0;
    refreshExam._questionTick++;
    if (refreshExam._questionTick >= 5 && unlocked) {
      refreshExam._questionTick = 0;
      try {
        const newQuestions = await api("/api/questions");
        if (JSON.stringify(newQuestions) !== JSON.stringify(questions)) {
          questions = newQuestions;
          const currentVal = $("#questionSelect").value;
          fillQuestionSelect();
          if (currentVal && questions.find(q => String(q.id) === currentVal)) {
            $("#questionSelect").value = currentVal;
          }
          loadQ();
        }
      } catch(_) {}
    }
    clearTimeout(timerId);
    timerId = setTimeout(refreshExam, 1000);
  }
}
function security() {
  if (securityBound) return;
  securityBound = true;

  // ── Unified departure / return focus state machine ───────────────────────
  let focusLossState = "FOCUSED"; // "FOCUSED" | "AWAY"
  let focusLostAt = 0;
  let lastViolationSentAt = 0;

  const record = (event, details = "") => {
    if (!isMonitoringActive()) return;
    if (window._suppressViolations) return;
    if (window._extPromptOpen && event !== "extension_detected") return;
    const now = Date.now();
    if (now - lastViolationSentAt < 350) return;   // 350ms debounce window
    lastViolationSentAt = now;
    sendActivity(event, details);
  };

  function handleFocusLoss(source, details = "") {
    if (!isMonitoringActive()) return;
    if (focusLossState === "AWAY") return;
    focusLossState = "AWAY";
    focusLostAt = Date.now();
    const eventType = document.hidden ? "tab_hidden" : "window_blur";
    const eventDetails = details || (document.hidden ? "visibility_hidden" : (source || "window_focus_lost"));
    record(eventType, eventDetails);
  }

  function handleFocusGain(source) {
    if (focusLossState !== "AWAY") return;
    const checkGain = () => {
      if (focusLossState !== "AWAY") return;
      if (!document.hidden && document.hasFocus()) {
        const awayMs = focusLostAt ? (Date.now() - focusLostAt) : 0;
        focusLossState = "FOCUSED";
        focusLostAt = 0;
        if (isMonitoringActive()) {
          record("window_refocus", `returned_after_${Math.round(awayMs / 1000)}s`);
        }
      }
    };
    if (document.hasFocus()) {
      checkGain();
    } else {
      setTimeout(checkGain, 50);
    }
  }

  // ── 1. Page Visibility API ──────────────────────────────────────────────
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      handleFocusLoss("visibilitychange", "visibility_hidden");
    } else {
      handleFocusGain("visibilitychange");
    }
  });

  // ── 2. Window blur & focus ──────────────────────────────────────────────
  window.addEventListener("blur", () => {
    handleFocusLoss("window_blur", "focus_lost");
  });
  window.addEventListener("focus", () => {
    handleFocusGain("window_focus");
  });

  // ── 5. Fullscreen exit ──────────────────────────────────────────────────
  document.addEventListener("fullscreenchange", () => {
    if (document.fullscreenElement) {
      $(["#fsPrompt"])?.classList.add("hidden");
      return;
    }
    if (!isMonitoringActive()) return;
    record("fullscreen_exit", "exited_fullscreen");
    $("#fsPrompt")?.classList.remove("hidden");
  });

  // ── 6. Screen size shrink ────────────────────────────────────────────────
  window.addEventListener("resize", () => {
    if (!isMonitoringActive()) return;
    if (innerWidth < window.screen.width * 0.9 || innerHeight < window.screen.height * 0.9) {
      record("screen_resize", `${innerWidth}x${innerHeight}`);
    }
  });

  // ── 6b. Dual / External Monitor Blocker ──────────────────────────────────
  function checkExternalMonitors() {
    if (!isMonitoringActive()) return;
    const isMultiScreen = Boolean(
      (window.screen && window.screen.isExtended) ||
      (window.screen && window.screen.availWidth > window.screen.width * 1.05) ||
      (window.screenLeft !== undefined && (window.screenLeft < -50 || window.screenLeft > window.screen.width * 0.95))
    );
    if (isMultiScreen) {
      window._externalMonitorActive = true;
      $("#externalMonitorPrompt")?.classList.remove("hidden");
      record("external_display_detected", "Dual/External Monitor or HDMI display active");
    } else {
      window._externalMonitorActive = false;
      $("#externalMonitorPrompt")?.classList.add("hidden");
    }
  }
  window.checkExternalMonitors = checkExternalMonitors;
  window.addEventListener("resize", checkExternalMonitors);
  if (window.screen && window.screen.orientation) {
    window.screen.orientation.addEventListener("change", checkExternalMonitors);
  }

  // ── 7. Keyboard: ONLY track strictly prohibited keys → violation ─────────
  document.addEventListener("keydown", (event) => {
    if (!isMonitoringActive()) return;
    const key = event.key;
    const keyL = key.toLowerCase();

    // 1. F1–F12
    const isFKey = /^f([1-9]|1[0-2])$/i.test(key);
    // 2. Alt / Alt+Tab / Alt+combinations (frequently used for extensions like Monica/Harpa)
    const isAlt = key === "Alt" || event.altKey;
    // 3. PrintScreen
    const isPrtSc = key === "PrintScreen" || key === "F13";
    // 4. Windows / Copilot
    const isMeta = key === "Meta" || key === "OS" || key === "Win" || key === "Copilot";
    // 5. Escape
    const isEsc = key === "Escape";

    if (isFKey || isAlt || isPrtSc || isMeta || isEsc) {
      event.preventDefault();
      event.stopPropagation();
      let label = key;
      if (isMeta) label = "Windows / Copilot";
      else if (isPrtSc) label = "PrintScreen";
      else if (isAlt) label = event.key === "Tab" ? "Alt + Tab" : `Alt + ${key}`;
      else if (isEsc) label = "Escape";
      record("blocked_key", label);
      return;
    }

    // Ctrl shortcuts (Monica Ctrl+M, Sider Ctrl+P/B, etc.)
    if (event.ctrlKey || event.metaKey) {
      if (event.shiftKey) {
        event.preventDefault();
        event.stopPropagation();
        record("blocked_key", `Ctrl + Shift + ${key}`);
        return;
      }

      // Prohibit copy, paste, extension hotkeys (m, p, b, j, k, u, h, g, e)
      const prohibitedCtrl = ['c', 'v', 'x', 'm', 'p', 'b', 'j', 'k', 'u', 'h', 'g', 'e', 's', 'r', 'f', 'o'];
      if (prohibitedCtrl.includes(keyL)) {
        event.preventDefault();
        event.stopPropagation();
        let comboName = keyL === 'v' ? "Ctrl + V (Paste)" : (keyL === 'c' ? "Ctrl + C (Copy)" : (keyL === 'm' ? "Ctrl + M (AI Extension)" : `Ctrl + ${key.toUpperCase()}`));
        record("blocked_key", comboName);
        return;
      }
    }
  }, true);

  // ── 8. Block right-click context menu ────────────────────────────────────
  document.addEventListener("contextmenu", (event) => {
    if (isMonitoringActive()) {
      event.preventDefault();
      record("blocked_key", "Right Click (Mouse)");
    }
  });

  // ── 9. Block copy via clipboard API ─────────────────────────────────────
  document.addEventListener("copy",  (event) => { if (isMonitoringActive()) { event.preventDefault(); record("blocked_key", "Clipboard Copy"); } });
  document.addEventListener("cut",   (event) => { if (isMonitoringActive()) { event.preventDefault(); record("blocked_key", "Clipboard Cut"); } });
  document.addEventListener("paste", (event) => { if (isMonitoringActive()) { event.preventDefault(); record("blocked_key", "Clipboard Paste"); } });

  // ── 10. Touch: 3-finger swipe (for touchpad laptops) ────────────────────
  let _tStart = [], _tInProgress = false, _tLast = 0;
  document.addEventListener("touchstart", (e) => {
    if (e.touches.length === 3) {
      _tStart = Array.from(e.touches).map(t => ({ x: t.clientX, y: t.clientY }));
      _tInProgress = false;
    } else { _tStart = []; }
  }, { passive: true });

  document.addEventListener("touchmove", (e) => {
    if (e.touches.length !== 3 || _tStart.length !== 3 || _tInProgress) return;
    const now = Date.now();
    if (now - _tLast < 2000) return;
    let moved = true;
    let sumDx = 0, sumDy = 0;
    Array.from(e.touches).forEach((t, i) => {
      const dx = t.clientX - _tStart[i].x, dy = t.clientY - _tStart[i].y;
      if (Math.sqrt(dx*dx + dy*dy) < 10) moved = false;
      sumDx += dx; sumDy += dy;
    });
    if (!moved) return;
    if (Math.sqrt((sumDx/3)**2 + (sumDy/3)**2) >= 30) {
      if (!isMonitoringActive()) return;
      _tInProgress = true; _tLast = now;
      record("three_finger_gesture", "touchpad_swipe");
    }
  }, { passive: true });

  document.addEventListener("touchend", () => { _tStart = []; _tInProgress = false; }, { passive: true });

  // ── 11. Beacon on close/refresh ─────────────────────────────────────────
  window.addEventListener("beforeunload", () => {
    if (isMonitoringActive()) navigator.sendBeacon("/api/activity", new Blob([JSON.stringify({event:"attempt_close"})], {type:"application/json"}));
  });

  // ── 12. Active Window & Sidebar Focus Tracker ───────────────────────────
  setInterval(() => {
    if (!isMonitoringActive()) return;
    if (!document.hasFocus() || document.hidden) {
      handleFocusLoss("active_sidebar_or_window_focus", "active_sidebar_or_window_focus");
    } else {
      handleFocusGain("focus_interval");
    }
  }, 400);

  // ── 13. Deep Comprehensive Browser Extension & AI Script Detector ─────────
  const nativeAppIds = new Set([
    'cursorGlow', 'leavePrompt', 'leaveNoBtn', 'leaveYesBtn', 'warningModal', 'warningOkBtn', 
    'fsPrompt', 'fsResumeBtn', 'extPrompt', 'detectedExtName', 'extPromptStatus', 'extRecheckBtn',
    'intro', 'auth', 'authForm', 'tabStudent', 'tabTeacher', 'tabAdmin', 'username', 'password', 'authSubmit',
    'student-home', 'homeExamKey', 'homeJoinExam', 'homeGateMessage', 'studentHomeName', 'homeExtensionBanner',
    'student', 'studentName', 'examTimer', 'questionSelect', 'questionText', 'profileInfo', 'illegalCount',
    'language', 'saveState', 'runButton', 'verifyButton', 'submitButton', 'studentLeaveButton',
    'gutter', 'highlight', 'code', 'terminalPanel', 'consoleDragHandle', 'status', 'output', 'termLive',
    'testResultsDragHandle', 'testResults', 'student-mcq', 'studentMcqName', 'mcqTimer', 'mcqContainer',
    'mcqQuestionText', 'mcqOptions', 'mcqOptA', 'mcqOptB', 'mcqOptC', 'mcqOptD', 'mcqTextA', 'mcqTextB',
    'mcqTextC', 'mcqTextD', 'mcqFinished', 'teacher', 'admin', 'passwordModal', 'codeViewModal', 'historyModal',
    'developerWatermark', 'codeViewTitle', 'codeViewSubtitle', 'codeViewCardsContainer', 'historyModalTitle',
    'historyModalMeta', 'historyModalBody', 'historyList', 'adminHistoryList', 'teacherExtensionAlert',
    'adminExtensionAlert', 'collegeSlider'
  ]);

  const nativeTagNames = new Set([
    'html', 'head', 'body', 'meta', 'title', 'link', 'style', 'script', 'main', 'header', 'section',
    'aside', 'article', 'div', 'span', 'b', 'small', 'p', 'h1', 'h2', 'h3', 'form', 'label', 'input',
    'button', 'select', 'option', 'textarea', 'pre', 'img', 'table', 'thead', 'tbody', 'tr', 'th', 'td',
    'optgroup', 'i', 'strong', 'em', 'svg', 'path', 'circle', 'line', 'polyline'
  ]);

  const suspiciousGlobals = [
    { key: '__always_active', name: 'Always Active Window' },
    { key: 'alwaysActive', name: 'Always Active Window' },
    { key: '__active_window', name: 'Active Window Spoofer' },
    { key: '__focus_keeper', name: 'Focus Keeper Extension' },
    { key: '__blur_blocker', name: 'Anti-Blur Extension' },
    { key: '__keep_active', name: 'Keep Active Extension' },
    { key: '__spoofer', name: 'Focus Spoofer' },
    { key: 'ActiveWindow', name: 'Active Window Extension' },
    { key: 'AlwaysActive', name: 'Always Active Extension' },
    { key: '__monica', name: 'Monica AI' },
    { key: 'Monica', name: 'Monica AI' },
    { key: '__sider', name: 'Sider AI' },
    { key: 'Sider', name: 'Sider AI' },
    { key: '__maxai', name: 'MaxAI' },
    { key: '__harpa', name: 'Harpa AI' },
    { key: '__merlin', name: 'Merlin AI' },
    { key: 'ChatGPT', name: 'ChatGPT Extension' },
    { key: '__grammarly', name: 'Grammarly' },
    { key: 'Grammarly', name: 'Grammarly' },
    { key: '__tampermonkey', name: 'Tampermonkey' },
    { key: '__violentmonkey', name: 'Violentmonkey' },
    { key: '__quillbot', name: 'Quillbot' },
    { key: 'GM_info', name: 'Userscript Engine' }
  ];

  // Purge dead/orphaned DOM elements and globals left behind by disabled extensions
  function purgeExtensionResidues() {
    try {
      const extAttrs = [
        'data-gr-ext-installed', 'data-monica-installed', 'data-monica', 'data-sider',
        'data-quillbot', 'data-copilot', 'data-grammarly-part', 'data-always-active',
        'data-active-window', 'data-extension-id', 'data-installed-extension', 'data-injected-by'
      ];
      extAttrs.forEach(attr => {
        document.documentElement.removeAttribute(attr);
        if (document.body) document.body.removeAttribute(attr);
      });

      const all = Array.from(document.querySelectorAll('*'));
      for (let i = 0; i < all.length; i++) {
        const el = all[i];
        if (!el || !el.parentNode) continue;
        if (nativeAppIds.has(el.id)) continue;

        const tag = (el.tagName || '').toLowerCase();
        const id = (el.id || '').toLowerCase();
        const cls = (typeof el.className === 'string' ? el.className : '').toLowerCase();

        let isExtNode = false;
        if (tag.includes('-') && !nativeTagNames.has(tag)) {
          isExtNode = true;
        } else if (el.shadowRoot) {
          isExtNode = true;
        } else if (tag === 'iframe') {
          isExtNode = true;
        } else if (
          id.includes('monica') || cls.includes('monica') || tag.includes('monica') ||
          id.includes('sider') || cls.includes('sider') || tag.includes('sider') ||
          id.includes('chatgpt') || cls.includes('chatgpt') || tag.includes('chatgpt') || id.includes('chathub') || id.includes('openai') ||
          id.includes('harpa') || cls.includes('harpa') || tag.includes('harpa') ||
          id.includes('maxai') || cls.includes('maxai') || tag.includes('maxai') || tag.includes('use-chat-gpt') ||
          id.includes('merlin') || cls.includes('merlin') || tag.includes('merlin') ||
          id.includes('quillbot') || cls.includes('quillbot') || tag.includes('quillbot') ||
          id.includes('copilot') || cls.includes('copilot') || tag.includes('copilot') ||
          id.includes('grammarly') || cls.includes('grammarly') || tag.includes('grammarly') ||
          id.includes('tampermonkey') || id.includes('violentmonkey') || cls.includes('tampermonkey') ||
          id.includes('active-window') || cls.includes('active-window') || id.includes('always-active') || cls.includes('always-active') ||
          id.includes('focus-keeper') || cls.includes('focus-keeper') || id.includes('keep-active') || cls.includes('keep-active') ||
          id.includes('anti-blur') || cls.includes('anti-blur') || id.includes('fake-focus') || cls.includes('fake-focus') ||
          id.includes('focus-fix') || cls.includes('focus-fix') || id.includes('blur-block') || cls.includes('blur-block') ||
          el.hasAttribute('data-always-active') || el.hasAttribute('data-active-window') ||
          el.hasAttribute('data-extension-id') || el.hasAttribute('data-installed-extension') || el.hasAttribute('data-injected-by')
        ) {
          isExtNode = true;
        }

        if (isExtNode) {
          try { el.remove(); } catch(_) { try { el.parentNode.removeChild(el); } catch(_) {} }
        }
      }

      // Remove any injected extension stylesheets / scripts
      const injectedResources = document.querySelectorAll('link[href*="chrome-extension://"], script[src*="chrome-extension://"], style[id*="monica"], style[id*="sider"], style[id*="harpa"], style[id*="merlin"], style[class*="monica"], style[class*="sider"]');
      injectedResources.forEach(res => {
        try { res.remove(); } catch(_) {}
      });

      // Clear globals
      suspiciousGlobals.forEach(g => {
        try { delete window[g.key]; } catch(_) {}
        try { window[g.key] = undefined; } catch(_) {}
      });

      // Restore document.hasFocus if shadowed
      if (Object.prototype.hasOwnProperty.call(document, 'hasFocus')) {
        try { delete document.hasFocus; } catch(_) {}
      }
    } catch(e) {
      console.warn('purgeExtensionResidues error:', e);
    }
  }
  window.purgeExtensionResidues = purgeExtensionResidues;

  function checkActiveExtensions() {
    // 0. Check root document attributes for installed extensions
    try {
      const rootAttrs = [
        'data-always-active', 'data-active-window', 'data-focus-fix', 'data-focus-keeper',
        'data-anti-blur', 'data-monica', 'data-sider', 'data-gr-ext-installed',
        'data-monica-installed', 'data-extension-id', 'data-installed-extension', 'data-injected-by'
      ];
      for (const attr of rootAttrs) {
        if (document.documentElement.hasAttribute(attr) || (document.body && document.body.hasAttribute(attr))) {
          return `Active Extension Signature (${attr})`;
        }
      }
    } catch(_) {}

    // A. Scan elements in the DOM
    const all = document.querySelectorAll('*');
    for (let i = 0; i < all.length; i++) {
      const el = all[i];
      if (nativeAppIds.has(el.id)) continue;

      const tag = (el.tagName || '').toLowerCase();
      const id = (el.id || '').toLowerCase();
      const cls = (typeof el.className === 'string' ? el.className : '').toLowerCase();

      // 1. Any shadowRoot attached
      if (el.shadowRoot) {
        return `ShadowRoot Extension (${tag})`;
      }

      // 2. Any custom Web Component tag with hyphen
      if (tag.includes('-') && !nativeTagNames.has(tag)) {
        return `Custom Element <${tag}>`;
      }

      // 3. Any iframe injected
      if (tag === 'iframe') {
        return 'Unauthorized Injected IFrame';
      }

      // 4. Focus Spoofers / "Active on Windows" / "Always Active" / Anti-Blur Extensions
      if (id.includes('active-window') || cls.includes('active-window') || id.includes('always-active') || cls.includes('always-active') ||
          id.includes('focus-keeper') || cls.includes('focus-keeper') || id.includes('keep-active') || cls.includes('keep-active') ||
          id.includes('anti-blur') || cls.includes('anti-blur') || id.includes('fake-focus') || cls.includes('fake-focus') ||
          id.includes('focus-fix') || cls.includes('focus-fix') || id.includes('blur-block') || cls.includes('blur-block') ||
          el.hasAttribute('data-always-active') || el.hasAttribute('data-active-window')) {
        return 'Active Windows / Focus Spoofer Extension';
      }

      // 5. Specific extension signatures (Monica, Sider, Harpa, MaxAI, Merlin, ChatGPT, Grammarly, etc.)
      if (id.includes('monica') || cls.includes('monica') || el.hasAttribute('data-monica') || tag.includes('monica')) {
        return 'Monica AI';
      }
      if (id.includes('sider') || cls.includes('sider') || el.hasAttribute('data-sider') || tag.includes('sider')) {
        return 'Sider AI';
      }
      if (id.includes('chatgpt') || cls.includes('chatgpt') || tag.includes('chatgpt') || id.includes('chathub') || id.includes('openai')) {
        return 'ChatGPT Assistant';
      }
      if (id.includes('harpa') || cls.includes('harpa') || tag.includes('harpa')) {
        return 'Harpa AI';
      }
      if (id.includes('maxai') || cls.includes('maxai') || tag.includes('maxai') || tag.includes('use-chat-gpt')) {
        return 'MaxAI';
      }
      if (id.includes('merlin') || cls.includes('merlin') || tag.includes('merlin')) {
        return 'Merlin AI';
      }
      if (id.includes('quillbot') || cls.includes('quillbot') || tag.includes('quillbot') || el.hasAttribute('data-quillbot')) {
        return 'Quillbot';
      }
      if (id.includes('copilot') || cls.includes('copilot') || tag.includes('copilot') || el.hasAttribute('data-copilot')) {
        return 'Copilot';
      }
      if (id.includes('grammarly') || cls.includes('grammarly') || tag.includes('grammarly') || el.hasAttribute('data-grammarly-part')) {
        return 'Grammarly';
      }
      if (id.includes('tampermonkey') || id.includes('violentmonkey') || cls.includes('tampermonkey')) {
        return 'Userscript / Tampermonkey';
      }

      // 6. Generic Extension data attributes
      if (el.hasAttribute('data-extension-id') || el.hasAttribute('data-installed-extension') || el.hasAttribute('data-injected-by')) {
        return 'Browser Extension Element';
      }
    }

    // B. Suspicious Global Variables & Functions
    for (const g of suspiciousGlobals) {
      if (window[g.key] !== undefined) {
        return g.name;
      }
    }

    // C. Focus / Visibility API Tampering Detection (Active Windows / Always Active / Anti-Blur)
    try {
      // 1. Check if document has its OWN property 'hasFocus' (it should be on Document.prototype)
      if (Object.prototype.hasOwnProperty.call(document, 'hasFocus')) {
        return 'Active Windows / Focus Spoofer Extension';
      }
      // 2. Check function signature of document.hasFocus
      if (typeof document.hasFocus === 'function') {
        const fnStr = Function.prototype.toString.call(document.hasFocus);
        if (!fnStr.includes('[native code]') || !fnStr.includes('hasFocus')) {
          return 'Active Windows / Focus Spoofer Extension';
        }
      }
      // 3. Check if document has its OWN property 'visibilityState' or 'hidden'
      if (Object.prototype.hasOwnProperty.call(document, 'visibilityState') ||
          Object.prototype.hasOwnProperty.call(document, 'hidden')) {
        return 'Active Windows / Page Visibility Tamper Extension';
      }
      // 4. Check Document.prototype.hasFocus
      if (Document.prototype && Document.prototype.hasFocus) {
        const protoStr = Function.prototype.toString.call(Document.prototype.hasFocus);
        if (!protoStr.includes('[native code]')) {
          return 'Active Windows / Focus Spoofer Extension';
        }
      }
      // 5. Injected extension stylesheet / script tags
      const extLinks = document.querySelectorAll('link[href*="chrome-extension://"], script[src*="chrome-extension://"], style[id*="monica"], style[id*="sider"], style[id*="harpa"], style[id*="merlin"], style[id*="always-active"], style[id*="active-window"]');
      if (extLinks.length > 0) {
        return 'Injected Chrome Extension Resource';
      }
    } catch(_) {}

    return null;
  }
  window.checkActiveExtensions = checkActiveExtensions;

  window._extPromptOpen = false;

  function detectExtensions() {
    if (!isMonitoringActive()) return;
    if (window._extPromptOpen) return; // Modal is already open, do not spam!
    const extName = checkActiveExtensions();
    if (extName) {
      window._extPromptOpen = true;
      sendActivity("extension_detected", `Active Extension: ${extName}`);
      showExtensionPrompt(extName);
    }
  }

  function showExtensionPrompt(name) {
    const modal = document.getElementById("extPrompt");
    if (modal) {
      const statusEl = document.getElementById("extPromptStatus");
      if (statusEl) {
        statusEl.style.display = "block";
        statusEl.innerHTML = `<span style="color:#ef4444; font-weight:700;">⚠️ Unauthorized Browser Extension Detected (${name})!</span><br>Please open browser settings (<code>chrome://extensions</code>), turn off or remove <b>ALL</b> extensions, then click Re-Check below.`;
      }
      modal.classList.remove("hidden");
    }
  }
  window.showExtensionPrompt = showExtensionPrompt;

  window.recheckExtensions = async function(isSilent = false) {
    if (!isSilent) {
      // User pressed Re-Check & Re-Verify! Refresh website to completely purge injected extension scripts & cache from browser memory
      const recheckBtn = document.getElementById("extRecheckBtn");
      if (recheckBtn) {
        recheckBtn.disabled = true;
        recheckBtn.textContent = "🔄 Refreshing & Clearing Cache...";
      }
      setTimeout(() => {
        window.location.reload();
      }, 150);
      return;
    }

    // Pass 1: Silent immediate background scan
    let extName = checkActiveExtensions();

    // Trigger test events to provoke active extension scripts
    try {
      const probe = document.createElement("span");
      probe.id = "__ext_probe";
      probe.style.cssText = "position:absolute;left:-9999px;opacity:0;";
      probe.textContent = "extension probe check";
      document.body.appendChild(probe);
      const sel = window.getSelection();
      const range = document.createRange();
      range.selectNodeContents(probe);
      sel.removeAllRanges();
      sel.addRange(range);
      document.dispatchEvent(new Event("selectionchange"));
      document.dispatchEvent(new MouseEvent("mousemove", { bubbles: true, clientX: 10, clientY: 10 }));
      sel.removeAllRanges();
      probe.remove();
    } catch (_) {}

    if (!extName) {
      extName = checkActiveExtensions();
    }

    if (!extName) {
      // 100% clean!
      window._extPromptOpen = false;
      const modal = document.getElementById("extPrompt");
      if (modal) modal.classList.add("hidden");
      const statusEl = document.getElementById("extPromptStatus");
      if (statusEl) statusEl.style.display = "none";
      window._suppressViolations = true;
      if (unlocked) {
        try { await document.documentElement.requestFullscreen(); } catch(_) {}
        setTimeout(() => { window._suppressViolations = false; }, 2000);
      }
      alert("✅ All extensions removed! You may now resume your exam.");
    }
  };

  // When student returns focus to the window after disabling extensions, auto re-check cleanly
  window.addEventListener("focus", async () => {
    if (window._extPromptOpen && unlocked && !exam_finished) {
      await window.recheckExtensions(true);
    }
  });

  // Continuous 1-second active extension scan
  setInterval(detectExtensions, 1000);
  setTimeout(detectExtensions, 400);

  // Auto-scan every 1 second during prompt so exam resumes immediately when extensions are disabled
  setInterval(async () => {
    if (window._extPromptOpen && unlocked && !exam_finished) {
      const stillActive = checkActiveExtensions();
      if (!stillActive) {
        await window.recheckExtensions(true);
      }
    }
  }, 1000);

  // Trigger detection on selection change (when extensions show tooltip/floating popup)
  document.addEventListener("selectionchange", () => {
    if (!isMonitoringActive()) return;
    if (!window._extPromptOpen) setTimeout(detectExtensions, 150);
  });

  // Dynamic MutationObserver for newly injected extension elements or attribute changes
  try {
    const observer = new MutationObserver(() => {
      if (!isMonitoringActive()) return;
      if (!window._extPromptOpen) detectExtensions();
    });
    observer.observe(document.documentElement, { childList: true, subtree: true, attributes: true });
  } catch(e) {}
}

async function sendActivity(event, details = "") {
  if (!isMonitoringActive()) return;
  try {
    const data = await api("/api/activity", {method: "POST", body: JSON.stringify({event, details})});
    const currentCount = Math.min(5, data.illegal_count || 0);
    $("#illegalCount").textContent = `Tab switches: ${currentCount} / 5`;
    if (data.ignored) return;
    if (data.locked || currentCount >= 5) {
      setExamLifecycleState(EXAM_STATES.FINISHED);
      unlocked = false;
      exam_finished = true;
      setEditorLock(true, "Exam locked due to violation limit (5/5)");
      $("#leavePrompt")?.classList.add("hidden");
      $("#fsPrompt")?.classList.add("hidden");
      $("#extPrompt")?.classList.add("hidden");
      alert("Fifth violation (5/5): Your exam has been locked and submitted due to security policy.");
      try { await document.exitFullscreen(); } catch (_) {}
      show("#student-home");
      const gateMsg = document.getElementById("homeGateMessage");
      if (gateMsg) {
        gateMsg.innerHTML = `<span style="color:#ef4444; font-weight:700;">🔒 Exam locked: Violation limit (5/5) exceeded. Contact your teacher.</span>`;
      }
      await refreshExam(true);
      return;
    }
    window._suppressViolations = true;
    const warning = ["", "First warning", "Second warning", "Third warning", "Fourth warning"][currentCount] || "Warning";
    $("#fsPrompt")?.classList.add("hidden");
    alert(`${warning}: Violation detected (${currentCount}/5). Return to fullscreen to continue.`);
    try { await document.documentElement.requestFullscreen(); } catch(_) {}
    setTimeout(() => { window._suppressViolations = false; }, 2000);
    if (document.fullscreenElement) {
      $("#fsPrompt")?.classList.add("hidden");
    } else {
      $("#fsPrompt")?.classList.remove("hidden");
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
    $("#testResults").innerHTML = `<div style="font-size:1.1em; margin-bottom:10px;"><b>Score: ${data.score}</b></div>` + data.results.map((r) => 
      `<div class="${r.passed ? "ok" : "bad"}" style="padding: 10px; border: 1px solid var(--border); margin-bottom: 8px; border-radius: 6px;">
        <div style="display:flex; justify-content:space-between; margin-bottom: 6px;">
          <strong>${esc(r.name)}</strong>
          <strong>${r.passed ? "Accepted" : "Failed"} (${r.marks} marks)</strong>
        </div>
        ${r.visible ? `<div style="font-family: monospace; font-size: 0.9em;">
          <div style="color: var(--text-2); margin-top: 4px;">Expected Output:</div>
          <pre style="margin: 4px 0 8px 0; padding: 6px; background: rgba(0,0,0,0.1); border-radius: 4px; white-space: pre-wrap;">${esc(r.expected) || ' '}</pre>
          <div style="color: var(--text-2);">Your Output:</div>
          <pre style="margin: 4px 0 0 0; padding: 6px; background: rgba(0,0,0,0.1); border-radius: 4px; white-space: pre-wrap;">${esc(r.output) || ' '}</pre>
        </div>` : `<div style="color: var(--text-2); font-style: italic; margin-top: 4px;">Hidden test case</div>`}
      </div>`
    ).join("");
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
  const res = await api("/api/submit", {method: "POST", body: JSON.stringify({question_id: q.id, code: code.value, language: $("#language").value, score: lastScore})});
  $("#saveState").textContent = "Submitted";
  // Task 1: Show live score feedback so student knows their score was saved
  if (res.live_score !== undefined) {
    $("#testResults").innerHTML = `<div class="ok"><b>Submitted! Live score: ${res.live_score} / ${lastScore}</b></div>` + ($("#testResults").innerHTML || '');
  }
  submittedQuestions.add(String(q.id));
  renderQuestionPalette();
}
async function leaveExam() {
  if (!isMonitoringActive()) return;
  setExamLifecycleState(EXAM_STATES.LEAVE_CONFIRMATION_OPEN);
  const promptEl = document.getElementById("leavePrompt");
  if (promptEl) promptEl.classList.remove("hidden");
  const yesBtn = document.getElementById("leaveYesBtn");
  if (yesBtn) {
    yesBtn.disabled = false;
    yesBtn.textContent = "YES";
  }
}
window.leaveExam = leaveExam;

document.getElementById("leaveNoBtn")?.addEventListener("click", () => {
  document.getElementById("leavePrompt")?.classList.add("hidden");
  if (examLifecycleState === EXAM_STATES.LEAVE_CONFIRMATION_OPEN) {
    setExamLifecycleState(EXAM_STATES.ACTIVE);
  }
});

document.getElementById("leaveYesBtn")?.addEventListener("click", async () => {
  const yesBtn = document.getElementById("leaveYesBtn");
  if (examLifecycleState === EXAM_STATES.FINALIZING || examLifecycleState === EXAM_STATES.FINISHED) return;
  setExamLifecycleState(EXAM_STATES.FINALIZING);
  if (yesBtn) {
    yesBtn.disabled = true;
    yesBtn.textContent = "Finalizing...";
  }
  try {
    const isMcq = window.isMcqExam || (window._examType === 'mcq') || (typeof currentExamType !== 'undefined' && currentExamType === 'mcq');
    if (isMcq) {
      try { await api("/api/mcqs/finish", { method: "POST" }); } catch(_) {}
    }
    const res = await api("/api/exam/student-exit", {
      method: "POST",
      body: JSON.stringify({ reason: "Student confirmed Leave Exam" })
    });
    if (!res || res.error) {
      throw new Error((res && res.error) || "Failed to finalize exam attempt.");
    }
    setExamLifecycleState(EXAM_STATES.FINISHED);
    document.getElementById("leavePrompt")?.classList.add("hidden");
    await exitExamToHome("Exam session completed. Your solutions were saved.", true);
    await refreshExam(true);
  } catch (error) {
    console.error("Exam finalization failed:", error);
    setExamLifecycleState(EXAM_STATES.LEAVE_CONFIRMATION_OPEN);
    if (yesBtn) {
      yesBtn.disabled = false;
      yesBtn.textContent = "YES";
    }
    alert("Unable to finalize exam: " + (error.message || "Network error. Please try again."));
  }
});

async function teacher() {
  show("#teacher");
  showTab("tab-exam");
  $("#teacherName").textContent = me.name;
  await loadTeacherQuestions();
  $("#questionForm").onsubmit = async (event) => {
    event.preventDefault();
    const getLangs = () => Array.from(document.querySelectorAll('input[name="qlang"]:checked')).map(c => c.value).join(',') || 'python';
    const payload = {title: $("#qtitle").value, prompt: $("#qprompt").value, language: getLangs()};
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
  // Task 2: Wire up question CSV/Excel import
  if ($("#questionImportForm")) {
    $("#questionImportForm").onsubmit = async (event) => {
      event.preventDefault();
      const file = $("#questionFile").files[0];
      if (!file) return;
      const form = new FormData(); form.append("file", file);
      const response = await fetch("/api/questions/import", {method: "POST", body: form});
      const data = await response.json();
      alert(response.ok ? `${data.imported} questions imported with test cases.` : data.error);
      if (response.ok) { event.target.reset(); await loadTeacherQuestions(); }
    };
  }
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

  // Two separate buttons for Coding and MCQ exams
  $("#startCodingBtn").onclick = async () => {
    const mins = $("#minutes").value || 60;
    try {
      const data = await api("/api/exam/start_coding", {method: "POST", body: JSON.stringify({minutes: parseInt(mins)})});
      $("#codingCodeDisplay").style.display = "block";
      $("#codingCodeValue").textContent = data.access_code;
      $("#examCode").textContent = "Coding exam is now active.";
    } catch(e) { alert(e.message); }
  };
  $("#startMcqBtn").onclick = async () => {
    try {
      const data = await api("/api/exam/start_mcq", {method: "POST", body: JSON.stringify({})});
      $("#mcqCodeDisplay").style.display = "block";
      $("#mcqCodeValue").textContent = data.mcq_access_code;
      $("#examCode").textContent = "MCQ exam is now active.";
    } catch(e) { alert(e.message); }
  };
  $("#stopExam").onclick = async () => {
    await api("/api/exam/stop", {method: "POST", body: JSON.stringify({})});
    $("#examCode").textContent = "Exam stopped.";
  };
  $("#clearKeysBtn").onclick = async () => {
    if (!confirm("Clear previously generated exam keys?")) return;
    await api("/api/exam/clear_keys", {method: "POST", body: JSON.stringify({})});
    $("#codingCodeDisplay").style.display = "none";
    $("#mcqCodeDisplay").style.display = "none";
    $("#codingCodeValue").textContent = "-";
    $("#mcqCodeValue").textContent = "-";
    $("#examCode").textContent = "Exam keys cleared.";
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
    await api("/api/students", {method: "POST", body: JSON.stringify({name: $("#studentFullName").value, username: $("#studentRegisterNumber").value, register_number: $("#studentRegisterNumber").value, department: $("#studentDepartment").value, year: $("#studentYear").value, class: $("#studentClass").value, semester: $("#studentSemester").value, section: $("#studentSection").value, password: $("#studentPassword").value})});
    event.target.reset();
    alert("Student saved.");
    await loadStudents();
    dashboard();
  };
  $("#studentSearch").oninput = renderStudents;
  await loadStudents();
  dashboard();
  // Auto-refresh activity every 5 seconds
  setInterval(() => {
    if (!document.hidden && !document.getElementById("teacher").classList.contains("hidden")) {
      dashboard();
    }
  }, 5000);
  const state = await api("/api/exam-state");
  if (state.active) {
    if (state.access_code) { $("#codingCodeDisplay").style.display="block"; $("#codingCodeValue").textContent=state.access_code; }
    if (state.mcq_access_code) { $("#mcqCodeDisplay").style.display="block"; $("#mcqCodeValue").textContent=state.mcq_access_code; }
    $("#examCode").textContent = "Exam is active.";
  }
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
  // Task 2: Add Delete button to each question
  target.innerHTML = questions.length ? questions.map((q) => `<div class="question-row"><span><b>${esc(q.title)}</b><small>${esc(q.language)}</small></span><div style="display:flex;gap:6px;"><button type="button" onclick="editQuestion(${q.id})">Edit</button><button type="button" style="color:var(--danger);" onclick="deleteQuestion(${q.id})">Delete</button></div></div>`).join("") : "No questions published yet.";
}
// Task 2: Delete question
window.deleteQuestion = async (id) => {
  if (!confirm('Delete this question and its test cases?')) return;
  await fetch(`/api/questions/${id}`, {method:'DELETE'});
  await loadTeacherQuestions();
};
function editQuestion(id) {
  const q = questions.find((item) => item.id === id);
  if (!q) return;
  $("#questionEditId").value = q.id;
  $("#qtitle").value = q.title;
  const arr = (q.language || "python").split(',').map(s => s.trim());
  document.querySelectorAll('input[name="qlang"]').forEach(c => c.checked = arr.includes(c.value));
  $("#qprompt").value = q.prompt;
  $("#questionSaveButton").textContent = "Update question";
}
async function loadCases() {
  const id = $("#caseQuestion").value;
  if (!id) return $("#caseList").innerHTML = "No question selected.";
  const cases = await api(`/api/test-cases/${id}`);
  // Task 6: Add Delete button to each test case
  $("#caseList").innerHTML = cases.length ? cases.map((c, i) =>
    `<div class="case-row">
      <b>Case ${i + 1}</b>
      <span>${c.visible ? "Visible" : "Hidden"}</span>
      <span>${c.marks} marks</span>
      <span style="font-size:0.8em;color:var(--muted)">In: ${esc(String(c.input_data||'').slice(0,30))} | Out: ${esc(String(c.expected_output||'').slice(0,30))}</span>
      <div style="display:flex;gap:6px">
        <button type="button" onclick="editCase(${c.id})">Edit</button>
        <button type="button" style="color:var(--danger)" onclick="deleteCase(${c.id})">Delete</button>
      </div>
    </div>`
  ).join("") : "No test cases yet.";
}
// Task 6: Delete test case
window.deleteCase = async (id) => {
  if (!confirm('Delete this test case?')) return;
  await api(`/api/test-cases/${id}`, {method: 'DELETE'});
  await loadCases();
};
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
// Shared dashboard fetch — works for both teacher and admin
async function dashboard() {
  teacherDashboardData = await api("/api/dashboard");
  renderDashboard();
}
function renderDashboard() {
  const data = teacherDashboardData;
  // Check if any student has extension_detected event in their activity
  const extDetectedStudents = [];
  data.forEach(s => {
    if (s.activity && s.activity.some(a => a.event === 'extension_detected')) {
      extDetectedStudents.push(`${s.name} (${s.register_number || s.username})`);
    }
  });

  const updateExtBanner = (bannerId) => {
    const banner = $(bannerId);
    if (!banner) return;
    if (extDetectedStudents.length > 0) {
      banner.style.display = "flex";
      banner.innerHTML = `<span style="font-size:1.3rem;">🚨</span> <div><strong>WARNING: Browser Extension Detected!</strong><br><span style="font-size:0.88rem; font-weight:normal; opacity:0.95;">Students with active unauthorized extensions: <b>${extDetectedStudents.map(esc).join(', ')}</b>. Screen is locked for them until extensions are removed.</span></div>`;
    } else {
      banner.style.display = "none";
      banner.innerHTML = "";
    }
  };
  updateExtBanner("#teacherExtensionAlert");
  updateExtBanner("#adminExtensionAlert");

  // PRESERVE SCROLL POSITIONS (Avoid snap/jump to top on auto-refresh)
  const winScrollY = window.scrollY;
  const winScrollX = window.scrollX;
  const actScrollY = $("#activity") ? $("#activity").scrollTop : 0;
  const actScrollX = $("#activity") ? $("#activity").scrollLeft : 0;
  const adminActScrollY = $("#adminActivity") ? $("#adminActivity").scrollTop : 0;
  const adminActScrollX = $("#adminActivity") ? $("#adminActivity").scrollLeft : 0;

  const scrollMap = {};
  document.querySelectorAll('[data-act-box]').forEach(el => {
    scrollMap[el.dataset.actBox] = { top: el.scrollTop, left: el.scrollLeft };
  });

  // Task 1: Build activity table with wide readable activity streams and zero jumping
  const activityHtml = `<div style="overflow-x:auto;"><table class="data-table" style="width:100%; min-width:980px;"><thead><tr><th style="width:180px;">Student</th><th style="width:130px; text-align:center;">Submissions</th><th style="width:130px; text-align:center;">Coding Score</th><th style="width:120px; text-align:center;">MCQ Score</th><th style="width:110px; text-align:center;">Violations</th><th style="min-width:480px;">Latest activity</th></tr></thead><tbody>${data.map((s) => {
    const codingDisplay = (s.submissions === 0 && s.score === 0 && s.pending) ? '<span style="opacity:0.6;">Pending</span>' : `<b style="color:var(--accent-1);">${s.score}</b>`;
    const sKey = `act_${s.username || s.id}`;
    const viewCodeBtn = (s.submissions > 0 && s.id) 
      ? `<br><button type="button" class="btn btn-sm" style="margin-top:6px; padding:3px 8px; font-size:0.75rem; border-radius:6px; background:rgba(13,148,136,0.18); border:1px solid rgba(13,148,136,0.4); color:var(--accent-1); cursor:pointer; font-weight:700; display:inline-flex; align-items:center; gap:4px;" onclick="viewLiveStudentCode(${s.id}, '${esc(s.name)}')">💻 View Code</button>` 
      : '';
    return `<tr><td><b>${esc(s.name)}</b><br><small style="color:var(--text-2); font-weight:600;">${esc(s.register_number || s.username)}</small></td><td style="text-align:center;"><span style="background:rgba(255,255,255,0.06); padding:4px 12px; border-radius:10px; font-weight:700;">${s.submissions}</span>${viewCodeBtn}</td><td style="text-align:center;">${codingDisplay}</td><td style="text-align:center;"><b style="color:var(--accent-2);">${s.mcq_score !== undefined && s.mcq_score !== null ? s.mcq_score : 0}</b></td><td style="text-align:center;"><span style="display:inline-block; padding:4px 12px; border-radius:12px; font-weight:800; font-size:1.05em; ${s.illegal_count > 0 ? 'background:rgba(239,68,68,0.15); color:#ef4444; border:1px solid rgba(239,68,68,0.35);' : 'background:rgba(255,255,255,0.05); color:var(--text-1);'}">${s.illegal_count}</span></td><td style="font-size:0.9em; min-width:440px;"><div data-act-box="${sKey}" style="max-height:240px; overflow-y:auto; overflow-x:hidden; padding:8px; background:rgba(0,0,0,0.03); border-radius:10px; border:1px solid var(--glass-border); display:flex; flex-direction:column; gap:6px; box-sizing:border-box;">${s.activity.map((a) => {
      const isExt = a.event === 'extension_detected';
      const isLock = a.event === 'exam_locked' || a.event === 'mcq_exam_locked';
      const isSwitch = a.event === 'tab_hidden' || a.event === 'window_blur' || a.event === 'fullscreen_exit' || a.event === 'screen_resize' || a.event === 'window_refocus' || a.event === 'mouse_left_viewport';
      const isKey = a.event === 'blocked_key' || a.event === 'blocked_paste';
      const isJoin = a.event === 'exam_join';
      const isSub = a.event === 'submission';
      const isClose = a.event === 'attempt_close' || a.event === 'exam_left';
      
      let badgeBg = "#64748b";
      let badgeIcon = "📌";
      let cardBg = "background:rgba(255,255,255,0.04);";
      let cardBorder = "border:1px solid var(--glass-border);";
      let detailColor = "var(--text-1)";
      let eventTitle = a.event.replace(/_/g, ' ').toUpperCase();

      if (isExt) {
        badgeBg = "#dc2626";
        badgeIcon = "🚨";
        cardBg = "background:rgba(239,68,68,0.08);";
        cardBorder = "border:1px solid rgba(239,68,68,0.35);";
        detailColor = "#dc2626";
        eventTitle = "EXTENSION DETECTED";
      } else if (isLock) {
        badgeBg = "#b91c1c";
        badgeIcon = "🔒";
        cardBg = "background:rgba(185,28,28,0.08);";
        cardBorder = "border:1px solid rgba(185,28,28,0.35);";
        detailColor = "#b91c1c";
        eventTitle = "EXAM LOCKED";
      } else if (a.event === 'fullscreen_exit') {
        badgeBg = "#ea580c";
        badgeIcon = "⚠️";
        cardBg = "background:rgba(234,88,12,0.07);";
        cardBorder = "border:1px solid rgba(234,88,12,0.3);";
        detailColor = "var(--text-0)";
        eventTitle = "FULLSCREEN EXIT";
      } else if (a.event === 'tab_hidden') {
        badgeBg = "#d97706";
        badgeIcon = "⚠️";
        cardBg = "background:rgba(217,119,6,0.07);";
        cardBorder = "border:1px solid rgba(217,119,6,0.3);";
        detailColor = "var(--text-0)";
        eventTitle = "TAB HIDDEN";
      } else if (a.event === 'window_blur') {
        badgeBg = "#d97706";
        badgeIcon = "⚠️";
        cardBg = "background:rgba(217,119,6,0.07);";
        cardBorder = "border:1px solid rgba(217,119,6,0.3);";
        detailColor = "var(--text-0)";
        eventTitle = "WINDOW BLUR";
      } else if (isSwitch) {
        badgeBg = "#d97706";
        badgeIcon = "⚠️";
        cardBg = "background:rgba(217,119,6,0.07);";
        cardBorder = "border:1px solid rgba(217,119,6,0.3);";
        detailColor = "var(--text-0)";
        eventTitle = a.event.replace(/_/g, ' ').toUpperCase();
      } else if (isKey) {
        badgeBg = "#ea580c";
        badgeIcon = "⌨️";
        cardBg = "background:rgba(234,88,12,0.07);";
        cardBorder = "border:1px solid rgba(234,88,12,0.3);";
        detailColor = "var(--text-0)";
        eventTitle = "BLOCKED KEY";
      } else if (isSub) {
        badgeBg = "#0d9488";
        badgeIcon = "💻";
        cardBg = "background:rgba(13,148,136,0.07);";
        cardBorder = "border:1px solid rgba(13,148,136,0.3);";
        detailColor = "var(--text-0)";
        eventTitle = "SUBMISSION";
      } else if (isJoin) {
        badgeBg = "#16a34a";
        badgeIcon = "🚀";
        cardBg = "background:rgba(22,163,74,0.07);";
        cardBorder = "border:1px solid rgba(22,163,74,0.3);";
        detailColor = "var(--text-0)";
        eventTitle = "EXAM JOIN";
      } else if (isClose) {
        badgeBg = "#7c3aed";
        badgeIcon = "🚪";
        cardBg = "background:rgba(124,58,237,0.07);";
        cardBorder = "border:1px solid rgba(124,58,237,0.3);";
        detailColor = "var(--text-0)";
        eventTitle = "EXAM LEFT";
      }

      // Friendly formatted details text
      let detailText = a.details || '';
      if (detailText.toLowerCase() === 'coding') {
        detailText = '📘 Exam Mode: Coding';
      } else if (detailText.toLowerCase() === 'mcq') {
        detailText = '📝 Exam Mode: MCQ';
      } else if (detailText === 'visibility_hidden') {
        detailText = 'Tab switched / browser hidden';
      } else if (detailText === 'focus_lost') {
        detailText = 'Window focus lost';
      } else if (detailText === 'exited_fullscreen') {
        detailText = 'Exited Fullscreen mode';
      } else if (detailText === 'coding violation limit exceeded') {
        detailText = '⛔ Violation limit exceeded (5/5) — Locked';
      } else if (detailText && (detailText.includes('Time Over') || detailText.includes('time_over'))) {
        badgeBg = "#dc2626";
        badgeIcon = "⏰";
        cardBg = "background:rgba(220,38,38,0.08);";
        cardBorder = "border:1px solid rgba(220,38,38,0.35);";
        detailColor = "#dc2626";
        eventTitle = "TIME OVER";
        detailText = "⏰ Time Over — Left Exam automatically";
      }

      return `<div style="padding:7px 10px; border-radius:8px; ${cardBg} ${cardBorder} font-size:0.85rem; line-height:1.35; box-sizing:border-box;"><div style="display:flex; justify-content:space-between; align-items:center; gap:6px; flex-wrap:wrap; margin-bottom:3px;"><span style="background:${badgeBg}; color:#ffffff; padding:2px 8px; border-radius:5px; font-weight:700; font-size:0.75rem; letter-spacing:0.3px; display:inline-flex; align-items:center; gap:4px; white-space:nowrap;">${badgeIcon} ${esc(eventTitle)}</span><span style="color:var(--text-2); font-size:0.75rem; font-weight:600; font-family:var(--font-mono); white-space:nowrap;">${esc(a.created_at)}</span></div>${detailText ? `<div style="color:${detailColor}; font-weight:600; font-size:0.82rem; word-break:break-word; margin-top:2px;">${esc(detailText)}</div>` : ''}</div>`;
    }).join("") || '<div style="opacity:0.6; font-style:italic; padding:6px; font-weight:500;">No activity recorded yet</div>'}</div></td></tr>`;
  }).join("")}</tbody></table></div>`;

  window.viewLiveStudentCode = async function(studentId, studentName) {
    try {
      const res = await api(`/api/teacher/student/${studentId}/submissions`);
      const st = res.student || {};
      const subs = res.submissions || [];

      const titleEl = document.getElementById('codeViewTitle');
      const subTitleEl = document.getElementById('codeViewSubtitle');
      const container = document.getElementById('codeViewCardsContainer');
      const modal = document.getElementById('codeViewModal');
      if (!container || !modal) return;

      if (titleEl) titleEl.textContent = `Submitted Solutions: ${st.name || studentName || 'Student'}`;
      if (subTitleEl) {
        subTitleEl.innerHTML = `
          <div class="code-view-meta-row">
            <span class="code-meta-badge"><strong>Reg No:</strong> ${esc(st.register_number || st.username || '-')}</span>
            <span class="code-meta-badge"><strong>Dept:</strong> ${esc(st.department || '-')}</span>
            <span class="code-meta-badge"><strong>Submissions:</strong> <b>${subs.length}</b></span>
          </div>
        `;
      }

      container.innerHTML = '';
      if (subs.length > 0) {
        subs.forEach((sub, qIdx) => {
          const card = document.createElement('div');
          card.className = 'code-question-card';
          const scoreBadge = sub.score !== undefined ? `<span style="background: rgba(13,148,136,0.18); color: var(--accent-1); padding: 3px 10px; border-radius: 12px; font-size: 0.8rem; font-weight: 700; border: 1px solid rgba(13,148,136,0.3);">Score: ${sub.score}</span>` : '';
          const langBadge = `<span style="background: rgba(109,91,208,0.18); color: var(--accent-2); padding: 3px 10px; border-radius: 12px; font-size: 0.8rem; font-weight: 700; text-transform: uppercase; border: 1px solid rgba(109,91,208,0.3);">${esc(sub.language || 'code')}</span>`;

          const rawCode = (sub.code || '').trim();
          const hasCode = Boolean(rawCode && rawCode !== '// No code submitted');
          const codeMarkup = hasCode 
            ? `<pre><code>${esc(sub.code)}</code></pre>` 
            : `<div class="code-empty-notice"><span>⚠️</span> No code submitted for this question</div>`;

          card.innerHTML = `
            <div class="code-question-header">
              <div style="display:flex; align-items:center; gap:10px; flex-wrap:wrap;">
                <b>📌 ${esc(sub.question_title || 'Question ' + (qIdx + 1))}</b>
                ${langBadge}
                ${scoreBadge}
              </div>
              <button class="btn btn-sm btn-ghost" onclick="copyCardCode(this)" style="padding:6px 14px; font-size:0.82rem; border-radius:8px; cursor:pointer; display:inline-flex; align-items:center; gap:6px;">📋 Copy Code</button>
            </div>
            <div class="code-question-body">
              ${codeMarkup}
            </div>
          `;
          container.appendChild(card);
        });
      } else {
        container.innerHTML = `<div style="text-align:center; padding:45px; color:var(--text-2); font-size:1rem; background:rgba(255,255,255,0.02); border-radius:12px; border:1px solid var(--glass-border);">No code submissions found for this student.</div>`;
      }

      modal.classList.remove('hidden');
    } catch(e) {
      alert("Could not load student code: " + e.message);
    }
  };

  if ($("#activity")) {
    $("#activity").innerHTML = activityHtml;
    if ($("#activityTitle")) $("#activityTitle").innerText = `Student activity (${data.length} Joined)`;
  }
  if ($("#adminActivity")) {
    $("#adminActivity").innerHTML = activityHtml;
    if ($("#adminActivityTitle")) $("#adminActivityTitle").innerText = `Student Activity (${data.length} Joined)`;
  }

  // RESTORE SCROLL POSITIONS (Zero jumping!)
  if ($("#activity")) {
    $("#activity").scrollTop = actScrollY;
    $("#activity").scrollLeft = actScrollX;
  }
  if ($("#adminActivity")) {
    $("#adminActivity").scrollTop = adminActScrollY;
    $("#adminActivity").scrollLeft = adminActScrollX;
  }
  window.scrollTo(winScrollX, winScrollY);
  document.querySelectorAll('[data-act-box]').forEach(el => {
    if (scrollMap[el.dataset.actBox]) {
      el.scrollTop = scrollMap[el.dataset.actBox].top;
      el.scrollLeft = scrollMap[el.dataset.actBox].left;
    }
  });

  // Render leaderboard — support both teacher and admin panels
  const panels = [
    {searchId:'#leaderboardClassSearch', tbodyId:'#leaderboardTableBody'},
    {searchId:'#adminLeaderboardClassSearch', tbodyId:'#adminLeaderboardTableBody'}
  ];
  panels.forEach(({searchId, tbodyId}) => {
    const tbody = $(tbodyId);
    if (!tbody) return;
    const searchVal = ($(searchId)?.value || '').trim().toLowerCase();
    // Task 5: Dynamic partial filter — match dept, section, combined class_name, or register number
    const filtered = searchVal ? data.filter(s => {
      const dept = (s.department || '').toLowerCase();
      const sec = (s.section || '').toLowerCase();
      const cls = (s.class_name || '').toLowerCase();
      const reg = (s.register_number || s.username || '').toLowerCase();
      return cls.includes(searchVal) || dept.includes(searchVal) || sec.includes(searchVal) || reg.includes(searchVal);
    }) : data;
    const sorted = [...filtered].sort((a,b) => {
      const sA = (a.score||0) + (a.mcq_score||0);
      const sB = (b.score||0) + (b.mcq_score||0);
      if (sB !== sA) return sB - sA;
      return (a.total_time||0) - (b.total_time||0);
    });
    // Task 1: Show real scores in leaderboard; only show 'Pending' when truly no data
    tbody.innerHTML = sorted.map((s, i) => {
      const trulyPending = s.submissions === 0 && s.score === 0 && s.pending;
      const cs = trulyPending ? 'Pending' : `${s.score} / ${s.coding_total ?? '?'}`;
      const ms = `${s.mcq_score||0} / ${s.mcq_total ?? '?'}`;
      const formatTimeStr = (secs) => {
          if (!secs) return "0s";
          let m = Math.floor(secs / 60);
          let sec = secs % 60;
          if (m > 0 && sec > 0) return `${m}m ${sec}s`;
          if (m > 0) return `${m}m`;
          return `${sec}s`;
      };
      return `<tr><td>${i+1}</td><td>${esc(s.name)}</td><td>${esc(s.register_number || s.username)}</td><td>${esc(s.department || '-')}</td><td>${esc(s.section || '-')}</td><td>${formatTimeStr(s.total_time||0)}</td><td>${cs}</td><td>${ms}</td></tr>`;
    }).join("");
  });
}
if ($("#leaderboardClassSearch")) $("#leaderboardClassSearch").addEventListener("input", renderDashboard);
if ($("#adminLeaderboardClassSearch")) $("#adminLeaderboardClassSearch").addEventListener("input", renderDashboard);
// Download leaderboard - format: 'csv' or 'xlsx', works for both teacher and admin
window.downloadLeaderboard = (format = 'csv', isAdmin = false) => {
  const searchId = isAdmin ? '#adminLeaderboardClassSearch' : '#leaderboardClassSearch';
  const cls = $(searchId)?.value || '';
  const url = `/api/leaderboard/download?class=${encodeURIComponent(cls)}&sort=desc&format=${format}`;
  const ext = format === 'xlsx' ? 'xlsx' : 'csv';
  const safe = cls.replace(/[^a-zA-Z0-9_-]/g,'_') || 'overall';
  const a = document.createElement('a'); a.href = url; a.download = `${safe}_leaderboard.${ext}`; a.click();
};

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
  target.innerHTML = list.length ? list.map((s) => `<div class="student-row"><div><b>${esc(s.name)}</b><small>${esc(s.register_number || s.username)} · ${esc(s.department || "No department")}</small><small>${esc(s.year || "")} ${esc(s.semester || "")} ${esc(s.section || "")}</small></div><div class="row-actions"><button data-access="${s.id}">${s.allowed ? "Allowed" : "Blocked"}</button><button data-reset="${s.id}">Reset</button><button data-delete="${s.id}">Delete</button></div></div>`).join("") : "No matching students.";
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
  showTab("tab-admin-teachers");
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
  // Task 6: Load activity + leaderboard data for admin (same as teacher dashboard)
  await dashboard();
  // Auto-refresh admin panel every 5 seconds
  setInterval(() => {
    if (!document.hidden && !document.getElementById("admin").classList.contains("hidden")) {
      loadAdminUsers();
      dashboard();
    }
  }, 5000);
}
async function loadAdminUsers() {
  const users = await api("/api/admin/users");
  $("#adminUsers").innerHTML = `<table><tr><th>Name</th><th>Username</th><th>Role</th><th>Actions</th></tr>${users.map((u) => `<tr><td>${esc(u.name)}</td><td>${esc(u.username)}</td><td>${esc(u.role)}</td><td>${u.role==='teacher' ? `<div class="row-actions"><button onclick="editTeacher(${u.id}, '${esc(u.name).replace(/'/g, "\\'")}', '${esc(u.username).replace(/'/g, "\\'")}')">Edit</button> <button style="color:var(--danger);border-color:var(--danger);" onclick="deleteTeacher(${u.id})">Delete</button></div>` : ''}</td></tr>`).join("")}</table>`;
}
window.editTeacher = async (id, oldName, oldUsername) => {
  const name = prompt("Teacher name", oldName); if (!name) return;
  const username = prompt("Teacher username", oldUsername); if (!username) return;
  const password = prompt("New password (leave blank to keep current)");
  await api(`/api/admin/teachers/${id}`, {method: "POST", body: JSON.stringify({name, username, password: password || ""})});
  await loadAdminUsers();
};
window.deleteTeacher = async (id) => {
  if (!confirm("Are you sure you want to delete this teacher?")) return;
  await api(`/api/teachers/${id}`, {method: "DELETE"});
  await loadAdminUsers();
};

setTimeout(() => $("#intro")?.remove(), 3000);


// Password settings modal
window.showPasswordModal = () => {
  const m = document.getElementById("passwordModal");
  if (m) m.classList.remove("hidden");
};
window.savePassword = async () => {
  const pw = document.getElementById("newPassword")?.value;
  if (!pw || pw.length < 4) { alert("Password must be at least 4 characters."); return; }
  try {
    await api("/api/change_password", {method: "POST", body: JSON.stringify({password: pw})});
    alert("Password updated successfully!");
    document.getElementById("passwordModal").classList.add("hidden");
    document.getElementById("newPassword").value = "";
  } catch(e) { alert("Failed: " + e.message); }
};

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
  const canvas = $("#bg-canvas"); if(!canvas) return;
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
let adminAllUsers = [];
async function loadAdminStudents() {
  try { adminAllUsers = await api("/api/admin/users"); } catch (e) {}
}
function renderAdminStudents() {
  const query = $("#adminStudentSearch").value.toLowerCase();
  if (query.length < 2) return;
  const s = adminAllUsers.find(x => x.username.toLowerCase() === query || (x.register_number && x.register_number.toLowerCase() === query));
  if (s) {
    $("#adminStudentEditFields").style.display = "block";
    $("#adminEditId").value = s.id;
    if ($("#adminEditRole")) $("#adminEditRole").value = s.role;
    $("#adminEditName").value = s.name;
    $("#adminEditUsername").value = s.username;
    $("#adminEditRegNo").value = s.register_number || "";
    $("#adminEditDept").value = s.department || "";
    $("#adminEditClass").value = s.class || "";
    $("#adminEditYear").value = (s.role === 'teacher' ? s.teacher_year : s.student_year) || "";
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
        const role = $("#adminEditRole") ? $("#adminEditRole").value : 'student';
        const data = {
            name: $("#adminEditName").value,
            username: $("#adminEditUsername").value,
            register_number: $("#adminEditRegNo").value,
            department: $("#adminEditDept").value,
            class: $("#adminEditClass").value,
            year: $("#adminEditYear").value,
            semester: $("#adminEditSem").value,
            section: $("#adminEditSec").value,
            password: $("#adminEditPassword").value
        };
        const endpoint = role === 'teacher' ? `/api/admin/teachers/${id}` : `/api/admin/students/${id}`;
        await api(endpoint, {method: role === 'teacher' ? "POST" : "PUT", body: JSON.stringify(data)});
        alert("User updated.");
        $("#adminStudentEditFields").style.display = "none";
        $("#adminStudentSearch").value = "";
        await loadAdminStudents();
        if (typeof loadAdminUsers !== 'undefined') await loadAdminUsers();
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
        await renderMcqQuestion();
    } else {
        await exitExamToHome("No MCQ questions available for this exam.", false);
    }
}

async function renderMcqQuestion() {
    if (currentMcqIndex >= mcqQuestions.length) {
        clearInterval(mcqInterval);
        try { await api('/api/mcqs/finish', {method:'POST'}); } catch(e) {}
        await exitExamToHome("MCQ Exam completed! All your answers have been submitted.", true);
        return;
    }
    const q = mcqQuestions[currentMcqIndex];
    $("#mcqQuestionText").textContent = q.question;
    $("#mcqTextA").textContent = q.option_a;
    $("#mcqTextB").textContent = q.option_b;
    $("#mcqTextC").textContent = q.option_c;
    $("#mcqTextD").textContent = q.option_d;
    mcqTimeRemaining = q.time_limit || 30;
    
    // Update MCQ palette
    renderMcqPalette();

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
    await renderMcqQuestion();
};

function refreshMcqExam() {
    // Liveness check every 5s
    api("/api/exam-state").then(async state => {
        if (!state.active) {
            await exitExamToHome("The MCQ exam has ended. You have been returned to home.", false);
        }
    }).catch(e => {});
    // Task 3: Re-fetch MCQ questions to pick up any teacher edits mid-exam
    if (unlocked && (window.isMcqExam || window._examType === 'mcq')) {
        api("/api/mcqs").then(newMcqs => {
            if (!newMcqs || !newMcqs.length) return;
            // Only update questions not yet answered (from currentMcqIndex onwards)
            let changed = false;
            for (let i = currentMcqIndex; i < Math.max(mcqQuestions.length, newMcqs.length); i++) {
                const oldQ = mcqQuestions[i];
                const newQ = newMcqs[i];
                if (!oldQ || !newQ) { changed = true; break; }
                if (oldQ.question !== newQ.question || oldQ.option_a !== newQ.option_a ||
                    oldQ.option_b !== newQ.option_b || oldQ.option_c !== newQ.option_c ||
                    oldQ.option_d !== newQ.option_d) {
                    changed = true;
                    break;
                }
            }
            if (changed) {
                // Merge: keep already-answered questions, update remaining ones
                const answeredPart = mcqQuestions.slice(0, currentMcqIndex);
                const updatedRemaining = newMcqs.slice(currentMcqIndex);
                mcqQuestions = [...answeredPart, ...updatedRemaining];
                // Refresh current question display (do NOT reset timer, just update text)
                if (currentMcqIndex < mcqQuestions.length) {
                    const q = mcqQuestions[currentMcqIndex];
                    $("#mcqQuestionText").textContent = q.question;
                    $("#mcqTextA").textContent = q.option_a;
                    $("#mcqTextB").textContent = q.option_b;
                    $("#mcqTextC").textContent = q.option_c;
                    $("#mcqTextD").textContent = q.option_d;
                }
            }
        }).catch(() => {});
    }
    if (unlocked && (window.isMcqExam || window._examType === 'mcq')) setTimeout(refreshMcqExam, 5000);
}

const origAdmin = admin;
admin = async function() {
    await origAdmin();
    await loadAdminStudents();
};

// Task 2: mcqDone — go back to student home page
window.mcqDone = async function() {
  try { await api('/api/mcqs/finish', {method:'POST'}); } catch(e) {}
  await exitExamToHome("MCQ Exam completed! All your answers have been submitted.", true);
};

// Task 5: Draggable console height
(function initResizableConsole() {
  const handle = document.getElementById('consoleDragHandle');
  const terminal = document.getElementById('terminalPanel');
  if (!handle || !terminal) return;
  let dragging = false, startY = 0, startH = 0;
  handle.style.cursor = 'ns-resize';
  handle.addEventListener('mousedown', (e) => {
    dragging = true;
    startY = e.clientY;
    startH = terminal.offsetHeight;
    document.body.style.userSelect = 'none';
    e.preventDefault();
  });
  document.addEventListener('mousemove', (e) => {
    if (!dragging) return;
    const dy = startY - e.clientY;
    const newH = Math.max(80, Math.min(600, startH + dy));
    const workContainer = document.querySelector('.work');
    if (workContainer) workContainer.style.gridTemplateRows = `60px 1fr ${newH}px`;
    terminal.style.height = '100%';
  });
  document.addEventListener('mouseup', () => {
    if (dragging) { dragging = false; document.body.style.userSelect = ''; }
  });

  // Task 5: Draggable test results height
  const trHandle = document.getElementById('testResultsDragHandle');
  const tr = document.getElementById('testResults');
  if (trHandle && tr) {
    let trDragging = false, trStartY = 0, trStartH = 0;
    trHandle.style.cursor = 'ns-resize';
    trHandle.style.padding = '4px 8px';
    trHandle.style.background = 'var(--surface-2, rgba(255,255,255,.06))';
    trHandle.style.borderTop = '1px solid var(--border, rgba(255,255,255,.1))';
    trHandle.style.fontSize = '0.75rem';
    trHandle.style.color = 'var(--text-2)';
    trHandle.style.textAlign = 'center';
    trHandle.addEventListener('mousedown', (e) => {
      trDragging = true;
      trStartY = e.clientY;
      trStartH = tr.offsetHeight;
      document.body.style.userSelect = 'none';
      e.preventDefault();
    });
    document.addEventListener('mousemove', (e) => {
      if (!trDragging) return;
      const dy = trStartY - e.clientY;
      const newH = Math.max(40, Math.min(500, trStartH + dy));
      tr.style.height = newH + 'px';
      tr.style.maxHeight = newH + 'px';
      tr.style.overflow = 'auto';
    });
    document.addEventListener('mouseup', () => {
      if (trDragging) { trDragging = false; document.body.style.userSelect = ''; }
    });
  }
})();

initSlider();
boot().catch(() => show("#login"));


function formatExamDate(dateStr) {
  if (!dateStr || dateStr === '-') return '-';
  if (typeof dateStr === 'string' && dateStr.includes('IST')) return dateStr;
  try {
    const isoStr = dateStr.includes('T') ? dateStr : dateStr.replace(' ', 'T') + '+05:30';
    const d = new Date(isoStr);
    if (!isNaN(d.getTime())) {
      return d.toLocaleString('en-IN', {
        timeZone: 'Asia/Kolkata',
        day: '2-digit',
        month: '2-digit',
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: true
      }) + ' IST';
    }
  } catch(e) {}
  return dateStr;
}

let currentHistoryResults = [];

async function viewHistoryDetails(id) {
  try {
    const res = await fetch(`/api/history/${id}/details`);
    if(!res.ok) { alert('Failed to load history details'); return; }
    const data = await res.json();
    
    const keyBadge = data.exam_key ? `&nbsp;|&nbsp; <strong>Exam Key:</strong> <span style="background:rgba(99,102,241,0.2); color:var(--accent-2); padding:2px 8px; border-radius:6px; font-family:var(--font-mono); font-weight:800;">🔑 ${esc(data.exam_key)}</span>` : '';
    document.getElementById('historyModalTitle').textContent = `Exam History #${data.id}`;
    document.getElementById('historyModalMeta').innerHTML = `
      <strong>Teacher:</strong> ${esc(data.teacher_name || 'Unknown')} 
      ${keyBadge} &nbsp;|&nbsp; 
      <strong>Date:</strong> ${formatExamDate(data.exam_date)} &nbsp;|&nbsp; 
      <strong>Total Students:</strong> ${data.total_students}
    `;
    
    const tbody = document.getElementById('historyModalBody');
    tbody.innerHTML = '';
    
    let results = data.result_data || [];
    currentHistoryResults = results;
    if(results.length === 0) {
      tbody.innerHTML = '<tr><td colspan="11" style="text-align:center; padding:20px; color:var(--text-2);">No student results recorded in this snapshot.</td></tr>';
    } else {
      // Sort like Leaderboard: Total Score descending, then Time ascending
      results.sort((a,b) => {
        const totalA = (Number(a.final_score)||0) + (Number(a.mcq_final_score)||0);
        const totalB = (Number(b.final_score)||0) + (Number(b.mcq_final_score)||0);
        if (totalB !== totalA) return totalB - totalA;
        return (Number(a.total_time)||0) - (Number(b.total_time)||0);
      });

      results.forEach((r, idx) => {
        const tr = document.createElement('tr');
        const hasCode = (r.submissions_list && r.submissions_list.length > 0) || (r.code_submitted && r.code_submitted.trim() !== '' && r.code_submitted.trim() !== 'None');
        const codeButton = hasCode
          ? `<button class="view-code-btn" onclick="openSubmittedCodeModal(${idx})">💻 View Code</button>`
          : `<span style="color:var(--text-2); opacity:0.6; font-size:0.85rem;">None</span>`;

        const formatTimeStr = (secs) => {
            if (!secs || secs <= 0) return "0s";
            let m = Math.floor(secs / 60);
            let sec = secs % 60;
            if (m > 0 && sec > 0) return `${m}m ${sec}s`;
            if (m > 0) return `${m}m`;
            return `${sec}s`;
        };
        const violationsCount = r.violations ?? r.illegal_count ?? 0;
        const violationsBadge = violationsCount > 0 
          ? `<span style="background:rgba(239,68,68,0.18); color:var(--danger,#ef4444); padding:3px 10px; border-radius:12px; font-weight:700; border:1px solid rgba(239,68,68,0.3);">${violationsCount}</span>` 
          : `<span style="color:var(--text-2); opacity:0.7;">0</span>`;

        const codingScore = Number(r.final_score) || 0;
        const mcqScore = Number(r.mcq_final_score) || 0;
        const totalScore = codingScore + mcqScore;

        tr.innerHTML = `
          <td><strong>${idx + 1}</strong></td>
          <td style="font-weight:600;">${esc(r.name || '')}</td>
          <td>${esc(r.register_number || r.username || '-')}</td>
          <td>${esc(r.department || '-')}</td>
          <td>${esc(r.section || '-')}</td>
          <td>${formatTimeStr(r.total_time ?? 0)}</td>
          <td style="text-align:center;">${codingScore}</td>
          <td style="text-align:center;">${mcqScore}</td>
          <td style="text-align:center; font-weight:700; color:var(--accent-1);">${totalScore}</td>
          <td style="text-align:center;">${violationsBadge}</td>
          <td style="text-align:center; min-width:130px; white-space:nowrap;">${codeButton}</td>
        `;
        tbody.appendChild(tr);
      });
    }
    
    document.getElementById('historyModal').classList.remove('hidden');
  } catch(e) {
    console.error(e);
    alert('Error loading history details');
  }
}

function openSubmittedCodeModal(idx) {
  const r = currentHistoryResults[idx];
  if(!r) return;
  document.getElementById('codeViewTitle').textContent = `Submitted Solutions: ${r.name || 'Student'}`;
  document.getElementById('codeViewSubtitle').innerHTML = `
    <div class="code-view-meta-row">
      <span class="code-meta-badge"><strong>Reg No:</strong> ${esc(r.register_number || r.username || '-')}</span>
      <span class="code-meta-badge"><strong>Dept:</strong> ${esc(r.department || '-')}</span>
      <span class="code-meta-badge"><strong>Coding Score:</strong> <b>${r.final_score || 0}</b></span>
      <span class="code-meta-badge"><strong>Total Time:</strong> ${r.total_time ? Math.floor(r.total_time/60)+'m '+(r.total_time%60)+'s' : '0s'}</span>
    </div>
  `;
  
  const container = document.getElementById('codeViewCardsContainer');
  if(!container) return;
  container.innerHTML = '';

  let list = r.submissions_list || [];
  
  // If list is empty but raw string is present, parse it into structured question items
  if (list.length === 0 && r.code_submitted && r.code_submitted.trim() !== '') {
    const raw = r.code_submitted.trim();
    const qMatches = [...raw.matchAll(/(?:^|\n)---\s*Question:\s*(.*?)\s*---\n([\s\S]*?)(?=(?:\n---\s*Question:|$))/g)];
    if (qMatches.length > 0) {
      list = qMatches.map(m => ({
        question_title: m[1].trim(),
        code: m[2].trim()
      }));
    } else {
      list = [{
        question_title: 'Submitted Solution',
        code: raw
      }];
    }
  }

  if (list && list.length > 0) {
    list.forEach((sub, qIdx) => {
      const card = document.createElement('div');
      card.className = 'code-question-card';
      
      const scoreBadge = sub.score !== undefined ? `<span style="background: rgba(13,148,136,0.18); color: var(--accent-1); padding: 3px 10px; border-radius: 12px; font-size: 0.8rem; font-weight: 700; border: 1px solid rgba(13,148,136,0.3);">Score: ${sub.score}</span>` : '';
      const langBadge = `<span style="background: rgba(109,91,208,0.18); color: var(--accent-2); padding: 3px 10px; border-radius: 12px; font-size: 0.8rem; font-weight: 700; text-transform: uppercase; border: 1px solid rgba(109,91,208,0.3);">${esc(sub.language || 'code')}</span>`;
      
      const rawCode = (sub.code || '').trim();
      const hasCode = Boolean(rawCode && rawCode !== '// No code submitted' && !rawCode.startsWith('// No code submitted'));
      const codeMarkup = hasCode 
        ? `<pre><code>${esc(sub.code)}</code></pre>` 
        : `<div class="code-empty-notice"><span>⚠️</span> No code submitted for this question</div>`;

      card.innerHTML = `
        <div class="code-question-header">
          <div style="display:flex; align-items:center; gap:10px; flex-wrap:wrap;">
            <b>📌 ${esc(sub.question_title || 'Question ' + (qIdx + 1))}</b>
            ${langBadge}
            ${scoreBadge}
          </div>
          <button class="btn btn-sm btn-ghost" onclick="copyCardCode(this)" style="padding:6px 14px; font-size:0.82rem; border-radius:8px; cursor:pointer; display:inline-flex; align-items:center; gap:6px;">📋 Copy Code</button>
        </div>
        <div class="code-question-body">
          ${codeMarkup}
        </div>
      `;
      container.appendChild(card);
    });
  } else {
    container.innerHTML = `<div style="text-align:center; padding:45px; color:var(--text-2); font-size:1rem; background:rgba(255,255,255,0.02); border-radius:12px; border:1px solid var(--glass-border);">No code submissions found for this student.</div>`;
  }
  
  document.getElementById('codeViewModal').classList.remove('hidden');
}

function copyCardCode(btn) {
  const card = btn.closest('.code-question-card') || btn.closest('div').parentElement;
  const codeEl = card ? card.querySelector('code') : null;
  if(!codeEl) return;
  navigator.clipboard.writeText(codeEl.textContent).then(() => {
    btn.textContent = '✅ Copied!';
    setTimeout(() => { btn.textContent = '📋 Copy Code'; }, 2000);
  }).catch(() => {
    alert('Code copied!');
  });
}

function downloadHistoryDb(id) {
  window.open(`/api/history/${id}/database`, '_blank');
}

async function loadHistory() {
  try {
    const res = await fetch('/api/history');
    const data = await res.json();
    const tbody = document.getElementById('historyList');
    if(!tbody) return;
    tbody.innerHTML = '';
    if(!Array.isArray(data) || data.length === 0) {
      tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; padding:25px; color:var(--text-2);">No exam history recorded yet.</td></tr>';
      return;
    }
    data.forEach(h => {
      const tr = document.createElement('tr');
      const keyBadge = h.exam_key ? `<span style="background:rgba(99,102,241,0.18); color:var(--accent-2); padding:3px 10px; border-radius:8px; font-weight:800; font-family:var(--font-mono); border:1px solid rgba(99,102,241,0.35);">🔑 ${esc(h.exam_key)}</span>` : '<span style="color:var(--text-2); opacity:0.6;">-</span>';

      tr.innerHTML = `
        <td style="font-weight:700; color:var(--text-1);">${h.id}</td>
        <td>${keyBadge}</td>
        <td><span style="font-weight:600;">${formatExamDate(h.exam_date)}</span></td>
        <td style="text-align:center;"><span style="background:rgba(255,255,255,0.08); padding:4px 14px; border-radius:12px; font-weight:700; border:1px solid var(--glass-border);">${h.total_students}</span></td>
        <td style="text-align:right;">
          <div style="display:inline-flex; gap:6px; justify-content:flex-end; align-items:center; flex-wrap:nowrap;">
            <button class="btn btn-sm btn-primary" onclick="viewHistoryDetails(${h.id})" style="padding:6px 12px; font-size:0.85rem;"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path><circle cx="12" cy="12" r="3"></circle></svg> Results</button>
            <button class="btn btn-sm btn-ghost" onclick="downloadHistory(${h.id})" style="padding:6px 10px; font-size:0.85rem;"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="7 10 12 15 17 10"></polyline><line x1="12" y1="15" x2="12" y2="3"></line></svg> CSV</button>
            <button class="btn btn-sm btn-danger" onclick="deleteHistory(${h.id})" style="padding:6px 10px; font-size:0.85rem;" title="Delete Record"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"></path><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"></path><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"></path><line x1="10" y1="11" x2="10" y2="17"></line><line x1="14" y1="11" x2="14" y2="17"></line></svg></button>
          </div>
        </td>
      `;
      tbody.appendChild(tr);
    });
  } catch(e) { console.error(e); }
}

async function deleteHistory(id) {
  if(!confirm('Are you sure you want to delete this exam history?')) return;
  try {
    await fetch(`/api/history/${id}`, {method:'DELETE'});
    loadHistory();
  } catch(e) { console.error(e); }
}

async function downloadHistory(id) {
  window.open(`/api/history/${id}/download`, '_blank');
}

async function loadAdminHistory() {
  try {
    const res = await fetch('/api/history');
    const data = await res.json();
    const tbody = document.getElementById('adminHistoryList');
    if(!tbody) return;
    tbody.innerHTML = '';
    if(!Array.isArray(data) || data.length === 0) {
      tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; padding:25px; color:var(--text-2);">No exam history recorded yet.</td></tr>';
      return;
    }
    data.forEach(h => {
      const tr = document.createElement('tr');
      const keyBadge = h.exam_key ? `<span style="background:rgba(99,102,241,0.18); color:var(--accent-2); padding:3px 10px; border-radius:8px; font-weight:800; font-family:var(--font-mono); border:1px solid rgba(99,102,241,0.35);">🔑 ${esc(h.exam_key)}</span>` : '<span style="color:var(--text-2); opacity:0.6;">-</span>';

      tr.innerHTML = `
        <td style="font-weight:700; color:var(--text-1);">${h.id}</td>
        <td>${keyBadge}</td>
        <td style="font-weight:600;">${esc(h.teacher_name || '-')}</td>
        <td><span>${formatExamDate(h.exam_date)}</span></td>
        <td style="text-align:center;"><span style="background:rgba(255,255,255,0.08); padding:4px 14px; border-radius:12px; font-weight:700; border:1px solid var(--glass-border);">${h.total_students}</span></td>
        <td style="text-align:right;">
          <div style="display:inline-flex; gap:6px; justify-content:flex-end; align-items:center; flex-wrap:nowrap;">
            <button class="btn btn-sm btn-primary" onclick="viewHistoryDetails(${h.id})" style="padding:6px 12px; font-size:0.85rem;"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path><circle cx="12" cy="12" r="3"></circle></svg> Results</button>
            <button class="btn btn-sm btn-ghost" onclick="downloadHistory(${h.id})" style="padding:6px 10px; font-size:0.85rem;"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="7 10 12 15 17 10"></polyline><line x1="12" y1="15" x2="12" y2="3"></line></svg> CSV</button>
            <button class="btn btn-sm btn-danger" onclick="deleteHistory(${h.id}); setTimeout(loadAdminHistory, 500);" style="padding:6px 10px; font-size:0.85rem;" title="Delete Record"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"></path><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"></path><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"></path><line x1="10" y1="11" x2="10" y2="17"></line><line x1="14" y1="11" x2="14" y2="17"></line></svg></button>
          </div>
        </td>
      `;
      tbody.appendChild(tr);
    });
  } catch(e) { console.error(e); }
}

function showPasswordModal(mandatory = false) {
  const modal = document.getElementById('passwordModal');
  if (modal) {
    modal.classList.remove('hidden');
    const cancelBtn = modal.querySelector('button.btn-ghost');
    if (mandatory && cancelBtn) {
      cancelBtn.style.display = 'none';
    } else if (cancelBtn) {
      cancelBtn.style.display = 'block';
    }
  }
}

async function savePassword() {
  const pw = document.getElementById('newPassword').value;
  if (!pw) return alert("Password required");
  try {
    const res = await api("/api/change_password", {method: "POST", body: JSON.stringify({password: pw})});
    if (res.ok) {
      alert("Password updated successfully.");
      document.getElementById('passwordModal').classList.add('hidden');
      if (me.force_password_change) {
          me.force_password_change = false;
          if (me.role === "admin") return admin();
          if (me.role === "teacher") return teacher();
          return student();
      }
    }
  } catch(e) { alert(e.message); }
}

async function createBackup() {
  try {
    const res = await api('/api/admin/backup', {method: 'POST'});
    if (res.ok) alert(`Backup created successfully: ${res.file}\nStored in the 'backups' directory.`);
  } catch (e) {
    alert('Backup failed: ' + e.message);
  }
}

async function loadTeacherProfile() {
  try {
    const res = await api('/api/teacher/profile');
    if (res.department !== undefined) {
      if ($("#teacherDept")) $("#teacherDept").value = res.department || "";
      if ($("#teacherClass")) $("#teacherClass").value = res.class || "";
      if ($("#teacherYear")) $("#teacherYear").value = res.year || "";
    }
  } catch (e) {
    console.error("Failed to load profile:", e);
  }
}

if ($("#teacherProfileForm")) {
  $("#teacherProfileForm").onsubmit = async (e) => {
    e.preventDefault();
    try {
      const data = {
        department: $("#teacherDept")?.value || "",
        class: $("#teacherClass")?.value || "",
        year: $("#teacherYear")?.value || ""
      };
      await api('/api/teacher/profile', {method: 'PUT', body: JSON.stringify(data)});
      alert("Teacher profile saved!");
    } catch (err) {
      alert("Error saving profile: " + err.message);
    }
  };
}

function renderMcqPalette() {
  // MCQ Question palette removed per user request
}
window.renderMcqPalette = renderMcqPalette;

window.recheckExternalMonitors = () => {
  const isMultiScreen = Boolean(
    (window.screen && window.screen.isExtended) ||
    (window.screen && window.screen.availWidth > window.screen.width * 1.05) ||
    (window.screenLeft !== undefined && (window.screenLeft < -50 || window.screenLeft > window.screen.width * 0.95))
  );
  const statusEl = document.getElementById("monitorPromptStatus");
  if (isMultiScreen) {
    if (statusEl) {
      statusEl.textContent = "⚠️ Dual monitor is still connected! Please disconnect or press Win+P.";
      statusEl.style.color = "#ef4444";
    }
  } else {
    $("#externalMonitorPrompt")?.classList.add("hidden");
    window._externalMonitorActive = false;
    alert("Display verified! You may resume your exam.");
  }
};

window.openStudentResultModal = async () => {
  try {
    const res = await api("/api/student/result");
    if (!res || !res.ok) {
      alert("Unable to load exam result.");
      return;
    }
    const { student, scores, questions: qList, mcqs: mList, exam_date } = res;
    const body = $("#scorecardBody");
    if (!body) return;
    
    const isPass = (scores.status === "PASS");
    const statusBadge = isPass 
      ? `<span class="badge-pass">PASS (EXCELLENT)</span>` 
      : `<span class="badge-fail">NEEDS IMPROVEMENT</span>`;
      
    body.innerHTML = `
      <div style="background:var(--bg-1, rgba(255,255,255,0.03)); border:1px solid var(--glass-border); border-radius:14px; padding:16px 20px; margin-bottom:18px;">
        <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap:12px; font-size:0.9rem;">
          <div><b style="color:var(--text-2);">Student Name:</b> <span style="font-weight:700; color:var(--text-0);">${esc(student.name)}</span></div>
          <div><b style="color:var(--text-2);">Register Number:</b> <span style="font-weight:700; color:var(--text-0);">${esc(student.register_number)}</span></div>
          <div><b style="color:var(--text-2);">Department / Class:</b> <span style="font-weight:700; color:var(--text-0);">${esc(student.department)} ${esc(student.class)}</span></div>
          <div><b style="color:var(--text-2);">Exam Date:</b> <span style="font-weight:700; color:var(--text-0);">${esc(exam_date)}</span></div>
        </div>
      </div>

      <div class="scorecard-summary-grid">
        <div class="scorecard-metric-card">
          <span class="metric-label">Total Score</span>
          <span class="metric-val" style="color:var(--accent-1);">${scores.total_score} <small style="font-size:0.9rem; color:var(--text-2);">/ ${scores.grand_total}</small></span>
          <span class="metric-sub">${scores.percentage}% marks scored</span>
        </div>
        <div class="scorecard-metric-card">
          <span class="metric-label">Result Status</span>
          <div style="margin-top:4px;">${statusBadge}</div>
          <span class="metric-sub">${scores.locked ? "Session Locked" : "Submitted Successfully"}</span>
        </div>
        ${scores.attended_coding ? `
        <div class="scorecard-metric-card">
          <span class="metric-label">Coding Score</span>
          <span class="metric-val">${scores.coding_score} <small style="font-size:0.9rem; color:var(--text-2);">/ ${scores.coding_total}</small></span>
          <span class="metric-sub">Multi-language execution</span>
        </div>` : ''}
        ${scores.attended_mcq ? `
        <div class="scorecard-metric-card">
          <span class="metric-label">MCQ Score</span>
          <span class="metric-val">${scores.mcq_score} <small style="font-size:0.9rem; color:var(--text-2);">/ ${scores.mcq_total}</small></span>
          <span class="metric-sub">Objective tests</span>
        </div>` : ''}
        <div class="scorecard-metric-card">
          <span class="metric-label">Time & Security</span>
          <span class="metric-val" style="font-size:1.25rem;">${fmt(scores.total_time)}</span>
          <span class="metric-sub">${scores.violations} Anti-cheat strikes</span>
        </div>
      </div>

      ${(scores.attended_coding && qList && qList.length > 0) ? `
      <h4 style="margin:22px 0 12px 0; font-size:1.05rem; font-weight:700; color:var(--text-0); display:flex; align-items:center; gap:8px;">
        <span>📝</span> <span>Questions Performance</span>
      </h4>
      <div class="scorecard-table-wrapper">
        <table class="scorecard-table">
          <thead>
            <tr>
              <th style="width: 50px; text-align: center;">#</th>
              <th style="min-width: 250px; text-align: left;">Question Title</th>
              <th style="width: 120px; text-align: center;">Language</th>
              <th style="width: 130px; text-align: center;">Marks</th>
              <th style="width: 140px; text-align: center;">Status</th>
            </tr>
          </thead>
          <tbody>
            ${(qList || []).map((q, idx) => `
              <tr>
                <td style="text-align: center; font-weight: 700; color: var(--text-2);">${idx + 1}</td>
                <td style="font-weight: 600; color: var(--text-0); line-height: 1.45; white-space: normal;">${esc(q.title)}</td>
                <td style="text-align: center;">
                  <span class="scorecard-lang-badge">${esc(q.language)}</span>
                </td>
                <td style="text-align: center; font-weight: 700; font-family: var(--font-mono);">
                  <span style="color: ${q.scored_marks > 0 ? 'var(--accent-1)' : 'var(--text-2)'};">${q.scored_marks}</span>
                  <span style="color: var(--text-2); font-weight: 500;"> / ${q.max_marks}</span>
                </td>
                <td style="text-align: center;">
                  <span class="${q.status === 'Full Marks' ? 'badge-pass' : (q.status === 'Partial' ? 'badge-partial' : 'badge-fail')}">${esc(q.status)}</span>
                </td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      </div>` : ''}

      ${(scores.attended_mcq && mList && mList.length > 0) ? `
      <h4 style="margin:24px 0 12px 0; font-size:1.05rem; font-weight:700; color:var(--text-0); display:flex; align-items:center; gap:8px;">
        <span>🎯</span> <span>MCQ Performance</span>
      </h4>
      <div class="scorecard-table-wrapper">
        <table class="scorecard-table">
          <thead>
            <tr>
              <th style="width: 50px; text-align: center;">#</th>
              <th style="min-width: 280px; text-align: left;">MCQ Question</th>
              <th style="width: 110px; text-align: center;">Selected</th>
              <th style="width: 130px; text-align: center;">Result</th>
              <th style="width: 120px; text-align: center;">Marks</th>
            </tr>
          </thead>
          <tbody>
            ${mList.map((m, idx) => `
              <tr>
                <td style="text-align: center; font-weight: 700; color: var(--text-2);">${idx + 1}</td>
                <td style="color: var(--text-0); line-height: 1.45; white-space: normal;">${esc(m.question)}</td>
                <td style="text-align: center;">
                  <span class="scorecard-mcq-opt">${esc(m.selected_option)}</span>
                </td>
                <td style="text-align: center;">
                  <span class="${m.is_correct ? 'badge-pass' : 'badge-fail'}">${m.is_correct ? 'CORRECT' : 'INCORRECT'}</span>
                </td>
                <td style="text-align: center; font-weight: 700; font-family: var(--font-mono);">
                  <span style="color: ${m.is_correct ? 'var(--accent-1)' : 'var(--text-2)'};">${m.is_correct ? m.marks : 0}</span>
                  <span style="color: var(--text-2); font-weight: 500;"> / ${m.marks}</span>
                </td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      </div>` : ''}
    `;
    $("#studentResultModal")?.classList.remove("hidden");
  } catch (err) {
    alert("Error loading scorecard: " + err.message);
  }
};

window.printScorecard = () => {
  window.print();
};

window.openPlagiarismModal = async (historyId = null) => {
  const modal = $("#plagiarismModal");
  const content = $("#plagiarismContent");
  if (!modal || !content) return;
  modal.classList.remove("hidden");
  content.innerHTML = `<div style="text-align:center; padding:40px; color:var(--text-2);"><span style="display:inline-block; animation:pulse 1.5s infinite; font-size:1.8rem;">🔍</span><p style="margin-top:12px; font-weight:600;">Scanning all student submissions with lexical and structural similarity analysis...</p></div>`;
  
  try {
    const url = "/api/plagiarism" + (historyId ? "?history_id=" + historyId : "");
    const res = await api(url);
    if (!res || !res.ok) {
      content.innerHTML = `<div style="color:#ef4444; padding:20px; text-align:center;">Failed to run plagiarism detector.</div>`;
      return;
    }
    const matches = res.matches || [];
    const subtitle = $("#plagiarismSubtitle");
    if (subtitle) {
      subtitle.textContent = historyId ? `Archived Exam #${historyId} • ${res.total_comparisons} Comparisons Analyzed` : `Active Exam Session • ${res.total_comparisons} Comparisons Analyzed`;
    }
    
    if (matches.length === 0) {
      content.innerHTML = `
        <div style="text-align:center; padding:40px 20px;">
          <div style="font-size:3rem; margin-bottom:10px;">✅</div>
          <h3 style="color:#10b981; margin:0 0 8px 0;">No Plagiarism Detected</h3>
          <p style="color:var(--text-2); font-size:0.9rem;">All submitted solutions are structurally independent, or fewer than 2 student submissions exist for comparison.</p>
        </div>
      `;
      return;
    }
    
    content.innerHTML = `
      <div class="plagiarism-stats-bar">
        <div class="plagiarism-stat-item">
          <span style="font-size:0.75rem; color:var(--text-2); font-weight:700; text-transform:uppercase;">Total Comparisons</span>
          <div style="font-size:1.5rem; font-weight:800; color:var(--text-0);">${res.total_comparisons}</div>
          <small style="color:var(--text-2);">Cross-compared submissions</small>
        </div>
        <div class="plagiarism-stat-item">
          <span style="font-size:0.75rem; color:var(--text-2); font-weight:700; text-transform:uppercase;">High Risk Matches (&ge;75%)</span>
          <div style="font-size:1.5rem; font-weight:800; color:#ef4444;">${res.high_risk_count}</div>
          <small style="color:#ef4444;">Probable copied solutions</small>
        </div>
        <div class="plagiarism-stat-item">
          <span style="font-size:0.75rem; color:var(--text-2); font-weight:700; text-transform:uppercase;">Highest Match</span>
          <div style="font-size:1.5rem; font-weight:800; color:var(--accent-1);">${matches[0]?.similarity || 0}%</div>
          <small style="color:var(--text-2);">${esc(matches[0]?.question_title || '')}</small>
        </div>
      </div>

      <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:14px;">
        <b style="font-size:1rem; color:var(--text-0);">Ranked Student Comparisons</b>
        <div style="display:flex; gap:8px;">
          <button class="btn btn-sm btn-ghost" onclick="filterPlagiarismList('all')" style="padding:4px 10px; font-size:0.78rem;">All (${matches.length})</button>
          <button class="btn btn-sm btn-ghost" onclick="filterPlagiarismList('high')" style="padding:4px 10px; font-size:0.78rem; color:#ef4444; border-color:rgba(239,68,68,0.4);">High Risk (&ge;75%)</button>
        </div>
      </div>

      <div id="plagiarismCardsContainer">
        ${matches.map((m, idx) => {
          const riskCls = m.risk === 'HIGH' ? 'plagiarism-risk-high' : (m.risk === 'MODERATE' ? 'plagiarism-risk-moderate' : 'plagiarism-risk-low');
          const pillCls = m.risk === 'HIGH' ? 'risk-pill-high' : (m.risk === 'MODERATE' ? 'risk-pill-moderate' : 'risk-pill-low');
          return `
            <div class="plagiarism-card ${riskCls}" data-risk="${m.risk}">
              <div style="flex:1;">
                <div style="display:flex; align-items:center; gap:10px; margin-bottom:6px;">
                  <span class="${pillCls}">${m.similarity}% MATCH</span>
                  <span style="font-weight:700; color:var(--text-0); font-size:0.95rem;">${esc(m.question_title)}</span>
                  <span style="font-size:0.75rem; text-transform:uppercase; color:var(--text-2); background:rgba(255,255,255,0.06); padding:2px 8px; border-radius:4px;">${esc(m.language)}</span>
                </div>
                <div style="font-size:0.86rem; color:var(--text-1); display:flex; gap:16px;">
                  <span><b>Student 1:</b> ${esc(m.student_1.name)} (${esc(m.student_1.username)})</span>
                  <span style="color:var(--text-2);">&#8644;</span>
                  <span><b>Student 2:</b> ${esc(m.student_2.name)} (${esc(m.student_2.username)})</span>
                </div>
                <div id="diffView_${idx}" class="plagiarism-diff-split" style="display:none;">
                  <div>
                    <div style="font-weight:700; font-size:0.8rem; margin-bottom:4px; color:var(--text-1);">${esc(m.student_1.name)} Code:</div>
                    <pre class="plagiarism-code-block">${esc(m.code_1)}</pre>
                  </div>
                  <div>
                    <div style="font-weight:700; font-size:0.8rem; margin-bottom:4px; color:var(--text-1);">${esc(m.student_2.name)} Code:</div>
                    <pre class="plagiarism-code-block">${esc(m.code_2)}</pre>
                  </div>
                </div>
              </div>
              <button class="btn btn-sm btn-ghost" onclick="togglePlagiarismDiff(${idx})" style="padding:6px 14px; font-size:0.82rem; font-weight:700; flex-shrink:0;">Compare Code &#8645;</button>
            </div>
          `;
        }).join('')}
      </div>
    `;
  } catch (err) {
    content.innerHTML = `<div style="color:#ef4444; padding:20px; text-align:center;">Error: ${esc(err.message)}</div>`;
  }
};

window.togglePlagiarismDiff = (idx) => {
  const diff = document.getElementById(`diffView_${idx}`);
  if (diff) {
    diff.style.display = (diff.style.display === "none") ? "grid" : "none";
  }
};

window.filterPlagiarismList = (filter) => {
  const cards = document.querySelectorAll("#plagiarismCardsContainer .plagiarism-card");
  cards.forEach(card => {
    if (filter === "all") {
      card.style.display = "flex";
    } else if (filter === "high") {
      card.style.display = (card.getAttribute("data-risk") === "HIGH") ? "flex" : "none";
    }
  });
};



// ==================== INDIVIDUAL STUDENT QUESTION ASSIGNMENT CONTROLLER ====================

const assignmentState = {
  teacher: { data: null, selectedExam: null, selectedStudentId: null },
  admin: { data: null, selectedExam: null, selectedStudentId: null }
};

window.loadAssignmentsUI = async (role = 'teacher') => {
  const isAdm = (role === 'admin');
  const examSel = document.getElementById(isAdm ? 'adminAssignmentExamSelect' : 'assignmentExamSelect');
  if (!examSel) return;
  
  examSel.innerHTML = '<option value="">Loading examinations...</option>';
  try {
    const res = await api('/api/assignments/exams');
    const exams = res.exams || [];
    if (!exams.length) {
      examSel.innerHTML = '<option value="">No examinations available</option>';
      renderAssignmentEmptyState(role, 'No active or upcoming examinations found.');
      return;
    }
    examSel.innerHTML = exams.map(e => `
      <option value="${e.id}" data-type="${e.exam_type}" data-key="${e.exam_key}" data-tid="${e.teacher_id}">
        ${esc(e.title)} (${e.total_questions} common questions) ${e.active ? '🟢 ACTIVE' : '⏳ UPCOMING'}
      </option>
    `).join('');
    
    await onAssignmentExamChange(role);
  } catch (err) {
    examSel.innerHTML = `<option value="">Error loading exams: ${esc(err.message)}</option>`;
  }
};

window.onAssignmentExamChange = async (role = 'teacher') => {
  const isAdm = (role === 'admin');
  const examSel = document.getElementById(isAdm ? 'adminAssignmentExamSelect' : 'assignmentExamSelect');
  if (!examSel || !examSel.value) return;
  
  const opt = examSel.selectedOptions[0];
  const examType = opt.getAttribute('data-type');
  const examKey = opt.getAttribute('data-key');
  const teacherId = opt.getAttribute('data-tid');
  
  const summaryEl = document.getElementById(isAdm ? 'adminAssignmentExamSummary' : 'assignmentExamSummary');
  if (summaryEl) {
    summaryEl.innerHTML = `<span style="color:var(--accent-1); font-weight:700;">Loading assignment data for key: ${examKey}...</span>`;
  }

  try {
    const res = await api(`/api/assignments?exam_key=${encodeURIComponent(examKey)}&exam_type=${encodeURIComponent(examType)}&teacher_id=${encodeURIComponent(teacherId)}`);
    assignmentState[role].data = res;
    assignmentState[role].selectedExam = { examKey, examType, teacherId };

    if (summaryEl) {
      summaryEl.innerHTML = `
        <div style="display:flex; gap:16px; align-items:center; flex-wrap:wrap;">
          <span>Type: <b>${examType.toUpperCase()}</b></span>
          <span>Exam Key: <code style="background:rgba(0,0,0,0.25); padding:2px 8px; border-radius:6px; font-weight:800; color:var(--accent-1);">${examKey}</code></span>
          <span>Common Questions: <b>${res.total_common}</b></span>
          <span>Enrolled Students: <b>${res.students.length}</b></span>
        </div>
      `;
    }

    renderAssignmentStudentDropdown(role);
    renderAssignmentOverviewTable(role);
    onAssignmentStudentChange(role);
  } catch (err) {
    if (summaryEl) summaryEl.innerHTML = `<span style="color:#ef4444;">Error loading assignments: ${esc(err.message)}</span>`;
  }
};

function renderAssignmentStudentDropdown(role = 'teacher') {
  const isAdm = (role === 'admin');
  const studSel = document.getElementById(isAdm ? 'adminAssignmentStudentSelect' : 'assignmentStudentSelect');
  if (!studSel) return;
  
  const data = assignmentState[role].data;
  if (!data || !data.students || !data.students.length) {
    studSel.innerHTML = '<option value="">No eligible students found</option>';
    return;
  }

  studSel.innerHTML = data.students.map(s => {
    const modeBadge = (s.mode === 'individual') ? ` [Indiv: ${s.assigned_question_ids.length} Qs]` : ' [Common: All]';
    const lockBadge = s.attempt_started ? ' 🔒 (Locked)' : '';
    return `<option value="${s.id}">${esc(s.name)} (${esc(s.register_number)})${modeBadge}${lockBadge}</option>`;
  }).join('');
}

window.onAssignmentStudentChange = (role = 'teacher') => {
  const isAdm = (role === 'admin');
  const studSel = document.getElementById(isAdm ? 'adminAssignmentStudentSelect' : 'assignmentStudentSelect');
  const qListEl = document.getElementById(isAdm ? 'adminAssignmentQuestionsList' : 'assignmentQuestionsList');
  const statusNotice = document.getElementById(isAdm ? 'adminAssignmentStatusNotice' : 'assignmentStatusNotice');
  const saveBtn = document.getElementById(isAdm ? 'adminAssignmentSaveBtn' : 'assignmentSaveBtn');
  const resetBtn = document.getElementById(isAdm ? 'adminAssignmentResetBtn' : 'assignmentResetBtn');
  
  if (!studSel || !qListEl) return;
  const data = assignmentState[role].data;
  if (!data || !data.students) return;

  const studentId = parseInt(studSel.value, 10);
  assignmentState[role].selectedStudentId = studentId;
  const student = data.students.find(s => s.id === studentId);

  if (!student) {
    qListEl.innerHTML = '<div style="color:var(--text-2); text-align:center; padding:15px;">No student selected.</div>';
    return;
  }

  const assignedSet = new Set(student.assigned_question_ids || []);
  const isIndiv = (student.mode === 'individual');
  const isLocked = Boolean(student.attempt_started || student.attempt_locked);

  if (statusNotice) {
    if (isLocked) {
      statusNotice.innerHTML = '<span style="color:#f59e0b; font-weight:700;">🔒 Attempt already started — Question set is locked and cannot be changed.</span>';
    } else if (isIndiv) {
      statusNotice.innerHTML = `<span style="color:var(--accent-1); font-weight:700;">Mode: Individual (${assignedSet.size} Questions Assigned)</span>`;
    } else {
      statusNotice.innerHTML = '<span style="color:var(--text-2);">Mode: Common Questions (Receives All)</span>';
    }
  }

  if (saveBtn) saveBtn.disabled = isLocked;
  if (resetBtn) resetBtn.disabled = isLocked;

  const commonQuestions = data.common_questions || [];
  if (!commonQuestions.length) {
    qListEl.innerHTML = '<div style="color:var(--text-2); text-align:center; padding:15px;">No questions in this examination.</div>';
    updateAssignmentSelectedCount(role);
    return;
  }

  qListEl.innerHTML = commonQuestions.map((q, idx) => {
    const isChecked = isIndiv ? assignedSet.has(q.id) : false;
    const title = q.title || `Question #${q.id}`;
    const marksBadge = (q.marks !== undefined) ? `<span style="font-size:0.75rem; background:rgba(255,255,255,0.08); padding:2px 8px; border-radius:6px; color:var(--text-2);">${q.marks} mark(s)</span>` : '';
    const langBadge = q.language ? `<span style="font-size:0.75rem; background:rgba(13,148,136,0.15); padding:2px 8px; border-radius:6px; color:var(--accent-1); font-weight:700;">${esc(q.language)}</span>` : '';
    
    return `
      <label style="display:flex; align-items:center; gap:12px; padding:8px 12px; border-radius:8px; background:var(--bg-1, rgba(255,255,255,0.02)); border:1px solid var(--glass-border); cursor:${isLocked ? 'not-allowed' : 'pointer'}; opacity:${isLocked ? '0.7' : '1'}; transition:all 0.15s ease;">
        <input type="checkbox" class="asgn-q-check-${role}" value="${q.id}" ${isChecked ? 'checked' : ''} ${isLocked ? 'disabled' : ''} onchange="updateAssignmentSelectedCount('${role}')" style="width:17px; height:17px; cursor:${isLocked ? 'not-allowed' : 'pointer'};">
        <b style="color:var(--text-0); font-size:0.9rem; min-width:85px;">Question ${idx + 1}:</b>
        <span style="flex:1; color:var(--text-1); font-size:0.9rem; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">${esc(title)}</span>
        <div style="display:flex; gap:6px; align-items:center;">
          ${langBadge}
          ${marksBadge}
        </div>
      </label>
    `;
  }).join('');

  updateAssignmentSelectedCount(role);
};

window.assignmentSelectAll = (checkAll, role = 'teacher') => {
  const checks = document.querySelectorAll(`.asgn-q-check-${role}:not(:disabled)`);
  checks.forEach(c => c.checked = Boolean(checkAll));
  updateAssignmentSelectedCount(role);
};

window.updateAssignmentSelectedCount = (role = 'teacher') => {
  const isAdm = (role === 'admin');
  const countEl = document.getElementById(isAdm ? 'adminAssignmentSelectedCount' : 'assignmentSelectedCount');
  if (!countEl) return;
  const checks = document.querySelectorAll(`.asgn-q-check-${role}:checked`);
  const total = document.querySelectorAll(`.asgn-q-check-${role}`).length;
  countEl.textContent = `${checks.length} / ${total} Selected`;
};

window.saveIndividualAssignment = async (role = 'teacher') => {
  const state = assignmentState[role];
  if (!state || !state.selectedExam || !state.selectedStudentId) {
    alert("Please select both an examination and a student.");
    return;
  }

  const student = (state.data.students || []).find(s => s.id === state.selectedStudentId);
  const studentName = student ? student.name : "the selected student";

  const checkedEls = document.querySelectorAll(`.asgn-q-check-${role}:checked`);
  const selectedIds = Array.from(checkedEls).map(el => parseInt(el.value, 10));

  if (!selectedIds.length) {
    alert("Please select at least 1 question for an individual assignment.\nTo restore all questions, use 'Reset to Common Questions'.");
    return;
  }

  const confirmMsg = `Assign the selected ${selectedIds.length} question(s) to ${studentName}?\n\nThis will REPLACE the common question set for this student. Other students will remain unaffected.`;
  if (!confirm(confirmMsg)) return;

  try {
    const payload = {
      exam_key: state.selectedExam.examKey,
      exam_type: state.selectedExam.examType,
      teacher_id: state.selectedExam.teacherId,
      student_id: state.selectedStudentId,
      question_ids: selectedIds
    };

    const res = await api('/api/assignments', {
      method: 'POST',
      body: JSON.stringify(payload)
    });

    alert(res.message || "Individual assignment saved successfully!");
    await onAssignmentExamChange(role);
  } catch (err) {
    alert(err.message || "Failed to save assignment.");
  }
};

window.resetIndividualAssignment = async (role = 'teacher') => {
  const state = assignmentState[role];
  if (!state || !state.selectedExam || !state.selectedStudentId) {
    alert("Please select both an examination and a student.");
    return;
  }

  const student = (state.data.students || []).find(s => s.id === state.selectedStudentId);
  const studentName = student ? student.name : "the selected student";

  const confirmMsg = `Reset ${studentName} to Common Questions mode?\n\nThis student will receive all ${state.data.total_common} common questions configured for this examination.`;
  if (!confirm(confirmMsg)) return;

  try {
    const payload = {
      exam_key: state.selectedExam.examKey,
      exam_type: state.selectedExam.examType,
      teacher_id: state.selectedExam.teacherId,
      student_id: state.selectedStudentId
    };

    const res = await api('/api/assignments/reset', {
      method: 'POST',
      body: JSON.stringify(payload)
    });

    alert(res.message || "Student reset to Common Questions mode!");
    await onAssignmentExamChange(role);
  } catch (err) {
    alert(err.message || "Failed to reset assignment.");
  }
};

function renderAssignmentOverviewTable(role = 'teacher') {
  const isAdm = (role === 'admin');
  const tbody = document.getElementById(isAdm ? 'adminAssignmentOverviewTableBody' : 'assignmentOverviewTableBody');
  if (!tbody) return;

  const data = assignmentState[role].data;
  if (!data || !data.students || !data.students.length) {
    tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; padding:20px; color:var(--text-2);">No students found for this examination.</td></tr>';
    return;
  }

  const totalCommon = data.total_common || 0;
  tbody.innerHTML = data.students.map(s => {
    const isIndiv = (s.mode === 'individual');
    const isLocked = Boolean(s.attempt_started || s.attempt_locked);
    const modeBadge = isIndiv
      ? `<span style="background:rgba(13,148,136,0.18); color:var(--accent-1); border:1px solid rgba(13,148,136,0.35); padding:3px 10px; border-radius:999px; font-weight:700; font-size:0.8rem;">Individual (${s.assigned_question_ids.length})</span>`
      : `<span style="background:rgba(255,255,255,0.06); color:var(--text-2); border:1px solid var(--glass-border); padding:3px 10px; border-radius:999px; font-weight:600; font-size:0.8rem;">Common (All ${totalCommon})</span>`;
    
    const lockNotice = isLocked
      ? `<span style="margin-left:6px; font-size:0.75rem; background:rgba(234,179,8,0.18); color:#f59e0b; border:1px solid rgba(234,179,8,0.35); padding:2px 8px; border-radius:999px; font-weight:700;">🔒 Attempt Started</span>`
      : '';

    const questionsListStr = isIndiv
      ? `Q${s.assigned_question_ids.map(qid => {
          const qIdx = data.common_questions.findIndex(cq => cq.id === qid);
          return (qIdx !== -1) ? (qIdx + 1) : `#${qid}`;
        }).join(', Q')}`
      : `All ${totalCommon} Questions`;

    return `
      <tr class="asgn-row-${role}" data-name="${esc((s.name || '').toLowerCase())}" data-reg="${esc((s.register_number || '').toLowerCase())}" data-dept="${esc((s.department || '').toLowerCase())}">
        <td style="padding:12px 14px; font-weight:700; color:var(--text-0);">${esc(s.name)}</td>
        <td style="padding:12px 14px; color:var(--text-1); font-family:monospace;">${esc(s.register_number)}</td>
        <td style="padding:12px 14px; color:var(--text-2);">${esc(s.department || '-')} ${s.section ? `(${esc(s.section)})` : ''}</td>
        <td style="padding:12px 14px;">${modeBadge}${lockNotice}</td>
        <td style="padding:12px 14px; color:var(--text-1); font-size:0.88rem; max-width:240px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${esc(questionsListStr)}">${esc(questionsListStr)}</td>
        <td style="padding:12px 14px; text-align:right;">
          <button type="button" class="btn btn-ghost" onclick="editStudentAssignmentFromTable(${s.id}, '${role}')" style="padding:5px 14px; font-size:0.82rem; font-weight:700;">
            ✏️ Manage
          </button>
        </td>
      </tr>
    `;
  }).join('');
}

window.editStudentAssignmentFromTable = (studentId, role = 'teacher') => {
  const isAdm = (role === 'admin');
  const studSel = document.getElementById(isAdm ? 'adminAssignmentStudentSelect' : 'assignmentStudentSelect');
  if (studSel) {
    studSel.value = String(studentId);
    onAssignmentStudentChange(role);
    studSel.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }
};

window.filterAssignmentOverviewTable = (role = 'teacher') => {
  const isAdm = (role === 'admin');
  const searchInput = document.getElementById(isAdm ? 'adminAssignmentTableSearch' : 'assignmentTableSearch');
  if (!searchInput) return;
  const q = searchInput.value.toLowerCase().trim();
  const rows = document.querySelectorAll(`.asgn-row-${role}`);
  rows.forEach(r => {
    const name = r.getAttribute('data-name') || '';
    const reg = r.getAttribute('data-reg') || '';
    const dept = r.getAttribute('data-dept') || '';
    if (!q || name.includes(q) || reg.includes(q) || dept.includes(q)) {
      r.style.display = '';
    } else {
      r.style.display = 'none';
    }
  });
};

function renderAssignmentEmptyState(role, msg) {
  const isAdm = (role === 'admin');
  const summaryEl = document.getElementById(isAdm ? 'adminAssignmentExamSummary' : 'assignmentExamSummary');
  const qListEl = document.getElementById(isAdm ? 'adminAssignmentQuestionsList' : 'assignmentQuestionsList');
  const tbody = document.getElementById(isAdm ? 'adminAssignmentOverviewTableBody' : 'assignmentOverviewTableBody');
  if (summaryEl) summaryEl.innerHTML = `<span style="color:var(--text-2);">${esc(msg)}</span>`;
  if (qListEl) qListEl.innerHTML = `<div style="color:var(--text-2); text-align:center; padding:20px;">${esc(msg)}</div>`;
  if (tbody) tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; padding:20px; color:var(--text-2);">${esc(msg)}</td></tr>`;
}
