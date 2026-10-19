/* C.A.P. desktop UI — vanilla JS, no build step. */
"use strict";

const TOKEN = document.querySelector('meta[name="cap-token"]').content;
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const view = $("#view");

const app = {
  status: null,
  route: "today",
  inboxView: "reply",
  inboxItems: [],
  selectedId: null,
  current: null,
  tone: "Professional",
  templates: [],
  autoTimer: null,
  jobTimer: null,
};

/* ---------- helpers ---------- */
function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

async function api(path, opts = {}) {
  const init = { method: opts.method || "GET", headers: { "X-CAP-Token": TOKEN } };
  if (opts.body instanceof FormData) init.body = opts.body;
  else if (opts.body !== undefined) { init.body = JSON.stringify(opts.body); init.headers["Content-Type"] = "application/json"; }
  const res = await fetch(path, init);
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!res.ok) {
    const err = new Error((data && data.detail) || `Request failed (${res.status})`); err.status = res.status;
    if (res.status === 423 && !path.startsWith("/api/lock")) showLock();
    throw err;
  }
  return data;
}

let toastTimer;
function toast(message, isError = false) {
  const el = $("#toast");
  el.textContent = message;
  el.className = `toast show${isError ? " error" : ""}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.className = "toast"), isError ? 6000 : 2800);
}

async function withBusy(button, fn) {
  const label = button ? button.innerHTML : "";
  if (button) { button.disabled = true; if (button.dataset.busy) button.textContent = button.dataset.busy; }
  try { return await fn(); }
  catch (err) { toast(err.message, true); }
  finally { if (button) { button.disabled = false; button.innerHTML = label; } }
}

/* Ollama treats "llama3.2" and "llama3.2:latest" as the same model. */
const sameModel = (a, b) => (a || "").replace(/:latest$/, "") === (b || "").replace(/:latest$/, "");

/* Sample inboxes: a fictional business per industry, for demos before any email is connected. */
const demoOptions = (selected = "tax") => (app.status?.demo_industries || []).map(d =>
  `<option value="${esc(d.id)}" ${d.id === selected ? "selected" : ""}>${esc(d.label)} (${esc(d.business)})</option>`).join("");

async function loadDemo(select) {
  const r = await api("/api/demo", { method: "POST", body: { industry: select?.value || null } });
  await loadStatus(); toast(`Sample inbox loaded: ${r.business}`);
  return r;
}

/* A local model's first load can take a minute; count seconds so the button doesn't look frozen. */
function testModel(button, body) {
  const started = Date.now();
  const tick = setInterval(() => {
    const secs = Math.round((Date.now() - started) / 1000);
    button.textContent = secs < 15 ? "Testing…" : `Loading model… ${secs}s`;
  }, 1000);
  return api("/api/models/test", { method: "POST", body }).finally(() => clearInterval(tick));
}

function relTime(iso) {
  if (!iso) return "";
  const d = new Date(iso), now = new Date();
  const mins = Math.round((now - d) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  if (d.toDateString() === now.toDateString()) return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  const days = Math.round((now - d) / 86400000);
  if (days < 7) return d.toLocaleDateString([], { weekday: "short" });
  return d.toLocaleDateString([], { month: "short", day: "numeric" });
}

function fmtMinutes(m) {
  if (!m) return "0 min";
  const h = Math.floor(m / 60), r = m % 60;
  return h ? `${h} h${r ? ` ${r} min` : ""}` : `${r} min`;
}

function plural(n, one, many) { return `${n} ${n === 1 ? one : many}`; }

function dueLabel(due) {
  if (!due) return "";
  const today = new Date().toISOString().slice(0, 10);
  const day = due.slice(0, 10);
  const hasTime = due.length > 10 && due.includes("T");
  const time = hasTime ? " " + new Date(due).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : "";
  if (day < today) return `<span class="over">Overdue · ${esc(new Date(day + "T12:00").toLocaleDateString([], { month: "short", day: "numeric" }))}</span>`;
  if (day === today) return `<span class="today">Today${esc(time)}</span>`;
  return esc(new Date(day + "T12:00").toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" }) + time);
}

function urgencyPill(item) {
  if (item.urgency === "URGENT") return `<span class="pill urgent">Urgent</span>`;
  if (item.urgency === "IMPORTANT") return `<span class="pill important">Important</span>`;
  return "";
}

/* ---------- status, nav, background check ---------- */
async function loadStatus() {
  try { app.status = await api("/api/status"); } catch (err) { return; }
  const s = app.status;
  const inboxCount = $("#nav-inbox");
  inboxCount.textContent = s.counts.reply || "";
  inboxCount.classList.toggle("urgent", s.counts.urgent > 0);
  const conn = $("#conn");
  if (s.connected) { conn.className = "conn ok"; conn.textContent = s.email; }
  else if (s.needs_reconnect) { conn.className = "conn warn"; conn.textContent = "Reconnect your email in Settings"; }
  else if (s.has_demo) { conn.className = "conn warn"; conn.textContent = `Sample inbox${s.demo ? `: ${s.demo.business}` : ""} (no email connected)`; }
  else { conn.className = "conn"; conn.textContent = "No email connected"; }
  $("#nav-settings-dot").hidden = s.setup.ai && (s.connected || s.has_demo);
  if (s.industry !== app.lastIndustry || app.trackerNavStale !== false) { app.lastIndustry = s.industry; app.trackerNavStale = false; refreshTrackerNav(); }
  $("#check-btn").disabled = !s.connected || s.job.running;
  renderJob(s.job);
  if (s.job.running) pollJob();
  scheduleAutoCheck();
}

function scheduleAutoCheck() {
  clearInterval(app.autoTimer);
  const mins = app.status?.auto_check_minutes || 0;
  if (!mins || !app.status?.connected) return;
  app.autoTimer = setInterval(() => startCheck(true), mins * 60000);
}

function renderJob(job) {
  const el = $("#job");
  if (job.running) {
    const n = Math.min(job.done + 1, job.total);
    const label = job.phase === "sorting" && job.total ? `Sorting ${n} of ${job.total}…`
      : job.phase === "drafting" && job.total ? `Writing drafts, urgent first: ${n} of ${job.total}…`
      : (job.current || "Checking…");
    const pct = job.total ? Math.round((job.done / job.total) * 100) : 6;
    el.innerHTML = `${esc(label)}<div class="bar"><i style="width:${pct}%"></i></div>`;
  } else if (job.error) {
    el.innerHTML = `<span style="color:var(--red)">${esc(job.error)}</span>`;
  } else if (job.result) {
    const r = job.result;
    const parts = [];
    if (r.needs_reply) parts.push(`${r.needs_reply} need you`);
    if (r.fyi + r.skipped) parts.push(`${r.fyi + r.skipped} FYI`);
    el.innerHTML = `${parts.length ? esc(parts.join(", ")) : "No new mail"} · ${esc(relTime(job.finished_at))}
      ${r.errors?.length ? `<br><span style="color:var(--red)">${esc(r.errors[0].message)}</span>` : ""}`;
  } else el.innerHTML = "";
}

async function startCheck(silent = false) {
  if (!app.status?.connected) return;
  try {
    const job = await api("/api/inbox/check", { method: "POST" });
    renderJob(job);
    $("#check-btn").disabled = true;
    pollJob();
  } catch (err) { if (!silent) toast(err.message, true); }
}

function pollJob() {
  clearTimeout(app.jobTimer);
  app.jobTimer = setTimeout(async () => {
    const job = await api("/api/inbox/job").catch(() => null);
    if (!job) return;
    const phaseChanged = job.phase !== app.lastPhase;
    app.lastPhase = job.phase;
    renderJob(job);
    if (job.running) {
      // Emails appear as soon as they are sorted; drafts fill in as they are written.
      if (job.phase === "drafting" && app.route === "inbox") loadInbox(false);
      if (phaseChanged && job.phase === "drafting") { loadStatus(); if (app.route === "today") renderToday(); }
      return pollJob();
    }
    $("#check-btn").disabled = false;
    await loadStatus();
    if (job.result?.needs_reply) toast(`${plural(job.result.needs_reply, "new email needs", "new emails need")} you`);
    if (app.route === "inbox") loadInbox(false);
    if (app.route === "today") renderToday();
  }, 1500);
}

/* ---------- router ---------- */
const routes = { today: renderToday, inbox: renderInbox, tasks: renderTasks, tracker: renderTracker, knowledge: renderKnowledge, security: renderSecurity, settings: renderSettings };

function go() {
  const hash = location.hash.replace("#", "") || "today";
  const [route, param] = hash.split("/");
  app.route = routes[route] ? route : "today";
  app.navSeq = (app.navSeq || 0) + 1;  // a page that finishes loading after the user moved on must not draw
  $$(".rail > a").forEach(a => a.classList.toggle("active", a.dataset.route === app.route));
  if (app.route === "inbox" && param) app.selectedId = decodeURIComponent(param);
  view.onclick = null;
  routes[app.route]();
  view.focus({ preventScroll: true });
}
window.addEventListener("hashchange", go);

/* ======================================================================
   TODAY
   ====================================================================== */
async function renderToday() {
  if (app.status && !app.status.setup_complete && !app.status.onboarding_dismissed) return renderSetup();
  const seq = app.navSeq;
  view.innerHTML = `<div class="page"><p class="muted">Loading your day…</p></div>`;
  let d;
  try { d = await api("/api/today"); } catch (err) { if (seq === app.navSeq) view.innerHTML = `<div class="page"><div class="notice error">${esc(err.message)}</div></div>`; return; }
  if (seq !== app.navSeq) return;
  const greet = d.hour < 12 ? "Good morning" : d.hour < 18 ? "Good afternoon" : "Good evening";
  const n = d.counts.reply, f = d.followups.length;
  const sentences = [];
  sentences.push(n ? `<em${d.counts.urgent ? ' class="warn"' : ""}>${plural(n, "email needs", "emails need")} you.</em>` : "Your inbox is clear.");
  if (f) sentences.push(`${plural(f, "person hasn't", "people haven't")} replied to you.`);
  if (d.time_saved.minutes_week) sentences.push(`You saved ${fmtMinutes(d.time_saved.minutes_week)} this week.`);

  view.innerHTML = `<div class="page">
    <p class="today-date">${greet}${d.user_name ? `, ${esc(d.user_name)}` : ""} · ${esc(d.date)}</p>
    <h1 class="today-lede">${sentences.join(" ")}</h1>
    ${d.time_saved.minutes_total ? `<p class="saved-note">${fmtMinutes(d.time_saved.minutes_total)} saved since you started. ${esc(d.time_saved.assumption)}</p>` : ""}
    ${d.time_saved.drafts_measured_30d >= 3 ? `<p class="saved-note">${d.time_saved.drafts_light_edit_30d} of your last ${d.time_saved.drafts_measured_30d} drafts went out with little or no editing.</p>` : ""}
    <div class="cols section">
      <div>
        <div class="section-head"><h2>Needs your reply</h2>${n > 6 ? `<a href="#inbox">See all ${n}</a>` : ""}</div>
        <div class="panel list" id="t-replies">${d.replies.length ? d.replies.map(r => `
          <div class="item">
            <div><div class="who">${esc(r.sender_name || r.sender_email)} ${r.fraud ? `<span class="pill urgent">${r.fraud === "high" ? "Likely fraud" : "Possible fraud"}</span>` : ""}${urgencyPill(r)} ${r.draft_state === "pending" ? '<span class="pill">Writing draft…</span>' : r.review_carefully ? '<span class="pill careful">Check carefully</span>' : ""}</div>
            <div class="what">${esc(r.summary || r.subject)}</div></div>
            <a class="btn sm" href="#inbox/${encodeURIComponent(r.gmail_id)}">Review draft</a>
          </div>`).join("") : `<div class="empty"><strong>Nothing waiting on you.</strong>${app.status?.connected ? "New mail is checked automatically." : "Connect your email in Settings, or load the sample inbox to try it."}</div>`}
        </div>

        <div class="section"><div class="section-head"><h2>Waiting on others</h2></div>
          <div class="panel list">${f ? d.followups.map(x => `
            <div class="item">
              <div><div class="who">${esc(x.sender_name || x.sender_email)}</div>
              <div class="what">“${esc(x.subject)}” · no reply for ${plural(x.days_waiting, "day", "days")}</div></div>
              <div class="item-actions"><button class="sm" data-nudge="${esc(x.gmail_id)}">Draft a nudge</button>
              <button class="sm quiet" data-dismiss="${esc(x.gmail_id)}" title="Stop tracking">Dismiss</button></div>
            </div>`).join("") : `<div class="empty">When you send a reply with “Remind me if no answer” ticked, it shows up here if they go quiet.</div>`}
          </div></div>
      </div>
      <div>
        ${d.key_dates && d.key_dates.industry ? `<div class="section-head"><h2>${d.key_dates.industry === "tax" ? "Key dates" : "Coming up"}</h2><a href="#tracker">${d.key_dates.industry === "tax" ? "Clients" : "Policies"}</a></div>
          <div class="panel kd-panel" style="margin-bottom:24px">${keyDatesHtml(d.key_dates, true)}</div>` : ""}
        <div class="section-head"><h2>Due today</h2><a href="#tasks">${d.open_tasks ? `All tasks (${d.open_tasks})` : "Tasks"}</a></div>
        <div class="panel list">${d.tasks.length ? d.tasks.map(t => `
          <div class="task" data-task="${t.id}"><input type="checkbox" aria-label="Mark done" data-toggle="${t.id}">
          <div><div class="t-title">${esc(t.title)}</div><div class="t-meta">${dueLabel(t.due_at)}</div></div><span></span></div>`).join("")
          : `<div class="empty">No tasks due today.</div>`}
        </div>
        ${d.suggested_tasks.length ? `<div class="section"><div class="section-head"><h2>Suggested from email</h2></div>
          <div class="panel">${d.suggested_tasks.map(s => `
            <div class="suggested-task"><div><div>${esc(s.title)}</div><small>from ${esc(s.from)}</small></div>
            <button class="sm" data-add-task="${esc(s.gmail_id)}" data-index="${s.index}">Add</button></div>`).join("")}</div></div>` : ""}
        <div class="section"><div class="section-head"><h2>Calendar</h2></div>
          <div class="panel">${d.events.length ? d.events.map(e => `
            <div class="event"><time>${e.all_day ? "All day" : esc(new Date(e.start).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }))}</time><div>${esc(e.summary)}</div></div>`).join("")
            : `<div class="empty">${app.status?.calendar_enabled ? "No events today." : "Connect Google Calendar in Settings to see today's events."}</div>`}</div></div>
      </div>
    </div></div>`;

  view.onclick = async e => {
    const t = e.target.closest("button, input");
    if (!t) return;
    if (t.dataset.toggle) { await api(`/api/tasks/${t.dataset.toggle}/toggle`, { method: "POST", body: { completed: true } }); toast("Task done"); renderToday(); }
    if (t.dataset.addTask) withBusy(t, async () => { await api(`/api/email/${encodeURIComponent(t.dataset.addTask)}/task`, { method: "POST", body: { index: +t.dataset.index } }); toast("Added to tasks"); renderToday(); });
    if (t.dataset.dismiss) { await api(`/api/followups/${encodeURIComponent(t.dataset.dismiss)}/dismiss`, { method: "POST" }); renderToday(); }
    if (t.dataset.nudge) openNudge(t.dataset.nudge, t);
  };
}

async function openNudge(id, button) {
  await withBusy(button, async () => {
    button.textContent = "Writing…";
    const { text } = await api(`/api/followups/${encodeURIComponent(id)}/draft`, { method: "POST" });
    const dlg = $("#dialog");
    dlg.innerHTML = `<h2>Follow-up</h2><p class="muted" style="margin-bottom:10px">Sent in the same thread. Edit anything before sending.</p>
      <textarea id="nudge-text" style="min-height:180px">${esc(text)}</textarea>
      <div class="row end" style="margin-top:14px"><button class="quiet" id="nudge-cancel">Cancel</button><button class="primary" id="nudge-send">Send follow-up</button></div>`;
    dlg.showModal();
    $("#nudge-cancel").onclick = () => dlg.close();
    $("#nudge-send").onclick = e => withBusy(e.target, async () => {
      await api(`/api/followups/${encodeURIComponent(id)}/send`, { method: "POST", body: { text: $("#nudge-text").value } });
      dlg.close(); toast("Follow-up sent"); renderToday();
    });
  });
}

/* ---------- connect an email account (used by setup and Settings) ---------- */
function mountConnectEmail(box, onDone) {
  let info = null, address = "";
  const step1 = () => {
    box.innerHTML = `<form class="row" id="ce-form" autocomplete="off">
        <input id="ce-addr" type="email" class="grow" placeholder="you@yourbusiness.com" aria-label="Your email address" value="${esc(address)}" required>
        <button class="primary" type="submit">Continue</button></form>
      <p class="muted" style="font-size:13px;margin-top:8px">Works with Gmail, Outlook/Microsoft 365, Yahoo, iCloud, Zoho, Fastmail and most business email.</p>`;
    $("#ce-form", box).onsubmit = async e => {
      e.preventDefault();
      address = $("#ce-addr", box).value.trim();
      try { info = await api("/api/account/detect", { method: "POST", body: { address } }); step2(); }
      catch (err) { toast(err.message, true); }
    };
  };
  const steps = () => `<ol class="howto">${info.steps.map(s => `<li>${esc(s)}</li>`).join("")}</ol>`;
  const step2 = () => {
    if (info.method === "microsoft") {
      box.innerHTML = `<p><strong>${esc(info.label)}</strong> · ${esc(address)} <button class="link-btn" id="ce-back" type="button">Change</button></p>
        ${info.microsoft_ready ? `${steps()}<div class="row"><button class="primary" id="ce-ms" data-busy="Starting…">Sign in with Microsoft</button></div><div id="ce-ms-code"></div>`
          : `<div class="notice">Microsoft no longer lets apps sign in to Outlook or Microsoft 365 with a password, and this copy of C.A.P. isn't set up for Microsoft sign-in yet. Ask whoever gave you C.A.P. for the updated version, or try the sample inbox for now.</div>`}`;
      $("#ce-back", box).onclick = step1;
      $("#ce-ms", box)?.addEventListener("click", e => withBusy(e.target, async () => {
        const flow = await api("/api/account/microsoft/start", { method: "POST" });
        showMsCode(flow);
      }));
      return;
    }
    box.innerHTML = `<p><strong>${esc(info.label)}</strong> · ${esc(address)} <button class="link-btn" id="ce-back" type="button">Change</button></p>
      ${steps()}${info.help_url ? `<p style="margin:-4px 0 10px"><a href="${esc(info.help_url)}" target="_blank" rel="noopener">Open ${esc(info.label.split(" /")[0])} security settings</a></p>` : ""}
      <form class="stack" id="ce-pw" autocomplete="off">
        <label class="field">${info.provider === "other" ? "Password (or app password)" : "App password"}<input id="ce-pass" type="password" autocomplete="new-password" required></label>
        <details ${info.custom_domain ? "open" : ""}><summary class="muted" style="cursor:pointer;font-size:13px">Server settings</summary>
          <div class="grid2" style="margin-top:8px">
            <label class="field">Incoming (IMAP) server<input id="ce-ih" value="${esc(info.imap_host)}"></label>
            <label class="field">Port<input id="ce-ip" type="number" value="${info.imap_port}"></label>
            <label class="field">Outgoing (SMTP) server<input id="ce-sh" value="${esc(info.smtp_host)}"></label>
            <label class="field">Port<input id="ce-sp" type="number" value="${info.smtp_port}"></label>
          </div></details>
        <div class="row"><button class="primary" type="submit" data-busy="Checking sign-in…">Connect</button>
          <span class="muted" style="font-size:13px">Kept in ${esc(app.status?.secret_store || "this computer")}. Only ever sent to your mail server.</span></div>
      </form>
      ${info.provider === "gmail" ? `<p class="muted" style="font-size:13px;margin-top:10px">Prefer Google sign-in? It also adds your calendar: Settings → Google Calendar.</p>` : ""}`;
    $("#ce-back", box).onclick = step1;
    $("#ce-pw", box).onsubmit = e => {
      e.preventDefault();
      withBusy(e.submitter || $("#ce-pw button", box), async () => {
        const r = await api("/api/account/password", { method: "POST", body: {
          address, password: $("#ce-pass", box).value, imap_host: $("#ce-ih", box).value, imap_port: +$("#ce-ip", box).value,
          smtp_host: $("#ce-sh", box).value, smtp_port: +$("#ce-sp", box).value } });
        toast(`Connected ${r.email}`);
        onDone();
      });
    };
  };
  const showMsCode = flow => {
    $("#ce-ms-code", box).innerHTML = `<div class="ms-code"><p>Open <a href="${esc(flow.verification_uri)}" target="_blank" rel="noopener">${esc(flow.verification_uri)}</a> and enter this code:</p>
      <div class="code" aria-label="Sign-in code">${esc(flow.user_code)}</div><p class="muted" id="ce-ms-state">Waiting for you to approve…</p></div>`;
    const poll = async () => {
      const st = await api("/api/account/microsoft/status").catch(() => null);
      if (!st || st.state === "waiting") return setTimeout(poll, 2500);
      if (st.state === "done") { toast(`Connected ${st.address}`); onDone(); }
      else $("#ce-ms-state", box).innerHTML = `<span style="color:var(--red)">${esc(st.error || "Sign-in didn't complete.")}</span>`;
    };
    setTimeout(poll, 2500);
  };
  step1();
}

/* ---------- first-run setup ---------- */
async function renderSetup() {
  const s = app.status, seq = app.navSeq;
  const [settings, models] = await Promise.all([api("/api/settings"), api("/api/models")]);
  if (seq !== app.navSeq) return;
  const ollama = models.ollama;
  const localOptions = ollama.models.length
    ? ollama.models.map(m => `<option value="${esc(m)}" ${sameModel(m, settings.model) ? "selected" : ""}>${esc(m.replace("ollama/", ""))}</option>`).join("")
    : "";
  view.innerHTML = `<div class="page setup">
    <h1>Set up C.A.P. in three steps</h1>
    <p class="muted" style="margin-top:6px">C.A.P. reads your inbox, sorts what needs you and drafts replies using your business details. It runs on this computer; you approve every email before it's sent.</p>
    <div class="steps">
      <section class="step ${s.setup.ai ? "done" : ""}"><div class="step-num"><span>1</span></div><div>
        <h3>Choose the AI that writes your drafts</h3>
        ${ollama.running
          ? (ollama.models.length
            ? `<p>Ollama is running on this computer, so drafts stay private and cost nothing.</p>
               <div class="row"><select id="s-model" class="grow">${localOptions}</select><button class="primary" id="s-test" data-busy="Testing…">Use this model</button></div>`
            : `<p>Ollama is running but has no models yet. Open a terminal and run <code>ollama pull llama3.1:8b</code>, then reload this page.</p>
               <button id="s-reload">Reload</button>`)
          : `<p>Install <a href="https://ollama.com/download" target="_blank" rel="noopener">Ollama</a> for free, private drafting on this PC, or use your own OpenAI, Anthropic or Gemini key in Settings → AI engine.</p>
             <div class="row"><button id="s-reload">I've started Ollama, check again</button><a class="btn" href="#settings">Use a cloud key instead</a></div>`}
      </div></section>
      <section class="step ${s.connected ? "done" : ""}"><div class="step-num"><span>2</span></div><div>
        <h3>Connect your email, or try the sample inbox</h3>
        ${s.connected ? `<p>Connected as ${esc(s.email)}.</p>` : `<p>Use the address your customers write to.</p><div id="s-connect-box"></div>`}
        <div class="row" style="margin-top:12px">${s.has_demo ? `<span class="muted">Sample inbox loaded${s.demo ? `: ${esc(s.demo.business)}` : ""}.</span>` : `<select id="s-demo-industry" aria-label="Sample business">${demoOptions()}</select><button id="s-demo">Load sample inbox</button><span class="muted">See how it works before connecting anything.</span>`}</div>
      </div></section>
      <section class="step ${s.setup.business && s.setup.name ? "done" : ""}"><div class="step-num"><span>3</span></div><div>
        <h3>Tell it about you and your business</h3>
        <p>Drafts only quote facts you give it, such as prices, hours and policies. You can add more later in Knowledge.</p>
        <div class="stack">
          <div class="grid2"><label class="field">Your name<input id="s-name" value="${esc(settings.user_name)}" placeholder="Sam Rivera"></label>
          <label class="field">Business name<input id="s-biz" placeholder="Brightside Home Cleaning"></label></div>
          <label class="field">What you offer and typical prices<textarea id="s-offer" placeholder="House cleaning in Austin. Standard clean $140, deep clean $220, move-out from $240."></textarea></label>
          <div class="row"><button class="primary" id="s-save">Save</button></div>
        </div>
      </div></section>
    </div>
    <div class="row" style="margin-top:22px"><button class="primary" id="s-finish">Go to Today</button><span class="muted">You can change everything later in Settings.</span></div>
  </div>`;
  api("/api/profile").then(p => { $("#s-biz").value = p.values.business_name; $("#s-offer").value = p.values.services_prices || p.values.about; });

  $("#s-test")?.addEventListener("click", e => withBusy(e.target, async () => {
    const r = await testModel(e.target, { model: $("#s-model").value });
    toast(`Model works (${r.latency_ms} ms)`); await loadStatus(); renderSetup();
  }));
  $("#s-reload")?.addEventListener("click", renderSetup);
  if ($("#s-connect-box")) mountConnectEmail($("#s-connect-box"), async () => { await loadStatus(); renderSetup(); startCheck(); });
  $("#s-demo")?.addEventListener("click", e => withBusy(e.target, async () => {
    await loadDemo($("#s-demo-industry")); renderSetup();
  }));
  $("#s-save").addEventListener("click", e => withBusy(e.target, async () => {
    const profile = (await api("/api/profile")).values;
    profile.business_name = $("#s-biz").value; profile.services_prices = $("#s-offer").value;
    await api("/api/profile", { method: "PUT", body: profile });
    await api("/api/settings", { method: "POST", body: { user_name: $("#s-name").value } });
    await loadStatus(); toast("Saved"); renderSetup();
  }));
  $("#s-finish").addEventListener("click", async () => {
    await api("/api/settings", { method: "POST", body: { onboarding_dismissed: true } });
    await loadStatus(); location.hash = "#today"; renderToday();
  });
}

/* ======================================================================
   INBOX
   ====================================================================== */
async function renderInbox() {
  view.innerHTML = `<div class="inbox">
    <section class="inbox-list" aria-label="Messages">
      <header>
        <div class="row" style="justify-content:space-between"><h2>Inbox</h2>${app.status?.has_demo ? `<button class="sm quiet" id="clear-demo">Remove samples</button>` : ""}</div>
        <div class="tabs" role="tablist">
          <button role="tab" data-v="reply">Needs reply <b class="count" id="c-reply"></b></button>
          <button role="tab" data-v="fyi">FYI <b class="count muted" id="c-fyi"></b></button>
          <button role="tab" data-v="snoozed" id="tab-snoozed" hidden>Snoozed <b class="count muted" id="c-snoozed"></b></button>
          <button role="tab" data-v="sent">Sent</button>
          <button role="tab" data-v="errors" id="tab-errors" hidden>Failed <b class="count urgent" id="c-errors"></b></button>
        </div>
        <input class="filter" id="filter" type="search" placeholder="Filter by sender or subject" aria-label="Filter">
      </header>
      <div class="msgs" id="msgs"></div>
    </section>
    <section class="reader" id="reader" aria-label="Message"></section>
  </div>`;
  $$(".tabs [data-v]").forEach(b => b.onclick = () => { app.inboxView = b.dataset.v; app.selectedId = null; loadInbox(true); });
  $("#filter").oninput = renderList;
  $("#clear-demo")?.addEventListener("click", async () => {
    await api("/api/demo", { method: "DELETE" }); await loadStatus(); toast("Sample emails removed"); renderInbox();
  });
  if (!app.templates.length) api("/api/templates").then(t => (app.templates = t)).catch(() => {});
  app.readerKey = null;
  await loadInbox(true);
}

async function loadInbox(selectFirst) {
  const data = await api(`/api/inbox?view=${app.inboxView}`).catch(err => { toast(err.message, true); return null; });
  if (!data || app.route !== "inbox") return;
  app.inboxItems = data.items;
  const c = data.counts;
  $("#c-reply").textContent = c.reply || "";
  $("#c-fyi").textContent = c.fyi || "";
  $("#c-errors").textContent = c.errors || "";
  $("#c-snoozed").textContent = c.snoozed || "";
  $("#tab-errors").hidden = !c.errors && app.inboxView !== "errors";
  $("#tab-snoozed").hidden = !c.snoozed && app.inboxView !== "snoozed";
  $$(".tabs [data-v]").forEach(b => b.setAttribute("aria-selected", b.dataset.v === app.inboxView));
  if (!app.inboxItems.find(i => i.gmail_id === app.selectedId)) app.selectedId = selectFirst && app.inboxItems[0] ? app.inboxItems[0].gmail_id : null;
  renderList();
  // Only redraw the reader when what it shows has changed, so background refreshes never wipe your edits.
  const sel = app.inboxItems.find(i => i.gmail_id === app.selectedId);
  const key = sel ? `${sel.gmail_id}:${sel.draft_state}` : "none";
  if (key !== app.readerKey) renderReader();
}

function statePill(i) {
  if (i.action !== "DRAFT" || i.sent_at) return "";
  if (i.draft_state === "pending") return '<span class="pill">Writing draft…</span>';
  if (i.draft_state === "failed") return '<span class="pill urgent">Draft failed</span>';
  return i.review_carefully ? '<span class="pill careful">Check carefully</span>' : "";
}

function renderList() {
  const q = ($("#filter")?.value || "").toLowerCase();
  const items = app.inboxItems.filter(i => !q || `${i.sender} ${i.subject} ${i.summary}`.toLowerCase().includes(q));
  const empty = {
    reply: app.status?.connected ? "<strong>All caught up.</strong>Nothing needs a reply right now." :
      "<strong>No emails yet.</strong>Connect your email in Settings, or load the sample inbox from Today.",
    fyi: "<strong>No FYI mail in the last 14 days.</strong>Newsletters, receipts and notifications land here so you can check nothing important was missed.",
    snoozed: "<strong>Nothing snoozed.</strong>",
    sent: "<strong>No replies sent yet.</strong>",
    errors: "<strong>No failed drafts.</strong>",
  }[app.inboxView];
  const when = i => app.inboxView === "sent" ? relTime(i.sent_at) : app.inboxView === "snoozed" ? `until ${fmtWhen(i.snooze_until)}` : relTime(i.received_at);
  $("#msgs").innerHTML = items.length ? items.map(i => `
    <button class="msg" data-id="${esc(i.gmail_id)}" aria-current="${i.gmail_id === app.selectedId}">
      <div class="top"><span class="from">${esc(i.sender_name || i.sender_email || i.sender)}</span>
      <span class="when">${esc(when(i))}</span></div>
      <div class="subj">${esc(i.subject)}</div>
      ${i.summary ? `<div class="sum">${esc(i.summary)}</div>` : ""}
      <div class="tags">${i.fraud ? `<span class="pill urgent" title="Shows signs of fraud or impersonation">${i.fraud === "high" ? "Likely fraud" : "Possible fraud"}</span>` : ""}${urgencyPill(i)}${statePill(i)}${i.sensitive ? '<span class="pill careful" title="Contains a Social Security, account, card or license number">Sensitive</span>' : ""}${i.is_demo ? '<span class="pill demo">Sample</span>' : ""}${app.inboxView === "sent" && i.followup_at && !i.followup_done ? '<span class="pill">Tracking reply</span>' : ""}</div>
    </button>`).join("") : `<div class="empty" style="padding:20px 16px">${empty}</div>`;
  $$("#msgs .msg").forEach(b => b.onclick = () => { app.selectedId = b.dataset.id; renderList(); renderReader(); });
}

function fmtWhen(iso) {
  const d = new Date(iso), now = new Date();
  const day = d.toDateString() === now.toDateString() ? "today"
    : d.toDateString() === new Date(now.getTime() + 86400000).toDateString() ? "tomorrow"
    : d.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });
  return `${day} ${d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`;
}

const SNOOZE_OPTIONS = [["later", "Later today"], ["tomorrow", "Tomorrow morning"], ["monday", "Next Monday"], ["week", "In a week"]];

async function renderReader() {
  const reader = $("#reader");
  if (!app.selectedId) {
    app.readerKey = "none";
    reader.innerHTML = `<div class="reader-inner"><div class="empty"><strong>Select an email</strong>Use <span class="kbd">J</span> and <span class="kbd">K</span> to move through the list.</div></div>`;
    return;
  }
  const id = app.selectedId;
  if (!app.current || app.current.gmail_id !== id) reader.innerHTML = `<div class="reader-inner"><p class="muted">Loading…</p></div>`;
  let m;
  try { m = await api(`/api/email/${encodeURIComponent(id)}`); } catch (err) { reader.innerHTML = `<div class="reader-inner"><div class="notice error">${esc(err.message)}</div></div>`; return; }
  if (app.selectedId !== id) return;
  app.current = m;
  app.readerKey = `${m.gmail_id}:${m.draft_state}`;
  const isDraft = m.action === "DRAFT" && !m.sent_at;
  const original = m.body ? `<details class="original" ${isDraft ? "" : "open"}><summary>Original message</summary><pre>${esc(m.body)}</pre></details>` : "";
  const head = `<h1>${esc(m.subject)}</h1>
    <div class="meta"><span>${esc(m.sender)}</span><span>${esc(new Date(m.received_at).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }))}</span>${m.is_demo ? '<span class="pill demo">Sample email</span>' : ""}</div>
    ${m.snoozed ? `<div class="notice info" style="margin-top:12px">Snoozed until ${esc(fmtWhen(m.snooze_until))}. <button class="link-btn" id="r-unsnooze" style="font-size:14px;color:var(--green)">Bring it back now</button></div>` : ""}
    ${m.fraud ? `<div class="notice fraud" style="margin-top:12px"><strong>${m.fraud.level === "high" ? "Likely fraud or impersonation" : "Possible fraud"}</strong>
      <ul>${m.fraud.signals.map(s => `<li>${esc(s.label)}</li>`).join("")}</ul>
      <p>${m.fraud.advice.map(esc).join(" ")}</p>
      <button class="sm" data-verify="${esc(m.gmail_id)}">Add a "verify by phone" task</button></div>` : ""}
    ${trackerReaderHtml(m)}
    ${m.sensitive ? `<div class="notice sensitive" style="margin-top:12px"><strong>Sensitive information:</strong> this email contains ${esc(m.sensitive.summary)} (${m.sensitive.items.map(i => esc(i.masked)).join(", ")}). Don't repeat it in your reply. Once it's saved somewhere secure, consider deleting this email.</div>` : ""}
    ${m.summary ? `<div class="summary">${esc(m.summary)}</div>` : ""}`;
  const bindUnsnooze = () => $("#r-unsnooze")?.addEventListener("click", async () => {
    await api(`/api/email/${encodeURIComponent(m.gmail_id)}/unsnooze`, { method: "POST" }); toast("Back in your inbox"); afterAction();
  });

  if (!isDraft) {
    const why = m.action === "SKIPPED" ? "Sorted as FYI: looks like a newsletter, receipt or automated message, so no AI time was spent on it."
      : m.action === "NO_REPLY_NEEDED" ? `Sorted as FYI: ${m.reason || "the AI found nothing to answer."}` : "";
    reader.innerHTML = `<div class="reader-inner">${head}
      ${why && !m.sent_at ? `<p class="muted" style="margin:12px 0">${esc(why)} If that's wrong, ask for a draft.</p>` : ""}${original}
      ${m.sent_at ? `<h3 style="margin:18px 0 8px">Your reply · sent ${esc(relTime(m.sent_at))}</h3><pre class="summary" style="white-space:pre-wrap;font:inherit">${esc(m.draft_reply)}</pre>` : ""}
      ${m.action === "DRAFT_ERROR" ? `<div class="notice error" style="margin:12px 0">${esc(m.reason)}</div>` : ""}
      <div class="row" style="margin-top:16px">
        ${!m.sent_at ? `<button class="primary" id="r-needs" data-busy="Writing draft…">Draft a reply anyway</button>` : ""}
        ${!m.sent_at && !m.is_archived ? `<button id="r-done">Done</button>` : ""}
      </div></div>`;
    $("#r-needs")?.addEventListener("click", e => withBusy(e.target, async () => {
      await api(`/api/email/${encodeURIComponent(m.gmail_id)}/needs-reply`, { method: "POST" });
      await api(`/api/email/${encodeURIComponent(m.gmail_id)}/regenerate`, { method: "POST", body: { tone: app.tone } });
      app.inboxView = "reply"; await loadStatus(); loadInbox(false);
    }));
    $("#r-done")?.addEventListener("click", markDone);
    bindUnsnooze();
    return;
  }

  if (m.draft_state !== "ready") {
    const failed = m.draft_state === "failed";
    reader.innerHTML = `<div class="reader-inner">${head}${original}
      <div class="writing ${failed ? "failed" : ""}" aria-live="polite">
        ${failed ? `<div class="notice error">${esc(m.reason || "The draft couldn't be written.")}</div>
            <div class="row" style="margin-top:12px"><button class="primary" id="r-retry" data-busy="Writing draft…">Try again</button><button id="r-done">Done</button></div>`
          : `<div class="pulse" aria-hidden="true"></div><div><strong>Writing your draft…</strong><p class="muted">Usually a few seconds; longer on slower computers. You can read the email meanwhile.</p></div>`}
      </div></div>`;
    $("#r-done")?.addEventListener("click", markDone);
    bindUnsnooze();
    const draftNow = async () => {
      try {
        await api(`/api/email/${encodeURIComponent(m.gmail_id)}/draft-now`, { method: "POST" });
      } catch (err) {
        if (!/already being written/.test(err.message)) { if (app.selectedId === m.gmail_id) renderReader(); return toast(err.message, true); }
        await new Promise(r => setTimeout(r, 2000));   // the background job is on it; check again shortly
      }
      if (app.selectedId === m.gmail_id) { app.readerKey = null; await renderReader(); loadInbox(false); }
    };
    if (failed) $("#r-retry").onclick = e => withBusy(e.target, draftNow);
    else draftNow();
    return;
  }

  const tones = ["Professional", "Friendly", "Concise"];
  reader.innerHTML = `<div class="reader-inner">${head}
    ${m.review_carefully ? `<div class="notice" style="margin-bottom:12px"><strong>Check this one carefully.</strong> ${esc(m.reason || "The AI wasn't confident about this reply.")}</div>` : ""}
    ${original}
    ${m.suggested_replies.length ? `<div class="chips" aria-label="Quick replies">${m.suggested_replies.map((s, i) => `<button class="chip sm" data-quick="${i}" title="Rewrite the draft around this answer">${esc(s)}</button>`).join("")}</div>` : ""}
    <div class="toolbar">
      <div class="seg" role="group" aria-label="Tone">${tones.map(t => `<button data-tone="${t}" aria-pressed="${t === app.tone}">${t}</button>`).join("")}</div>
      <div class="row">
        ${app.templates.length ? `<select id="tpl" aria-label="Insert saved reply" style="width:auto"><option value="">Insert saved reply…</option>${app.templates.map(t => `<option value="${t.id}">${esc(t.title)}</option>`).join("")}</select>` : ""}
        <button class="sm quiet" id="save-tpl" title="Save this draft as a reusable reply">Save as saved reply</button>
      </div>
    </div>
    <div class="draft-wrap"><div class="hl" id="hl" aria-hidden="true"></div><textarea id="draft" aria-label="Draft reply" spellcheck="true">${esc(m.draft_reply)}</textarea></div>
    <div class="fact-legend" id="facts" aria-live="polite"></div>
    <div class="row" style="margin-top:10px">
      <input id="instr" class="grow" placeholder="Tell the AI what to change, e.g. “offer Thursday instead and mention the 10% repeat discount”">
      <button id="rewrite" data-busy="Rewriting…">Rewrite</button>
    </div>
    ${m.suggested_tasks.length ? `<div style="margin-top:16px"><h3 style="margin-bottom:4px">Tasks from this email</h3>${m.suggested_tasks.map((t, i) => `
      <div class="suggested-task"><span>${esc(t.title)}${t.due ? ` <small>· due ${esc(t.due)}</small>` : ""}</span><button class="sm" data-task-index="${i}">Add task</button></div>`).join("")}</div>` : ""}
    <div class="sendbar">
      <button class="primary" id="send" data-busy="Sending…" title="Send (Ctrl+Enter)">Send reply</button>
      <label class="check"><input type="checkbox" id="followup" ${/\?/.test(m.draft_reply) ? "checked" : ""}> Remind me if no answer</label>
      <select id="snooze" class="quiet-select" aria-label="Snooze"><option value="">Snooze…</option>${SNOOZE_OPTIONS.map(([v, l]) => `<option value="${v}">${l}</option>`).join("")}</select>
      <button class="quiet" id="mk-task" title="Create a task from this email">Add task</button>
      <button class="quiet" id="done" title="Archive without replying (E)">Done</button>
      <button class="quiet danger" id="trash" title="Move to Trash">Delete</button>
    </div>
  </div>`;

  const draft = $("#draft");
  autoGrow(draft);
  paintFacts(m.check);
  let saveTimer, factTimer;
  draft.oninput = () => {
    autoGrow(draft);
    paintFacts(null);   // drop stale highlights immediately; re-check after typing pauses
    clearTimeout(saveTimer); clearTimeout(factTimer);
    saveTimer = setTimeout(() => api(`/api/email/${encodeURIComponent(m.gmail_id)}/save-draft`, { method: "POST", body: { text: draft.value } }).catch(() => {}), 800);
    factTimer = setTimeout(async () => {
      const text = draft.value;
      const check = await api(`/api/email/${encodeURIComponent(m.gmail_id)}/facts`, { method: "POST", body: { text } }).catch(() => null);
      if (check && draft.value === text) paintFacts(check);
    }, 500);
  };
  bindUnsnooze();
  $$("[data-tone]").forEach(b => b.onclick = () => { app.tone = b.dataset.tone; rewrite(b, null); });
  $$("[data-quick]").forEach(b => b.onclick = () => rewrite(b, `Base the reply on this answer: "${m.suggested_replies[+b.dataset.quick]}"`));
  $("#rewrite").onclick = e => rewrite(e.target, $("#instr").value.trim() || null);
  $("#instr").onkeydown = e => { if (e.key === "Enter") { e.preventDefault(); $("#rewrite").click(); } };
  $("#tpl")?.addEventListener("change", async e => {
    if (!e.target.value) return;
    const { text } = await api(`/api/templates/${e.target.value}/render`, { method: "POST", body: { gmail_id: m.gmail_id } });
    draft.value = text; draft.dispatchEvent(new Event("input")); e.target.value = ""; toast("Saved reply inserted. Edit the details before sending.");
  });
  $("#save-tpl").onclick = () => openTemplateDialog({ title: m.subject.replace(/^re:\s*/i, ""), body: draft.value });
  $$("[data-task-index]").forEach(b => b.onclick = () => withBusy(b, async () => {
    await api(`/api/email/${encodeURIComponent(m.gmail_id)}/task`, { method: "POST", body: { index: +b.dataset.taskIndex } });
    b.textContent = "Added"; b.disabled = true; toast("Added to tasks");
  }));
  $("#mk-task").onclick = e => withBusy(e.target, async () => {
    await api(`/api/email/${encodeURIComponent(m.gmail_id)}/task`, { method: "POST", body: {} }); toast("Task added");
  });
  $("#snooze").onchange = async e => {
    if (!e.target.value) return;
    try {
      const r = await api(`/api/email/${encodeURIComponent(m.gmail_id)}/snooze`, { method: "POST", body: { until: e.target.value } });
      toast(`Snoozed until ${fmtWhen(r.snoozed_until)}`); afterAction();
    } catch (err) { toast(err.message, true); }
  };
  $("#send").onclick = sendCurrent;
  $("#done").onclick = markDone;
  $("#trash").onclick = e => withBusy(e.target, async () => {
    if (!confirmInline(e.target, "Click again to delete")) return;
    await api(`/api/email/${encodeURIComponent(m.gmail_id)}/trash`, { method: "POST" });
    toast(m.is_demo ? "Removed" : "Moved to Trash"); afterAction();
  });
}

function autoGrow(ta) {
  ta.style.height = "auto";
  ta.style.height = Math.max(260, ta.scrollHeight + 2) + "px";
}

/* Green = fact found in the email, your profile, files, saved replies or signature. Amber = check it. */
function paintFacts(check) {
  const ta = $("#draft"), hl = $("#hl"), legend = $("#facts");
  if (!ta || !hl) return;
  const text = ta.value;
  if (!check) { hl.innerHTML = esc(text) + "\n "; return; }
  let html = "", pos = 0;
  for (const f of check.facts) {
    if (f.start < pos || f.end > text.length) continue;
    html += esc(text.slice(pos, f.start)) + `<mark class="${f.supported ? "ok" : "warn"}">${esc(text.slice(f.start, f.end))}</mark>`;
    pos = f.end;
  }
  hl.innerHTML = html + esc(text.slice(pos)) + "\n ";
  const ok = check.facts.filter(f => f.supported), warn = check.facts.filter(f => !f.supported);
  if (!check.facts.length) { legend.innerHTML = `<span class="muted">No prices, dates or promises in this draft.</span>`; return; }
  const bySource = {};
  ok.forEach(f => (bySource[f.source] = bySource[f.source] || []).push(f.text));
  legend.innerHTML = `
    ${warn.length ? `<div class="legend-row"><span class="key warn"></span><strong>Check ${warn.length === 1 ? "this" : `these ${warn.length}`}:</strong>
      ${warn.map(f => `<button class="fact-chip" data-start="${f.start}" data-end="${f.end}" title="Not found in the email or your Knowledge. Click to select it.">${esc(f.text)}</button>`).join("")}</div>` : ""}
    ${ok.length ? `<div class="legend-row muted"><span class="key ok"></span>${warn.length ? "" : "<strong style=\"color:var(--ink)\">Every fact checks out.</strong> "}
      ${Object.entries(bySource).map(([src, list]) => `From ${esc(src)}: ${list.map(esc).join(", ")}`).join(" · ")}</div>` : ""}`;
  $$(".fact-chip", legend).forEach(b => b.onclick = () => { ta.focus(); ta.setSelectionRange(+b.dataset.start, +b.dataset.end); });
}

function confirmInline(button, label) {
  if (button.dataset.armed) return true;
  button.dataset.armed = "1";
  const old = button.textContent;
  setTimeout(() => { button.textContent = label; }, 0);
  setTimeout(() => { delete button.dataset.armed; button.textContent = old; }, 3000);
  return false;
}

async function rewrite(button, instruction) {
  const m = app.current;
  const reader = $("#reader .reader-inner");
  await withBusy(button, async () => {
    reader.classList.add("busy");
    try {
      await api(`/api/email/${encodeURIComponent(m.gmail_id)}/regenerate`, { method: "POST", body: { tone: app.tone, instruction } });
    } finally { reader.classList.remove("busy"); }
    app.readerKey = null;
    await renderReader();
    toast("Draft updated");
  });
}

async function sendCurrent(allowSensitive) {
  const m = app.current, btn = $("#send");
  if (!m || !btn) return;
  const text = $("#draft").value.trim();
  if (!text) return toast("The reply is empty.", true);
  await withBusy(btn, async () => {
    let r;
    try {
      r = await api(`/api/email/${encodeURIComponent(m.gmail_id)}/send`, { method: "POST", body: { text, followup: $("#followup").checked, allow_sensitive: allowSensitive === true } });
    } catch (err) {
      if (err.status === 409) return openSensitiveDialog(err.message);
      throw err;
    }
    toast(r.demo ? "Sample reply marked as sent (nothing was emailed)" : `Sent to ${m.sender_name || m.sender_email}${r.followup_days ? ` · we'll remind you in ${r.followup_days} days if they don't answer` : ""}`);
    afterAction();
    if (r.learn && (r.learn.suggestions.length || r.learn.offer_template)) openLearnDialog(r.learn, text, r.subject);
  });
}

/* Sensitive-number guard: a reply that contains an SSN, account or card number needs a second look. */
function openSensitiveDialog(message) {
  const dlg = $("#dialog");
  dlg.innerHTML = `<h2>Sensitive number in this reply</h2>
    <p style="margin-bottom:10px">${esc(message)}</p>
    <p class="muted">Remove it and point the client to your secure portal or a phone call, or send anyway if you're sure.</p>
    <div class="row end" style="margin-top:16px"><button class="danger quiet" id="sn-send">Send anyway</button><button class="primary" id="sn-edit">Edit reply</button></div>`;
  dlg.showModal();
  $("#sn-edit").onclick = () => { dlg.close(); $("#draft")?.focus(); };
  $("#sn-send").onclick = () => { dlg.close(); sendCurrent(true); };
}

/* After sending: offer to teach C.A.P. what the user changed, so the next draft gets it right. */
function openLearnDialog(learn, sentText, subject) {
  const dlg = $("#dialog");
  dlg.innerHTML = `<h2>Teach C.A.P. from your edit?</h2>
    <p class="muted" style="margin-bottom:14px">You changed the draft before sending. Save what's still true so future drafts get it right.</p>
    <div class="stack" id="learn-list">
      ${learn.suggestions.map((s, i) => `<div class="learn-item">
        ${s.type === "replace"
          ? `<div>Your profile says <s>${esc(s.old)}</s>; you wrote <strong>${esc(s.new)}</strong>.<br><small class="muted">Update ${esc(s.field_label)}?</small></div>`
          : `<div>You added <strong>${esc(s.new)}</strong>: “${esc(s.text)}”<br><small class="muted">Add to ${esc(s.field_label)}?</small></div>`}
        <button class="sm" data-learn="${i}">${s.type === "replace" ? "Update profile" : "Add to profile"}</button></div>`).join("")}
      ${learn.offer_template ? `<div class="learn-item"><div>You rewrote most of this one.<br><small class="muted">Save it as a saved reply for similar emails?</small></div>
        <button class="sm" id="learn-tpl">Save as saved reply</button></div>` : ""}
    </div>
    <div class="row end" style="margin-top:16px"><button class="quiet" id="learn-close">Not now</button></div>`;
  dlg.showModal();
  $("#learn-close").onclick = () => dlg.close();
  $$("[data-learn]", dlg).forEach(b => b.onclick = () => withBusy(b, async () => {
    await api("/api/learn", { method: "POST", body: learn.suggestions[+b.dataset.learn] });
    b.textContent = "Saved"; b.disabled = true; b.dataset.done = 1; toast("Profile updated");
  }));
  $("#learn-tpl")?.addEventListener("click", () => { dlg.close(); openTemplateDialog({ title: (subject || "").replace(/^re:\s*/i, ""), body: sentText }); });
}

async function markDone() {
  const m = app.current;
  try {
    const r = await api(`/api/email/${encodeURIComponent(m.gmail_id)}/done`, { method: "POST" });
    toast(r.archived_on_server ? "Done, archived in your mailbox" : "Done");
    afterAction();
  } catch (err) { toast(err.message, true); }
}

async function afterAction() {
  const idx = app.inboxItems.findIndex(i => i.gmail_id === app.current?.gmail_id);
  const next = app.inboxItems[idx + 1] || app.inboxItems[idx - 1];
  app.selectedId = next ? next.gmail_id : null;
  app.readerKey = null;
  await loadStatus();
  loadInbox(false);
}

/* ======================================================================
   TASKS
   ====================================================================== */
async function renderTasks() {
  view.innerHTML = `<div class="page">
    <div class="page-head"><div><h1>Tasks</h1><p>Your to-dos, including ones pulled from email.</p></div>
      <button class="quiet" id="clear-done">Clear completed</button></div>
    <div class="cols">
      <div>
        <form class="panel stack" id="task-form" autocomplete="off">
          <input id="t-title" placeholder="Add a task, e.g. “Send Maria the booking link”" aria-label="Task title" required>
          <div class="row">
            <input id="t-due" type="datetime-local" style="width:auto" aria-label="Due">
            <select id="t-pri" style="width:auto" aria-label="Priority"><option value="MEDIUM">Normal</option><option value="HIGH">High</option><option value="LOW">Low</option></select>
            <label class="check" title="Needs a time and a connected Google Calendar"><input type="checkbox" id="t-cal"> Also add to calendar</label>
            <span class="grow"></span><button class="primary" type="submit">Add task</button>
          </div>
        </form>
        <div class="panel" style="margin-top:16px" id="task-list"><p class="muted">Loading…</p></div>
      </div>
      <div><div class="section-head"><h2>Next 7 days</h2></div><div class="panel" id="upcoming"><p class="muted">Loading…</p></div></div>
    </div></div>`;

  $("#task-form").onsubmit = async e => {
    e.preventDefault();
    const body = { title: $("#t-title").value, due_at: $("#t-due").value || null, priority: $("#t-pri").value, add_to_calendar: $("#t-cal").checked };
    try {
      const r = await api("/api/tasks", { method: "POST", body });
      toast(r.calendar && r.calendar !== "added" ? `Task added; calendar ${r.calendar}` : "Task added");
      $("#t-title").value = ""; $("#t-due").value = ""; loadTasks(); loadStatus();
    } catch (err) { toast(err.message, true); }
  };
  $("#clear-done").onclick = async () => { const r = await api("/api/tasks/completed", { method: "DELETE" }); toast(`Cleared ${plural(r.deleted, "task", "tasks")}`); loadTasks(); };
  loadTasks();
  api("/api/calendar/upcoming?days=7").then(d => {
    $("#upcoming").innerHTML = !d.available ? `<div class="empty">${esc(d.reason)}</div>` : d.events.length ? d.events.map(ev => `
      <div class="event"><time>${esc(new Date(ev.start).toLocaleDateString([], { weekday: "short" }))}<br>${ev.all_day ? "All day" : esc(new Date(ev.start).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }))}</time>
      <div>${esc(ev.summary)}</div></div>`).join("") : `<div class="empty">Nothing on your calendar this week.</div>`;
  }).catch(() => ($("#upcoming").innerHTML = `<div class="empty">Calendar unavailable.</div>`));
}

async function loadTasks() {
  const tasks = await api("/api/tasks");
  const open = tasks.filter(t => !t.completed).length;
  $("#nav-tasks").textContent = open || "";
  const box = $("#task-list");
  if (!tasks.length) { box.innerHTML = `<div class="empty"><strong>No tasks yet.</strong>Add one above, or click “Add task” on any email.</div>`; return; }
  box.innerHTML = tasks.map(t => `
    <div class="task ${t.completed ? "done" : ""}">
      <input type="checkbox" ${t.completed ? "checked" : ""} data-toggle="${t.id}" aria-label="Done">
      <div><div class="t-title">${t.priority === "HIGH" ? "<strong>" : ""}${esc(t.title)}${t.priority === "HIGH" ? "</strong>" : ""}</div>
        <div class="t-meta">${[dueLabel(t.due_at), t.source_email ? `<a href="#inbox/${encodeURIComponent(t.source_email)}">from email</a>` : ""].filter(Boolean).join(" · ")}</div></div>
      <button class="sm quiet" data-del="${t.id}" aria-label="Delete task">Delete</button>
    </div>`).join("");
  box.onclick = async e => {
    const t = e.target;
    if (t.dataset.toggle) { await api(`/api/tasks/${t.dataset.toggle}/toggle`, { method: "POST", body: { completed: t.checked } }); loadTasks(); }
    if (t.dataset.del) { await api(`/api/tasks/${t.dataset.del}`, { method: "DELETE" }); loadTasks(); }
  };
}

/* ======================================================================
   KNOWLEDGE
   ====================================================================== */
let knowledgeTab = "profile";
async function renderKnowledge() {
  view.innerHTML = `<div class="page">
    <div class="page-head"><div><h1>Knowledge</h1><p>What the AI is allowed to use when it writes for you. Drafts never invent prices or policies that aren't here.</p></div></div>
    <div class="tabs" role="tablist">
      ${[["profile", "Business profile"], ["replies", "Saved replies"], ["files", "Files & notes"], ["ask", "Ask"]].map(([k, l]) =>
        `<button role="tab" data-k="${k}" aria-selected="${k === knowledgeTab}">${l}</button>`).join("")}
    </div>
    <div id="k-body"></div></div>`;
  $$("[data-k]").forEach(b => b.onclick = () => { knowledgeTab = b.dataset.k; renderKnowledge(); });
  ({ profile: kProfile, replies: kReplies, files: kFiles, ask: kAsk })[knowledgeTab]();
}

async function kProfile() {
  const p = await api("/api/profile");
  const placeholders = {
    business_name: "Brightside Home Cleaning",
    about: "Residential cleaning for homes and small offices in North Austin. Owner-operated, 4-person team.",
    services_prices: "Standard clean $140 (2-bed). Deep clean $220. Move-out from $240. Oven add-on $35.",
    hours_location: "Mon–Sat 8am–6pm. We cover zip codes 78727–78759.",
    policies: "24h cancellation or $40 fee. 25% deposit to book. Satisfaction guarantee: free re-clean within 48h.",
    links: "Book: https://example.com/book",
    faq: "Q: Do you bring supplies? A: Yes, all supplies and equipment.\nQ: Insured? A: Yes, fully insured and bonded.",
    never_say: "Never promise same-day service. Don't offer discounts above 10%.",
  };
  $("#k-body").innerHTML = `<form class="stack" id="bp" style="max-width:720px">
    ${p.fields.map(f => `<label class="field">${esc(f.label)}
      ${["business_name", "links"].includes(f.key) ? `<input name="${f.key}" value="${esc(p.values[f.key])}" placeholder="e.g. ${esc(placeholders[f.key])}">`
        : `<textarea name="${f.key}" placeholder="e.g. ${esc(placeholders[f.key])}" rows="3">${esc(p.values[f.key])}</textarea>`}</label>`).join("")}
    <div class="row"><button class="primary" type="submit">Save profile</button><span class="muted">Used in every draft and in Ask.</span></div></form>`;
  $("#bp").onsubmit = async e => {
    e.preventDefault();
    const values = Object.fromEntries(new FormData(e.target).entries());
    await api("/api/profile", { method: "PUT", body: values }).then(() => { toast("Profile saved"); loadStatus(); }).catch(err => toast(err.message, true));
  };
}

async function kReplies() {
  app.templates = await api("/api/templates");
  $("#k-body").innerHTML = `<div class="section-head"><p class="muted" style="max-width:60ch">Answers you send often. Insert one into any draft, and the AI also borrows their wording for similar emails. Placeholders: <code>{first_name}</code> <code>{my_name}</code> <code>{business_name}</code> <code>{booking_link}</code></p>
      <div class="row"><a class="btn quiet" href="#settings/industry">Industry starter kits</a><button class="primary" id="new-tpl">New saved reply</button></div></div>
    <div class="panel">${app.templates.length ? app.templates.map(t => `
      <div class="tpl"><div class="row" style="justify-content:space-between"><strong>${esc(t.title)}</strong>
        <div class="row"><small>${t.uses ? `used ${plural(t.uses, "time", "times")}` : ""}</small><button class="sm quiet" data-edit="${t.id}">Edit</button><button class="sm quiet danger" data-del="${t.id}">Delete</button></div></div>
        <pre>${esc(t.body)}</pre></div>`).join("") : `<div class="empty"><strong>No saved replies yet.</strong>Start with your most common answer, such as pricing, availability or how to book.</div>`}</div>`;
  $("#new-tpl").onclick = () => openTemplateDialog({ title: "", body: "Hi {first_name},\n\n\n\nBest regards,\n{my_name}" });
  $("#k-body").onclick = async e => {
    const id = e.target.dataset.edit || e.target.dataset.del;
    if (!id) return;
    if (e.target.dataset.del) { await api(`/api/templates/${id}`, { method: "DELETE" }); kReplies(); }
    else openTemplateDialog(app.templates.find(t => String(t.id) === id));
  };
}

function openTemplateDialog(t) {
  const dlg = $("#dialog");
  dlg.innerHTML = `<h2>${t.id ? "Edit saved reply" : "New saved reply"}</h2>
    <div class="stack"><label class="field">Name<input id="tp-title" value="${esc(t.title)}" placeholder="Pricing — standard clean"></label>
    <label class="field">Text<textarea id="tp-body" style="min-height:220px">${esc(t.body)}</textarea></label></div>
    <div class="row end" style="margin-top:14px"><button class="quiet" id="tp-cancel">Cancel</button><button class="primary" id="tp-save">Save</button></div>`;
  dlg.showModal();
  $("#tp-cancel").onclick = () => dlg.close();
  $("#tp-save").onclick = async () => {
    try {
      const body = { title: $("#tp-title").value, body: $("#tp-body").value };
      await api(t.id ? `/api/templates/${t.id}` : "/api/templates", { method: t.id ? "PUT" : "POST", body });
      dlg.close(); toast(t.id ? "Saved reply updated" : "Saved reply added"); app.templates = await api("/api/templates");
      if (app.route === "knowledge") kReplies();
    } catch (err) { toast(err.message, true); }
  };
}

async function kFiles() {
  const [docs, notes] = await Promise.all([api("/api/documents"), api("/api/notes")]);
  $("#k-body").innerHTML = `<div class="cols">
    <div><div class="section-head"><h2>Files</h2></div>
      <label class="drop" id="drop" tabindex="0"><button type="button" class="primary" id="upload-btn" style="margin-bottom:10px">Upload files</button><br>Drop a price list, FAQ, menu or policy here, or click to choose.<br><small>PDF, Word, Excel, PowerPoint, text. Up to 25 MB.</small>
        <input type="file" id="file-in" multiple hidden accept=".pdf,.docx,.xlsx,.pptx,.txt,.md,.csv,.html,.htm,.json"></label>
      <div class="panel" style="margin-top:12px">${docs.length ? docs.map(d => `
        <div class="file"><div><div>${esc(d.name)}</div><small>${(d.characters / 1000).toFixed(1)}k characters · added ${esc(relTime(d.added_at))}</small></div>
        <button class="sm quiet danger" data-deldoc="${d.id}">Remove</button></div>`).join("") : `<div class="empty">No files yet.</div>`}</div>
    </div>
    <div><div class="section-head"><h2>Notes</h2><button class="sm" id="new-note">New note</button></div>
      <div class="panel">${notes.length ? notes.map(n => `
        <div class="file"><div class="grow"><div>${esc(n.title)}</div><small style="display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(n.content)}</small></div>
        <div class="row" style="flex-wrap:nowrap"><button class="sm quiet" data-editnote="${n.id}">Edit</button><button class="sm quiet danger" data-delnote="${n.id}">Delete</button></div></div>`).join("")
        : `<div class="empty">Quick facts that don't fit the profile, like a parking note or a supplier's name.</div>`}</div>
    </div></div>`;
  const input = $("#file-in"), drop = $("#drop");
  const upload = async files => {
    if (!files.length) return;
    const fd = new FormData(); [...files].forEach(f => fd.append("files", f));
    drop.textContent = "Reading files…";
    try { const r = await api("/api/documents", { method: "POST", body: fd }); toast(`Added ${plural(r.added.length, "file", "files")}${r.errors.length ? `; ${r.errors.join("; ")}` : ""}`, !!r.errors.length); }
    catch (err) { toast(err.message, true); }
    kFiles();
  };
  input.onchange = () => upload(input.files);
  $("#upload-btn").onclick = e => { e.preventDefault(); input.click(); };
  drop.onkeydown = e => { if (e.target === drop && (e.key === "Enter" || e.key === " ")) input.click(); };
  drop.ondragover = e => { e.preventDefault(); drop.classList.add("over"); };
  drop.ondragleave = () => drop.classList.remove("over");
  drop.ondrop = e => { e.preventDefault(); upload(e.dataTransfer.files); };
  $("#new-note").onclick = () => openNoteDialog({});
  $("#k-body").onclick = async e => {
    const d = e.target.dataset;
    if (d.deldoc) { await api(`/api/documents/${d.deldoc}`, { method: "DELETE" }); kFiles(); }
    if (d.delnote) { await api(`/api/notes/${d.delnote}`, { method: "DELETE" }); kFiles(); }
    if (d.editnote) openNoteDialog(notes.find(n => String(n.id) === d.editnote));
  };
}

function openNoteDialog(n) {
  const dlg = $("#dialog");
  dlg.innerHTML = `<h2>${n.id ? "Edit note" : "New note"}</h2><div class="stack">
    <label class="field">Title<input id="n-title" value="${esc(n.title || "")}"></label>
    <label class="field">Note<textarea id="n-body" style="min-height:180px">${esc(n.content || "")}</textarea></label></div>
    <div class="row end" style="margin-top:14px"><button class="quiet" id="n-cancel">Cancel</button><button class="primary" id="n-save">Save note</button></div>`;
  dlg.showModal();
  $("#n-cancel").onclick = () => dlg.close();
  $("#n-save").onclick = async () => {
    try {
      await api(n.id ? `/api/notes/${n.id}` : "/api/notes", { method: n.id ? "PUT" : "POST", body: { title: $("#n-title").value, content: $("#n-body").value } });
      dlg.close(); toast("Note saved"); kFiles();
    } catch (err) { toast(err.message, true); }
  };
}

function kAsk() {
  $("#k-body").innerHTML = `<div style="max-width:720px">
    <p class="muted" style="margin-bottom:12px">Ask about your own business info. Answers come only from your profile, files and notes, and say which ones they used.</p>
    <form class="row" id="ask-form"><input id="ask-q" class="grow" placeholder="What's our cancellation policy for same-week bookings?" aria-label="Question"><button class="primary" type="submit" data-busy="Thinking…">Ask</button></form>
    <div id="answers" class="stack" style="margin-top:18px"></div></div>`;
  $("#ask-form").onsubmit = e => {
    e.preventDefault();
    const q = $("#ask-q").value.trim();
    if (!q) return;
    withBusy(e.submitter || $("#ask-form button"), async () => {
      const r = await api("/api/ask", { method: "POST", body: { question: q } });
      $("#answers").insertAdjacentHTML("afterbegin", `<div class="panel"><h3 style="margin-bottom:8px">${esc(q)}</h3><div class="answer">${esc(r.answer)}</div>
        ${r.sources.length ? `<p class="muted" style="margin-top:10px;font-size:13px">Sources: ${r.sources.map((s, i) => `[${i + 1}] ${esc(s)}`).join(", ")}</p>` : ""}</div>`);
      $("#ask-q").value = "";
    });
  };
}

/* ======================================================================
   SETTINGS
   ====================================================================== */
async function renderSettings() {
  const seq = app.navSeq;
  const [s, models, acct, ind] = await Promise.all([api("/api/settings"), api("/api/models"), api("/api/account"), api("/api/industry")]);
  if (seq !== app.navSeq) return;
  const st = app.status || {};
  const isLocal = s.model.startsWith("ollama/");
  const cloudOptions = Object.entries(models.cloud).map(([group, list]) =>
    `<optgroup label="${esc(group)}">${list.map(m => `<option value="${esc(m.model)}" ${m.model === s.model ? "selected" : ""}>${esc(m.label)}</option>`).join("")}</optgroup>`).join("");
  const installed = models.ollama.models;
  const localOptions = installed.map(m => `<option value="${esc(m)}" ${sameModel(m, s.model) ? "selected" : ""}>${esc(m.replace("ollama/", ""))}</option>`).join("");

  view.innerHTML = `<div class="page" style="max-width:780px">
    <div class="page-head"><div><h1>Settings</h1><p>Everything is stored on this computer in ${esc(s.data_dir)}</p></div></div>

    <section class="section panel stack" id="sec-email" style="margin-top:0">
      <h2>Email account</h2>
      ${acct.connected ? `<p>Connected: <strong>${esc(acct.email)}</strong> · ${esc(acct.label)}${acct.kind === "imap" ? ` · password kept in ${esc(acct.secret_store)}` : ""}</p>
        <div class="row">${acct.kind === "gmail_api" ? "" : `<button id="a-disconnect">Disconnect</button>`}</div>`
        : `<div id="a-connect"></div>`}
    </section>

    <section class="section panel stack" id="sec-you">
      <h2>You</h2>
      <div class="grid2"><label class="field">Your name<input id="u-name" value="${esc(s.user_name)}"></label>
      <label class="field">Default tone<select id="u-tone">${["Professional", "Friendly", "Concise"].map(t => `<option ${t === s.default_tone ? "selected" : ""}>${t}</option>`).join("")}</select></label></div>
      <label class="field">Signature <span class="hint">Added to the end of every draft exactly as written.</span><textarea id="u-sig" rows="3" placeholder="Best regards,\nSam Rivera\nBrightside Home Cleaning · (512) 555-0134">${esc(s.user_signature)}</textarea></label>
      <div class="row"><button class="primary" id="save-you">Save</button></div>
      <hr style="border:0;border-top:1px solid var(--line);width:100%">
      <div class="row" style="justify-content:space-between"><div><h3>Learn my writing style</h3>
        <p class="muted" style="font-size:14px">${s.voice ? `Learned from ${s.voice.sample_count} sent emails: opens with “${esc(s.voice.greeting || "—")}”, signs off “${esc(s.voice.signoff || "—")}”, about ${s.voice.avg_words} words.` : "Reads your recent sent mail to match your greeting, sign-off and length."}</p></div>
        <button id="calibrate" data-busy="Reading sent mail…">${s.voice ? "Relearn" : "Learn my style"}</button></div>
    </section>

    <section class="section panel stack" id="sec-industry">
      <h2>Your industry</h2>
      <p class="muted" style="font-size:14px">Tells the AI how your kind of office sorts and answers mail (what's urgent, what never to promise), and adds a starter kit of saved replies. Nothing you've already written is changed.</p>
      <div class="row"><select id="ind-select" aria-label="Industry" style="width:auto">
        <option value="">General small business</option>
        ${ind.kits.map(k => `<option value="${esc(k.id)}" ${k.id === ind.industry ? "selected" : ""}>${esc(k.label)}</option>`).join("")}
      </select></div>
      <div id="ind-kit"></div>
      ${st.has_demo ? `<p class="muted" style="font-size:13px">While a sample inbox is loaded, this follows the sample business.</p>` : ""}
    </section>

    <section class="section panel stack" id="sec-ai">
      <h2>AI engine</h2>
      <div class="seg" role="group" aria-label="Where AI runs"><button data-mode="local" aria-pressed="${isLocal}">On this computer (Ollama)</button><button data-mode="cloud" aria-pressed="${!isLocal}">Cloud, with your API key</button></div>
      <div id="ai-local" ${isLocal ? "" : "hidden"} class="stack">
        ${models.ollama.running ? (installed.length ? `<label class="field">Installed model<select id="m-local">${localOptions}</select></label>` : `<div class="notice">Ollama is running but no models are installed. Run <code>ollama pull llama3.1:8b</code> in a terminal, then reload.</div>`)
          : `<div class="notice">Ollama isn't running. Install it from <a href="https://ollama.com/download" target="_blank" rel="noopener">ollama.com</a> and start it.</div>`}
        <details><summary class="muted" style="cursor:pointer">Recommended models</summary><ul style="margin:8px 0 0;padding-left:18px;font-size:14px">${models.recommended_local.map(m => `<li>${esc(m.label)} · <code>ollama pull ${esc(m.model.replace("ollama/", ""))}</code></li>`).join("")}</ul></details>
      </div>
      <div id="ai-cloud" ${isLocal ? "hidden" : ""} class="stack">
        <label class="field">Model<select id="m-cloud">${cloudOptions}<option value="__custom">Other (type a LiteLLM model id)…</option></select></label>
        <input id="m-custom" placeholder="provider/model-name" hidden>
        <label class="field">API key <span class="hint">${s.api_key_configured ? "A key is saved. Leave blank to keep it." : `Kept in ${esc(s.secret_store)}. Email text you draft is sent to this provider.`}</span><input id="m-key" type="password" autocomplete="off" placeholder="${s.api_key_configured ? "••••••••  (saved)" : "Paste your key"}"></label>
        ${s.api_key_configured ? `<button class="sm quiet danger" id="m-clear" style="justify-self:start">Remove saved key</button>` : ""}
      </div>
      <div class="row"><button class="primary" id="m-test" data-busy="Testing…">Test and use this model</button><span class="muted" id="m-result">Current: ${esc(s.model)}</span></div>
      <label class="check"><input type="checkbox" id="q-pass" ${s.quality_pass ? "checked" : ""}> Extra quality check on each draft <span class="muted">(about twice as slow on local models)</span></label>
    </section>

    <section class="section panel stack" id="sec-google">
      <h2>Google Calendar <span class="muted" style="font-weight:400;font-size:14px">optional</span></h2>
      <p class="muted" style="font-size:14px">Shows your schedule on Today and lets drafts avoid times you're already booked. Also an alternative way to connect Gmail.</p>
      ${st.google_connected ? `<p>Connected as <strong>${esc(acct.kind === "gmail_api" ? acct.email : (app.status.google_email || ""))}</strong>. Calendar ${st.calendar_enabled ? "is on" : "isn't enabled in your Google Cloud project"}.</p>
        <div class="row"><button id="g-disconnect">Disconnect Google</button></div>`
        : `${st.needs_reconnect ? `<div class="notice">Reconnect Google to grant the permissions this version needs.</div>` : ""}
        ${st.credentials_present ? `<div class="row"><button class="primary" id="g-connect" data-busy="Waiting for Google…">Connect Google</button></div>`
        : `<details><summary style="cursor:pointer">Set up Google sign-in (about 5 minutes)</summary>
          <ol style="margin:8px 0 0;padding-left:20px;font-size:14px;line-height:1.7">
            <li>Open <a href="https://console.cloud.google.com/projectcreate" target="_blank" rel="noopener">Google Cloud Console</a> and create a project.</li>
            <li>Enable the <a href="https://console.cloud.google.com/apis/library/calendar-json.googleapis.com" target="_blank" rel="noopener">Calendar API</a> (and the <a href="https://console.cloud.google.com/apis/library/gmail.googleapis.com" target="_blank" rel="noopener">Gmail API</a> if you'll read Gmail this way).</li>
            <li>OAuth consent screen: choose External, then add your own email under Test users.</li>
            <li>Credentials → Create credentials → OAuth client ID → Desktop app → Download JSON.</li>
            <li>Add that file here:</li></ol>
          <label class="drop" style="padding:14px;margin-top:8px">Choose the downloaded JSON file<input type="file" id="g-file" accept=".json,application/json" hidden></label></details>`}`}
    </section>

    <section class="section panel stack">
      <h2>Inbox</h2>
      <div class="grid2">
        <label class="field">Check for new mail<select id="i-auto">${[[0, "Only when I click"], [5, "Every 5 minutes"], [10, "Every 10 minutes"], [15, "Every 15 minutes"], [30, "Every 30 minutes"], [60, "Every hour"]].map(([v, l]) => `<option value="${v}" ${v === s.auto_check_minutes ? "selected" : ""}>${l}</option>`).join("")}</select></label>
        <label class="field">Write drafts<select id="i-mode"><option value="background" ${s.draft_mode === "background" ? "selected" : ""}>Right after sorting, urgent first</option><option value="on_open" ${s.draft_mode === "on_open" ? "selected" : ""}>Only when I open an email (fastest on slow PCs)</option></select></label>
        <label class="field">Remind me about unanswered replies after<select id="i-follow">${[1, 2, 3, 5, 7, 14].map(d => `<option value="${d}" ${d === s.followup_days ? "selected" : ""}>${plural(d, "day", "days")}</option>`).join("")}</select></label>
      </div>
      <div class="row"><button class="primary" id="save-inbox">Save</button>
      </div>
      <div class="demo-box">
        <h3>Sample inbox for demos</h3>
        <p class="muted">${st.has_demo && st.demo ? `Showing ${esc(st.demo.business)}. Your own profile, sign-off and saved replies come back when you remove it.` : "Load a fictional business to show C.A.P. to a client. Your own settings are kept."}</p>
        <div class="row demo-row">
          <select id="demo-industry" aria-label="Sample business">${demoOptions(st.demo?.id)}</select>
          <button id="demo-on">${st.has_demo ? "Switch sample" : "Load sample inbox"}</button>
          ${st.has_demo ? `<button class="quiet" id="demo-off">Remove samples</button>` : ""}
        </div>
      </div>
    </section>

    <section class="section panel stack">
      <h2>Your data</h2>
      <p class="muted" style="font-size:14px">Email text, drafts, tasks and files live in a SQLite file in the folder above. Nothing goes to C.A.P.'s makers, because there's no C.A.P. server. Gmail and Calendar talk to Google directly. If you pick a cloud AI model, the text needed for each draft goes to that provider.</p>
      <div class="row"><a class="btn" href="/api/export" download>Export my data (JSON)</a><button id="sec-summary">Summary for your security plan</button><button class="danger" id="purge">Delete email history</button></div>
    </section>
    <p class="muted" style="margin-top:20px;font-size:13px">C.A.P. ${esc(s.version)} · Keyboard: press <span class="kbd">?</span> for shortcuts</p>
  </div>`;

  if (location.hash.endsWith("/google")) $("#sec-google").scrollIntoView();
  if (location.hash.endsWith("/industry")) $("#sec-industry").scrollIntoView();
  const renderKitBox = () => {
    const key = $("#ind-select").value, kit = ind.kits.find(k => k.id === key);
    $("#ind-kit").innerHTML = !kit ? "" : `<div class="kit-box">
      <strong>${esc(kit.label)} starter kit</strong>
      <ul><li>${kit.templates} saved replies for everyday questions${kit.installed ? ` (${kit.installed} already added)` : ""}</li>
        <li>"Never promise or say" safety rules, if your profile doesn't have any yet</li>
        <li>${esc(kit.checklist.replace(/\.txt$/, ""))} in Knowledge</li></ul>
      <button class="primary" id="ind-kit-add" ${kit.installed === kit.templates ? "disabled" : ""}>${kit.installed === kit.templates ? "Starter kit added" : "Add starter kit"}</button></div>`;
    $("#ind-kit-add")?.addEventListener("click", e => withBusy(e.target, async () => {
      const r = await api("/api/industry/kit", { method: "POST", body: { industry: key } });
      toast(`Added ${plural(r.templates_added, "saved reply", "saved replies")}${r.rules_added ? ", safety rules" : ""}${r.checklist_added ? " and a checklist" : ""}`);
      await loadStatus(); renderSettings();
    }));
  };
  renderKitBox();
  $("#ind-select").onchange = async () => {
    await api("/api/industry", { method: "POST", body: { industry: $("#ind-select").value } });
    ind.industry = $("#ind-select").value; toast("Industry saved"); renderKitBox(); loadStatus();
  };
  $("#sec-summary").onclick = openSecuritySummary;
  if (location.hash.endsWith("/email")) $("#sec-email").scrollIntoView();
  if ($("#a-connect")) mountConnectEmail($("#a-connect"), async () => { await loadStatus(); renderSettings(); startCheck(); });
  $("#a-disconnect")?.addEventListener("click", e => {
    if (!confirmInline(e.target, "Click again to disconnect")) return;
    api("/api/account", { method: "DELETE" }).then(async () => { toast("Email disconnected"); await loadStatus(); renderSettings(); });
  });
  let mode = isLocal ? "local" : "cloud";
  $$("[data-mode]").forEach(b => b.onclick = () => {
    mode = b.dataset.mode; $$("[data-mode]").forEach(x => x.setAttribute("aria-pressed", x === b));
    $("#ai-local").hidden = mode !== "local"; $("#ai-cloud").hidden = mode !== "cloud";
  });
  $("#m-cloud").onchange = e => ($("#m-custom").hidden = e.target.value !== "__custom");
  if (!isLocal && !$("#m-cloud").querySelector("option[selected]")) { $("#m-cloud").value = "__custom"; $("#m-custom").hidden = false; $("#m-custom").value = s.model; }

  $("#save-you").onclick = e => withBusy(e.target, async () => {
    await api("/api/settings", { method: "POST", body: { user_name: $("#u-name").value, user_signature: $("#u-sig").value, default_tone: $("#u-tone").value } });
    app.tone = $("#u-tone").value; toast("Saved"); loadStatus();
  });
  $("#calibrate").onclick = e => withBusy(e.target, async () => {
    const r = await api("/api/voice/calibrate", { method: "POST" });
    toast(r.calibrated ? `Learned your style from ${r.sample_count} emails` : r.message, !r.calibrated);
    if (r.calibrated) renderSettings();
  });
  $("#m-test").onclick = e => withBusy(e.target, async () => {
    let model = mode === "local" ? $("#m-local")?.value : $("#m-cloud").value;
    if (model === "__custom") model = $("#m-custom").value.trim();
    if (!model) throw new Error("Pick a model first.");
    const r = await testModel(e.target, { model, api_key: mode === "cloud" ? $("#m-key").value : null });
    $("#m-result").textContent = `Working: ${r.model} replied in ${r.latency_ms} ms`;
    toast("AI engine ready"); loadStatus();
  });
  $("#m-clear")?.addEventListener("click", async () => { await api("/api/settings", { method: "POST", body: { clear_api_key: true } }); toast("Key removed"); renderSettings(); });
  $("#q-pass").onchange = e => api("/api/settings", { method: "POST", body: { quality_pass: e.target.checked } }).then(() => toast("Saved"));
  $("#g-connect")?.addEventListener("click", e => withBusy(e.target, async () => {
    await api("/api/google/connect", { method: "POST" }); await loadStatus(); toast("Google connected"); renderSettings(); startCheck();
  }));
  $("#g-disconnect")?.addEventListener("click", async () => { await api("/api/google/disconnect", { method: "POST" }); await loadStatus(); renderSettings(); });
  $("#g-file")?.addEventListener("change", async e => {
    const fd = new FormData(); fd.append("file", e.target.files[0]);
    try { await api("/api/google/credentials", { method: "POST", body: fd }); toast("Sign-in file added"); await loadStatus(); renderSettings(); }
    catch (err) { toast(err.message, true); }
  });
  $("#save-inbox").onclick = e => withBusy(e.target, async () => {
    await api("/api/settings", { method: "POST", body: { auto_check_minutes: +$("#i-auto").value, followup_days: +$("#i-follow").value, draft_mode: $("#i-mode").value } });
    toast("Saved"); loadStatus();
  });
  $("#demo-on")?.addEventListener("click", e => withBusy(e.target, async () => { await loadDemo($("#demo-industry")); renderSettings(); }));
  $("#demo-off")?.addEventListener("click", async () => { await api("/api/demo", { method: "DELETE" }); await loadStatus(); toast("Sample emails removed"); renderSettings(); });
  $("#purge").onclick = e => {
    if (!confirmInline(e.target, "Click again: deletes all local email history")) return;
    api("/api/purge-email", { method: "POST" }).then(r => { toast(`Deleted ${r.deleted} emails from this computer (Gmail is untouched)`); loadStatus(); });
  };
}

/* ---------- keyboard ---------- */
document.addEventListener("keydown", e => {
  const typing = /INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName);
  if ((e.ctrlKey || e.metaKey) && e.key === "Enter" && app.route === "inbox" && $("#send")) { e.preventDefault(); sendCurrent(); return; }
  if (typing || e.ctrlKey || e.metaKey || e.altKey || $("#dialog").open) return;
  const keys = { "1": "today", "2": "inbox", "3": "tasks", "4": "tracker", "5": "knowledge", "6": "security", "7": "settings" };
  if (keys[e.key]) { location.hash = `#${keys[e.key]}`; return; }
  if (e.key === "?") return showShortcuts();
  if (app.route !== "inbox") return;
  const idx = app.inboxItems.findIndex(i => i.gmail_id === app.selectedId);
  if (e.key === "j" || e.key === "k") {
    const next = app.inboxItems[Math.max(0, Math.min(app.inboxItems.length - 1, idx + (e.key === "j" ? 1 : -1)))];
    if (next) { app.selectedId = next.gmail_id; renderList(); renderReader(); $(`.msg[data-id="${CSS.escape(next.gmail_id)}"]`)?.scrollIntoView({ block: "nearest" }); }
  }
  if (e.key === "e" && app.current) markDone();
  if (e.key === "r" && $("#instr")) { e.preventDefault(); $("#instr").focus(); }
});

function showShortcuts() {
  const dlg = $("#dialog");
  dlg.innerHTML = `<h2>Keyboard shortcuts</h2><div class="shortcuts">
    <span class="kbd">1</span><span>Today</span><span class="kbd">2</span><span>Inbox</span><span class="kbd">3</span><span>Tasks</span>
    <span class="kbd">4</span><span>Clients / Policies</span><span class="kbd">5</span><span>Knowledge</span>
    <span class="kbd">6</span><span>Security</span><span class="kbd">7</span><span>Settings</span>
    <span class="kbd">J / K</span><span>Next / previous email</span><span class="kbd">R</span><span>Tell the AI what to change</span>
    <span class="kbd">E</span><span>Done (archive)</span><span class="kbd">Ctrl + Enter</span><span>Send reply</span></div>
    <div class="row end" style="margin-top:16px"><button id="sc-close">Close</button></div>`;
  dlg.showModal(); $("#sc-close").onclick = () => dlg.close();
}

/* The data-handling summary for a firm's written security plan (Settings and Security pages). */
async function openSecuritySummary() {
    const text = await api("/api/security-summary");
    const dlg = $("#dialog");
    dlg.innerHTML = `<h2>Summary for your security plan</h2>
      <p class="muted" style="margin-bottom:10px">How C.A.P. handles client email on this computer, written from your current settings. Add it to your written information security plan.</p>
      <pre class="sec-text">${esc(text)}</pre>
      <div class="row end" style="margin-top:14px"><button class="quiet" id="ss-close">Close</button><button id="ss-copy">Copy</button><a class="btn primary" href="/api/security-summary?download=true" download>Download .txt</a></div>`;
    dlg.showModal();
    $("#ss-close").onclick = () => dlg.close();
    $("#ss-copy").onclick = async () => { try { await navigator.clipboard.writeText(text); toast("Copied"); } catch { toast("Couldn't copy; use Download instead.", true); } };
}

/* ======================================================================
   SECURITY CENTER
   ====================================================================== */
const RETENTION_LABELS = { 0: "Keep email text until I delete it", 30: "30 days after it's handled", 90: "90 days after it's handled", 180: "6 months after it's handled", 365: "1 year after it's handled", 730: "2 years after it's handled" };

function scoreRing(score) {
  const r = 52, c = 2 * Math.PI * r, tone = score >= 80 ? "var(--green)" : score >= 50 ? "var(--amber)" : "var(--red)";
  return `<svg class="score-ring" viewBox="0 0 128 128" role="img" aria-label="Protection score ${score} out of 100">
    <circle cx="64" cy="64" r="${r}" fill="none" stroke="var(--sunk)" stroke-width="12"/>
    <circle cx="64" cy="64" r="${r}" fill="none" stroke="${tone}" stroke-width="12" stroke-linecap="round"
      stroke-dasharray="${(c * score / 100).toFixed(1)} ${c.toFixed(1)}" transform="rotate(-90 64 64)"/>
    <text x="64" y="62" text-anchor="middle" font-size="30" font-weight="700" fill="var(--ink)">${score}</text>
    <text x="64" y="84" text-anchor="middle" font-size="12" fill="var(--slate)">out of 100</text></svg>`;
}

async function renderSecurity() {
  const seq = app.navSeq;
  const [sec, lock] = await Promise.all([api("/api/security"), api("/api/lock/status")]);
  if (seq !== app.navSeq) return;
  app.lockInfo = lock; syncLockButton();
  const on = sec.checks.filter(c => c.ok).length, st = sec.settings, fraudList = sec.alerts.fraud, sens = sec.alerts.sensitive;
  $("#nav-security").textContent = fraudList.length || "";
  const control = c => ({
    ai: `<label class="switch"><input type="checkbox" id="sx-firewall" ${st.cloud_redaction ? "checked" : ""}> Mask SSNs and account numbers before any cloud AI${st.is_local_model ? " (ready if you switch)" : ""}</label>`,
    lock: lock.enabled
      ? `<div class="row"><select id="sx-idle" aria-label="Auto-lock" style="width:auto">${[5, 10, 15, 30, 60].map(m => `<option value="${m}" ${m === st.lock_idle_minutes ? "selected" : ""}>Lock after ${m} min away</option>`).join("")}</select><button class="sm" id="sx-pin-change">Change PIN</button><button class="sm quiet danger" id="sx-pin-off">Turn off</button></div>`
      : `<button class="sm primary" id="sx-pin-on">Turn on app lock</button>`,
    retention: `<select id="sx-retention" aria-label="Email text retention" style="width:auto">${Object.entries(RETENTION_LABELS).map(([d, l]) => `<option value="${d}" ${+d === st.retention_days ? "selected" : ""}>${l}</option>`).join("")}</select>`,
    mask: `<div class="row"><label class="switch"><input type="checkbox" id="sx-mask" ${st.mask_handled ? "checked" : ""}> Mask numbers once an email is handled</label>${sens.handled ? `<button class="sm" id="sx-mask-now">Mask ${plural(sens.handled, "handled email", "handled emails")} now</button>` : ""}</div>`,
    bitlocker: `<label class="switch"><input type="checkbox" id="sx-bitlocker" ${st.bitlocker_confirmed ? "checked" : ""}> BitLocker is on for this computer</label><small class="muted">Windows Settings → Privacy &amp; security → Device encryption (or BitLocker drive encryption).</small>`,
    rules: c.ok ? "" : `<a class="btn sm" href="#settings/industry">Choose your industry</a>`,
  })[c.id] || "";

  view.innerHTML = `<div class="page" style="max-width:880px">
    <div class="page-head"><div><h1>Security</h1><p>How C.A.P. protects your clients' information on this computer, and the record that proves it.</p></div></div>
    <section class="panel sec-hero">
      ${scoreRing(sec.score)}
      <div><h2>${sec.score >= 80 ? "Well protected" : sec.score >= 50 ? "Partly protected" : "Needs attention"}</h2>
        <p class="muted">${on} of ${sec.checks.length} protections are on. ${sec.checks.filter(c => !c.ok).length ? "Turn on the ones marked below to raise your score." : "Nothing left to turn on."}</p>
        <div class="row" style="margin-top:10px"><button id="sx-summary">Summary for your security plan</button><a class="btn" href="/api/audit.csv" download>Download audit log</a>${lock.enabled ? `<button id="sx-lock-now">Lock now</button>` : ""}</div></div>
    </section>

    ${fraudList.length || sens.total ? `<section class="section panel stack">
      <h2>Needs your attention</h2>
      ${fraudList.map(f => `<a class="alert-row" href="#inbox/${encodeURIComponent(f.gmail_id)}"><span class="pill urgent">${f.level === "high" ? "Likely fraud" : "Possible fraud"}</span><span><strong>${esc(f.subject)}</strong><br><small class="muted">${esc(f.sender)} · ${esc(f.reason)}</small></span></a>`).join("")}
      ${sens.total ? `<div class="alert-row"><span class="pill careful">Sensitive</span><span>${plural(sens.total, "stored email holds", "stored emails hold")} SSNs, account or card numbers${sens.handled ? `, ${sens.handled} of them already handled` : ""}.</span></div>` : ""}
    </section>` : ""}

    <section class="section panel stack">
      <h2>Protections</h2>
      ${sec.checks.map(c => `<div class="check-row ${c.ok ? "ok" : "off"}">
        <span class="check-dot" aria-hidden="true">${c.ok ? "✓" : "!"}</span>
        <div><strong>${esc(c.title)}</strong><p class="muted">${esc(c.detail)}</p>${control(c)}</div></div>`).join("")}
    </section>

    <section class="section panel stack">
      <div class="row" style="justify-content:space-between"><h2>Audit log</h2><a class="btn sm" href="/api/audit.csv" download>Download CSV</a></div>
      ${sec.audit.length ? `<table class="audit"><thead><tr><th>When</th><th>What</th><th>Details</th></tr></thead><tbody>
        ${sec.audit.map(a => `<tr><td>${esc(new Date(a.at).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }))}</td><td>${esc(a.action)}</td><td>${esc(a.detail)}</td></tr>`).join("")}</tbody></table>`
        : `<p class="muted">Nothing recorded yet.</p>`}
    </section></div>`;

  const save = async (body, msg) => { await api("/api/security/settings", { method: "POST", body }); toast(msg); renderSecurity(); };
  $("#sx-summary").onclick = openSecuritySummary;
  $("#sx-lock-now")?.addEventListener("click", lockNow);
  $("#sx-firewall").onchange = e => save({ cloud_redaction: e.target.checked }, e.target.checked ? "Privacy firewall on" : "Privacy firewall off");
  $("#sx-retention").onchange = e => save({ retention_days: +e.target.value }, "Retention saved");
  $("#sx-mask").onchange = e => save({ mask_handled: e.target.checked }, e.target.checked ? "Handled emails will be masked" : "Masking off");
  $("#sx-bitlocker").onchange = e => save({ bitlocker_confirmed: e.target.checked }, "Saved");
  $("#sx-idle")?.addEventListener("change", e => save({ lock_idle_minutes: +e.target.value }, "Auto-lock saved"));
  $("#sx-mask-now")?.addEventListener("click", e => withBusy(e.target, async () => {
    const r = await api("/api/security/mask-now", { method: "POST" }); toast(`Masked numbers in ${plural(r.masked, "email", "emails")}`); renderSecurity();
  }));
  $("#sx-pin-on")?.addEventListener("click", () => openPinDialog(false));
  $("#sx-pin-change")?.addEventListener("click", () => openPinDialog(true));
  $("#sx-pin-off")?.addEventListener("click", openPinOffDialog);
}

function openPinDialog(hasPin) {
  const dlg = $("#dialog");
  dlg.innerHTML = `<h2>${hasPin ? "Change your PIN" : "Turn on app lock"}</h2>
    <p class="muted" style="margin-bottom:12px">C.A.P. will ask for this PIN when it opens and after you've been away. Use something staff can't guess.</p>
    <div class="stack">${hasPin ? `<label class="field">Current PIN<input id="pn-cur" type="password" autocomplete="off"></label>` : ""}
      <label class="field">New PIN (4 to 32 characters)<input id="pn-new" type="password" autocomplete="new-password"></label>
      <label class="field">Type it again<input id="pn-again" type="password" autocomplete="new-password"></label></div>
    <div class="row end" style="margin-top:14px"><button class="quiet" id="pn-cancel">Cancel</button><button class="primary" id="pn-save">Save PIN</button></div>`;
  dlg.showModal();
  $("#pn-cancel").onclick = () => dlg.close();
  $("#pn-save").onclick = e => withBusy(e.target, async () => {
    if ($("#pn-new").value !== $("#pn-again").value) throw new Error("The two PINs don't match.");
    const r = await api("/api/lock/pin", { method: "POST", body: { pin: $("#pn-new").value, current_pin: $("#pn-cur")?.value || "" } });
    dlg.innerHTML = `<h2>Save your recovery code</h2>
      <p style="margin-bottom:10px">If you forget your PIN, this code is the only way back into C.A.P. Write it down and keep it somewhere safe, away from this computer.</p>
      <p class="recovery-code">${esc(r.recovery_code)}</p>
      <div class="row end" style="margin-top:14px"><button class="primary" id="pn-done">I've saved it</button></div>`;
    $("#pn-done").onclick = () => { dlg.close(); toast("App lock is on"); renderSecurity(); };
  });
}

function openPinOffDialog() {
  const dlg = $("#dialog");
  dlg.innerHTML = `<h2>Turn off app lock?</h2>
    <p class="muted" style="margin-bottom:12px">Anyone at this computer will be able to open client email in C.A.P.</p>
    <label class="field">Current PIN<input id="po-cur" type="password" autocomplete="off"></label>
    <div class="row end" style="margin-top:14px"><button class="quiet" id="po-cancel">Cancel</button><button class="danger" id="po-ok">Turn off</button></div>`;
  dlg.showModal();
  $("#po-cancel").onclick = () => dlg.close();
  $("#po-ok").onclick = e => withBusy(e.target, async () => {
    await api("/api/lock/pin", { method: "DELETE", body: { current_pin: $("#po-cur").value } });
    dlg.close(); toast("App lock is off"); renderSecurity();
  });
}

/* ---------- app lock: the server refuses data while locked; this screen asks for the PIN ---------- */
app.lastActivity = Date.now();
["mousemove", "keydown", "click", "scroll", "touchstart"].forEach(ev => addEventListener(ev, () => (app.lastActivity = Date.now()), { passive: true }));

async function refreshLockInfo() {
  try { app.lockInfo = await api("/api/lock/status"); } catch { app.lockInfo = { enabled: false, locked: false, idle_minutes: 10 }; }
  syncLockButton();
  return app.lockInfo;
}
function syncLockButton() { $("#lock-btn").hidden = !app.lockInfo?.enabled; }

async function lockNow() {
  try { await api("/api/lock", { method: "POST" }); } catch (err) { return toast(err.message, true); }
  showLock("Locked.");
}

function showLock(message) {
  if (app.locked && $("#lock-screen").innerHTML) return;
  app.locked = true;
  if ($("#dialog").open) $("#dialog").close();
  view.innerHTML = "";  // no client data stays on screen behind the lock
  const el = $("#lock-screen");
  el.hidden = false;
  el.innerHTML = `<div class="lock-card" role="dialog" aria-modal="true" aria-labelledby="lock-title">
    <span class="brand-mark" aria-hidden="true">C</span>
    <h1 id="lock-title">C.A.P. is locked</h1>
    <p class="muted">${esc(message || "Enter your PIN to continue.")}</p>
    <form id="lock-form" class="stack"><input id="lock-pin" type="password" autocomplete="off" aria-label="PIN" placeholder="PIN">
      <button class="primary" type="submit">Unlock</button></form>
    <button class="link-btn" id="lock-recovery" type="button">Forgot your PIN? Use your recovery code</button></div>`;
  $("#lock-pin").focus();
  $("#lock-form").onsubmit = async e => {
    e.preventDefault();
    try {
      const r = await api("/api/lock/unlock", { method: "POST", body: { pin: $("#lock-pin").value } });
      await hideLock();
      if (r.recovered) toast("Unlocked with your recovery code. The lock is off; set a new PIN in Security.", true);
    } catch (err) { $("#lock-pin").value = ""; toast(err.message, true); }
  };
  $("#lock-recovery").onclick = () => { const i = $("#lock-pin"); i.type = "text"; i.placeholder = "Recovery code, like 1A2B-3C4D-5E6F-7A8B"; i.focus(); };
}

async function hideLock() {
  app.locked = false; app.lastActivity = Date.now();
  $("#lock-screen").hidden = true; $("#lock-screen").innerHTML = "";
  await refreshLockInfo();
  if (!app.booted) return bootRest();
  await loadStatus(); go();
}

setInterval(async () => {
  if (!app.lockInfo?.enabled || app.locked) return;
  if (Date.now() - app.lastActivity > (app.lockInfo.idle_minutes || 10) * 60000) {
    try { await api("/api/lock", { method: "POST" }); } catch {}
    showLock("Locked after time away.");
  }
}, 20000);

document.addEventListener("click", e => {
  const id = e.target?.dataset?.verify;
  if (!id) return;
  withBusy(e.target, async () => {
    await api(`/api/email/${encodeURIComponent(id)}/verify-task`, { method: "POST" });
    toast("Task added: verify by phone before acting"); e.target.textContent = "Verification task added"; e.target.disabled = true;
    api("/api/tasks").then(t => ($("#nav-tasks").textContent = t.filter(x => !x.completed).length || "")).catch(() => {});
  });
});
$("#lock-btn").onclick = lockNow;

/* ======================================================================
   TRACKER: client returns (tax) · certificates and renewals (insurance)
   ====================================================================== */
const TRACKER_NAV = { tax: "Clients", insurance: "Policies" };
let trackerFilter = "open";

function daysChip(days) {
  if (days === null || days === undefined) return "";
  const cls = days < 0 ? "urgent" : days <= 3 ? "urgent" : days <= 14 ? "careful" : "";
  const text = days < 0 ? `${plural(-days, "day", "days")} overdue` : days === 0 ? "today" : days === 1 ? "tomorrow" : `in ${days} days`;
  return `<span class="pill ${cls}">${text}</span>`;
}
const fmtDay = iso => iso ? new Date(iso + "T12:00:00").toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" }) : "";

function keyDatesHtml(k, compact) {
  if (!k || !k.industry) return "";
  if (k.industry === "tax") {
    const counts = Object.entries(k.counts || {}).filter(([, n]) => n).map(([l, n]) => `${n} ${l.toLowerCase()}`).join(" · ");
    return `${k.deadlines.length ? k.deadlines.map(d => `<div class="kd-row"><div><strong>${esc(fmtDay(d.date))}</strong> <span class="muted">${d.client_name ? `${esc(d.client_name)}: ` : ""}${esc(d.label)}</span></div>${daysChip(d.days_left)}</div>`).join("")
        : `<div class="kd-row muted">Nothing due in the next 120 days.</div>`}
      ${counts ? `<div class="kd-row muted" style="font-size:13px">Clients: ${esc(counts)}</div>` : ""}
      ${compact ? k.items.map(i => `<a class="kd-row" href="#tracker"><span>${esc(i.name)}</span><span class="muted">${esc(i.detail)}</span></a>`).join("") : ""}
      <p class="muted kd-note">Federal dates are worked out each year. Add state deadlines and any IRS disaster-relief postponements as your own dates.</p>`;
  }
  return k.items.length ? k.items.map(i => `<a class="kd-row" href="#tracker"><div><strong>${esc(i.detail)}</strong><br><small class="muted">${esc(i.name)}</small></div>${daysChip(i.days_left)}</a>`).join("")
    : `<div class="empty">No certificates due this week and no renewals in the next 60 days.</div>`;
}

async function renderTracker() {
  const seq = app.navSeq;
  const t = await api("/api/tracker");
  if (seq !== app.navSeq) return;
  app.trackerMeta = t;
  if (!t.industry) {
    view.innerHTML = `<div class="page"><div class="page-head"><div><h1>Tracker</h1><p>Client returns for tax firms, certificates and renewals for insurance agencies.</p></div></div>
      <div class="empty panel"><strong>Choose your industry first.</strong>The tracker fits the way your kind of office works. <a href="#settings/industry">Pick your industry in Settings</a>.</div></div>`;
    return;
  }
  const tax = t.industry === "tax";
  const items = t.items;
  const open = items.filter(i => i.open);
  view.innerHTML = `<div class="page" style="max-width:980px">
    <div class="page-head"><div><h1>${tax ? "Clients" : "Certificates & renewals"}</h1>
      <p>${tax ? "Every client's return, what they still owe you, and a reminder that lists it." : "Certificate requests with due dates, and renewals coming up, so nothing slips."}</p></div>
      <div class="row">${tax ? `<button class="primary" data-new="client">Add client</button>` : `<button data-new="renewal">Add renewal</button><button class="primary" data-new="coi">Add certificate request</button>`}</div></div>
    ${tax ? `<section class="panel kd-panel"><h2>Key dates</h2>${keyDatesHtml(t.key_dates)}${yourDatesHtml(t.custom_dates)}</section>` : ""}
    ${tax ? taxTrackerHtml(items) : insuranceTrackerHtml(items)}
  </div>`;
  $("#nav-tracker").textContent = open.length || "";
  view.onclick = trackerClick;
  $$("[data-status-for]").forEach(sel => sel.onchange = async () => {
    await api(`/api/tracker/${sel.dataset.statusFor}`, { method: "PUT", body: { status: sel.value } });
    toast("Status updated"); renderTracker();
  });
}

const repeatNote = d => d.yearly ? ' <small class="muted">· every year</small>' : "";
const dateActions = d => `<button class="sm quiet" data-edit-date="${d.id}">Edit</button><button class="sm quiet danger" data-del-date="${d.id}">Remove</button>`;

function yourDatesHtml(dates) {
  const firm = (dates || []).filter(d => !d.client_id);
  return `<div class="kd-sub"><div class="section-head"><h3>Your dates</h3><button class="sm" data-new-date="">Add date</button></div>
    ${firm.length ? firm.map(d => `<div class="kd-row ${d.days_left < 0 ? "kd-past" : ""}"><div><strong>${esc(fmtDay(d.date))}</strong> <span class="muted">${esc(d.label)}</span>${repeatNote(d)}</div>
      <div class="row" style="flex-wrap:nowrap">${d.days_left >= 0 ? daysChip(d.days_left) : ""}${dateActions(d)}</div></div>`).join("")
      : `<div class="kd-row muted" style="font-size:13px">State deadlines, office closures, anything the whole firm works to. Dates for one client go on that client's timeline below.</div>`}</div>`;
}

function clientTimelineHtml(c) {
  const rows = (app.trackerMeta.custom_dates || []).filter(d => d.client_id === c.id).map(d => ({ ...d, custom: true }));
  if (c.due_at) rows.push({ date: c.due_at.slice(0, 10), label: "Return due", days_left: c.days_left });
  rows.sort((a, b) => a.date.localeCompare(b.date));
  return `<div class="tr-timeline"><div class="row" style="justify-content:space-between"><small class="muted">Timeline</small><button class="sm quiet" data-new-date="${c.id}">Add date</button></div>
    ${rows.length ? rows.map(d => `<div class="kd-row ${d.days_left < 0 ? "kd-past" : ""}"><div><strong>${esc(fmtDay(d.date))}</strong> <span class="muted">${esc(d.label)}</span>${repeatNote(d)}</div>
      <div class="row" style="flex-wrap:nowrap">${d.days_left >= 0 && c.open ? daysChip(d.days_left) : ""}${d.custom ? dateActions(d) : ""}</div></div>`).join("")
      : `<div class="kd-row muted" style="font-size:13px">No dates yet. Add appointments, a state return deadline, or when you promised the return.</div>`}</div>`;
}

function openDateDialog(d) {
  const dlg = $("#dialog"), isNew = !d.id;
  const clients = (app.trackerMeta.items || []).filter(i => i.kind === "client");
  dlg.innerHTML = `<h2>${isNew ? "Add a date" : "Edit date"}</h2><div class="stack">
    <div class="grid2"><label class="field">Date<input id="kd-date" type="date" value="${esc(d.set_date || "")}"></label>
      <label class="field">For<select id="kd-client"><option value="">The whole firm</option>${clients.map(c => `<option value="${c.id}" ${c.id === d.client_id ? "selected" : ""}>${esc(c.name)}</option>`).join("")}</select></label></div>
    <label class="field">What's due<input id="kd-label" value="${esc(d.label || "")}" placeholder="State individual returns due"></label>
    <label class="switch"><input type="checkbox" id="kd-yearly" ${d.yearly ? "checked" : ""}> Repeats every year on this date</label></div>
    <div class="row end" style="margin-top:14px"><button class="quiet" id="kd-cancel">Cancel</button><button class="primary" id="kd-save">Save</button></div>`;
  dlg.showModal();
  $("#kd-cancel").onclick = () => dlg.close();
  $("#kd-save").onclick = e => withBusy(e.target, async () => {
    const body = { date: $("#kd-date").value, label: $("#kd-label").value, client_id: $("#kd-client").value ? +$("#kd-client").value : null, yearly: $("#kd-yearly").checked };
    if (isNew) await api("/api/key-dates", { method: "POST", body }); else await api(`/api/key-dates/${d.id}`, { method: "PUT", body });
    dlg.close(); toast(isNew ? "Date added" : "Date saved"); renderTracker();
  });
}

function taxTrackerHtml(items) {
  const statuses = app.trackerMeta.kinds.client.statuses;
  const shown = items.filter(i => trackerFilter === "all" ? true : trackerFilter === "open" ? i.open : i.status === trackerFilter);
  const tabs = [["open", "Open"], ...statuses, ["all", "All"]].map(([k, l]) => {
    const n = k === "all" ? items.length : k === "open" ? items.filter(i => i.open).length : items.filter(i => i.status === k).length;
    return `<button role="tab" data-filter="${k}" aria-selected="${k === trackerFilter}">${esc(l)} <small>${n}</small></button>`;
  }).join("");
  return `<div class="tabs" role="tablist" style="margin-top:20px">${tabs}</div>
    <div class="stack" style="margin-top:12px">${shown.length ? shown.map(c => {
      const docs = c.data.docs || [], got = docs.filter(d => d.received).length;
      return `<article class="panel tr-card">
        <div class="tr-head"><div><h3>${esc(c.name)}</h3><small class="muted">${esc([c.data.return_type, c.data.tax_year && `Tax year ${c.data.tax_year}`, c.email].filter(Boolean).join(" · "))}</small></div>
          <select data-status-for="${c.id}" aria-label="Status" style="width:auto">${statuses.map(([k, l]) => `<option value="${k}" ${k === c.status ? "selected" : ""}>${esc(l)}</option>`).join("")}</select></div>
        ${docs.length ? `<div class="tr-progress" aria-label="${got} of ${docs.length} documents received"><span style="width:${Math.round(100 * got / docs.length)}%"></span></div>
          <div class="tr-docs"><small class="muted">${got} of ${docs.length} documents received. Click one to mark it.</small>
          <div class="chips">${docs.map(d => `<button class="chip ${d.received ? "got" : ""}" data-doc="${c.id}" data-doc-name="${esc(d.name)}" data-received="${d.received ? 1 : 0}">${d.received ? "✓ " : ""}${esc(d.name)}</button>`).join("")}</div></div>` : ""}
        ${clientTimelineHtml(c)}
        <div class="row tr-actions">${c.docs_outstanding.length && c.email ? `<button class="sm" data-remind="${c.id}">Reminder for ${plural(c.docs_outstanding.length, "missing document", "missing documents")}</button>` : ""}
          <button class="sm quiet" data-edit-item="${c.id}">Edit</button><button class="sm quiet danger" data-del-item="${c.id}">Remove</button>
          ${c.is_demo ? '<span class="pill demo">Sample</span>' : ""}</div>
      </article>`;
    }).join("") : `<div class="empty panel"><strong>No clients here.</strong>Add a client, or open a client's email and choose "Track this client's return".</div>`}</div>`;
}

function insuranceTrackerHtml(items) {
  const cois = items.filter(i => i.kind === "coi"), renewals = items.filter(i => i.kind === "renewal");
  const sel = (i, kind) => `<select data-status-for="${i.id}" aria-label="Status" style="width:auto">${app.trackerMeta.kinds[kind].statuses.map(([k, l]) => `<option value="${k}" ${k === i.status ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>`;
  const actions = i => `<button class="sm quiet" data-edit-item="${i.id}">Edit</button><button class="sm quiet danger" data-del-item="${i.id}">Remove</button>`;
  const sample = i => i.is_demo ? ' <span class="pill demo">Sample</span>' : "";
  return `<section class="section"><div class="section-head"><h2>Certificate requests</h2></div>
    <div class="panel list">${cois.length ? cois.map(i => `<div class="tr-row">
      <div><strong>${esc(i.data.holder || "Certificate")}</strong>${i.data.additional_insured ? ' <span class="pill">Additional insured</span>' : ""}${sample(i)}<br><small class="muted">Requested by ${esc(i.name)}${i.due_at ? ` · due ${esc(fmtDay(i.due_at))}` : ""}</small></div>
      <div class="row">${i.open ? daysChip(i.days_left) : ""}${sel(i, "coi")}${actions(i)}</div></div>`).join("")
      : `<div class="empty">No certificate requests. Open an email asking for a certificate and choose "Track this certificate request".</div>`}</div></section>
    <section class="section"><div class="section-head"><h2>Renewals</h2></div>
    <div class="panel list">${renewals.length ? renewals.map(i => `<div class="tr-row">
      <div><strong>${esc(i.name)}</strong> · ${esc(i.data.policy || "Policy")}${i.data.premium_change ? ` <span class="pill careful">${esc(i.data.premium_change)}</span>` : ""}${sample(i)}<br>
        <small class="muted">${i.due_at ? `Renews ${esc(fmtDay(i.due_at))}` : "No renewal date"}${i.data.carrier ? ` · ${esc(i.data.carrier)}` : ""}</small></div>
      <div class="row">${i.open ? daysChip(i.days_left) : ""}${i.email && i.open ? `<button class="sm" data-remind="${i.id}">Check-in email</button>` : ""}${sel(i, "renewal")}${actions(i)}</div></div>`).join("")
      : `<div class="empty">No renewals tracked yet.</div>`}</div></section>`;
}

async function trackerClick(e) {
  const b = e.target.closest("button"); if (!b) return;
  const d = b.dataset;
  if (d.filter) { trackerFilter = d.filter; return renderTracker(); }
  if (d.new) return openTrackerDialog({ kind: d.new });
  if (d.editItem) return openTrackerDialog(app.trackerMeta.items.find(i => String(i.id) === d.editItem));
  if (d.delItem) {
    if (!confirmInline(b, "Click again to remove")) return;
    await api(`/api/tracker/${d.delItem}`, { method: "DELETE" }); toast("Removed"); return renderTracker();
  }
  if (d.doc) {
    await api(`/api/tracker/${d.doc}/docs`, { method: "POST", body: { names: [d.docName], received: d.received !== "1" } });
    return renderTracker();
  }
  if (d.remind) return openReminder(d.remind);
  if (d.newDate !== undefined) return openDateDialog({ client_id: d.newDate ? +d.newDate : null });
  if (d.editDate) return openDateDialog(app.trackerMeta.custom_dates.find(x => String(x.id) === d.editDate));
  if (d.delDate) {
    if (!confirmInline(b, "Click again to remove")) return;
    await api(`/api/key-dates/${d.delDate}`, { method: "DELETE" }); toast("Date removed"); return renderTracker();
  }
}

async function openReminder(id) {
  const r = await api(`/api/tracker/${id}/reminder`);
  const dlg = $("#dialog");
  dlg.innerHTML = `<h2>${esc(r.subject)}</h2>
    <p class="muted" style="margin-bottom:10px">To ${esc(r.to || "")}. Review it, then copy it into a new email or open it in your mail app.</p>
    <textarea id="rm-text" style="min-height:260px">${esc(r.text)}</textarea>
    <div class="row end" style="margin-top:14px"><button class="quiet" id="rm-close">Close</button><button id="rm-copy">Copy</button><a class="btn primary" id="rm-open" href="${esc(r.mailto)}">Open in email app</a></div>`;
  dlg.showModal();
  $("#rm-close").onclick = () => dlg.close();
  $("#rm-copy").onclick = async () => { try { await navigator.clipboard.writeText($("#rm-text").value); toast("Copied"); } catch { toast("Couldn't copy. Select the text and copy it.", true); } };
  $("#rm-text").oninput = () => { $("#rm-open").href = `mailto:${encodeURIComponent(r.to || "")}?subject=${encodeURIComponent(r.subject)}&body=${encodeURIComponent($("#rm-text").value)}`; };
}

async function openTrackerDialog(item, afterSave) {
  const meta = app.trackerMeta || await api("/api/tracker"); app.trackerMeta = meta;
  const kind = item.kind, data = item.data || {}, isNew = !item.id;
  const statuses = (meta.kinds[kind] || {}).statuses || [];
  const docsNow = new Set((data.docs || meta.default_docs.map(n => ({ name: n }))).map(x => x.name));
  const title = { client: isNew ? "Track a client's return" : "Edit client", coi: isNew ? "Track a certificate request" : "Edit certificate request", renewal: isNew ? "Track a renewal" : "Edit renewal" }[kind];
  const dlg = $("#dialog");
  dlg.innerHTML = `<h2>${title}</h2><div class="stack">
    <div class="grid2"><label class="field">${kind === "client" ? "Client name" : kind === "coi" ? "Requested by" : "Client"}<input id="ti-name" value="${esc(item.name || "")}"></label>
      <label class="field">Email<input id="ti-email" value="${esc(item.email || "")}"></label></div>
    <div class="grid2"><label class="field">Status<select id="ti-status">${statuses.map(([k, l]) => `<option value="${k}" ${k === item.status ? "selected" : ""}>${esc(l)}</option>`).join("")}</select></label>
      <label class="field">${kind === "renewal" ? "Renewal date" : kind === "coi" ? "Due" : "Due (optional)"}<input id="ti-due" type="date" value="${esc((item.due_at || "").slice(0, 10))}"></label></div>
    ${kind === "client" ? `<div class="grid2"><label class="field">Return type<input id="ti-type" value="${esc(data.return_type || "Form 1040")}"></label>
        <label class="field">Tax year<input id="ti-year" value="${esc(data.tax_year || String(new Date().getFullYear() - (new Date().getMonth() < 9 ? 1 : 0)))}"></label></div>
      <fieldset class="doc-pick"><legend>Documents this client needs to send</legend>${meta.doc_names.map(n => `<label class="switch"><input type="checkbox" value="${esc(n)}" ${docsNow.has(n) ? "checked" : ""}> ${esc(n)}</label>`).join("")}</fieldset>` : ""}
    ${kind === "coi" ? `<label class="field">Certificate holder<input id="ti-holder" value="${esc(data.holder || "")}" placeholder="Hillcrest Builders"></label>
      <label class="switch"><input type="checkbox" id="ti-ai" ${data.additional_insured ? "checked" : ""}> Wants to be listed as additional insured</label>` : ""}
    ${kind === "renewal" ? `<div class="grid2"><label class="field">Policy<input id="ti-policy" value="${esc(data.policy || "")}" placeholder="Homeowners policy"></label>
      <label class="field">Carrier<input id="ti-carrier" value="${esc(data.carrier || "")}"></label></div>
      <label class="field">Premium change at renewal<input id="ti-change" value="${esc(data.premium_change || "")}" placeholder="+12%"></label>` : ""}
  </div>
  <div class="row end" style="margin-top:14px"><button class="quiet" id="ti-cancel">Cancel</button><button class="primary" id="ti-save">Save</button></div>`;
  dlg.showModal();
  $("#ti-cancel").onclick = () => dlg.close();
  $("#ti-save").onclick = e => withBusy(e.target, async () => {
    const body = { kind, name: $("#ti-name").value, email: $("#ti-email").value, status: $("#ti-status").value, due_at: $("#ti-due").value || "", source_email: item.source_email };
    if (kind === "client") {
      const received = new Set((data.docs || []).filter(x => x.received).map(x => x.name));
      body.data = { return_type: $("#ti-type").value, tax_year: $("#ti-year").value,
        docs: $$(".doc-pick input:checked").map(i => ({ name: i.value, received: received.has(i.value) })) };
    }
    if (kind === "coi") body.data = { ...data, holder: $("#ti-holder").value, additional_insured: $("#ti-ai").checked };
    if (kind === "renewal") body.data = { ...data, policy: $("#ti-policy").value, carrier: $("#ti-carrier").value, premium_change: $("#ti-change").value };
    if (isNew) await api("/api/tracker", { method: "POST", body }); else await api(`/api/tracker/${item.id}`, { method: "PUT", body });
    dlg.close(); toast(isNew ? "Now tracking it" : "Saved");
    if (afterSave) afterSave(); else renderTracker();
  });
}

function trackerReaderHtml(m) {
  const t = m.tracker; if (!t) return "";
  const parts = [];
  t.tracked.forEach(x => parts.push(`<div><strong>Tracked:</strong> <a href="#tracker">${esc(x.name)}</a> · ${esc(x.status_label)}${x.docs_outstanding && x.docs_outstanding.length ? ` · still owes ${esc(x.docs_outstanding.join(", "))}` : ""}${x.due_at ? ` · due ${esc(fmtDay(x.due_at))}` : ""}</div>`));
  if (t.docs) parts.push(`<div class="row" style="justify-content:space-between"><span>${esc(t.docs.client)} says these were sent: <strong>${esc(t.docs.names.join(", "))}</strong></span><button class="sm primary" data-docs-received="${t.docs.client_id}">Mark received</button></div>`);
  t.offers.forEach((o, i) => parts.push(`<div class="row" style="justify-content:space-between"><span class="muted">${o.kind === "client" ? "Keep track of this client's documents and status." : o.kind === "coi" ? `Certificate request${o.prefill.due_at ? `, due ${esc(fmtDay(o.prefill.due_at))}` : ""}.` : "Renewal mentioned in this email."}</span><button class="sm" data-track-offer="${i}">${esc(o.label)}</button></div>`));
  return parts.length ? `<div class="tr-reader">${parts.join("")}</div>` : "";
}

document.addEventListener("click", async e => {
  const b = e.target.closest?.("button"); if (!b || !app.current?.tracker) return;
  const t = app.current.tracker;
  if (b.dataset.docsReceived) {
    await withBusy(b, async () => {
      await api(`/api/tracker/${b.dataset.docsReceived}/docs`, { method: "POST", body: { names: t.docs.names, received: true } });
      toast(`Marked ${t.docs.names.join(", ")} received`); b.textContent = "Marked received"; b.disabled = true;
    });
  }
  if (b.dataset.trackOffer !== undefined) {
    const o = t.offers[+b.dataset.trackOffer];
    openTrackerDialog({ kind: o.kind, ...o.prefill, source_email: app.current.gmail_id }, () => { b.textContent = "Tracking"; b.disabled = true; refreshTrackerNav(); });
  }
});

async function refreshTrackerNav() {
  const ind = app.status?.industry;
  const link = $("#nav-tracker-link");
  link.hidden = !ind;
  $("#nav-tracker-label").textContent = TRACKER_NAV[ind] || "Tracker";
  if (!ind) return;
  try { const t = await api("/api/tracker"); $("#nav-tracker").textContent = t.items.filter(i => i.open).length || ""; } catch {}
}

/* ---------- theme & boot ---------- */
function syncThemeButton() {
  const dark = document.documentElement.dataset.theme === "dark" || (!document.documentElement.dataset.theme && matchMedia("(prefers-color-scheme: dark)").matches);
  $("#theme-btn").textContent = dark ? "Light mode" : "Dark mode";
  return dark;
}
$("#theme-btn").onclick = () => {
  const next = syncThemeButton() ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem("cap-theme", next); } catch (e) {}
  syncThemeButton();
};
$("#check-btn").onclick = () => startCheck();

async function bootRest() {
  app.booted = true;
  await loadStatus();
  try { app.tone = (await api("/api/settings")).default_tone; } catch {}
  api("/api/tasks").then(t => ($("#nav-tasks").textContent = t.filter(x => !x.completed).length || "")).catch(() => {});
  go();
  if (app.status?.connected && app.status.auto_check_minutes && !app.status.job.running) startCheck(true);
}

(async function boot() {
  syncThemeButton();
  const lock = await refreshLockInfo();
  if (lock.locked) return showLock();
  bootRest();
})();
