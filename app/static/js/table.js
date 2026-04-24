// Table enhancement: adds a search input + click-to-sort headers to an
// existing <table>. Operates on the DOM — works with whatever page code
// populates the table body.

import { el, $$ } from "./dom.js";
import { icons } from "./ui.js";

/**
 * @param {HTMLTableElement} table
 * @param {object} opts
 * @param {string}   [opts.searchPlaceholder] -- if omitted, no search input
 * @param {number[]} [opts.searchColumns]     -- column indices to search (defaults: all)
 * @param {boolean}  [opts.sortable]          -- default true
 * @param {Array<{label:string, value:string, test:(tr:HTMLTableRowElement)=>boolean}>} [opts.filters]
 *        -- optional filter pills; rendered next to search
 */
export function enhance(table, opts = {}) {
  const {
    searchPlaceholder,
    searchColumns,
    sortable = true,
    filters,
  } = opts;

  const toolbar = el("div", { class: "table-toolbar" });
  const searchWrap = el("div", {
    class: "search",
    style: "position:relative;display:flex;align-items:center",
  });
  let input = null;
  if (searchPlaceholder !== undefined && searchPlaceholder !== null) {
    const iconEl = el("span", {
      style: "position:absolute;left:10px;color:var(--muted);display:flex",
    });
    iconEl.innerHTML = icons.search;
    input = el("input", {
      type: "search",
      placeholder: searchPlaceholder,
      style: "padding-left:32px",
    });
    searchWrap.appendChild(iconEl);
    searchWrap.appendChild(input);
    toolbar.appendChild(searchWrap);
  }

  let activeFilter = null;
  if (filters?.length) {
    const select = el("select", {},
      el("option", { value: "" }, "All"),
      ...filters.map((f) => el("option", { value: f.value }, f.label)));
    select.addEventListener("change", () => {
      const v = select.value;
      activeFilter = v ? filters.find((f) => f.value === v) : null;
      apply();
    });
    toolbar.appendChild(select);
  }

  // Insert toolbar just before table.
  table.parentNode.insertBefore(toolbar, table);

  // Sortable headers.
  if (sortable) {
    const ths = $$("thead th", table);
    ths.forEach((th, idx) => {
      if (th.dataset.noSort !== undefined || !th.textContent.trim()) return;
      th.classList.add("sortable");
      th.addEventListener("click", () => {
        const current = th.dataset.sortDir;
        ths.forEach((x) => delete x.dataset.sortDir);
        th.dataset.sortDir = current === "asc" ? "desc" : "asc";
        sortBy(table, idx, th.dataset.sortDir);
        apply();
      });
    });
  }

  // Keep original body order so search/filter doesn't permanently drop rows.
  // We toggle display instead.
  function apply() {
    const q = (input?.value || "").trim().toLowerCase();
    const tbody = table.tBodies[0];
    if (!tbody) return;
    const rows = Array.from(tbody.rows);
    rows.forEach((tr) => {
      const matchesSearch =
        !q ||
        (searchColumns || rangeAll(tr.cells.length)).some((ci) => {
          const cell = tr.cells[ci];
          return cell && cell.textContent.toLowerCase().includes(q);
        });
      const matchesFilter = !activeFilter || activeFilter.test(tr);
      tr.style.display = (matchesSearch && matchesFilter) ? "" : "none";
    });
  }

  if (input) input.addEventListener("input", apply);

  return { apply };
}

function rangeAll(n) {
  const a = new Array(n);
  for (let i = 0; i < n; i++) a[i] = i;
  return a;
}

function sortBy(table, colIdx, dir) {
  const tbody = table.tBodies[0];
  if (!tbody) return;
  const rows = Array.from(tbody.rows);
  const sign = dir === "asc" ? 1 : -1;
  const isNumeric = rows.every((tr) => {
    const c = tr.cells[colIdx];
    if (!c) return true;
    if (c.classList.contains("num")) return true;
    const t = c.textContent.trim();
    return t === "" || t === "—" || !isNaN(parseFloat(t.replace(/[,$]/g, "")));
  });
  rows.sort((a, b) => {
    const ta = (a.cells[colIdx]?.textContent || "").trim();
    const tb = (b.cells[colIdx]?.textContent || "").trim();
    if (isNumeric) {
      const na = parseFloat(ta.replace(/[,$]/g, ""));
      const nb = parseFloat(tb.replace(/[,$]/g, ""));
      const aN = isNaN(na) ? -Infinity : na;
      const bN = isNaN(nb) ? -Infinity : nb;
      return sign * (aN - bN);
    }
    return sign * ta.localeCompare(tb, undefined, { sensitivity: "base" });
  });
  rows.forEach((r) => tbody.appendChild(r));
}
