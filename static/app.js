/* ── State ── */
let selectedFile = null;
let _lastData = null;
let _activeFilter = "all";

/* ── Boot ── */
document.addEventListener("DOMContentLoaded", () => bindEvents());

/* ── Events ── */
function bindEvents() {
  const zone = document.getElementById("upload-zone");
  const input = document.getElementById("file-input");

  zone.addEventListener("click", () => input.click());
  input.addEventListener("change", () => handleFile(input.files[0]));

  zone.addEventListener("dragover", (e) => { e.preventDefault(); zone.classList.add("drag-over"); });
  zone.addEventListener("dragleave", () => zone.classList.remove("drag-over"));
  zone.addEventListener("drop", (e) => {
    e.preventDefault();
    zone.classList.remove("drag-over");
    handleFile(e.dataTransfer.files[0]);
  });

  document.getElementById("analyze-btn").addEventListener("click", runAnalysis);
}

function handleFile(file) {
  if (!file?.name?.toLowerCase().endsWith(".pdf")) {
    alert("Please select a PDF file.");
    return;
  }
  selectedFile = file;
  document.getElementById("file-preview").innerHTML = `
    <div class="file-selected">
      <span class="file-icon">📄</span>
      <div style="flex:1;min-width:0">
        <div class="file-name">${escHtml(file.name)}</div>
        <div class="file-size">${(file.size / 1024).toFixed(1)} KB</div>
      </div>
      <button class="btn-remove" onclick="clearFile()" title="Remove">✕</button>
    </div>`;
  document.getElementById("analyze-btn").disabled = false;
}

function clearFile() {
  selectedFile = null;
  document.getElementById("file-preview").innerHTML = "";
  document.getElementById("file-input").value = "";
  document.getElementById("analyze-btn").disabled = true;
}

/* ── Main analysis flow ── */
const _LOADING_STEPS = [
  [2, "Reading your brochure…",         "Extracting project name, location & pricing"],
  [2, "Understanding the project…",     "Identifying unit types, amenities & highlights"],
  [3, "Searching for nearby brokers…",  "Scanning broker listings in the project area"],
  [3, "Collecting contact details…",    "Phone numbers, emails & firm websites"],
  [3, "Looking up key contacts…",       "Finding founders & directors at each firm"],
  [3, "Almost done…",                   "Cleaning up and ranking results for you"],
];

async function runAnalysis() {
  if (!selectedFile) return;
  setStep(1);
  clearResults();
  showLoader("Starting…", "");

  const formData = new FormData();
  formData.append("file", selectedFile);

  // Each step shows for exactly 1 second; run all steps before rendering
  const MIN_STEP_MS = 3000;
  const animationDone = (async () => {
    for (const [step, label, sub] of _LOADING_STEPS) {
      setStep(step);
      updateLoader(label, sub);
      await new Promise(r => setTimeout(r, MIN_STEP_MS));
    }
  })();

  try {
    const [resp] = await Promise.all([
      fetch("/api/analyze", { method: "POST", body: formData }),
      animationDone,
    ]);
    const data = await resp.json();
    hideLoader();
    if (!resp.ok) throw new Error(data.detail || "Server error");
    setStep(4, "done");
    renderResults(data);
  } catch (err) {
    clearInterval(ticker);
    hideLoader();
    markStepError(err.message);
  }
}

function showLoader(label, sub) {
  const ov = document.getElementById("loader-overlay");
  ov.style.display = "flex";
  document.getElementById("loader-label").textContent = label;
  document.getElementById("loader-sub").textContent = sub || "";
}

function updateLoader(label, sub) {
  document.getElementById("loader-label").textContent = label;
  document.getElementById("loader-sub").textContent = sub || "";
}

function hideLoader() {
  document.getElementById("loader-overlay").style.display = "none";
}

/* ── Step indicator ── */
function setStep(n, state = "active") {
  for (let i = 1; i <= 4; i++) {
    const el = document.getElementById(`step-${i}`);
    if (!el) continue;
    el.classList.remove("active", "done", "error");
    if (i < n) el.classList.add("done");
    else if (i === n) el.classList.add(state);
  }
}

function markStepError(msg) {
  for (let i = 1; i <= 4; i++) {
    const el = document.getElementById(`step-${i}`);
    if (el?.classList.contains("active")) {
      el.classList.remove("active");
      el.classList.add("error");
    }
  }
  const errDiv = document.getElementById("global-error");
  errDiv.textContent = "Error: " + msg;
  errDiv.style.display = "block";
}

/* ── Render ── */
function clearResults() {
  _lastData = null;
  _activeFilter = "all";
  document.getElementById("results-section").innerHTML = "";
  document.getElementById("global-error").style.display = "none";
  document.getElementById("topbar-actions").style.display = "none";
  document.getElementById("topbar-title").textContent = "Analyzing brochure…";
  document.getElementById("project-mini").style.display = "none";
  document.getElementById("project-mini").innerHTML = "";
  document.getElementById("email-status").textContent = "";
  document.getElementById("email-status").className = "email-status-msg";
}

function renderResults(data) {
  _lastData = data;
  _activeFilter = "all";

  const pd = data.project_details || {};
  const brokers = data.unified_brokers || [];
  const loc = pd.location || {};
  const price = pd.price_range || {};
  let priceStr = "";
  if (price.min && price.max) priceStr = `₹ ${price.min} – ${price.max}`;
  else if (price.min) priceStr = `₹ ${price.min}`;

  // Topbar
  document.getElementById("topbar-title").textContent = pd.project_name || "Analysis Complete";
  document.getElementById("topbar-actions").style.display = "flex";

  // Sidebar project card
  const miniEl = document.getElementById("project-mini");
  miniEl.style.display = "block";
  miniEl.innerHTML = `
    <div class="project-mini-card">
      <div class="pm-name">${escHtml(pd.project_name || "Project")}</div>
      <div class="pm-dev">${escHtml(pd.developer_name || "")}</div>
      <div class="pm-loc">📍 ${escHtml([loc.area, loc.city].filter(Boolean).join(", ") || "")}</div>
      ${priceStr ? `<div class="pm-price">${escHtml(priceStr)}</div>` : ""}
    </div>`;

  // Stats
  const withPhone = brokers.filter(b => b.phone).length;
  const withEmail = brokers.filter(b => b.email).length;
  const pct = n => brokers.length ? Math.round(n / brokers.length * 100) : 0;

  const statsHtml = `
    <div class="stats-row">
      <div class="stat-card">
        <div class="stat-label">Total Brokers</div>
        <div class="stat-value">${brokers.length}</div>
        <div class="stat-sub">contacts found</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">With Phone</div>
        <div class="stat-value">${withPhone}</div>
        <div class="stat-sub">${pct(withPhone)}% coverage</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">With Email</div>
        <div class="stat-value">${withEmail}</div>
        <div class="stat-sub">${pct(withEmail)}% coverage</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">City</div>
        <div class="stat-value" style="font-size:.85rem">${escHtml(loc.city || "—")}</div>
        <div class="stat-sub">${escHtml(loc.state || "")}</div>
      </div>
    </div>`;

  const sec = document.getElementById("results-section");
  sec.innerHTML = statsHtml + renderBrokersSection(brokers);
}

function renderBrokersSection(brokers) {
  const withPhone = brokers.filter(b => b.phone).length;
  const withEmail = brokers.filter(b => b.email).length;

  const filtered = brokers
    .filter(b => {
      if (_activeFilter === "phone") return !!b.phone;
      if (_activeFilter === "email") return !!b.email;
      return true;
    })
    .sort((a, b) => {
      // Score: key_person + evidence = 2, either one = 1, neither = 0
      const score = x => (x.key_person ? 1 : 0) + (x.evidence ? 1 : 0);
      return score(b) - score(a);
    });

  const filterHtml = `
    <div class="filter-row">
      <button class="filter-tab ${_activeFilter === "all" ? "active" : ""}" onclick="setFilter('all')">
        All <span class="tc">${brokers.length}</span>
      </button>
      <button class="filter-tab ${_activeFilter === "phone" ? "active" : ""}" onclick="setFilter('phone')">
        With Phone <span class="tc">${withPhone}</span>
      </button>
      <button class="filter-tab ${_activeFilter === "email" ? "active" : ""}" onclick="setFilter('email')">
        With Email <span class="tc">${withEmail}</span>
      </button>
    </div>`;

  const tableHtml = filtered.length
    ? `<div class="broker-table-wrap">
        <table class="broker-table">
          <thead><tr>
            <th>#</th><th>Firm</th><th>Key Person</th><th>Phone</th><th>Email</th>
            <th>Address</th><th>Rating</th><th>Evidence</th><th></th>
          </tr></thead>
          <tbody>${filtered.map((b, i) => brokerRow(b, i + 1)).join("")}</tbody>
        </table>
      </div>`
    : `<div class="no-results"><div class="nr-icon">📭</div><p>No contacts match this filter.</p></div>`;

  return `
    <div class="section-header">
      <h2>Brokers &amp; Contacts</h2>
      ${brokers.length ? `<span class="count-badge">${brokers.length}</span>` : ""}
    </div>
    ${filterHtml}${tableHtml}`;
}

function setFilter(f) {
  _activeFilter = f;
  if (!_lastData) return;
  const brokers = _lastData.unified_brokers || [];
  const sec = document.getElementById("results-section");
  const statsRow = sec.querySelector(".stats-row");
  const statsHtml = statsRow ? statsRow.outerHTML : "";
  sec.innerHTML = statsHtml + renderBrokersSection(brokers);
}

function brokerRow(b, idx) {
  const rating = b.rating
    ? `<span class="rating-txt">⭐ ${b.rating}</span>`
    : `<span class="dot dot-gray"></span>`;
  const keyPerson = [b.key_person, b.key_person_title].filter(Boolean).join(", ");
  let link = "", linkLabel = "";
  if (b.website) { link = b.website; linkLabel = "↗ Site"; }
  else if (b.maps_url) { link = b.maps_url; linkLabel = "↗ Maps"; }
  return `
    <tr>
      <td style="color:var(--text-3);font-size:.78rem">${idx}</td>
      <td>
        <div class="td-name">${escHtml(b.name || "Unknown")}</div>
        ${b.categories?.length ? `<div style="font-size:.72rem;color:var(--text-3)">${escHtml(b.categories[0])}</div>` : ""}
      </td>
      <td>${keyPerson ? `<div class="td-person">${escHtml(keyPerson)}</div>` : `<span class="dot dot-gray"></span>`}</td>
      <td>${b.phone ? `<a class="phone-pill" href="tel:${escHtml(b.phone)}">📞 ${escHtml(b.phone)}</a>` : `<span class="dot dot-gray"></span>`}</td>
      <td>${b.email ? `<a class="email-link" href="mailto:${escHtml(b.email)}">${escHtml(b.email)}</a>` : `<span class="dot dot-gray"></span>`}</td>
      <td><div class="td-addr">${escHtml(b.address || "")}</div></td>
      <td>${rating}</td>
      <td><div class="td-evidence">${escHtml(b.evidence || "")}</div></td>
      <td>${link ? `<a class="web-link" href="${escHtml(link)}" target="_blank">${linkLabel}</a>` : ""}</td>
    </tr>`;
}

/* ── CSV Export ── */
async function downloadCsv() {
  if (!_lastData) return;
  const projectName = _lastData.project_details?.project_name || "Project";
  try {
    const resp = await fetch("/api/download-csv", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        project_name: projectName,
        unified_brokers: _lastData.unified_brokers || [],
        results: _lastData.results || {},
      }),
    });
    if (!resp.ok) throw new Error((await resp.json()).detail);
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `brokers_${projectName}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  } catch (err) {
    setEmailStatus("error", "Download failed: " + err.message);
  }
}

async function sendCsv() {
  if (!_lastData) return;
  const email = document.getElementById("email-input").value.trim();
  if (!email?.includes("@")) {
    setEmailStatus("error", "Please enter a valid email address.");
    return;
  }
  const projectName = _lastData.project_details?.project_name || "Project";
  setEmailStatus("sending", "Sending…");
  try {
    const resp = await fetch("/api/send-csv", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        email,
        project_name: projectName,
        unified_brokers: _lastData.unified_brokers || [],
        results: _lastData.results || {},
      }),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail);
    setEmailStatus("ok", `✓ Sent to ${email}`);
  } catch (err) {
    setEmailStatus("error", "Failed: " + err.message);
  }
}

function setEmailStatus(type, msg) {
  const el = document.getElementById("email-status");
  if (!el) return;
  el.className = "email-status-msg " + type;
  el.textContent = msg;
  if (type === "ok") setTimeout(() => { el.textContent = ""; el.className = "email-status-msg"; }, 4000);
}

/* ── Utils ── */
function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
function escHtml(s) {
  return String(s ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}
