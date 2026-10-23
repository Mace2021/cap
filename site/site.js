"use strict";
/* C.A.P. product site: tabs, the product tour, and download links from the latest GitHub release. */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const REPO = (() => {
  const v = $('meta[name="cap-repo"]')?.content || "";
  return /^[\w.-]+\/[\w.-]+$/.test(v) && v !== "OWNER/REPO" ? v : null;
})();
const OS_NAMES = { windows: "Windows", mac: "macOS", linux: "Linux" };

function detectOS() {
  const p = (navigator.userAgentData?.platform || navigator.platform || navigator.userAgent || "").toLowerCase();
  const ua = navigator.userAgent.toLowerCase();
  if (/android|iphone|ipad|ipod/.test(ua)) return "mobile";
  if (p.includes("mac")) return "mac";
  if (p.includes("win")) return "windows";
  if (p.includes("linux") || p.includes("x11")) return "linux";
  return "windows";
}
const myOS = detectOS();

/* ---------- tabs ---------- */
const TABS = ["home", "about", "download", "setup", "privacy", "feedback"];
const TITLES = { home: "C.A.P. Inbox Assistant", about: "About · C.A.P.", download: "Download · C.A.P.", setup: "Setup · C.A.P.", privacy: "Privacy & security · C.A.P.", feedback: "Help & feedback · C.A.P." };

function route() {
  const hash = location.hash.slice(1);
  const tab = TABS.includes(hash) ? hash : "home";
  $$(".tab").forEach(s => { s.hidden = s.id !== `tab-${tab}`; });
  $$(".tabs a").forEach(a => {
    if (a.dataset.tab === tab) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  });
  document.title = TITLES[tab];
  const fab = $("#fb-fab");  // may be missing for a few minutes after a deploy, while an older page is cached
  if (fab) fab.hidden = tab === "feedback";
  if (hash && !TABS.includes(hash) && document.getElementById(hash)) {
    document.getElementById(hash).scrollIntoView();  // e.g. #tour on the home tab
  } else {
    window.scrollTo(0, 0);
    if (hash) $("#main").focus({ preventScroll: true });
  }
}
window.addEventListener("hashchange", route);

/* ---------- visit and download counts (GoatCounter, cookie-free) ---------- */
// Sends one tiny anonymous count per page view or download click: the page, the linking site and the screen width.
// Nothing is counted on a local preview or when the browser asks not to be tracked.
const COUNTER = (() => {
  const code = $('meta[name="goatcounter"]')?.content || "";
  const off = !/^[a-z0-9-]+$/.test(code) || !/\.github\.io$/.test(location.hostname)
    || navigator.doNotTrack === "1" || window.doNotTrack === "1" || navigator.globalPrivacyControl === true;
  return off ? null : `https://${code}.goatcounter.com/count`;
})();
let lastCounted = "";

function count(path, title, { event = false, referrer = "" } = {}) {
  if (!COUNTER) return;
  const params = new URLSearchParams({ p: path, t: title, s: `${screen.width},${screen.height},${devicePixelRatio || 1}`,
                                       rnd: Math.random().toString(36).slice(2) });
  if (event) params.set("e", "true");
  if (referrer) params.set("r", referrer);
  new Image().src = `${COUNTER}?${params}`;
}

function countPage() {
  const tab = TABS.includes(location.hash.slice(1)) ? location.hash.slice(1) : "home";
  if (tab === lastCounted) return;  // #tour and similar in-page links stay on the same tab
  const first = !lastCounted;
  lastCounted = tab;
  // Only the first view carries the outside site that sent the visitor; tab changes are within this site.
  const ref = first && document.referrer && !document.referrer.startsWith(location.origin) ? document.referrer : "";
  count(`/${tab}`, TITLES[tab], { referrer: ref });
}
window.addEventListener("hashchange", countPage);

/* ---------- OS switches (setup steps, checksum commands) ---------- */
function bindSwitch(attr) {
  const buttons = $$(`[data-${attr}]`).filter(b => b.tagName === "BUTTON");
  const show = os => {
    buttons.forEach(b => b.setAttribute("aria-selected", String(b.dataset[attr] === os)));
    $$(`[data-${attr}-panel]`).forEach(p => { p.hidden = p.dataset[`${attr}Panel`] !== os; });
  };
  buttons.forEach(b => b.addEventListener("click", () => show(b.dataset[attr])));
  show(OS_NAMES[myOS] ? myOS : "windows");
}

/* ---------- product tour ---------- */
const TOUR = [
  { img: "today.png", title: "Your day at a glance",
    text: "<p>Today opens on what matters: how many emails need you, who hasn't replied to you, tasks due, and the key dates coming up.</p>",
    marks: [[26.2, 4.4, 28.6, 12.2], [64.6, 24.2, 24.2, 44.1]] },
  { img: "today.png", title: "Fraud is flagged before you reply",
    text: "<p>This email looks like it's from a client and asks to send her refund to a new bank account. The sender's address is one letter off, so C.A.P. marks it <strong>Likely fraud</strong> and the draft won't act on it.</p>",
    marks: [[26.4, 47.5, 36.1, 8]] },
  { img: "inbox.png", title: "A draft in your voice, from your facts",
    text: "<p>Kevin says he uploaded his documents. C.A.P. knows he's a tracked client and offers to mark them received (1).</p><p>The draft (2) quotes your real turnaround time and avoids promising a refund amount. Change the tone with one click, then send.</p>",
    marks: [[42.5, 10.2, 47.8, 8.7], [42.5, 57.1, 47.8, 35.3]] },
  { img: "clients.png", title: "Clients and key dates",
    text: "<p>Federal deadlines are worked out every year, and your own state and client dates sit beside them (1).</p><p>Each client card shows the documents still owed and a timeline (2), with a reminder email that lists exactly what's missing.</p>",
    marks: [[26.25, 13.8, 62.5, 38.4], [26.25, 79.4, 62.5, 7.8]] },
  { img: "knowledge.png", title: "The only facts it uses",
    text: "<p>Your prices, hours, policies, links and common questions live in Knowledge, with your saved replies and files. Drafts quote these, and every fact in a draft is checked against them.</p>",
    marks: [[26.25, 20, 50, 78]] },
  { img: "security.png", title: "Security you can see",
    text: "<p>A protection score shows what's on (1), and anything that needs attention, like impersonation attempts and emails holding Social Security numbers, is listed for you (2).</p>",
    marks: [[29.7, 13.8, 55.6, 18.7], [29.7, 36.5, 55.6, 35]] },
  { img: "industry.png", title: "Ready for your industry",
    text: "<p>Choose tax &amp; bookkeeping or insurance and add the starter kit: 14 saved replies, \"never promise\" safety rules and a document checklist, without touching anything you've written.</p><p><a href=\"#download\">Download C.A.P.</a> and try it with a sample inbox.</p>",
    marks: [[34.7, 20.2, 45.6, 18.4]] },
];
let step = 0;

function showStep(i) {
  step = (i + TOUR.length) % TOUR.length;
  const s = TOUR[step];
  const img = $("#tour-img");
  img.src = `assets/${s.img}`;
  img.alt = `${s.title}: screenshot of C.A.P.`;
  $("#tour-count").textContent = `Step ${step + 1} of ${TOUR.length}`;
  $("#tour-title").textContent = s.title;
  $("#tour-text").innerHTML = s.text;  // fixed text written above, never from outside data
  const marks = $("#tour-marks");
  marks.replaceChildren(...s.marks.map(([x, y, w, h], n) => {
    const m = document.createElement("div");
    m.className = "tour-mark";
    Object.assign(m.style, { left: `${x}%`, top: `${y}%`, width: `${w}%`, height: `${h}%` });
    if (s.marks.length > 1) { const b = document.createElement("span"); b.textContent = n + 1; m.append(b); }
    return m;
  }));
  $$("#tour-dots button").forEach((d, n) => d.setAttribute("aria-selected", String(n === step)));
  $("#tour-prev").disabled = step === 0;
  $("#tour-next").textContent = step === TOUR.length - 1 ? "Start again" : "Next →";
}

function initTour() {
  $("#tour-dots").replaceChildren(...TOUR.map((s, n) => {
    const d = document.createElement("button");
    d.type = "button"; d.setAttribute("role", "tab"); d.setAttribute("aria-label", `Step ${n + 1}: ${s.title}`);
    d.addEventListener("click", () => showStep(n));
    return d;
  }));
  $("#tour-prev").addEventListener("click", () => showStep(step - 1));
  $("#tour-next").addEventListener("click", () => showStep(step + 1));
  $(".tour").addEventListener("keydown", e => {
    if (e.key === "ArrowRight") { e.preventDefault(); showStep(step + 1); }
    if (e.key === "ArrowLeft") { e.preventDefault(); showStep(step - 1); }
  });
  TOUR.forEach(s => { const i = new Image(); i.src = `assets/${s.img}`; });  // no flash between steps
  showStep(0);
}

/* ---------- downloads ---------- */
const osOf = name => /windows/i.test(name) ? "windows" : /mac/i.test(name) ? "mac" : /linux/i.test(name) ? "linux" : null;
const mb = bytes => bytes ? `${Math.round(bytes / 1048576)} MB` : "";

async function latestRelease() {
  if (REPO) {
    try {
      const r = await fetch(`https://api.github.com/repos/${REPO}/releases/latest`, { headers: { Accept: "application/vnd.github+json" } });
      if (r.ok) {
        const rel = await r.json();
        return { source: "github", version: rel.tag_name.replace(/^v/, ""), page: rel.html_url, published: rel.published_at,
                 assets: rel.assets.map(a => ({ name: a.name, size: a.size, url: a.browser_download_url,
                                                sha256: (a.digest || "").replace(/^sha256:/, "") })) };
      }
    } catch { /* offline or rate-limited: fall back to the copy published with the site */ }
  }
  const r = await fetch("release.json", { cache: "no-cache" });
  const rel = await r.json();
  return { source: "file", version: rel.version, published: rel.published,
           page: REPO ? `https://github.com/${REPO}/releases/tag/${rel.tag}` : null,
           assets: rel.assets.map(a => ({ ...a, url: REPO ? `https://github.com/${REPO}/releases/download/${rel.tag}/${a.name}` : null })) };
}

async function initDownloads() {
  const list = $("#dl-list");
  let rel;
  try { rel = await latestRelease(); } catch {
    list.innerHTML = `<p class="muted">Couldn't load the release list. ${REPO ? `See <a href="https://github.com/${esc(REPO)}/releases">all releases on GitHub</a>.` : ""}</p>`;
    return;
  }
  const byOS = {};
  rel.assets.filter(a => !/\.sha256$|SHA256SUMS/i.test(a.name)).forEach(a => { const os = osOf(a.name); if (os && !byOS[os]) byOS[os] = a; });

  $$("[data-version]").forEach(e => { e.textContent = rel.version; });
  $$("[data-file]").forEach(e => { const a = byOS[e.dataset.file]; if (a) e.textContent = a.name; });
  const releasesUrl = REPO ? `https://github.com/${REPO}/releases` : null;
  $$("[data-releases-link]").forEach(a => { if (releasesUrl) a.href = releasesUrl; });
  $$("[data-security-link]").forEach(a => { if (REPO) a.href = `https://github.com/${REPO}/security/advisories/new`; });
  $("[data-version-line]").textContent = `Version ${rel.version} · Windows · macOS · Linux`;

  const mine = byOS[myOS];
  if (mine?.url) {
    $("#dl-main").innerHTML = `<a class="btn primary" href="${esc(mine.url)}">Download for ${OS_NAMES[myOS]}</a>
      <p class="fine">${esc(mine.name)} · ${mb(mine.size)} · version ${esc(rel.version)}. Other systems are listed below.</p>`;
    $$("[data-os-label]").forEach(a => { a.textContent = `Download for ${OS_NAMES[myOS]}`; });
  } else if (myOS === "mobile") {
    $("#dl-main").innerHTML = `<p class="notice">C.A.P. is a desktop app for Windows, macOS and Linux. Open this page on your computer to download it.</p>`;
  }

  list.innerHTML = ["windows", "mac", "linux"].map(os => {
    const a = byOS[os];
    if (!a) return `<article class="dl-item"><div><h3>${OS_NAMES[os]} <span class="pill soon">Not available yet</span></h3>
      <p class="meta">This version hasn't been built for ${OS_NAMES[os]}. Check back soon${releasesUrl ? `, or see <a href="${esc(releasesUrl)}">all releases</a>` : ""}.</p></div></article>`;
    const link = a.url ? `<a class="btn ${os === myOS ? "primary" : ""}" href="${esc(a.url)}">Download</a>`
                       : `<button class="btn" disabled title="Not published yet">Download</button>`;
    return `<article class="dl-item ${os === myOS ? "yours" : ""}">
      <div><h3>${OS_NAMES[os]}${os === myOS ? ' <span class="pill">Your computer</span>' : ""}</h3>
        <p class="meta">${esc(a.name)} · ${mb(a.size)} · <a href="#setup" data-goto-setup="${os}">setup steps</a></p></div>
      ${link}
      ${a.sha256 ? `<div class="sum"><span class="fine">SHA-256</span><code>${esc(a.sha256)}</code><button class="btn small" type="button" data-copy="${esc(a.sha256)}">Copy</button></div>` : ""}
    </article>`;
  }).join("");
  const when = rel.published ? new Date(rel.published).toLocaleDateString(undefined, { year: "numeric", month: "long", day: "numeric" }) : "";
  $("#dl-source").innerHTML = `Version ${esc(rel.version)}${when ? `, released ${esc(when)}` : ""}.${rel.page ? ` <a href="${esc(rel.page)}">Release notes and all files on GitHub</a>.` : ""}`;
}

document.addEventListener("click", async e => {
  const dl = e.target.closest('a[href*="/releases/download/"]');
  if (dl) count(`download-${osOf(dl.href) || "other"}`, `Download ${dl.href.split("/").pop()}`, { event: true });
  const copy = e.target.closest("[data-copy]");
  if (copy) {
    try { await navigator.clipboard.writeText(copy.dataset.copy); copy.textContent = "Copied"; }
    catch { copy.textContent = "Select and copy"; }
    setTimeout(() => { copy.textContent = "Copy"; }, 1800);
  }
  const setup = e.target.closest("[data-goto-setup]");
  if (setup) setTimeout(() => $(`button[data-setup="${setup.dataset.gotoSetup}"]`)?.click(), 0);
});

/* ---------- help & feedback ---------- */
// Each kind opens a GitHub form with the visitor's words filled in; the field names match .github's form ids.
const FEEDBACK = {
  question: { label: "Your question", path: "discussions/new", params: { category: "q-a" }, field: "question" },
  idea:     { label: "Your idea", path: "discussions/new", params: { category: "ideas" }, field: "idea" },
  bug:      { label: "What happened?", path: "issues/new", params: { template: "bug_report.yml" }, field: "what-happened" },
  chat:     { label: "Your message", path: "discussions/new", params: { category: "general" }, field: "message" },
};
// Things that shouldn't be in a public post: email addresses, phone numbers, SSN-like and long account numbers.
const PRIVATE_RX = /[\w.+-]+@[\w-]+\.[\w.]+|\(?\b\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b|\b\d{3}-\d{2}-\d{4}\b|\b\d{9,17}\b/;

function initFeedback() {
  const form = $("#fb-form"), kind = () => $('input[name="kind"]:checked', form).value;
  if (!form) return;
  $("#fb-system").value = OS_NAMES[myOS] || "";
  const sync = () => {
    $("#fb-message-label").textContent = FEEDBACK[kind()].label;
    $("#fb-warn").hidden = !PRIVATE_RX.test(`${$("#fb-title").value} ${$("#fb-message").value}`);
  };
  form.addEventListener("change", sync);
  form.addEventListener("input", sync);
  sync();
  $$("[data-gh]").forEach(a => { if (REPO) { a.href = `https://github.com/${REPO}/${a.dataset.gh}`; a.rel = "noopener"; a.target = "_blank"; } });
  form.addEventListener("submit", e => {
    e.preventDefault();
    const message = $("#fb-message").value.trim();
    if (!message) { $("#fb-status").textContent = "Please write your message first."; $("#fb-message").focus(); return; }
    if (!REPO) { $("#fb-status").textContent = "Feedback opens once the site is published on GitHub."; return; }
    const k = FEEDBACK[kind()];
    const title = $("#fb-title").value.trim() || message.split("\n")[0].slice(0, 80);
    const params = new URLSearchParams({ ...k.params, title, [k.field]: message });
    if (k.field !== "message" && k.field !== "idea") {
      if ($("#fb-system").value) params.set("system", $("#fb-system").value);
      if ($("#fb-version").value.trim()) params.set("version", $("#fb-version").value.trim());
    }
    window.open(`https://github.com/${REPO}/${k.path}?${params}`, "_blank", "noopener");
    $("#fb-status").textContent = "GitHub opened in a new tab. Sign in if asked, check your post, then click the green button there to post it.";
  });
}

/* ---------- start ---------- */
route();
bindSwitch("setup");
bindSwitch("verify");
initTour();
initDownloads();
initFeedback();
countPage();
