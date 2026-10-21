"use strict";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];

const STATUS_PT = {
  new: "nova", notified: "notificada", interested: "interessado",
  applied: "aplicada", discarded: "descartada", seen: "vista",
};
const BREAKDOWN_PT = {
  role_match: "Cargo", seniority: "Senioridade", keywords: "Keywords",
  remote: "Remoto", recency: "Recência", hard_exclude: "Excluída",
};
const ICONS = {
  interested: '<svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true"><path fill="currentColor" d="M8 1.5l1.9 4 4.4.5-3.3 3 .9 4.3L8 11.1l-3.9 2.2.9-4.3-3.3-3 4.4-.5z"/></svg>',
  applied: '<svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true"><path fill="currentColor" d="M1.5 7.5L14.5 1.5 10 14.5 7.3 9.4 1.5 7.5zM7.8 8.9l2 3.8 3-8.9-9 4.2 4 1z"/></svg>',
  seen: '<svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="1.4" d="M1.5 8s2.5-4.5 6.5-4.5S14.5 8 14.5 8 12 12.5 8 12.5 1.5 8 1.5 8Z"/><circle cx="8" cy="8" r="2" fill="currentColor"/></svg>',
  discarded: '<svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="1.6" d="M3.5 3.5l9 9m0-9l-9 9"/></svg>',
};

const state = {
  filterStatus: "", minScore: "",
  jobs: [], knownUids: new Set(), firstLoad: true,
  daemonRunning: false, searchPolling: null, expandedUid: null,
  lastStatusJson: "",
};

/* ---------- infra ---------- */

async function api(path, options = {}) {
  let resp;
  try {
    resp = await fetch(path, {
      headers: { "Content-Type": "application/json", "X-Requested-With": "vaga-hunter" },
      ...options,
    });
  } catch {
    toast("Servidor fora do ar?", "danger");
    throw new Error("network");
  }
  const body = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    toast(body.error || `Erro ${resp.status}`, "danger");
    throw new Error(body.error || resp.status);
  }
  return body;
}

function esc(text) {
  return String(text ?? "")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;").replaceAll("'", "&#39;");
}

function safeUrl(url) {
  try {
    const parsed = new URL(url, window.location.href);
    if (parsed.protocol === "http:" || parsed.protocol === "https:") return url;
  } catch {}
  return "#";
}

function toast(message, kind = "", actionLabel = "", onAction = null) {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.innerHTML = `<span>${esc(message)}</span>`;
  if (actionLabel && onAction) {
    const btn = document.createElement("button");
    btn.textContent = actionLabel;
    btn.addEventListener("click", () => { onAction(); el.remove(); });
    el.appendChild(btn);
  }
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), 5000);
}

function relDate(iso) {
  if (!iso) return "";
  const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86400000);
  if (days <= 0) return "hoje";
  if (days === 1) return "ontem";
  if (days < 30) return `há ${days} dias`;
  return `há ${Math.floor(days / 30)} mês(es)`;
}

function scoreBand(score) {
  if (score >= 80) return "hot";
  if (score >= 60) return "good";
  if (score >= 40) return "mid";
  return "low";
}

/* ---------- jobs list ---------- */

function jobCard(job) {
  const status = job.status;
  const isNew = status === "new";
  const badges = [];
  const SOURCE_PT = { linkedin_jobs: "LinkedIn", linkedin_posts: "LinkedIn · post" };
  badges.push(`<span class="badge outline source">${esc(SOURCE_PT[job.source] || job.source)}</span>`);
  const MODALITY_PT = { remote: "🏠 remoto", hybrid: "🔄 híbrido", onsite: "🏢 presencial" };
  if (job.modality) badges.push(`<span class="badge outline modality-${esc(job.modality)}">${MODALITY_PT[job.modality]}</span>`);
  if (!isNew) badges.push(`<span class="badge ${esc(status)}">${esc(STATUS_PT[status] || status)}</span>`);

  const actions = ["interested", "applied", "seen", "discarded"].map((s) => `
    <button class="action ${status === s ? "current" : ""}" data-status="${s}"
      data-tip="${{ interested: "Interessado", applied: "Apliquei", seen: "Vista", discarded: "Descartar" }[s]}"
      aria-label="${{ interested: "Marcar interessado", applied: "Marcar aplicada", seen: "Marcar vista", discarded: "Descartar" }[s]}">
      ${ICONS[s]}</button>`).join("");

  return `
  <li class="job ${isNew ? "is-new" : ""} ${status === "discarded" ? "discarded" : ""}"
      data-uid="${esc(job.uid)}">
    <div class="job-row1">
      <span class="score-chip score-${scoreBand(job.score)}"
            aria-label="score ${Math.round(job.score)} de 100">${Math.round(job.score)}</span>
      <a class="job-title" href="${esc(safeUrl(job.url))}" target="_blank" rel="noopener">${esc(job.title)}</a>
      <div class="job-actions">${actions}</div>
    </div>
    <div class="job-row2">
      <span>${esc(job.company || "?")}</span>
      ${job.location ? `<span class="sep">·</span><span>${esc(job.location)}</span>` : ""}
      ${job.posted_at ? `<span class="sep">·</span><span title="${esc(job.posted_at)}">${relDate(job.posted_at)}</span>` : ""}
      ${badges.join(" ")}
      <button class="job-expand" aria-expanded="false">▸ detalhes</button>
    </div>
  </li>`;
}

function detailsPanel(job) {
  const details = job.score_details || {};
  const rows = Object.entries(details).map(([key, value]) => {
    const width = Math.min(100, Math.abs(value) * 3.3);
    return `<div class="bk-row"><span>${esc(BREAKDOWN_PT[key] || key)}</span>
      <span class="bk-bar ${value < 0 ? "neg" : ""}"><i style="width:${width}%"></i></span>
      <span class="bk-val">${value > 0 ? "+" : ""}${Math.round(value)}</span></div>`;
  }).join("") || '<p class="muted">sem detalhes</p>';

  const desc = (job.description || "").trim();
  return `<div class="job-details">
    <div class="job-desc">${desc ? esc(desc.slice(0, 600)) + (desc.length >= 600 ? "…" : "") : '<span class="muted">sem descrição salva</span>'}
      <p><a href="${esc(safeUrl(job.url))}" target="_blank" rel="noopener">abrir vaga ↗</a></p></div>
    <div class="breakdown"><h4>Score</h4>${rows}</div>
  </div>`;
}

async function loadJobs() {
  const params = new URLSearchParams();
  if (state.filterStatus) params.set("status", state.filterStatus);
  if (state.minScore) params.set("min_score", state.minScore);

  if (state.firstLoad) {
    $("#jobs").innerHTML = '<li class="skeleton"></li>'.repeat(6);
  }
  const jobs = await api(`/api/jobs?${params}`);
  const previousKnown = state.knownUids;
  state.jobs = jobs;
  state.expandedUid = null;

  $("#jobs").innerHTML = jobs.map(jobCard).join("");
  if (!state.firstLoad) {
    for (const el of $$(".job")) {
      if (!previousKnown.has(el.dataset.uid)) el.classList.add("flash");
    }
  }
  for (const job of jobs) state.knownUids.add(job.uid);
  state.firstLoad = false;

  $("#jobs-count").textContent = `${jobs.length} vaga${jobs.length === 1 ? "" : "s"}`;
  const empty = $("#empty-state");
  if (jobs.length === 0) {
    empty.classList.remove("hidden");
    const filtered = state.filterStatus || state.minScore;
    $("#empty-text").textContent = filtered ? "Nada com esses filtros." : "Nenhuma vaga ainda.";
    $("#empty-action").textContent = filtered ? "Limpar filtros" : "Buscar agora";
  } else {
    empty.classList.add("hidden");
  }
}

/* ---------- status updates (optimistic) ---------- */

async function setStatus(card, newStatus) {
  const uid = card.dataset.uid;
  const job = state.jobs.find((j) => j.uid === uid);
  if (!job || job.status === newStatus) return;
  const previous = job.status;
  job.status = newStatus;

  // Atualização otimista do card
  card.outerHTML = jobCard(job);

  const leavesFilter = state.filterStatus && state.filterStatus !== newStatus;
  if (leavesFilter) {
    const fresh = document.querySelector(`.job[data-uid="${CSS.escape(uid)}"]`);
    fresh?.classList.add("removing");
    setTimeout(() => fresh?.remove(), 200);
  }
  if (newStatus === "discarded" || leavesFilter) {
    toast(`${STATUS_PT[newStatus]} · ${job.title.slice(0, 40)}`, "", "desfazer", () => {
      job.status = previous;
      api(`/api/jobs/${encodeURIComponent(uid)}/status`, {
        method: "POST", body: JSON.stringify({ status: previous }),
      }).then(() => { loadJobs(); refreshStatus(true); });
    });
  }

  try {
    await api(`/api/jobs/${encodeURIComponent(uid)}/status`, {
      method: "POST", body: JSON.stringify({ status: newStatus }),
    });
    refreshStatus(true);
  } catch {
    job.status = previous;
    loadJobs();
  }
}

/* ---------- status/daemon polling ---------- */

function renderStatus(data) {
  const json = JSON.stringify(data);
  if (json === state.lastStatusJson) return;
  state.lastStatusJson = json;

  const { search, daemon, counts, posts } = data;

  // pill da fonte de posts (só aparece se ligada no config)
  const postsPill = $("#posts-pill");
  if (posts?.enabled) {
    postsPill.classList.remove("hidden");
    state.postsState = posts.state;
    postsPill.classList.toggle("on", posts.state === "ready");
    $("#posts-label").textContent = {
      ready: "Posts prontos",
      chrome_closed: "Posts: abrir Chrome",
    }[posts.state] || "Posts";
  } else {
    postsPill.classList.add("hidden");
  }
  state.daemonRunning = daemon.running;

  const pill = $("#daemon-pill");
  pill.classList.toggle("on", daemon.running);
  pill.classList.toggle("warn", daemon.running && !daemon.in_window);
  pill.setAttribute("aria-checked", String(daemon.running));
  let label = daemon.running ? "Daemon ligado" : "Daemon desligado";
  if (daemon.running && daemon.next_run) label += ` · próxima ${daemon.next_run.slice(11, 16)}`;
  if (daemon.running && !daemon.in_window) label += " · fora da janela";
  $("#daemon-label").textContent = label;

  // chips com contagem
  const total = Object.values(counts || {}).reduce((a, b) => a + b, 0);
  for (const chip of $$(".chip")) {
    const key = chip.dataset.status;
    const value = key === "" ? total : (counts?.[key] || 0);
    chip.querySelector(".count").textContent = value ? `${value}` : "";
  }

  // busca manual
  const btn = $("#btn-search");
  btn.disabled = search.running;
  btn.classList.toggle("loading", search.running);
  btn.querySelector(".btn-label").textContent = search.running ? "Buscando…" : "Buscar agora";

  const logCard = $("#log-card");
  if (search.messages.length) {
    logCard.classList.remove("hidden");
    $("#log-title").innerHTML = search.running
      ? '<span class="pulse-dot" aria-hidden="true"></span> Busca em andamento…'
      : "Busca concluída";
    const log = $("#log");
    log.textContent = search.messages.join("\n");
    log.scrollTop = log.scrollHeight;
  }
  if (!search.running && state.searchPolling) {
    clearInterval(state.searchPolling);
    state.searchPolling = null;
    if (search.report) {
      toast(`${search.report.found} vagas, ${search.report.new} novas, ${search.report.notified} notificadas`,
            search.report.errors?.length ? "danger" : "ok");
    }
    loadJobs();
    setTimeout(() => logCard.classList.add("hidden"), 10000);
  }
}

async function refreshStatus(force = false) {
  if (force) state.lastStatusJson = "";
  renderStatus(await api("/api/status"));
}

/* ---------- config dialog ---------- */

async function loadConfig() {
  const cfg = await api("/api/config");
  $("#cfg-queries").value = cfg.queries.join("\n");
  $("#cfg-location").value = cfg.location;
  const timeSel = $("#cfg-time-range");
  if (![...timeSel.options].some((o) => o.value === cfg.time_range)) {
    timeSel.add(new Option(cfg.time_range, cfg.time_range));  // valor custom do config.toml
  }
  timeSel.value = cfg.time_range;
  $("#cfg-remote").value = cfg.remote;
  $("#cfg-max-pages").value = cfg.max_pages_per_query;
  $("#cfg-threshold").value = cfg.threshold;
}

/* ---------- wiring ---------- */

$("#btn-search").addEventListener("click", async () => {
  try {
    await api("/api/search", { method: "POST" });
  } catch { return; }
  if (!state.searchPolling) state.searchPolling = setInterval(() => refreshStatus(), 1000);
  refreshStatus(true);
});

$("#posts-pill").addEventListener("click", async () => {
  // Automatiza o fluxo de posts: garante o Chrome do bot (perfil próprio,
  // porta CDP) e dispara a busca
  $("#posts-label").textContent = "Preparando Chrome…";
  let prep;
  try {
    prep = await api("/api/posts/prepare", { method: "POST" });
  } catch { refreshStatus(true); return; }
  refreshStatus(true);
  if (prep.first_use) {
    toast("Primeiro uso: faça login no LinkedIn na janela que abriu e clique no pill de novo", "ok");
    return;
  }
  if (prep.state === "ready") $("#btn-search").click();
});

$("#daemon-pill").addEventListener("click", async () => {
  $("#daemon-label").textContent = "…";
  await api(`/api/daemon/${state.daemonRunning ? "stop" : "start"}`, { method: "POST" });
  refreshStatus(true);
});

$("#jobs").addEventListener("click", (event) => {
  const actionBtn = event.target.closest("button.action");
  const card = event.target.closest(".job");
  if (!card) return;
  if (actionBtn) { setStatus(card, actionBtn.dataset.status); return; }

  const expandBtn = event.target.closest(".job-expand");
  if (expandBtn || (!event.target.closest("a") && !event.target.closest("button"))) {
    const uid = card.dataset.uid;
    const open = state.expandedUid === uid;
    document.querySelector(".job-details")?.remove();
    $$(".job-expand").forEach((b) => { b.setAttribute("aria-expanded", "false"); b.textContent = "▸ detalhes"; });
    state.expandedUid = open ? null : uid;
    if (!open) {
      const job = state.jobs.find((j) => j.uid === uid);
      card.insertAdjacentHTML("beforeend", detailsPanel(job));
      const btn = card.querySelector(".job-expand");
      btn.setAttribute("aria-expanded", "true");
      btn.textContent = "▾ fechar";
    }
  }
});

for (const chip of $$(".chip")) {
  chip.addEventListener("click", () => {
    $$(".chip").forEach((c) => { c.classList.remove("active"); c.setAttribute("aria-pressed", "false"); });
    chip.classList.add("active");
    chip.setAttribute("aria-pressed", "true");
    state.filterStatus = chip.dataset.status;
    loadJobs();
  });
}

$("#filter-score").addEventListener("change", () => {
  state.minScore = $("#filter-score").value;
  loadJobs();
});

$("#empty-action").addEventListener("click", () => {
  if (state.filterStatus || state.minScore) {
    state.filterStatus = ""; state.minScore = "";
    $("#filter-score").value = "";
    $$(".chip").forEach((c) => c.classList.toggle("active", c.dataset.status === ""));
    loadJobs();
  } else {
    $("#btn-search").click();
  }
});

$("#log-close").addEventListener("click", () => $("#log-card").classList.add("hidden"));

$("#btn-settings").addEventListener("click", () => $("#params-dialog").showModal());
$("#btn-close-config").addEventListener("click", () => $("#params-dialog").close());
$("#btn-save-config").addEventListener("click", async () => {
  const result = await api("/api/config", {
    method: "POST",
    body: JSON.stringify({
      queries: $("#cfg-queries").value.split("\n"),
      location: $("#cfg-location").value,
      time_range: $("#cfg-time-range").value,
      remote: $("#cfg-remote").value,
      max_pages_per_query: $("#cfg-max-pages").value,
      threshold: $("#cfg-threshold").value,
    }),
  });
  if (result.ok) {
    $("#params-dialog").close();
    toast(result.saved_to_file ? "Parâmetros salvos no config.toml" : "Parâmetros aplicados", "ok");
  }
});

refreshStatus();
loadJobs();
loadConfig();
setInterval(() => refreshStatus(), 5000);
