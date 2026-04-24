// Awesome-Claude-Code catalog browser (view-only, external links).
// Renders into #setupAccPane built by setup.js.

import { api } from "./api.js";
import { el, clear, $, fmtRelDate } from "./dom.js";
import { toast, emptyState } from "./ui.js";

let built = false;
let status = null;
let categories = [];
let items = [];
let activeCategory = "all";
let query = "";
let debounceTimer = null;

export async function loadAcc() {
  buildShell();
  try {
    status = await api("/api/acc/status");
  } catch (e) { toast.error(e.message); return; }
  renderStatus();
  if (status.cached) {
    await fetchCatalog();
    renderCategories();
    renderRows();
  } else {
    renderNeedsSync();
  }
}

function buildShell() {
  if (built) return;
  const pane = $("#setupAccPane");
  if (!pane) return;
  clear(pane);

  pane.appendChild(el("div", { class: "card" },
    el("div", { class: "card-header" }, el("h2", {}, "Source: awesome-claude-code")),
    el("p", { class: "muted", style: "margin:0 0 var(--space-3)" },
      "A curated list of external Claude Code resources (skills, tools, status lines, hooks, clients). This is a ",
      el("em", {}, "link catalog"),
      " — click any project to visit its own install instructions. Nothing is installed automatically."),
    el("div", { class: "row", style: "gap:8px;align-items:center" },
      el("span", { id: "accStatusMeta", class: "muted", style: "font-size:var(--fs-sm)" }, "Checking…"),
      el("div", { class: "grow" }),
      el("a", { href: "https://github.com/hesreallyhim/awesome-claude-code", target: "_blank", rel: "noopener",
        class: "btn secondary small", style: "text-decoration:none" }, "View repo"),
      el("button", { class: "btn", id: "accSyncBtn", onclick: doSync }, "Sync now"),
    ),
  ));
  pane.appendChild(el("div", { class: "card" },
    el("input", { type: "search", id: "accSearch", placeholder: "Search name, description, author…",
      style: "width:100%;margin-bottom:var(--space-3)",
      oninput: (e) => { query = e.target.value; scheduleRender(); } }),
    el("div", { id: "accCategories", class: "row", style: "gap:6px;flex-wrap:wrap;margin-bottom:var(--space-3)" }),
    el("div", { style: "max-height:640px;overflow:auto" },
      el("table", { id: "accTable", class: "stack-on-mobile" },
        el("thead", {}, el("tr", {},
          el("th", {}, "Project"),
          el("th", {}, "Category"),
          el("th", {}, "Author"),
          el("th", {}, "Description"),
          el("th", {}, ""))),
        el("tbody", {}),
      ),
    ),
    el("div", { id: "accFooter", class: "muted", style: "margin-top:var(--space-2);font-size:var(--fs-xs)" }, ""),
  ));

  built = true;
}

function renderStatus() {
  const meta = $("#accStatusMeta");
  if (!meta) return;
  clear(meta);
  if (!status?.cached) {
    meta.appendChild(el("span", { class: "badge off" }, "not synced"));
    return;
  }
  const stale = isStale(status.synced_at, 24);
  meta.appendChild(el("span", { class: "badge " + (stale ? "warn" : "ok") }, stale ? "stale" : "cached"));
  if (status.synced_at) {
    meta.appendChild(document.createTextNode(` • synced ${fmtRelDate(status.synced_at)}`));
  }
  if (status.size_bytes) {
    meta.appendChild(document.createTextNode(` • ${(status.size_bytes / 1024).toFixed(1)} KB`));
  }
  if (stale) {
    meta.appendChild(el("span", { class: "muted", style: "margin-left:8px;font-size:var(--fs-xs)" },
      "(>24h old — consider syncing)"));
  }
}

function isStale(isoString, hoursThreshold) {
  if (!isoString) return false;
  const t = new Date(isoString).getTime();
  if (isNaN(t)) return false;
  return (Date.now() - t) > hoursThreshold * 3600 * 1000;
}

async function doSync() {
  const btn = $("#accSyncBtn");
  if (!btn) return;
  btn.disabled = true;
  const prev = btn.textContent;
  btn.textContent = "Syncing…";
  try {
    status = await api("/api/acc/sync", { method: "POST" });
    renderStatus();
    await fetchCatalog();
    renderCategories();
    renderRows();
    toast.success("Catalog refreshed");
  } catch (e) {
    toast.error(e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = prev;
  }
}

function renderNeedsSync() {
  const tbody = $("#accTable tbody");
  if (tbody) {
    clear(tbody);
    tbody.appendChild(el("tr", {},
      el("td", { colspan: 5, style: "padding:var(--space-4)" },
        emptyState({ title: "Sync the catalog first", message: "Click ‘Sync now’ to download the resources list." }))));
  }
}

async function fetchCatalog() {
  try {
    const res = await api(`/api/acc/catalog?category=${encodeURIComponent(activeCategory)}&q=${encodeURIComponent(query)}`);
    items = res.items || [];
    if (activeCategory === "all") categories = res.categories || [];
  } catch (e) {
    toast.error(e.message);
    items = [];
  }
}

function renderCategories() {
  const host = $("#accCategories");
  if (!host) return;
  clear(host);
  const mk = (id, label, count) => el("button", {
    class: "btn secondary small",
    style: activeCategory === id ? "" : "opacity:0.7",
    onclick: async () => {
      activeCategory = id;
      await fetchCatalog();
      renderCategories();
      renderRows();
    },
  }, count != null ? `${label} (${count})` : label);
  const total = categories.reduce((n, c) => n + c.count, 0);
  host.appendChild(mk("all", "All", total));
  for (const c of categories) host.appendChild(mk(c.name, c.name, c.count));
}

function renderRows() {
  const tbody = $("#accTable tbody");
  const footer = $("#accFooter");
  if (!tbody) return;
  clear(tbody);
  if (!items.length) {
    tbody.appendChild(el("tr", {},
      el("td", { colspan: 5, style: "padding:var(--space-4)" },
        emptyState({ title: "No results" }))));
    if (footer) footer.textContent = "";
    return;
  }
  const slice = items.slice(0, 500);
  for (const it of slice) {
    const primary = el("a", { href: it.url || "#", target: "_blank", rel: "noopener" },
      el("strong", {}, it.name));
    const secondary = it.secondary_url ? el("a", {
      href: it.secondary_url, target: "_blank", rel: "noopener",
      class: "muted", style: "font-size:var(--fs-xs);margin-left:8px;text-decoration:none",
    }, "(also)") : null;
    const catCell = el("td", {},
      el("span", { class: "badge" }, it.category),
      it.subcategory ? el("span", { class: "muted", style: "font-size:var(--fs-xs);margin-left:6px" }, it.subcategory) : null,
    );
    const authorCell = it.author_url
      ? el("td", {}, el("a", { href: it.author_url, target: "_blank", rel: "noopener", class: "muted" }, it.author || ""))
      : el("td", { class: "muted" }, it.author || "");
    tbody.appendChild(el("tr", {},
      el("td", {}, primary, secondary),
      catCell,
      authorCell,
      el("td", { class: "muted", style: "font-size:var(--fs-sm)" }, it.description || ""),
      el("td", {},
        el("a", {
          href: it.url || "#", target: "_blank", rel: "noopener",
          class: "btn secondary small", style: "text-decoration:none",
        }, "Open →")),
    ));
  }
  if (footer) {
    footer.textContent = items.length > 500
      ? `Showing first 500 of ${items.length}. Narrow your search to see more.`
      : `${items.length} project${items.length === 1 ? "" : "s"}`;
  }
}

function scheduleRender() {
  if (debounceTimer) clearTimeout(debounceTimer);
  debounceTimer = setTimeout(async () => {
    await fetchCatalog();
    renderRows();
  }, 300);
}
