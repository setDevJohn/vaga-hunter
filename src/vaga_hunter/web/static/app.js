"use strict";

const $ = (sel) => document.querySelector(sel);
let daemonRunning = false;
let polling = null;

async function api(path, options = {}) {
  const resp = await fetch(path, {
    headers: {
      "Content-Type": "application/json",
      // Força preflight CORS: bloqueia POST cross-origin de outras páginas
      "X-Requested-With": "vaga-hunter",
    },
    ...options,
  });
  return resp.json();
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

async function loadJobs() {
  const params = new URLSearchParams();
  const status = $("#filter-status").value;
  const score = $("#filter-score").value;
  if (status) params.set("status", status);
  if (score) params.set("min_score", score);
  const jobs = await api(`/api/jobs?${params}`);
  const tbody = $("#jobs tbody");
  tbody.innerHTML = jobs.map((job) => `
    <tr data-uid="${esc(job.uid)}">
      <td class="score">${job.score.toFixed(1)}</td>
      <td><a href="${esc(safeUrl(job.url))}" target="_blank" rel="noopener">${esc(job.title)}</a></td>
      <td>${esc(job.company)}</td>
      <td>${esc(job.location)}</td>
      <td>${job.posted_at ? job.posted_at.slice(0, 10) : "—"}</td>
      <td><span class="badge ${esc(job.status)}">${esc(job.status)}</span></td>
      <td class="actions">
        <button data-status="interested" title="Interessado">⭐</button>
        <button data-status="discarded" title="Descartar">🗑️</button>
        <button data-status="seen" title="Marcar como vista">👁️</button>
      </td>
    </tr>`).join("");
}

async function setStatus(uid, status) {
  await api(`/api/jobs/${encodeURIComponent(uid)}/status`, {
    method: "POST",
    body: JSON.stringify({ status }),
  });
  loadJobs();
}

function renderStatus(data) {
  const { search, daemon } = data;
  daemonRunning = daemon.running;
  const btn = $("#btn-daemon");
  btn.textContent = daemonRunning ? "Daemon: LIGADO" : "Daemon: desligado";
  btn.classList.toggle("on", daemonRunning);

  const info = [];
  if (daemon.last_run) info.push(`última: ${daemon.last_run.slice(11, 16)}`);
  if (daemonRunning && daemon.next_run) info.push(`próxima: ${daemon.next_run.slice(11, 16)}`);
  if (daemonRunning && !daemon.in_window) info.push("fora da janela");
  $("#daemon-info").textContent = info.join(" · ");

  $("#btn-search").disabled = search.running;
  const panel = $("#log-panel");
  if (search.messages.length) {
    panel.classList.remove("hidden");
    const log = $("#log");
    log.textContent = search.messages.join("\n");
    log.parentElement.scrollTop = log.parentElement.scrollHeight;
  }
  if (!search.running && polling) {
    clearInterval(polling);
    polling = null;
    loadJobs();
  }
}

async function refreshStatus() {
  renderStatus(await api("/api/status"));
}

$("#btn-search").addEventListener("click", async () => {
  const result = await api("/api/search", { method: "POST" });
  if (result.ok && !polling) polling = setInterval(refreshStatus, 1000);
  refreshStatus();
});

$("#btn-daemon").addEventListener("click", async () => {
  await api(`/api/daemon/${daemonRunning ? "stop" : "start"}`, { method: "POST" });
  refreshStatus();
});

$("#jobs tbody").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-status]");
  if (!button) return;
  setStatus(button.closest("tr").dataset.uid, button.dataset.status);
});

async function loadConfig() {
  const cfg = await api("/api/config");
  $("#cfg-queries").value = cfg.queries.join("\n");
  $("#cfg-location").value = cfg.location;
  $("#cfg-time-range").value = cfg.time_range;
  $("#cfg-remote").value = cfg.remote;
  $("#cfg-max-pages").value = cfg.max_pages_per_query;
  $("#cfg-threshold").value = cfg.threshold;
}

$("#btn-save-config").addEventListener("click", async () => {
  const feedback = $("#cfg-feedback");
  feedback.textContent = "salvando…";
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
  feedback.textContent = result.ok
    ? (result.saved_to_file ? "✓ salvo no config.toml" : "✓ aplicado (sem config.toml)")
    : `erro: ${result.error}`;
  setTimeout(() => { feedback.textContent = ""; }, 4000);
});

$("#filter-status").addEventListener("change", loadJobs);
$("#filter-score").addEventListener("change", loadJobs);

refreshStatus();
loadJobs();
loadConfig();
setInterval(refreshStatus, 5000);
