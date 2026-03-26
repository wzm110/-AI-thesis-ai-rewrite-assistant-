const state = {
  jobId: null,
  total: 0,
  done: 0,
  error: 0,
  source: null,
  cards: new Map(),
};

async function sendFrontendLog(level, message, payload = {}) {
  appendDebugLog(`frontend_log ${level} ${message}`, payload);
  const body = {
    level,
    message,
    job_id: state.jobId,
    payload,
  };
  try {
    await fetch("/api/frontend-log", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (e) {
    appendDebugLog("frontend_log_upload_failed", { error: String(e) });
  }
}

const el = {
  startBtn: document.getElementById("startBtn"),
  concurrencyInput: document.getElementById("concurrencyInput"),
  downloadBtn: document.getElementById("downloadBtn"),
  cards: document.getElementById("cards"),
  jobId: document.getElementById("jobId"),
  totalCount: document.getElementById("totalCount"),
  doneCount: document.getElementById("doneCount"),
  errorCount: document.getElementById("errorCount"),
  statusText: document.getElementById("statusText"),
  finalText: document.getElementById("finalText"),
  debugLog: document.getElementById("debugLog"),
  docxFileInput: document.getElementById("docxFileInput"),
  docxUploadBtn: document.getElementById("docxUploadBtn"),
  docxUploadStatus: document.getElementById("docxUploadStatus"),
};

function appendDebugLog(line, payload = null) {
  const now = new Date().toISOString().replace("T", " ").slice(0, 19);
  let text = `[${now}] ${line}`;
  if (payload && typeof payload === "object") {
    text += ` | ${JSON.stringify(payload)}`;
  }
  if (el.debugLog) {
    el.debugLog.textContent += `${text}\n`;
    el.debugLog.scrollTop = el.debugLog.scrollHeight;
  }
  console.log(text);
}

function escapeHtml(text) {
  return text
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function lcsDiff(a, b) {
  const maxForDiff = 800;
  if (a.length > maxForDiff || b.length > maxForDiff) {
    return {
      oldHtml: escapeHtml(a),
      newHtml: escapeHtml(b),
      mixHtml: `<div>${escapeHtml(b)}</div>`,
    };
  }

  const n = a.length;
  const m = b.length;
  const dp = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));

  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      if (a[i] === b[j]) {
        dp[i][j] = dp[i + 1][j + 1] + 1;
      } else {
        dp[i][j] = Math.max(dp[i + 1][j], dp[i][j + 1]);
      }
    }
  }

  let i = 0;
  let j = 0;
  let mix = "";
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      mix += escapeHtml(a[i]);
      i++;
      j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      mix += `<del>${escapeHtml(a[i])}</del>`;
      i++;
    } else {
      mix += `<ins>${escapeHtml(b[j])}</ins>`;
      j++;
    }
  }

  while (i < n) {
    mix += `<del>${escapeHtml(a[i])}</del>`;
    i++;
  }
  while (j < m) {
    mix += `<ins>${escapeHtml(b[j])}</ins>`;
    j++;
  }

  return {
    oldHtml: escapeHtml(a),
    newHtml: escapeHtml(b),
    mixHtml: mix,
  };
}

function setProgress() {
  el.jobId.textContent = state.jobId || "-";
  el.totalCount.textContent = String(state.total);
  el.doneCount.textContent = String(state.done);
  el.errorCount.textContent = String(state.error);
}

function createCard(order, originalText, sectionTitle = "") {
  const card = document.createElement("article");
  card.className = "card";
  card.dataset.order = String(order);
  card.innerHTML = `
    <div class="card-head">
      <strong>分段 #${order}${sectionTitle ? ` | ${escapeHtml(sectionTitle)}` : ""}</strong>
      <span class="badge running">处理中</span>
    </div>
    <div class="compare">
      <section class="panel">
        <h3>原文</h3>
        <div class="text original">${escapeHtml(originalText)}</div>
      </section>
      <section class="panel">
        <h3>改写后</h3>
        <div class="text rewritten">等待结果...</div>
      </section>
      <section class="panel">
        <h3>差异高亮</h3>
        <div class="text diff">等待结果...</div>
      </section>
    </div>
  `;
  state.cards.set(order, card);
  el.cards.appendChild(card);
}

function onTaskStarted(payload) {
  sendFrontendLog("info", "event_task_started", {
    order: payload.order,
    taskId: payload.task_id,
    originalLen: (payload.original_text || "").length,
  });
  const order = payload.order;
  if (!state.cards.has(order)) {
    createCard(order, payload.original_text, payload.section_title || "");
  }
}

function onTaskDone(payload) {
  sendFrontendLog("info", "event_task_done", {
    order: payload.order,
    doneCount: payload.done_count,
    errorCount: payload.error_count,
    rewrittenLen: (payload.rewritten_text || "").length,
  });
  const order = payload.order;
  const card = state.cards.get(order);
  if (!card) {
    createCard(order, payload.original_text, payload.section_title || "");
  }
  const target = state.cards.get(order);
  const badge = target.querySelector(".badge");
  badge.className = "badge done";
  badge.textContent = "成功";

  const rewrittenEl = target.querySelector(".rewritten");
  const diffEl = target.querySelector(".diff");
  const diff = lcsDiff(payload.original_text, payload.rewritten_text);
  rewrittenEl.innerHTML = diff.newHtml;
  diffEl.innerHTML = diff.mixHtml;

  state.done = payload.done_count;
  state.error = payload.error_count;
  setProgress();
}

function onTaskError(payload) {
  sendFrontendLog("error", "event_task_error", {
    order: payload.order,
    error: payload.error,
    doneCount: payload.done_count,
    errorCount: payload.error_count,
  });
  const order = payload.order;
  if (!state.cards.has(order)) {
    createCard(order, payload.original_text, payload.section_title || "");
  }
  const target = state.cards.get(order);
  const badge = target.querySelector(".badge");
  badge.className = "badge error";
  badge.textContent = "失败";

  const rewrittenEl = target.querySelector(".rewritten");
  const diffEl = target.querySelector(".diff");
  rewrittenEl.textContent = payload.error;
  diffEl.textContent = "该分段改写失败，未生成差异。";

  state.done = payload.done_count;
  state.error = payload.error_count;
  setProgress();
}

async function loadFinalResult() {
  if (!state.jobId) {
    return;
  }
  const resp = await fetch(`/api/result/${state.jobId}`);
  if (!resp.ok) {
    sendFrontendLog("error", "load_final_result_failed", { status: resp.status });
    return;
  }
  const data = await resp.json();
  sendFrontendLog("info", "load_final_result_success", {
    finished: data.finished,
    doneCount: data.done_count,
    errorCount: data.error_count,
  });
  el.finalText.value = data.merged_text || "";
  el.downloadBtn.href = `/api/download/${state.jobId}`;
  el.downloadBtn.classList.remove("disabled");
}

function openStream() {
  if (!state.jobId) {
    return;
  }
  if (state.source) {
    state.source.close();
  }
  const source = new EventSource(`/api/stream/${state.jobId}`);
  state.source = source;
  sendFrontendLog("info", "sse_open_called", { jobId: state.jobId });

  source.addEventListener("open", () => {
    sendFrontendLog("info", "sse_opened", { jobId: state.jobId });
  });

  source.addEventListener("job_started", (e) => {
    const data = JSON.parse(e.data);
    sendFrontendLog("info", "event_job_started", data);
    el.statusText.textContent = "改写中";
  });

  source.addEventListener("task_started", (e) => {
    onTaskStarted(JSON.parse(e.data));
  });

  source.addEventListener("task_done", (e) => {
    onTaskDone(JSON.parse(e.data));
  });

  source.addEventListener("task_error", (e) => {
    onTaskError(JSON.parse(e.data));
  });

  source.addEventListener("all_done", async (e) => {
    const data = JSON.parse(e.data);
    sendFrontendLog("info", "event_all_done", data);
    state.done = data.done_count;
    state.error = data.error_count;
    setProgress();
    el.statusText.textContent = "已完成";
    source.close();
    await loadFinalResult();
  });

  source.onerror = () => {
    sendFrontendLog("error", "sse_onerror", { readyState: source.readyState });
    el.statusText.textContent = "连接中断";
  };
}

async function startJob() {
  const maxConcurrency = Number(el.concurrencyInput.value || "10");
  el.startBtn.disabled = true;
  el.downloadBtn.classList.add("disabled");
  el.cards.innerHTML = "";
  el.finalText.value = "";
  if (el.debugLog) {
    el.debugLog.textContent = "";
  }
  state.cards.clear();
  state.done = 0;
  state.error = 0;
  state.total = 0;
  setProgress();
  el.statusText.textContent = "初始化中";

  try {
    sendFrontendLog("info", "start_job_click", { maxConcurrency });
    const resp = await fetch("/api/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ max_concurrency: Math.min(10, Math.max(1, maxConcurrency)) }),
    });
    if (!resp.ok) {
      const msg = await resp.text();
      sendFrontendLog("error", "start_job_failed_response", { status: resp.status, msg });
      throw new Error(msg);
    }

    const data = await resp.json();
    sendFrontendLog("info", "start_job_success", data);
    state.jobId = data.job_id;
    state.total = data.total;
    setProgress();
    openStream();
  } catch (err) {
    sendFrontendLog("error", "start_job_exception", { err: String(err) });
    el.statusText.textContent = `启动失败: ${String(err)}`;
  } finally {
    el.startBtn.disabled = false;
  }
}

el.startBtn.addEventListener("click", startJob);

function setDocxStatus(message, isError = false) {
  if (!el.docxUploadStatus) {
    return;
  }
  el.docxUploadStatus.textContent = message;
  el.docxUploadStatus.classList.toggle("error", Boolean(isError));
}

async function uploadDocx(file) {
  setDocxStatus("上传中…", false);
  const form = new FormData();
  form.append("file", file);
  try {
    const resp = await fetch("/api/upload-docx", {
      method: "POST",
      body: form,
    });
    const raw = await resp.text();
    let data = {};
    try {
      data = raw ? JSON.parse(raw) : {};
    } catch {
      data = {};
    }
    if (!resp.ok) {
      const detail = data.detail;
      const msg =
        typeof detail === "string"
          ? detail
          : Array.isArray(detail) && detail[0]?.msg
            ? detail[0].msg
            : raw || `HTTP ${resp.status}`;
      setDocxStatus(`失败：${msg}`, true);
      sendFrontendLog("error", "upload_docx_failed", { status: resp.status, msg });
      return;
    }
    const chars = data.chars ?? "?";
    setDocxStatus(`已写入论文.txt（约 ${chars} 字）`, false);
    sendFrontendLog("info", "upload_docx_ok", { chars: data.chars, path: data.path });
    appendDebugLog("upload_docx_ok", data);
  } catch (err) {
    setDocxStatus(`失败：${String(err)}`, true);
    sendFrontendLog("error", "upload_docx_exception", { err: String(err) });
  }
}

if (el.docxUploadBtn && el.docxFileInput) {
  el.docxUploadBtn.addEventListener("click", () => {
    el.docxFileInput.click();
  });
  el.docxFileInput.addEventListener("change", (e) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) {
      return;
    }
    const lower = file.name.toLowerCase();
    if (!lower.endsWith(".docx")) {
      setDocxStatus("请选择 .docx 文件", true);
      return;
    }
    uploadDocx(file);
  });
}

window.addEventListener("error", (e) => {
  sendFrontendLog("error", "window_error", {
    message: e.message,
    filename: e.filename,
    lineno: e.lineno,
    colno: e.colno,
  });
});

window.addEventListener("unhandledrejection", (e) => {
  sendFrontendLog("error", "window_unhandledrejection", {
    reason: String(e.reason),
  });
});

async function loadDiagnostics() {
  try {
    const resp = await fetch("/api/diagnostics");
    if (!resp.ok) {
      appendDebugLog("diagnostics_failed", { status: resp.status });
      return;
    }
    const data = await resp.json();
    appendDebugLog("diagnostics_ok", data);
    sendFrontendLog("info", "diagnostics_ok", data);
  } catch (err) {
    appendDebugLog("diagnostics_exception", { err: String(err) });
    sendFrontendLog("error", "diagnostics_exception", { err: String(err) });
  }
}

loadDiagnostics();

