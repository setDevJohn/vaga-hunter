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
  filterStatus: "", minScore: "", textFilter: "",
  sortKey: null, sortDir: "desc",  // null = ordem do servidor (score desc)
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

function normalize(text) {
  return String(text || "").toLowerCase()
    .normalize("NFD").replace(/[̀-ͯ]/g, "");
}

function visibleJobs() {
  let jobs = state.jobs;
  if (state.textFilter) {
    const needle = normalize(state.textFilter);
    jobs = jobs.filter((j) =>
      normalize(j.title).includes(needle) || normalize(j.company).includes(needle));
  }
  if (state.sortKey) {
    const dir = state.sortDir === "asc" ? 1 : -1;
    jobs = [...jobs].sort((a, b) => {
      if (state.sortKey === "score") return (a.score - b.score) * dir;
      const da = a.posted_at || a.first_seen_at || "";
      const db = b.posted_at || b.first_seen_at || "";
      return da.localeCompare(db) * dir;
    });
  }
  return jobs;
}

function renderSortButtons() {
  for (const btn of $$(".sort-btn")) {
    const active = state.sortKey === btn.dataset.sort;
    btn.classList.toggle("active", active);
    const label = btn.dataset.sort === "score" ? "Score" : "Recente";
    btn.textContent = active ? `${label} ${state.sortDir === "desc" ? "↓" : "↑"}` : label;
  }
  $("#sort-clear").classList.toggle("hidden", !state.sortKey);
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
  const SOURCE_PT = { linkedin_jobs: "LinkedIn", linkedin_posts: "LinkedIn · post",
                      infojobs: "InfoJobs", catho: "Catho",
                      programathor: "Programathor", trampos: "Trampos.co" };
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
      <a class="job-title" href="${esc(safeUrl(job.url))}" target="_blank"
         rel="noopener noreferrer" referrerpolicy="no-referrer">${esc(job.title)}</a>
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
      <p><a href="${esc(safeUrl(job.url))}" target="_blank" rel="noopener noreferrer"
            referrerpolicy="no-referrer">abrir vaga ↗</a></p></div>
    <div class="breakdown"><h4>Score</h4>${rows}</div>
  </div>`;
}

async function loadJobs() {
  const params = new URLSearchParams();
  params.set("limit", "1000");  // default do servidor é 200 — banco cresce
  if (state.filterStatus) params.set("status", state.filterStatus);
  if (state.minScore) params.set("min_score", state.minScore);

  if (state.firstLoad) {
    $("#jobs").innerHTML = '<li class="skeleton"></li>'.repeat(6);
  }
  const jobs = await api(`/api/jobs?${params}`);
  const previousKnown = state.knownUids;
  state.jobs = jobs;
  renderJobs(previousKnown);
  for (const job of jobs) state.knownUids.add(job.uid);
  state.firstLoad = false;
}

function renderJobs(previousKnown = null) {
  state.expandedUid = null;
  const jobs = visibleJobs();

  $("#jobs").innerHTML = jobs.map(jobCard).join("");
  if (previousKnown && !state.firstLoad) {
    for (const el of $$(".job")) {
      if (!previousKnown.has(el.dataset.uid)) el.classList.add("flash");
    }
  }

  $("#jobs-count").textContent = `${jobs.length} vaga${jobs.length === 1 ? "" : "s"}`;
  const empty = $("#empty-state");
  if (jobs.length === 0) {
    empty.classList.remove("hidden");
    const filtered = state.filterStatus || state.minScore || state.textFilter;
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

  // botão de busca em posts (só aparece com a fonte ligada no config)
  const postsBtn = $("#btn-posts");
  const postsSearching = search.running && search.what === "posts";
  if (posts?.enabled) {
    postsBtn.classList.remove("hidden");
    state.postsState = posts.state;
    postsBtn.disabled = search.running || daemon.busy;
    postsBtn.classList.toggle("loading", postsSearching);
    // status do Chrome direto no botão: bolinha + rótulo
    postsBtn.querySelector(".dot").dataset.state = posts.state;
    postsBtn.querySelector(".btn-label").textContent = postsSearching
      ? "Buscando posts…"
      : (posts.state === "ready"
          ? "Buscar posts · Chrome pronto"
          : "Buscar posts · Chrome fechado");
  } else {
    postsBtn.classList.add("hidden");
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
  for (const chip of $$(".chips .chip")) {  // só os chips de status (sort usa a mesma classe)
    const key = chip.dataset.status;
    const value = key === "" ? total : (counts?.[key] || 0);
    const count = chip.querySelector(".count");
    if (count) count.textContent = value ? `${value}` : "";
  }

  // busca manual
  const btn = $("#btn-search");
  const jobsSearching = search.running && search.what === "jobs";
  btn.disabled = search.running;
  btn.classList.toggle("loading", jobsSearching);
  btn.querySelector(".btn-label").textContent = jobsSearching ? "Buscando…" : "Buscar vagas";
  $("#btn-cancel").classList.toggle("hidden", !search.running);

  const logCard = $("#log-card");
  const active = search.running || daemon.busy;
  if (search.messages.length) {
    logCard.classList.remove("hidden");
    const titles = {
      jobs: ["Busca de vagas em andamento…", "Busca de vagas concluída"],
      posts: ["Busca de posts em andamento…", "Busca de posts concluída"],
      daemon: ["Daemon buscando…", "Daemon: última busca"],
    };
    const [busyTitle, doneTitle] = titles[search.what] || titles.jobs;
    $("#log-title").innerHTML = active
      ? `<span class="pulse-dot" aria-hidden="true"></span> ${busyTitle}`
      : doneTitle;
    const log = $("#log");
    log.textContent = search.messages.join("\n");
    log.scrollTop = log.scrollHeight;
  }
  if (daemon.busy && !state.searchPolling) {
    state.searchPolling = setInterval(() => refreshStatus(), 1000);  // acompanha o daemon
  }
  if (!active && state.searchPolling) {
    clearInterval(state.searchPolling);
    state.searchPolling = null;
    if (search.report && search.what !== "daemon") {
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

const TIME_OPTIONS = [
  ["r30m", "últimos 30 min"], ["r1h", "última 1 hora"], ["r2h", "últimas 2 horas"],
  ["r6h", "últimas 6 horas"], ["r12h", "últimas 12 horas"], ["r24h", "últimas 24h"],
  ["r3d", "últimos 3 dias"], ["r7d", "últimos 7 dias"], ["r1m", "último mês"],
];
const SOURCE_PAGES = {
  linkedin_jobs: "LinkedIn Jobs",
  linkedin_posts: "LinkedIn Posts",
  infojobs: "InfoJobs",
  catho: "Catho",
  programathor: "Programathor",
  trampos: "Trampos.co",
};

function fillTimeSelect(select, value, inheritLabel = null) {
  select.innerHTML = "";
  if (inheritLabel !== null) select.add(new Option(inheritLabel, ""));
  for (const [v, label] of TIME_OPTIONS) select.add(new Option(label, v));
  if (value && ![...select.options].some((o) => o.value === value)) {
    select.add(new Option(value, value));  // valor custom do config.toml
  }
  select.value = value || (inheritLabel !== null ? "" : "r24h");
}

function buildSourcePage(name, src) {
  const section = document.createElement("section");
  section.className = "settings-page";
  section.dataset.page = name;
  section.innerHTML = `
    <label class="source-toggle">
      <input type="checkbox" data-field="enabled" ${src.enabled ? "checked" : ""}>
      Fonte ativa
    </label>
    <div class="params-grid">
      <label class="full">Buscas desta fonte (vazio = usa as buscas padrão)
        <textarea data-field="queries" rows="4" spellcheck="false"></textarea>
      </label>
      <label>Localização <input type="text" data-field="location"
        placeholder="padrão" value="${esc(src.location)}"></label>
      <label>Período <select data-field="time_range" class="time-range-select"></select></label>
      <label>Modalidade
        <select data-field="remote">
          <option value="">padrão</option>
          <option value="any">qualquer</option>
          <option value="remote">remoto</option>
          <option value="hybrid">híbrido</option>
          <option value="onsite">presencial</option>
        </select>
      </label>
      <label>Páginas por busca <input type="number" data-field="max_pages"
        min="0" max="10" placeholder="padrão" value="${src.max_pages || ""}"></label>
    </div>
    ${name === "linkedin_posts"
      ? '<p class="muted">⚠️ Busca em posts usa o Chrome do bot com a sua sessão do LinkedIn. Roda só pelo botão "Buscar posts", nunca pelo daemon.</p>'
      : ""}`;
  section.querySelector('[data-field="queries"]').value = (src.queries || []).join("\n");
  fillTimeSelect(section.querySelector('[data-field="time_range"]'), src.time_range, "padrão");
  section.querySelector('[data-field="remote"]').value = src.remote || "";
  return section;
}

function readSourcePage(name) {
  const page = document.querySelector(`.settings-page[data-page="${name}"]`);
  if (!page) return null;
  const get = (field) => page.querySelector(`[data-field="${field}"]`);
  return {
    enabled: get("enabled").checked,
    queries: get("queries").value.split("\n"),
    location: get("location").value,
    time_range: get("time_range").value,
    remote: get("remote").value,
    max_pages: parseInt(get("max_pages").value, 10) || 0,
  };
}

function updateNavBadges() {
  for (const item of $$(".nav-item")) {
    const name = item.dataset.page;
    if (name === "geral") continue;
    const src = readSourcePage(name);
    item.innerHTML = `${SOURCE_PAGES[name]}${src && src.enabled ? "" : ' <span class="off">off</span>'}`;
  }
}

async function loadConfig() {
  const cfg = await api("/api/config");
  $("#cfg-queries").value = cfg.queries.join("\n");
  $("#cfg-location").value = cfg.location;
  fillTimeSelect($("#cfg-time-range"), cfg.time_range);
  $("#cfg-remote").value = cfg.remote;
  $("#cfg-max-pages").value = cfg.max_pages_per_query;
  $("#cfg-threshold").value = cfg.threshold;

  const body = $(".settings-body");
  for (const old of body.querySelectorAll('.settings-page:not([data-page="geral"])')) old.remove();
  for (const name of Object.keys(SOURCE_PAGES)) {
    body.appendChild(buildSourcePage(name, cfg.sources[name] || {}));
  }
  body.addEventListener("change", updateNavBadges);
  updateNavBadges();
}

/* ---------- wiring ---------- */

async function startSearch(what) {
  try {
    await api("/api/search", { method: "POST", body: JSON.stringify({ what }) });
  } catch { return; }
  if (!state.searchPolling) state.searchPolling = setInterval(() => refreshStatus(), 1000);
  refreshStatus(true);
}

$("#btn-search").addEventListener("click", () => startSearch("jobs"));

$("#btn-cancel").addEventListener("click", async () => {
  $("#btn-cancel").disabled = true;
  try { await api("/api/search/cancel", { method: "POST" }); } catch {}
  $("#btn-cancel").disabled = false;
  refreshStatus(true);
});

$("#btn-posts").addEventListener("click", async () => {
  // Garante o Chrome do bot (perfil próprio, porta CDP) e busca SÓ posts
  const btn = $("#btn-posts");
  if (state.postsState !== "ready") {
    btn.querySelector(".btn-label").textContent = "Abrindo Chrome…";
    btn.disabled = true;
  }
  let prep;
  try {
    prep = await api("/api/posts/prepare", { method: "POST" });
  } catch { refreshStatus(true); return; }
  refreshStatus(true);
  if (prep.first_use) {
    toast("Primeiro uso: faça login no LinkedIn na janela que abriu e clique de novo", "ok");
    return;
  }
  if (prep.state === "ready") startSearch("posts");
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

for (const chip of $$(".chips .chip")) {
  chip.addEventListener("click", () => {
    $$(".chips .chip").forEach((c) => { c.classList.remove("active"); c.setAttribute("aria-pressed", "false"); });
    chip.classList.add("active");
    chip.setAttribute("aria-pressed", "true");
    state.filterStatus = chip.dataset.status;
    loadJobs();
  });
}

for (const btn of $$(".sort-btn")) {
  btn.addEventListener("click", () => {
    const key = btn.dataset.sort;
    if (state.sortKey === key) {
      state.sortDir = state.sortDir === "desc" ? "asc" : "desc";  // 2º clique alterna
    } else {
      state.sortKey = key;
      state.sortDir = "desc";
    }
    renderSortButtons();
    renderJobs();
  });
}

$("#sort-clear").addEventListener("click", () => {
  state.sortKey = null;
  state.sortDir = "desc";
  renderSortButtons();
  renderJobs();
});

let textDebounce = null;
$("#filter-text").addEventListener("input", () => {
  clearTimeout(textDebounce);
  textDebounce = setTimeout(() => {
    state.textFilter = $("#filter-text").value.trim();
    renderJobs();  // filtro local, sem refetch
  }, 200);
});

$("#filter-score").addEventListener("change", () => {
  state.minScore = $("#filter-score").value;
  loadJobs();
});

$("#empty-action").addEventListener("click", () => {
  if (state.filterStatus || state.minScore || state.textFilter) {
    state.filterStatus = ""; state.minScore = ""; state.textFilter = "";
    $("#filter-score").value = "";
    $("#filter-text").value = "";
    $$(".chips .chip").forEach((c) => c.classList.toggle("active", c.dataset.status === ""));
    loadJobs();
  } else {
    $("#btn-search").click();
  }
});

$("#log-close").addEventListener("click", () => $("#log-card").classList.add("hidden"));

$("#btn-settings").addEventListener("click", () => $("#params-dialog").showModal());
$("#btn-close-config").addEventListener("click", () => $("#params-dialog").close());
for (const item of $$(".nav-item")) {
  item.addEventListener("click", () => {
    $$(".nav-item").forEach((n) => { n.classList.remove("active"); n.setAttribute("aria-selected", "false"); });
    item.classList.add("active");
    item.setAttribute("aria-selected", "true");
    $$(".settings-page").forEach((p) =>
      p.classList.toggle("active", p.dataset.page === item.dataset.page));
  });
}
document.querySelector('.settings-page[data-page="geral"]').classList.add("active");

$("#btn-save-config").addEventListener("click", async () => {
  const sources = {};
  for (const name of Object.keys(SOURCE_PAGES)) {
    const src = readSourcePage(name);
    if (src) sources[name] = src;
  }
  const result = await api("/api/config", {
    method: "POST",
    body: JSON.stringify({
      queries: $("#cfg-queries").value.split("\n"),
      location: $("#cfg-location").value,
      time_range: $("#cfg-time-range").value,
      remote: $("#cfg-remote").value,
      max_pages_per_query: $("#cfg-max-pages").value,
      threshold: $("#cfg-threshold").value,
      sources,
    }),
  });
  if (result.ok) {
    $("#params-dialog").close();
    toast(result.saved_to_file ? "Configurações salvas no config.toml" : "Configurações aplicadas", "ok");
    refreshStatus(true);  // botão de posts pode ter mudado de visibilidade
  }
});

refreshStatus();
loadJobs();
loadConfig();
setInterval(() => refreshStatus(), 5000);
