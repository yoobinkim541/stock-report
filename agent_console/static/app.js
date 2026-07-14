const state = {
  surface: localStorage.getItem("agent_console_surface") || "market",
  hours: Number(localStorage.getItem("agent_console_hours") || 72),
  context: null,
};

const $ = (id) => document.getElementById(id);

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function toast(message) {
  const node = $("toast");
  node.textContent = message;
  node.classList.add("show");
  window.clearTimeout(toast.timer);
  toast.timer = window.setTimeout(() => node.classList.remove("show"), 2400);
}

async function requestJson(path, options = {}) {
  const resp = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok || data.ok === false) {
    throw new Error(data.error || `${resp.status} ${resp.statusText}`);
  }
  return data;
}

function setBusy(button, busy, label) {
  if (!button) return;
  if (!button.dataset.label) button.dataset.label = button.textContent;
  button.disabled = busy;
  button.textContent = busy ? label || "처리 중" : button.dataset.label;
}

function activateSurface(surface) {
  state.surface = surface;
  localStorage.setItem("agent_console_surface", surface);
  document.querySelectorAll("#surfaceTabs button").forEach((button) => {
    button.classList.toggle("active", button.dataset.surface === surface);
  });
  $("activeSurface").textContent = surface;
}

async function loadHealth() {
  try {
    const data = await requestJson("/api/health");
    $("healthBadge").textContent = "연결됨";
    $("healthBadge").className = "badge ok";
    $("dbPath").textContent = data.db;
  } catch (error) {
    $("healthBadge").textContent = "연결 실패";
    $("healthBadge").className = "badge warn";
    $("dbPath").textContent = error.message;
  }
}

async function loadContext() {
  const url = `/api/context/overview?surface=${encodeURIComponent(state.surface)}&hours=${state.hours}`;
  const data = await requestJson(url);
  state.context = data;
  renderContext(data);
}

async function loadMemory() {
  const data = await requestJson("/api/memory/events?limit=80");
  renderMemory(data.events || []);
}

async function loadScenarios() {
  const data = await requestJson("/api/portfolio-lab/scenarios");
  renderScenarios(data.scenarios || []);
}

async function loadInstallPrompt() {
  const data = await requestJson("/api/local-install-prompt");
  $("installPrompt").value = data.prompt || "";
}

function renderContext(data) {
  $("contextStamp").textContent = data.generated_at || "";
  $("eventCount").textContent = `${(data.sources?.events || []).length} events`;
  $("focusList").innerHTML = (data.focus || [])
    .map((item) => `<span class="chip">${escapeHtml(item)}</span>`)
    .join("");

  const sourceCounts = data.sources?.source_counts || [];
  const symbolCounts = data.sources?.symbol_counts || [];
  const models = data.models?.items || [];
  const reports = data.reports || [];

  $("sourceSummary").textContent = sourceCounts.length
    ? sourceCounts.slice(0, 4).map(([name, count]) => `${name} ${count}`).join(" · ")
    : "-";
  $("symbolSummary").textContent = symbolCounts.length
    ? symbolCounts.slice(0, 6).map(([name, count]) => `${name} ${count}`).join(" · ")
    : "-";
  $("modelSummary").textContent = models.length ? `${models.length} files` : "-";
  $("reportSummary").textContent = reports[0]?.name || "-";

  $("eventList").innerHTML = renderCompactItems(data.sources?.events || [], {
    empty: "최근 이벤트 없음",
    title: (row) => row.title || row.summary || "(제목 없음)",
    meta: (row) => `${row.source || "source"} · ${row.published_at || row.collected_at || ""}`,
  });
  renderPaper(data.paper || {});
}

function renderPaper(paper) {
  const rows = [];
  for (const key of ["kr", "us"]) {
    const item = paper[key];
    if (!item) continue;
    rows.push({
      title: `${item.surface || key} · ${item.currency || ""}`,
      meta: [
        item.nav == null ? null : `NAV ${formatNumber(item.nav)}`,
        item.cum_ret == null ? null : `누적 ${formatPct(item.cum_ret)}`,
        item.strat_mdd == null ? null : `MDD ${formatPct(item.strat_mdd)}`,
      ].filter(Boolean).join(" · "),
    });
  }
  if (paper.errors?.length) {
    rows.push({ title: "paper context warning", meta: paper.errors.join(" · ") });
  }
  $("paperState").innerHTML = rows.length
    ? rows.map((row) => `<div class="compact-item"><strong>${escapeHtml(row.title)}</strong><span class="meta">${escapeHtml(row.meta)}</span></div>`).join("")
    : `<div class="empty">모의투자 상태 없음</div>`;
}

function renderCompactItems(rows, options) {
  if (!rows.length) return `<div class="empty">${escapeHtml(options.empty)}</div>`;
  return rows.slice(0, 8).map((row) => {
    const title = options.title(row);
    const meta = options.meta(row);
    return `<div class="compact-item"><strong>${escapeHtml(title)}</strong><span class="meta">${escapeHtml(meta)}</span></div>`;
  }).join("");
}

function inlineMarkdown(value) {
  return escapeHtml(value)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
}

function renderLiteMarkdown(value) {
  const lines = String(value ?? "").split("\n");
  const html = [];
  let listMode = null;
  let tableRows = [];

  const closeList = () => {
    if (!listMode) return;
    html.push(`</${listMode}>`);
    listMode = null;
  };
  const flushTable = () => {
    if (!tableRows.length) return;
    const [head, ...body] = tableRows;
    html.push(`<table class="md-table"><thead><tr>${head.map((cell) => `<th>${inlineMarkdown(cell)}</th>`).join("")}</tr></thead><tbody>`);
    body.forEach((row) => {
      html.push(`<tr>${row.map((cell) => `<td>${inlineMarkdown(cell)}</td>`).join("")}</tr>`);
    });
    html.push("</tbody></table>");
    tableRows = [];
  };

  lines.forEach((raw) => {
    const line = raw.trimEnd();
    const trimmed = line.trim();
    if (!trimmed) {
      closeList();
      flushTable();
      return;
    }
    if (/^\|.+\|$/.test(trimmed)) {
      closeList();
      const cells = trimmed.slice(1, -1).split("|").map((cell) => cell.trim());
      if (!cells.every((cell) => /^:?-{3,}:?$/.test(cell))) tableRows.push(cells);
      return;
    }
    flushTable();
    if (trimmed.startsWith("#### ")) {
      closeList();
      html.push(`<h4>${inlineMarkdown(trimmed.slice(5))}</h4>`);
    } else if (trimmed.startsWith("### ")) {
      closeList();
      html.push(`<h3>${inlineMarkdown(trimmed.slice(4))}</h3>`);
    } else if (trimmed.startsWith("> ")) {
      closeList();
      html.push(`<blockquote>${inlineMarkdown(trimmed.slice(2))}</blockquote>`);
    } else if (/^\d+\.\s+/.test(trimmed)) {
      if (listMode !== "ol") {
        closeList();
        listMode = "ol";
        html.push("<ol>");
      }
      html.push(`<li>${inlineMarkdown(trimmed.replace(/^\d+\.\s+/, ""))}</li>`);
    } else if (trimmed.startsWith("- ")) {
      if (listMode !== "ul") {
        closeList();
        listMode = "ul";
        html.push("<ul>");
      }
      html.push(`<li>${inlineMarkdown(trimmed.slice(2))}</li>`);
    } else {
      closeList();
      html.push(`<p>${inlineMarkdown(trimmed)}</p>`);
    }
  });
  closeList();
  flushTable();
  return html.join("");
}

function renderMemory(events) {
  $("memoryCount").textContent = `${events.length} rows`;
  $("memoryList").innerHTML = events.length
    ? events.map((event) => {
        const symbols = (event.symbols || []).join(", ");
        const meta = [event.observed_at, event.source, event.kind, symbols].filter(Boolean).join(" · ");
        return `<article class="timeline-item">
          <strong>${escapeHtml(event.title || "(제목 없음)")}</strong>
          <div class="meta">${escapeHtml(meta)}</div>
          <p>${escapeHtml(event.body || "")}</p>
        </article>`;
      }).join("")
    : `<div class="empty">시장 기억 없음</div>`;
}

function renderScenarios(scenarios) {
  $("scenarioCount").textContent = `${scenarios.length} saved`;
  $("scenarioList").innerHTML = scenarios.length
    ? scenarios.map((scenario) => {
        const allocs = (scenario.allocations || []).map((item) => {
          const symbol = item.symbol || item.ticker || "ASSET";
          const weight = item.weight_pct ?? item.weight ?? 0;
          return `<span class="alloc">${escapeHtml(symbol)} ${escapeHtml(weight)}%</span>`;
        }).join("");
        const maxLoss = scenario.rules?.max_loss_pct;
        return `<article class="scenario-item">
          <strong>${escapeHtml(scenario.name)}</strong>
          <div class="meta">${escapeHtml(scenario.updated_at || "")}${maxLoss == null ? "" : ` · max loss ${escapeHtml(maxLoss)}%`}</div>
          <p>${escapeHtml(scenario.description || "")}</p>
          <div class="allocs">${allocs}</div>
        </article>`;
      }).join("")
    : `<div class="empty">저장된 시나리오 없음</div>`;
}

function addMessage(role, body) {
  const conversation = $("conversation");
  const node = document.createElement("article");
  node.className = `message ${role}`;
  if (role === "assistant") {
    node.innerHTML = `<span class="role">Agent</span><div class="body rich">${renderLiteMarkdown(body)}</div>`;
  } else {
    node.innerHTML = `<span class="role">나</span><pre class="body"></pre>`;
    node.querySelector("pre").textContent = body;
  }
  conversation.appendChild(node);
  conversation.scrollTop = conversation.scrollHeight;
}

async function sendChat(event) {
  event.preventDefault();
  const input = $("chatInput");
  const message = input.value.trim();
  if (!message) return;
  const button = event.submitter;
  addMessage("user", message);
  input.value = "";
  setBusy(button, true, "답변 중");
  try {
    const data = await requestJson("/api/agent/chat", {
      method: "POST",
      body: JSON.stringify({ surface: state.surface, message }),
    });
    addMessage("assistant", data.answer || "");
    toast("답변 생성 완료");
  } catch (error) {
    addMessage("assistant", `오류: ${error.message}`);
  } finally {
    setBusy(button, false);
  }
}

async function ingestMemory() {
  const button = $("ingestBtn");
  setBusy(button, true, "적재 중");
  try {
    const data = await requestJson("/api/memory/ingest", {
      method: "POST",
      body: JSON.stringify({ hours: state.hours }),
    });
    await loadMemory();
    toast(`메모리 ${data.changed}건 반영`);
  } catch (error) {
    toast(`메모리 적재 실패: ${error.message}`);
  } finally {
    setBusy(button, false);
  }
}

async function saveMemory(event) {
  event.preventDefault();
  const title = $("memoryTitleInput").value.trim();
  const body = $("memoryBodyInput").value.trim();
  const symbols = $("memorySymbolsInput").value.split(/[,\s]+/).map((x) => x.trim().toUpperCase()).filter(Boolean);
  if (!title && !body) {
    toast("제목 또는 관찰 내용을 입력해 주세요");
    return;
  }
  const payload = {
    event: {
      observed_at: new Date().toISOString(),
      source: "manual",
      kind: "market_note",
      title: title || body.slice(0, 80),
      body,
      symbols,
      impact: "context",
      confidence: 0.65,
      metadata: { surface: state.surface },
    },
  };
  const button = event.submitter;
  setBusy(button, true, "추가 중");
  try {
    await requestJson("/api/memory/events", { method: "POST", body: JSON.stringify(payload) });
    $("memoryTitleInput").value = "";
    $("memoryBodyInput").value = "";
    $("memorySymbolsInput").value = "";
    await loadMemory();
    toast("시장 기억 추가 완료");
  } catch (error) {
    toast(`추가 실패: ${error.message}`);
  } finally {
    setBusy(button, false);
  }
}

async function saveScenario(event) {
  event.preventDefault();
  const name = $("scenarioName").value.trim();
  const allocations = parseAllocations($("scenarioAllocations").value);
  const maxLossPct = Number($("scenarioMaxLoss").value || 0);
  const total = allocations.reduce((sum, item) => sum + Number(item.weight_pct || 0), 0);
  const rulesText = $("scenarioRules").value.trim();
  const payload = {
    name,
    description: $("scenarioDesc").value.trim(),
    allocations,
    rules: {
      max_loss_pct: maxLossPct,
      text: rulesText,
      live_orders: false,
      actual_asset_link: false,
    },
    assumptions: {
      surface: state.surface,
      total_weight_pct: Number(total.toFixed(2)),
    },
    metrics: {
      saved_from: "agent_console",
      allocation_count: allocations.length,
    },
  };
  const button = event.submitter;
  setBusy(button, true, "저장 중");
  try {
    await requestJson("/api/portfolio-lab/scenarios", { method: "POST", body: JSON.stringify(payload) });
    await loadScenarios();
    toast(`시나리오 저장 완료 · 합계 ${total.toFixed(1)}%`);
  } catch (error) {
    toast(`저장 실패: ${error.message}`);
  } finally {
    setBusy(button, false);
  }
}

function parseAllocations(text) {
  return text.split("\n").map((line) => {
    const parts = line.trim().split(/\s+/).filter(Boolean);
    if (parts.length < 2) return null;
    const symbol = parts[0].toUpperCase();
    const weight = Number(parts[1]);
    if (!symbol || !Number.isFinite(weight)) return null;
    return { symbol, weight_pct: weight, note: parts.slice(2).join(" ") };
  }).filter(Boolean);
}

function formatNumber(value) {
  return Number(value).toLocaleString("en-US", { maximumFractionDigits: 0 });
}

function formatPct(value) {
  return `${Number(value).toFixed(2)}%`;
}

async function refreshAll() {
  const button = $("refreshBtn");
  setBusy(button, true, "갱신 중");
  try {
    await Promise.all([loadContext(), loadMemory(), loadScenarios(), loadInstallPrompt()]);
    toast("갱신 완료");
  } catch (error) {
    toast(`갱신 실패: ${error.message}`);
  } finally {
    setBusy(button, false);
  }
}

function wireEvents() {
  document.querySelectorAll("#surfaceTabs button").forEach((button) => {
    button.addEventListener("click", async () => {
      activateSurface(button.dataset.surface);
      await loadContext().catch((error) => toast(`컨텍스트 실패: ${error.message}`));
    });
  });
  $("hoursSelect").value = String(state.hours);
  $("hoursSelect").addEventListener("change", async (event) => {
    state.hours = Number(event.target.value || 72);
    localStorage.setItem("agent_console_hours", String(state.hours));
    await loadContext().catch((error) => toast(`컨텍스트 실패: ${error.message}`));
  });
  $("refreshBtn").addEventListener("click", refreshAll);
  $("ingestBtn").addEventListener("click", ingestMemory);
  $("installBtn").addEventListener("click", () => $("installPrompt").focus());
  $("copyPromptBtn").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText($("installPrompt").value);
      toast("프롬프트 복사 완료");
    } catch {
      $("installPrompt").select();
      toast("프롬프트를 선택했습니다");
    }
  });
  $("chatForm").addEventListener("submit", sendChat);
  $("memoryForm").addEventListener("submit", saveMemory);
  $("scenarioForm").addEventListener("submit", saveScenario);
}

async function boot() {
  activateSurface(state.surface);
  wireEvents();
  addMessage("assistant", "현재 프로젝트의 리포트, 모의투자, ML 활동, 시장 기억을 묶어서 답변합니다.");
  await loadHealth();
  await refreshAll();
}

boot().catch((error) => toast(error.message));
