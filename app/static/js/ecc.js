// Claude Code Setup panel — install agents/skills/commands/rules from
// the everything-claude-code repo into ~/.claude/ or <project>/.claude/.
//
// The panel is mostly built here (DOM-first) rather than in admin.html to
// keep the HTML skeleton light. Only the empty <section id="panel-ecc">
// container is defined in admin.html.

import { api } from "./api.js";
import { el, clear, $, $$, fmtDate, fmtRelDate } from "./dom.js";
import { toast, modal, confirm, emptyState } from "./ui.js";

// ---------- module state ----------

let built = false;          // panel DOM built yet?
let status = null;          // {cached, commit_short, synced_at, ...}
let catalog = null;         // {agents, skills, commands, rules}
let presets = [];           // [{id, name, description, items_count}]
let targetPref = { target: "user", project_path: "" };
let browseSelection = new Set();  // "category:id"
let activeBrowseCategory = "all";

// ---------- entry points ----------

export async function loadEcc() {
  buildShell();
  try {
    const st = await api("/api/ecc/status");
    status = st;
    targetPref = {
      target: st.default_target || "user",
      project_path: st.default_project_path || "",
    };
  } catch (e) {
    toast.error(e.message);
    return;
  }
  renderStatus();
  renderTarget();
  refreshAutoSync();
  if (status.cached) {
    await refreshCatalogAndPresets();
  } else {
    renderNeedsSync();
  }
}

// ---------- build static shell once ----------

function buildShell() {
  if (built) return;
  const panel = $("#setupEccPane");
  if (!panel) return;
  clear(panel);

  panel.appendChild(card("Source: everything-claude-code", buildStatusCard()));
  panel.appendChild(card("Install target", buildTargetCard()));
  panel.appendChild(buildInstallCard());

  built = true;
}

function card(titleText, bodyEl, extraHeader) {
  const hdr = el("div", { class: "card-header" },
    el("h2", {}, titleText),
    extraHeader || null);
  return el("div", { class: "card" }, hdr, bodyEl);
}

// ---------- status card ----------

function buildStatusCard() {
  const body = el("div", { id: "eccStatusBody" },
    el("p", { class: "muted", style: "margin:0 0 var(--space-3)" },
      "Sync clones (or updates) the repo into ~/.cache/claude-croxy/ecc-repo — no global install, nothing written to ~/.claude/ until you install."),
    el("div", { class: "row", style: "gap:8px;align-items:center;flex-wrap:wrap" },
      el("div", { id: "eccStatusMeta", class: "muted", style: "font-size:var(--fs-sm)" }, "Checking…"),
      el("div", { class: "grow" }),
      el("button", { class: "btn", id: "eccSyncBtn", onclick: doSync }, "Sync now"),
    ),
    el("div", { id: "eccAutoSyncRow", class: "row", style: "gap:8px;align-items:center;flex-wrap:wrap;margin-top:var(--space-3);padding-top:var(--space-3);border-top:1px solid var(--border)" },
      el("label", { class: "row", style: "gap:8px;align-items:center;cursor:pointer" },
        el("input", { type: "checkbox", id: "eccAutoSyncEnabled", onchange: onAutoSyncChange }),
        el("span", {}, "Auto-sync every"),
      ),
      el("input", {
        type: "number", id: "eccAutoSyncInterval", min: "1", max: "168",
        value: "24",
        style: "width:70px",
        onchange: onAutoSyncChange,
      }),
      el("span", { class: "muted" }, "hours"),
      el("div", { class: "grow" }),
      el("span", { id: "eccAutoSyncMeta", class: "muted", style: "font-size:var(--fs-xs)" }, ""),
    ),
  );
  return body;
}

let autoSyncCfg = null;

async function refreshAutoSync() {
  try {
    autoSyncCfg = await api("/api/ecc/autosync");
  } catch (e) { return; }
  const cb = $("#eccAutoSyncEnabled");
  const intv = $("#eccAutoSyncInterval");
  const meta = $("#eccAutoSyncMeta");
  if (cb) cb.checked = !!autoSyncCfg.enabled;
  if (intv) intv.value = String(autoSyncCfg.interval_hours || 24);
  if (meta) {
    const parts = [];
    if (autoSyncCfg.running) parts.push("running");
    if (autoSyncCfg.last_run_at) parts.push(`last run ${fmtRelDate(autoSyncCfg.last_run_at)}`);
    if (autoSyncCfg.last_error) parts.push(`⚠ ${autoSyncCfg.last_error}`);
    meta.textContent = parts.join(" • ");
  }
}

async function onAutoSyncChange() {
  const enabled = $("#eccAutoSyncEnabled")?.checked ?? false;
  const interval_hours = parseInt($("#eccAutoSyncInterval")?.value || "24", 10);
  try {
    autoSyncCfg = await api("/api/ecc/autosync", {
      method: "POST",
      body: { enabled, interval_hours },
    });
    toast.success(enabled ? `Auto-sync on (${interval_hours}h)` : "Auto-sync off");
    await refreshAutoSync();
  } catch (e) { toast.error(e.message); }
}

function renderStatus() {
  const meta = $("#eccStatusMeta");
  if (!meta) return;
  clear(meta);
  if (!status?.cached) {
    meta.appendChild(el("span", { class: "badge off" }, "not synced"));
    return;
  }
  const stale = _isStale(status.synced_at, 24);
  meta.appendChild(el("span", { class: "badge " + (stale ? "warn" : "ok") }, stale ? "stale" : "cached"));
  meta.appendChild(document.createTextNode(" "));
  if (status.commit_short) {
    meta.appendChild(el("code", {}, status.commit_short));
    meta.appendChild(document.createTextNode(" • "));
  }
  if (status.synced_at) {
    meta.appendChild(document.createTextNode(`synced ${fmtRelDate(status.synced_at)}`));
  }
}

function _isStale(isoString, hoursThreshold) {
  if (!isoString) return false;
  const t = new Date(isoString).getTime();
  if (isNaN(t)) return false;
  return (Date.now() - t) > hoursThreshold * 3600 * 1000;
}

async function doSync() {
  const btn = $("#eccSyncBtn");
  if (!btn) return;
  btn.disabled = true;
  const prev = btn.textContent;
  btn.textContent = "Syncing…";
  try {
    const st = await api("/api/ecc/sync", { method: "POST" });
    status = { ...status, ...st };
    renderStatus();
    await refreshCatalogAndPresets();
    toast.success(`Synced ${st.commit_short || ""}`.trim());
  } catch (e) {
    toast.error(e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = prev;
  }
}

// ---------- target card ----------

function buildTargetCard() {
  const userRadio = el("input", {
    type: "radio", name: "eccTarget", value: "user", id: "eccTargetUser",
    onchange: onTargetChange,
  });
  const projectRadio = el("input", {
    type: "radio", name: "eccTarget", value: "project", id: "eccTargetProject",
    onchange: onTargetChange,
  });
  const pathInput = el("input", {
    type: "text", id: "eccProjectPath",
    placeholder: "/absolute/path/to/your/project",
    onchange: onTargetChange,
    onblur: onTargetChange,
  });

  return el("div", {},
    el("div", { class: "form-stack" },
      el("label", { class: "row", style: "gap:8px;align-items:center" },
        userRadio,
        el("span", {}, "User level — ", el("code", {}, "~/.claude/")),
      ),
      el("label", { class: "row", style: "gap:8px;align-items:center" },
        projectRadio,
        el("span", {}, "Project level — ", el("code", {}, "<project>/.claude/")),
      ),
      el("div", { class: "field", id: "eccProjectField" },
        el("label", {}, "Project path (absolute)"),
        pathInput,
      ),
    ),
  );
}

function renderTarget() {
  const u = $("#eccTargetUser");
  const p = $("#eccTargetProject");
  const input = $("#eccProjectPath");
  const field = $("#eccProjectField");
  if (!u || !p || !input || !field) return;

  if (targetPref.target === "project") { p.checked = true; }
  else { u.checked = true; }
  input.value = targetPref.project_path || "";
  field.style.opacity = targetPref.target === "project" ? "1" : "0.5";
  input.disabled = targetPref.target !== "project";
}

async function onTargetChange() {
  const target = $("#eccTargetProject")?.checked ? "project" : "user";
  const project_path = $("#eccProjectPath")?.value.trim() || "";
  targetPref = { target, project_path };
  renderTarget();
  // persist silently (don't block UI on errors)
  try {
    await api("/api/ecc/target-pref", {
      method: "POST",
      body: { target, project_path: project_path || null },
    });
  } catch { /* ignore */ }
}

// ---------- install card (tabs: presets, browse) ----------

function buildInstallCard() {
  const mkTab = (key, label) => el("button", {
    class: "btn secondary small",
    id: `eccTab_${key}`,
    onclick: () => showTab(key),
  }, label);

  const tabBar = el("div", { class: "row", style: "gap:8px;margin-bottom:var(--space-3);flex-wrap:wrap" },
    mkTab("presets", "Presets"),
    mkTab("browse", "Browse"),
    mkTab("mcp", "MCP"),
    mkTab("hooks", "Hooks"),
    mkTab("tokenfilter", "Token Filter"),
    mkTab("installed", "Installed"),
  );

  const presetsPane = el("div", { id: "eccPresetsPane" });
  const browsePane = el("div", { id: "eccBrowsePane", class: "hidden" });
  const mcpPane = el("div", { id: "eccMcpPane", class: "hidden" });
  const hooksPane = el("div", { id: "eccHooksPane", class: "hidden" });
  const tokenfilterPane = el("div", { id: "eccTokenfilterPane", class: "hidden" });
  const installedPane = el("div", { id: "eccInstalledPane", class: "hidden" });

  const hdr = el("div", { class: "card-header" }, el("h2", {}, "Install"));
  const body = el("div", {},
    tabBar,
    presetsPane,
    browsePane,
    mcpPane,
    hooksPane,
    tokenfilterPane,
    installedPane,
  );
  return el("div", { class: "card" }, hdr, body);
}

const ECC_TABS = ["presets", "browse", "mcp", "hooks", "tokenfilter", "installed"];

function showTab(which) {
  for (const key of ECC_TABS) {
    const pane = $(`#ecc${key[0].toUpperCase() + key.slice(1)}Pane`);
    if (pane) pane.classList.toggle("hidden", key !== which);
    const btn = $(`#eccTab_${key}`);
    if (btn) btn.classList.toggle("active", key === which);
  }
  if (which === "mcp") renderMcp();
  if (which === "hooks") renderHooks();
  if (which === "tokenfilter") renderTokenFilter();
  if (which === "installed") renderInstalled();
}

function renderNeedsSync() {
  const p = $("#eccPresetsPane");
  const b = $("#eccBrowsePane");
  if (p) { clear(p); p.appendChild(emptyState({ title: "Sync the repo first", message: "Click ‘Sync now’ above to fetch the catalog." })); }
  if (b) { clear(b); }
}

async function refreshCatalogAndPresets() {
  try {
    const [cat, pres] = await Promise.all([
      api("/api/ecc/catalog"),
      api("/api/ecc/presets"),
    ]);
    catalog = cat;
    presets = pres.presets || [];
    renderPresets();
    renderBrowse();
  } catch (e) {
    toast.error(e.message);
  }
}

// ---------- presets pane ----------

function renderPresets() {
  const pane = $("#eccPresetsPane");
  if (!pane) return;
  clear(pane);
  if (!presets.length) {
    pane.appendChild(emptyState({ title: "No presets available" }));
    return;
  }
  const grid = el("div", { class: "stat-grid" });
  for (const p of presets) {
    grid.appendChild(el("div", { class: "stat", style: "text-align:left" },
      el("div", { style: "font-weight:700;font-size:var(--fs-md)" }, p.name),
      el("div", { class: "muted", style: "font-size:var(--fs-sm);margin:6px 0 10px;min-height:34px" }, p.description || ""),
      el("div", { class: "row", style: "justify-content:space-between;align-items:center" },
        el("span", { class: "badge" }, `${p.items_count} item${p.items_count === 1 ? "" : "s"}`),
        el("button", {
          class: "btn small",
          onclick: () => installPreset(p.id),
        }, "Install"),
      ),
    ));
  }
  pane.appendChild(grid);
}

async function installPreset(presetId) {
  let items;
  try {
    const res = await api(`/api/ecc/presets/${encodeURIComponent(presetId)}/items`);
    items = res.items;
  } catch (e) {
    toast.error(e.message);
    return;
  }
  if (!items.length) {
    toast.warn("Preset matched zero items in the current catalog.");
    return;
  }
  await runInstallFlow(items, `preset "${presetId}"`);
}

// ---------- browse pane ----------

function renderBrowse() {
  const pane = $("#eccBrowsePane");
  if (!pane) return;
  clear(pane);
  if (!catalog) return;

  const counts = {
    all: Object.values(catalog).reduce((n, v) => n + (Array.isArray(v) ? v.length : 0), 0),
    agents: catalog.agents?.length || 0,
    skills: catalog.skills?.length || 0,
    commands: catalog.commands?.length || 0,
    rules: catalog.rules?.length || 0,
  };

  const chip = (name, label) => el("button", {
    class: "btn secondary small",
    dataset: { cat: name },
    style: activeBrowseCategory === name ? "" : "opacity:0.7",
    onclick: (e) => {
      activeBrowseCategory = name;
      renderBrowse();
    },
  }, `${label} (${counts[name] || 0})`);

  const filters = el("div", { class: "row", style: "gap:6px;flex-wrap:wrap;margin-bottom:var(--space-3)" },
    chip("all", "All"),
    chip("agents", "Agents"),
    chip("skills", "Skills"),
    chip("commands", "Commands"),
    chip("rules", "Rules"),
  );

  const searchInput = el("input", {
    type: "search",
    placeholder: "Search name / description…",
    id: "eccBrowseSearch",
    style: "width:100%;margin-bottom:var(--space-3)",
    oninput: (e) => applyBrowseFilter(e.target.value),
  });

  const tbl = el("table", { id: "eccBrowseTable", class: "stack-on-mobile" },
    el("thead", {},
      el("tr", {},
        el("th", { style: "width:32px" }, ""),
        el("th", {}, "Category"),
        el("th", {}, "ID"),
        el("th", {}, "Description"),
      )),
    el("tbody", {}),
  );

  const footer = el("div", { class: "row", style: "gap:8px;align-items:center;margin-top:var(--space-3)" },
    el("span", { id: "eccSelectedCount", class: "muted" }, "0 selected"),
    el("div", { class: "grow" }),
    el("button", { class: "btn secondary small", onclick: () => { browseSelection.clear(); renderBrowse(); } }, "Clear"),
    el("button", { class: "btn", id: "eccInstallSelected", onclick: onInstallSelected }, "Install selected"),
  );

  pane.appendChild(filters);
  pane.appendChild(searchInput);
  pane.appendChild(el("div", { style: "max-height:520px;overflow:auto" }, tbl));
  pane.appendChild(footer);

  renderBrowseRows("");
  updateSelectedCount();
}

function currentBrowseItems() {
  if (!catalog) return [];
  if (activeBrowseCategory === "all") {
    return [
      ...(catalog.agents || []),
      ...(catalog.skills || []),
      ...(catalog.commands || []),
      ...(catalog.rules || []),
    ];
  }
  return catalog[activeBrowseCategory] || [];
}

function renderBrowseRows(query) {
  const tbody = $("#eccBrowseTable tbody");
  if (!tbody) return;
  clear(tbody);
  const q = (query || "").toLowerCase().trim();
  const items = currentBrowseItems().filter((it) => {
    if (!q) return true;
    return (it.id + " " + (it.name || "") + " " + (it.description || "")).toLowerCase().includes(q);
  });
  if (!items.length) {
    tbody.appendChild(el("tr", {},
      el("td", { colspan: 4, style: "padding:var(--space-4)" },
        emptyState({ title: "No items match" }))));
    return;
  }
  for (const it of items.slice(0, 500)) {
    const key = it.category + ":" + it.id;
    const cb = el("input", {
      type: "checkbox",
      checked: browseSelection.has(key),
      onchange: (e) => {
        if (e.target.checked) browseSelection.add(key);
        else browseSelection.delete(key);
        updateSelectedCount();
      },
    });
    tbody.appendChild(el("tr", {},
      el("td", {}, cb),
      el("td", {}, el("span", { class: "badge" }, it.category)),
      el("td", {}, el("code", {}, it.id)),
      el("td", { class: "muted", style: "font-size:var(--fs-sm)" }, it.description || ""),
    ));
  }
  if (items.length > 500) {
    tbody.appendChild(el("tr", {},
      el("td", { colspan: 4, class: "muted", style: "padding:var(--space-3);text-align:center" },
        `Showing first 500 of ${items.length}. Narrow your search to see more.`)));
  }
}

function applyBrowseFilter(q) { renderBrowseRows(q); }

function updateSelectedCount() {
  const el1 = $("#eccSelectedCount");
  if (el1) el1.textContent = `${browseSelection.size} selected`;
}

async function onInstallSelected() {
  if (!browseSelection.size) {
    toast.warn("Select at least one item.");
    return;
  }
  const items = [...browseSelection].map((s) => {
    const [category, id] = s.split(":", 2);
    return { category, id };
  });
  await runInstallFlow(items, `${items.length} item${items.length === 1 ? "" : "s"}`);
}

// ---------- install flow (shared by preset + browse) ----------

async function runInstallFlow(items, label) {
  const target = targetPref.target;
  const project_path = targetPref.project_path?.trim() || null;
  if (target === "project" && !project_path) {
    toast.error("Enter an absolute project path first.");
    return;
  }

  let plan;
  try {
    plan = await api("/api/ecc/install/plan", {
      method: "POST",
      body: { items, target, project_path },
    });
  } catch (e) {
    toast.error(e.message);
    return;
  }

  showInstallModal({ items, plan, label });
}

// ---------- MCP sub-tab ----------

let mcpServers = null;  // cached list from /api/ecc/mcp/servers
const mcpSelection = new Set();

async function renderMcp() {
  const pane = $("#eccMcpPane");
  if (!pane) return;
  clear(pane);
  pane.appendChild(el("div", { class: "muted", style: "margin-bottom:var(--space-3);font-size:var(--fs-sm)" },
    "Selected servers are merged into ",
    el("code", {},
      targetPref.target === "project" ? `${targetPref.project_path || "<project>"}/.mcp.json` : "~/.claude.json"),
    ". Servers flagged ", el("span", { class: "badge warn" }, "placeholders"),
    " need API keys filled in after install."));

  if (!mcpServers) {
    try {
      const r = await api("/api/ecc/mcp/servers");
      mcpServers = r.servers || [];
    } catch (e) {
      toast.error(e.message);
      return;
    }
  }

  const tbl = el("table", { class: "stack-on-mobile" },
    el("thead", {}, el("tr", {},
      el("th", { style: "width:32px" }, ""),
      el("th", {}, "Server"),
      el("th", {}, "Type"),
      el("th", {}, "Description"),
    )),
    el("tbody", {}),
  );
  const tbody = tbl.querySelector("tbody");
  for (const s of mcpServers) {
    const cb = el("input", {
      type: "checkbox",
      checked: mcpSelection.has(s.id),
      onchange: (e) => {
        if (e.target.checked) mcpSelection.add(s.id);
        else mcpSelection.delete(s.id);
        $("#eccMcpSelectedCount").textContent = `${mcpSelection.size} selected`;
      },
    });
    tbody.appendChild(el("tr", {},
      el("td", {}, cb),
      el("td", {},
        el("code", {}, s.id),
        s.has_placeholders ? el("span", { class: "badge warn", style: "margin-left:6px" }, "placeholders") : null),
      el("td", { class: "muted" }, s.type || "stdio"),
      el("td", { class: "muted", style: "font-size:var(--fs-sm)" }, s.description || ""),
    ));
  }
  pane.appendChild(el("div", { style: "max-height:520px;overflow:auto" }, tbl));
  pane.appendChild(el("div", { class: "row", style: "gap:8px;align-items:center;margin-top:var(--space-3)" },
    el("span", { id: "eccMcpSelectedCount", class: "muted" }, `${mcpSelection.size} selected`),
    el("div", { class: "grow" }),
    el("button", { class: "btn secondary small", onclick: () => { mcpSelection.clear(); renderMcp(); } }, "Clear"),
    el("button", { class: "btn", onclick: onInstallMcp }, "Install selected MCP"),
  ));
}

async function onInstallMcp() {
  if (!mcpSelection.size) { toast.warn("Select at least one server."); return; }
  if (targetPref.target === "project" && !targetPref.project_path?.trim()) {
    toast.error("Set an absolute project path above first.");
    return;
  }
  const ids = [...mcpSelection];
  let plan;
  try {
    plan = await api("/api/ecc/mcp/install/plan", {
      method: "POST",
      body: {
        server_ids: ids,
        target: targetPref.target,
        project_path: targetPref.project_path?.trim() || null,
      },
    });
  } catch (e) { toast.error(e.message); return; }

  const backupCb = el("input", { type: "checkbox", checked: true });
  const hasPh = plan.entries.some((e) => e.has_placeholders);
  const h = modal((body) => {
    body.appendChild(el("h2", {}, "MCP install plan"));
    body.appendChild(el("p", { class: "modal-sub" },
      `${plan.entries.length} server(s) → `, el("code", {}, plan.target_path)));
    if (plan.missing?.length) {
      body.appendChild(el("div", { class: "info-box" }, `⚠ not found: ${plan.missing.join(", ")}`));
    }
    const list = el("div", { style: "max-height:220px;overflow:auto;font-family:var(--font-mono);font-size:var(--fs-xs);background:var(--surface-2);padding:var(--space-2);border-radius:var(--radius)" });
    for (const e of plan.entries) {
      list.appendChild(el("div", {},
        `${e.exists ? "[overwrite] " : "[add] "}${e.id}`,
        e.has_placeholders ? " (placeholders)" : ""));
    }
    body.appendChild(list);
    if (hasPh) {
      body.appendChild(el("div", { class: "info-box", style: "margin-top:var(--space-3)" },
        "⚠ Some servers contain placeholder API keys (YOUR_*_HERE). Fill them in after install, otherwise those servers won't work."));
    }
    if (plan.preview_merged) {
      const details = el("details", { style: "margin-top:var(--space-3)" });
      details.appendChild(el("summary", { style: "cursor:pointer;font-weight:600;font-size:var(--fs-sm)" },
        "Preview merged config"));
      details.appendChild(el("pre", {
        style: "max-height:280px;overflow:auto;background:var(--surface-2);padding:var(--space-2);border-radius:var(--radius);font-size:var(--fs-xs);margin-top:var(--space-2)",
      }, JSON.stringify(plan.preview_merged, null, 2)));
      body.appendChild(details);
    }
    body.appendChild(el("label", { class: "row", style: "gap:8px;align-items:center;margin-top:var(--space-3)" },
      backupCb, el("span", {}, "Back up target file first")));

    body.appendChild(el("div", { class: "modal-actions" },
      el("button", { class: "btn secondary", onclick: () => h.close() }, "Cancel"),
      el("button", {
        class: "btn",
        onclick: async () => {
          try {
            const res = await api("/api/ecc/mcp/install/apply", {
              method: "POST",
              body: {
                server_ids: ids,
                target: targetPref.target,
                project_path: targetPref.project_path?.trim() || null,
                backup: backupCb.checked,
              },
            });
            h.close();
            toast.success(`Installed ${res.installed} MCP server(s) into ${res.target_path}`);
            mcpSelection.clear();
            installedCache = null;  // invalidate installed tab
          } catch (e) {
            toast.error(e.message);
          }
        },
      }, "Install"),
    ));
  });
}

// ---------- Hooks sub-tab ----------

let hooksData = null;

async function renderHooks() {
  const pane = $("#eccHooksPane");
  if (!pane) return;
  clear(pane);

  try {
    hooksData = await api("/api/ecc/hooks/list");
  } catch (e) { toast.error(e.message); return; }

  const link = hooksData.plugin_link || {};
  pane.appendChild(el("div", { class: "info-box", style: "margin-bottom:var(--space-3)" },
    "Hooks rely on ECC's plugin bootstrap. On install we symlink the repo cache into ",
    el("code", {}, link.path),
    link.is_symlink ? " (already linked ✓)" :
      link.exists ? " (a directory already exists here — uninstall then re-install)" :
        " (will be created)",
    "."));

  const events = hooksData.events || {};
  const eventKeys = Object.keys(events).sort();
  if (!eventKeys.length) {
    pane.appendChild(emptyState({ title: "No hooks found", message: "Sync the repo first." }));
    return;
  }
  let totalCount = 0;
  for (const ek of eventKeys) {
    const entries = events[ek] || [];
    totalCount += entries.length;
    const section = el("div", { class: "card", style: "margin-bottom:var(--space-3)" },
      el("div", { class: "card-header" },
        el("h3", { style: "margin:0;font-size:var(--fs-md)" }, `${ek} (${entries.length})`)));
    const list = el("div", { style: "font-size:var(--fs-sm)" });
    for (const e of entries) {
      list.appendChild(el("div", { style: "padding:6px 0;border-top:1px solid var(--border)" },
        el("code", {}, e.id || "(no-id)"),
        " ",
        el("span", { class: "muted" }, e.matcher ? `matcher=${e.matcher}` : ""),
        el("div", { class: "muted", style: "font-size:var(--fs-xs);margin-top:2px" }, e.description || "")));
    }
    section.appendChild(list);
    pane.appendChild(section);
  }
  pane.appendChild(el("div", { class: "row", style: "gap:8px;align-items:center;margin-top:var(--space-3)" },
    el("span", { class: "muted" }, `${totalCount} hook entries`),
    el("div", { class: "grow" }),
    el("button", { class: "btn", onclick: onInstallHooks }, "Install all hooks"),
  ));
}

async function onInstallHooks() {
  if (targetPref.target === "project" && !targetPref.project_path?.trim()) {
    toast.error("Set an absolute project path above first.");
    return;
  }
  let plan;
  try {
    plan = await api("/api/ecc/hooks/install/plan", {
      method: "POST",
      body: {
        target: targetPref.target,
        project_path: targetPref.project_path?.trim() || null,
      },
    });
  } catch (e) { toast.error(e.message); return; }

  const backupCb = el("input", { type: "checkbox", checked: true });
  const h = modal((body) => {
    body.appendChild(el("h2", {}, "Hook install plan"));
    body.appendChild(el("p", { class: "modal-sub" },
      `Merge hooks into `, el("code", {}, plan.target_path), "."));
    body.appendChild(el("p", { class: "modal-sub" },
      `Plugin link: `, el("code", {}, plan.plugin_link.path), ` (${plan.plugin_link.status})`));
    body.appendChild(el("div", { style: "font-size:var(--fs-sm)" },
      `${plan.creates_count} new • ${plan.overwrites_count} overwrite`));
    body.appendChild(el("label", { class: "row", style: "gap:8px;align-items:center;margin-top:var(--space-3)" },
      backupCb, el("span", {}, "Back up settings.json first")));
    body.appendChild(el("div", { class: "modal-actions" },
      el("button", { class: "btn secondary", onclick: () => h.close() }, "Cancel"),
      el("button", {
        class: "btn",
        onclick: async () => {
          try {
            const res = await api("/api/ecc/hooks/install/apply", {
              method: "POST",
              body: {
                target: targetPref.target,
                project_path: targetPref.project_path?.trim() || null,
                backup: backupCb.checked,
              },
            });
            h.close();
            toast.success(`Installed ${res.installed} hook entries. Plugin ${res.plugin_link.method}.`);
            installedCache = null;
          } catch (e) { toast.error(e.message); }
        },
      }, "Install"),
    ));
  });
}

// ---------- Token Filter sub-tab ----------

const TOKEN_FILTER_RULES = [
  { cmd: "git log", desc: "Add -n 50 when no limit flag present" },
  { cmd: "git diff", desc: "Pipe through head -N" },
  { cmd: "git status", desc: "Pipe through head -100" },
  { cmd: "find", desc: "Pipe through head -N" },
  { cmd: "grep -r / rg", desc: "Pipe through head -N" },
  { cmd: "ls -R / tree", desc: "Pipe through head -N" },
  { cmd: "cat / bat", desc: "Replace with head -N" },
  { cmd: "pytest / jest / cargo test", desc: "Pipe through tail -N (summary at end)" },
  { cmd: "docker ps/images/logs", desc: "Pipe through head -N" },
  { cmd: "ps", desc: "Pipe through head -N" },
  { cmd: "Unmatched (MLX)", desc: "MLX classifies → HEAD / TAIL / SUMMARIZE (requires MLX mode)" },
];

async function renderTokenFilter() {
  const pane = $("#eccTokenfilterPane");
  if (!pane) return;
  clear(pane);

  let st;
  try {
    st = await api(`/api/ecc/token-filter/status?target=${targetPref.target}&project_path=${encodeURIComponent(targetPref.project_path || "")}`);
  } catch (e) { toast.error(e.message); return; }

  const cfg = st.config || {};

  // Status badge
  const badge = el("span", {
    class: st.installed ? "badge success" : "badge",
    style: "font-size:var(--fs-sm)",
  }, st.installed ? "Installed" : "Not installed");

  pane.appendChild(el("div", { style: "display:flex;align-items:center;gap:12px;margin-bottom:var(--space-3)" },
    el("h3", { style: "margin:0" }, "Hybrid Token Filter"),
    badge,
  ));

  pane.appendChild(el("p", { class: "muted", style: "font-size:var(--fs-sm);margin-bottom:var(--space-3)" },
    "A PreToolUse hook that rewrites verbose CLI commands to include output truncation. Combines regex fast path with optional MLX local inference for broader coverage. Reduces token consumption by 60-98%.",
  ));

  // Config form
  const maxInput = el("input", { type: "number", value: cfg.max_lines || 300, min: 50, max: 2000, style: "width:80px" });
  const tailInput = el("input", { type: "number", value: cfg.tail_lines || 150, min: 20, max: 1000, style: "width:80px" });
  const mlxToggle = el("input", { type: "checkbox", checked: !!cfg.mlx_enabled });
  const mlxThresholdInput = el("input", { type: "number", value: cfg.mlx_threshold || 2000, min: 500, max: 50000, style: "width:100px" });
  const mlxUrlInput = el("input", { type: "text", value: cfg.mlx_url || "http://localhost:8899", style: "width:220px;font-size:var(--fs-sm)" });

  const mlxFields = el("div", {
    style: `display:${cfg.mlx_enabled ? "flex" : "none"};gap:24px;align-items:center;flex-wrap:wrap;padding:8px 0;border-top:1px solid var(--border);margin-top:8px`,
  },
    el("label", { style: "display:flex;align-items:center;gap:8px;font-size:var(--fs-sm)" },
      "MLX URL:", mlxUrlInput),
    el("label", { style: "display:flex;align-items:center;gap:8px;font-size:var(--fs-sm)" },
      "Summarize threshold (chars):", mlxThresholdInput),
  );
  mlxToggle.onchange = () => { mlxFields.style.display = mlxToggle.checked ? "flex" : "none"; };

  const configCard = el("div", { class: "card", style: "margin-bottom:var(--space-3)" },
    el("div", { class: "card-header" }, el("h3", { style: "margin:0;font-size:var(--fs-md)" }, "Configuration")),
    el("div", { style: "display:flex;gap:24px;align-items:center;flex-wrap:wrap;padding:8px 0" },
      el("label", { style: "display:flex;align-items:center;gap:8px;font-size:var(--fs-sm)" },
        "Max lines (head):", maxInput),
      el("label", { style: "display:flex;align-items:center;gap:8px;font-size:var(--fs-sm)" },
        "Tail lines (tests):", tailInput),
      el("label", { style: "display:flex;align-items:center;gap:8px;font-size:var(--fs-sm);margin-left:12px" },
        mlxToggle, "MLX hybrid mode"),
    ),
    mlxFields,
    el("div", { style: "padding:8px 0" },
      el("button", {
        class: "btn secondary small",
        onclick: async () => {
          try {
            await api("/api/ecc/token-filter/config", {
              method: "POST",
              body: {
                max_lines: +maxInput.value,
                tail_lines: +tailInput.value,
                mlx_enabled: mlxToggle.checked,
                mlx_threshold: +mlxThresholdInput.value,
                mlx_url: mlxUrlInput.value.trim(),
              },
            });
            toast.success("Config saved.");
          } catch (e) { toast.error(e.message); }
        },
      }, "Save"),
    ),
  );
  pane.appendChild(configCard);

  // Filter rules reference
  const rulesCard = el("div", { class: "card", style: "margin-bottom:var(--space-3)" },
    el("div", { class: "card-header" },
      el("h3", { style: "margin:0;font-size:var(--fs-md)" }, "Filter Rules")),
  );
  const rulesList = el("div", { style: "font-size:var(--fs-sm)" });
  for (const r of TOKEN_FILTER_RULES) {
    rulesList.appendChild(el("div", { style: "padding:4px 0;border-top:1px solid var(--border);display:flex;gap:12px" },
      el("code", { style: "min-width:180px" }, r.cmd),
      el("span", { class: "muted" }, r.desc),
    ));
  }
  rulesCard.appendChild(rulesList);
  pane.appendChild(rulesCard);

  // Script path info
  const infoItems = [
    "Script: ", el("code", {}, st.script_path),
    st.script_exists ? " (exists)" : " (will be created)",
    el("br"),
    "Target: ", el("code", {}, st.target_path || "(set project path above)"),
  ];
  if (st.mlx_filter_script_path) {
    infoItems.push(el("br"), "MLX filter: ", el("code", {}, st.mlx_filter_script_path),
      st.mlx_filter_script_exists ? " (exists)" : " (will be created when MLX enabled)");
  }
  pane.appendChild(el("div", { class: "info-box", style: "margin-bottom:var(--space-3);font-size:var(--fs-sm)" },
    ...infoItems,
  ));

  // Install / Uninstall button
  if (st.installed) {
    pane.appendChild(el("button", {
      class: "btn danger",
      onclick: () => onUninstallTokenFilter(),
    }, "Uninstall Token Filter"));
  } else {
    pane.appendChild(el("button", {
      class: "btn",
      onclick: () => onInstallTokenFilter(+maxInput.value, +tailInput.value, mlxToggle.checked, +mlxThresholdInput.value, mlxUrlInput.value.trim()),
    }, "Install Token Filter"));
  }
}

async function onInstallTokenFilter(maxLines, tailLines, mlxEnabled, mlxThreshold, mlxUrl) {
  if (targetPref.target === "project" && !targetPref.project_path?.trim()) {
    toast.error("Set an absolute project path above first.");
    return;
  }
  const backupCb = el("input", { type: "checkbox", checked: true });
  const h = modal((body) => {
    body.appendChild(el("h2", {}, "Install Token Filter"));
    body.appendChild(el("p", { class: "modal-sub" },
      "This will:"));
    const items = [
      el("li", {}, "Write filter script to ", el("code", {}, "~/.claude/croxy-token-filter.sh")),
      el("li", {}, "Add PreToolUse hook to ", el("code", {},
        targetPref.target === "project" ? `${targetPref.project_path}/.claude/settings.json` : "~/.claude/settings.json")),
    ];
    if (mlxEnabled) {
      items.push(el("li", {}, "Write MLX summarizer to ", el("code", {}, "~/.claude/croxy-mlx-filter.py")));
      items.push(el("li", {}, "Enable MLX classification via ", el("code", {}, mlxUrl)));
    }
    body.appendChild(el("ul", { style: "font-size:var(--fs-sm);margin:8px 0" }, ...items));
    const configLine = mlxEnabled
      ? `Max lines: ${maxLines} | Tail lines: ${tailLines} | MLX: ON (threshold: ${mlxThreshold})`
      : `Max lines: ${maxLines} | Tail lines: ${tailLines} | MLX: OFF`;
    body.appendChild(el("div", { style: "font-size:var(--fs-sm);margin:8px 0" }, configLine));
    body.appendChild(el("label", { class: "row", style: "gap:8px;align-items:center;margin-top:var(--space-3)" },
      backupCb, el("span", {}, "Back up settings.json first")));
    body.appendChild(el("div", { class: "modal-actions" },
      el("button", { class: "btn secondary", onclick: () => h.close() }, "Cancel"),
      el("button", {
        class: "btn",
        onclick: async () => {
          try {
            const res = await api("/api/ecc/token-filter/install", {
              method: "POST",
              body: {
                target: targetPref.target,
                project_path: targetPref.project_path?.trim() || null,
                backup: backupCb.checked,
                max_lines: maxLines,
                tail_lines: tailLines,
                mlx_enabled: mlxEnabled,
                mlx_threshold: mlxThreshold,
                mlx_url: mlxUrl,
              },
            });
            h.close();
            toast.success("Token filter installed.");
            installedCache = null;
            renderTokenFilter();
          } catch (e) { toast.error(e.message); }
        },
      }, "Install"),
    ));
  });
}

async function onUninstallTokenFilter() {
  const ok = await confirm("Uninstall the token filter hook and remove the script?");
  if (!ok) return;
  try {
    await api("/api/ecc/token-filter/uninstall", {
      method: "POST",
      body: {
        target: targetPref.target,
        project_path: targetPref.project_path?.trim() || null,
      },
    });
    toast.success("Token filter uninstalled.");
    installedCache = null;
    renderTokenFilter();
  } catch (e) { toast.error(e.message); }
}

// ---------- Installed sub-tab ----------

let installedCache = null;

async function renderInstalled() {
  const pane = $("#eccInstalledPane");
  if (!pane) return;
  clear(pane);
  try {
    const r = await api("/api/ecc/installs");
    installedCache = r.installs || [];
  } catch (e) { toast.error(e.message); return; }

  // Action row: export + import at the top, so empty list still shows them.
  const actionRow = el("div", { class: "row", style: "gap:8px;align-items:center;margin-bottom:var(--space-3);flex-wrap:wrap" },
    el("span", { class: "muted", style: "font-size:var(--fs-sm)" },
      `${installedCache.length} tracked install${installedCache.length === 1 ? "" : "s"}`),
    el("div", { class: "grow" }),
    el("button", { class: "btn secondary small", onclick: onExportProfile }, "Export profile"),
    el("button", { class: "btn secondary small", onclick: onImportProfile }, "Import profile…"),
  );
  pane.appendChild(actionRow);

  if (!installedCache.length) {
    pane.appendChild(emptyState({ title: "Nothing installed yet" }));
    return;
  }

  const selected = new Set();

  const tbl = el("table", { class: "stack-on-mobile" },
    el("thead", {}, el("tr", {},
      el("th", { style: "width:32px" }, ""),
      el("th", {}, "Category"),
      el("th", {}, "Item"),
      el("th", {}, "Status"),
      el("th", {}, "Target"),
      el("th", {}, "Installed"),
    )),
    el("tbody", {}),
  );
  const tbody = tbl.querySelector("tbody");
  let modifiedCount = 0;
  let upstreamChangedCount = 0;
  for (const r of installedCache) {
    const cb = el("input", {
      type: "checkbox",
      onchange: (e) => { if (e.target.checked) selected.add(r.id); else selected.delete(r.id); $("#eccInstalledCount").textContent = `${selected.size} selected`; },
    });
    const status = el("td", {});
    if (r.modified_by_user) {
      status.appendChild(el("span", { class: "badge warn", title: "On-disk content differs from what we installed" }, "modified"));
      modifiedCount++;
    }
    if (r.upstream_changed) {
      if (status.childNodes.length) status.appendChild(document.createTextNode(" "));
      status.appendChild(el("span", { class: "badge", title: "Upstream repo has a newer version" }, "upstream changed"));
      upstreamChangedCount++;
    }
    if (r.backup_path) {
      if (status.childNodes.length) status.appendChild(document.createTextNode(" "));
      status.appendChild(el("span", { class: "badge ok", title: "Original file backed up" }, "backup"));
    }
    tbody.appendChild(el("tr", {},
      el("td", {}, cb),
      el("td", {}, el("span", { class: "badge" }, r.category)),
      el("td", {}, el("code", {}, r.item_id)),
      status,
      el("td", { class: "muted", style: "font-size:var(--fs-xs)" }, r.target_dir),
      el("td", { class: "muted", style: "font-size:var(--fs-xs)" }, fmtDate(r.installed_at)),
    ));
  }
  pane.appendChild(el("div", { style: "max-height:520px;overflow:auto" }, tbl));

  const summary = [];
  if (modifiedCount) summary.push(`${modifiedCount} modified by you`);
  if (upstreamChangedCount) summary.push(`${upstreamChangedCount} updated upstream`);
  if (summary.length) {
    pane.appendChild(el("div", { class: "muted", style: "font-size:var(--fs-xs);margin-top:var(--space-2)" },
      summary.join(" • ")));
  }

  pane.appendChild(el("div", { class: "row", style: "gap:8px;align-items:center;margin-top:var(--space-3)" },
    el("span", { id: "eccInstalledCount", class: "muted" }, "0 selected"),
    el("div", { class: "grow" }),
    el("button", {
      class: "btn secondary small",
      onclick: () => {
        for (const r of installedCache) selected.add(r.id);
        $("#eccInstalledPane").querySelectorAll("tbody input[type=checkbox]").forEach((c) => c.checked = true);
        $("#eccInstalledCount").textContent = `${selected.size} selected`;
      },
    }, "Select all"),
    el("button", {
      class: "btn danger",
      onclick: async () => {
        if (!selected.size) { toast.warn("Select items to uninstall."); return; }
        const pickedModified = installedCache.filter((r) => selected.has(r.id) && r.modified_by_user).length;
        const extra = pickedModified
          ? `\n\n⚠ ${pickedModified} of these were modified on disk since install — uninstall will still remove them.`
          : "";
        const ok = await confirm({
          title: `Uninstall ${selected.size} item(s)?`,
          message: "Files and JSON entries will be removed; backups restored when available." + extra,
          confirmText: "Uninstall",
          danger: true,
        });
        if (!ok) return;
        try {
          const res = await api("/api/ecc/uninstall", { method: "POST", body: { install_ids: [...selected] } });
          toast.success(`Uninstalled ${res.removed} item(s)`);
          await renderInstalled();
        } catch (e) { toast.error(e.message); }
      },
    }, "Uninstall"),
  ));
}

async function onExportProfile() {
  try {
    const profile = await api("/api/ecc/profile/export");
    const blob = new Blob([JSON.stringify(profile, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `claude-setup-profile-${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    toast.success("Profile downloaded");
  } catch (e) { toast.error(e.message); }
}

function onImportProfile() {
  const input = el("input", { type: "file", accept: "application/json,.json" });
  input.addEventListener("change", async () => {
    const file = input.files?.[0];
    if (!file) return;
    let profile;
    try {
      profile = JSON.parse(await file.text());
    } catch (e) {
      toast.error(`Invalid JSON: ${e.message}`);
      return;
    }
    const summary = describeProfile(profile);
    const ok = await confirm({
      title: "Import install profile?",
      message: summary + "\n\nFiles, MCP servers, and hooks from the profile will be installed. Existing items are preserved (backups where applicable).",
      confirmText: "Import",
    });
    if (!ok) return;
    try {
      const res = await api("/api/ecc/profile/import", { method: "POST", body: { profile, backup: true } });
      toast.success("Profile imported");
      await renderInstalled();
    } catch (e) { toast.error(e.message); }
  });
  input.click();
}

function describeProfile(profile) {
  if (!profile || typeof profile !== "object") return "Not a valid profile.";
  const files = profile.files?.items?.length || 0;
  const mcp = profile.mcp?.server_ids?.length || 0;
  const hooks = profile.hooks?.installed ? "yes" : "no";
  return `version ${profile.version}, exported ${profile.exported_at || "?"}\n` +
    `• ${files} file item(s) (agents/skills/commands/rules)\n` +
    `• ${mcp} MCP server(s)\n` +
    `• hooks: ${hooks}`;
}

function showInstallModal({ items, plan, label }) {
  const backupCb = el("input", { type: "checkbox", checked: true });
  const targetLabel = plan.target_dir;

  const createList = plan.entries.filter((e) => !e.exists);
  const overwriteList = plan.entries.filter((e) => e.exists);

  const listBox = (title, entries, emptyText) => {
    const box = el("div", { style: "margin-top:var(--space-3)" },
      el("div", { style: "font-weight:600;margin-bottom:6px" }, `${title} (${entries.length})`),
    );
    if (!entries.length) {
      box.appendChild(el("div", { class: "muted", style: "font-size:var(--fs-sm)" }, emptyText));
      return box;
    }
    const list = el("div", { style: "max-height:200px;overflow:auto;font-family:var(--font-mono);font-size:var(--fs-xs);background:var(--surface-2);border-radius:var(--radius);padding:var(--space-2)" });
    for (const e of entries.slice(0, 200)) {
      list.appendChild(el("div", {}, `${e.category}/${e.id}  →  ${e.dest}`));
    }
    if (entries.length > 200) list.appendChild(el("div", { class: "muted" }, `… +${entries.length - 200} more`));
    box.appendChild(list);
    return box;
  };

  let applying = false;

  const h = modal((body) => {
    body.appendChild(el("h2", {}, "Install plan"));
    body.appendChild(el("p", { class: "modal-sub" },
      `Install ${label} into `, el("code", {}, targetLabel), "."));

    if (plan.missing?.length) {
      body.appendChild(el("div", { class: "info-box", style: "margin-top:var(--space-3)" },
        el("strong", {}, `⚠ ${plan.missing.length} item(s) no longer exist in the catalog — skipped.`)));
    }

    body.appendChild(listBox("Create", createList, "Nothing new to create."));
    body.appendChild(listBox("Overwrite", overwriteList, "No files will be overwritten."));

    body.appendChild(el("label", { class: "row", style: "gap:8px;align-items:center;margin-top:var(--space-3)" },
      backupCb,
      el("span", {}, "Back up existing files to ", el("code", {}, "*.bak.<timestamp>")),
    ));

    const applyBtn = el("button", {
      class: "btn",
      onclick: async () => {
        if (applying) return;
        applying = true;
        applyBtn.disabled = true;
        applyBtn.textContent = "Installing…";
        try {
          const res = await api("/api/ecc/install/apply", {
            method: "POST",
            body: {
              items,
              target: targetPref.target,
              project_path: targetPref.project_path?.trim() || null,
              backup: backupCb.checked,
            },
          });
          const r = res.result || {};
          h.close();
          const msg = `Installed ${r.installed}${r.backed_up?.length ? `, backed up ${r.backed_up.length}` : ""}${r.errors?.length ? `, ${r.errors.length} error(s)` : ""}`;
          if (r.errors?.length) toast.error(msg);
          else toast.success(msg);
          browseSelection.clear();
          updateSelectedCount();
          installedCache = null;
        } catch (e) {
          toast.error(e.message);
          applyBtn.disabled = false;
          applyBtn.textContent = "Install";
          applying = false;
        }
      },
    }, "Install");

    body.appendChild(el("div", { class: "modal-actions" },
      el("button", { class: "btn secondary", onclick: () => h.close() }, "Cancel"),
      applyBtn,
    ));
  });
}
