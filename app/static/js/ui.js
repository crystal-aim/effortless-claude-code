// UI primitives: toast, modal (+ confirm/prompt variants), skeleton, empty-state,
// copyToClipboard. Depends only on dom.js.

import { el, clear, $, $$, appendChildren } from "./dom.js";

// ---------- Icons (inline SVG strings for small footprint) ----------
export const icons = {
  sun:  '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"/></svg>',
  moon: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>',
  menu: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18M3 12h18M3 18h18"/></svg>',
  close: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6L6 18M6 6l12 12"/></svg>',
  check: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>',
  warn:  '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><path d="M12 9v4M12 17h.01"/></svg>',
  info:  '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/></svg>',
  copy:  '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>',
  key:   '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4"/></svg>',
  users: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/></svg>',
  home:  '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M9 22V12h6v10"/></svg>',
  shield:'<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>',
  logout:'<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="M16 17l5-5-5-5M21 12H9"/></svg>',
  plus:  '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5v14M5 12h14"/></svg>',
  search:'<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/></svg>',
  inbox: '<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/></svg>',
  cpu:   '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/><path d="M9 1v3M15 1v3M9 20v3M15 20v3M20 9h3M20 14h3M1 9h3M1 14h3"/></svg>',
};

function iconSpan(name) {
  const s = el("span", { class: "icon" });
  s.innerHTML = icons[name] || "";
  return s;
}

// ---------- Toasts ----------
let toastRoot = null;

function ensureToastRoot() {
  if (toastRoot && document.body.contains(toastRoot)) return toastRoot;
  toastRoot = el("div", { class: "toast-root", role: "status", "aria-live": "polite" });
  document.body.appendChild(toastRoot);
  return toastRoot;
}

export function toast(message, opts = {}) {
  const { type = "info", duration = 4000 } = opts;
  const root = ensureToastRoot();
  const iconName =
    type === "success" ? "check" :
    type === "error"   ? "warn"  :
    type === "warn"    ? "warn"  : "info";
  const node = el("div", { class: "toast " + type, role: "alert" },
    Object.assign(el("span", { class: "toast-icon" }), { innerHTML: icons[iconName] }),
    el("div", { class: "toast-body" }, message),
    el("button", { class: "toast-close", "aria-label": "Dismiss",
      onclick: () => dismiss() }, (() => {
        const x = el("span"); x.innerHTML = icons.close; return x;
      })()));
  root.appendChild(node);
  let timer = null;
  const dismiss = () => {
    if (!node.isConnected) return;
    node.classList.add("out");
    setTimeout(() => node.remove(), 200);
  };
  if (duration > 0) {
    timer = setTimeout(dismiss, duration);
    node.addEventListener("mouseenter", () => timer && clearTimeout(timer));
    node.addEventListener("mouseleave", () => {
      timer = setTimeout(dismiss, 2000);
    });
  }
  return { dismiss };
}

toast.success = (m, d) => toast(m, { ...d, type: "success" });
toast.error   = (m, d) => toast(m, { ...d, type: "error" });
toast.warn    = (m, d) => toast(m, { ...d, type: "warn" });
toast.info    = (m, d) => toast(m, { ...d, type: "info" });

// ---------- Modal ----------
const modalStack = [];

export function modal(content, opts = {}) {
  const prevActive = document.activeElement;
  const backdrop = el("div", { class: "modal-backdrop", role: "dialog", "aria-modal": "true" });
  const body = el("div", { class: "modal" });
  if (typeof content === "function") content(body);
  else appendChildren(body, Array.isArray(content) ? content : [content]);
  backdrop.appendChild(body);

  backdrop.addEventListener("click", (e) => {
    if (e.target === backdrop && opts.dismissOnBackdrop !== false) close();
  });

  const onKey = (e) => {
    if (e.key === "Escape" && opts.dismissOnEsc !== false) { e.preventDefault(); close(); }
  };
  document.addEventListener("keydown", onKey);
  document.body.appendChild(backdrop);

  const first = body.querySelector(
    'input, select, textarea, button, [tabindex]:not([tabindex="-1"])',
  );
  if (first) setTimeout(() => first.focus(), 20);

  const handle = {
    close() { close(); },
    body,
  };
  modalStack.push(handle);

  function close() {
    if (!backdrop.isConnected) return;
    document.removeEventListener("keydown", onKey);
    backdrop.remove();
    const i = modalStack.indexOf(handle);
    if (i >= 0) modalStack.splice(i, 1);
    if (prevActive && typeof prevActive.focus === "function") prevActive.focus();
    opts.onClose?.();
  }

  return handle;
}

export function closeAllModals() {
  while (modalStack.length) modalStack.pop().close();
}

export function confirm({
  title = "Are you sure?",
  message = "",
  confirmText = "Confirm",
  cancelText = "Cancel",
  danger = false,
} = {}) {
  return new Promise((resolve) => {
    let resolved = false;
    const done = (v) => { if (!resolved) { resolved = true; resolve(v); h.close(); } };
    const h = modal((body) => {
      appendChildren(body, [
        el("h2", {}, title),
        message ? el("p", { class: "modal-sub" }, message) : null,
        el("div", { class: "modal-actions" },
          el("button", { class: "btn secondary", onclick: () => done(false) }, cancelText),
          el("button", {
            class: "btn " + (danger ? "danger" : ""),
            onclick: () => done(true),
          }, confirmText)),
      ]);
    }, { onClose: () => done(false) });
  });
}

export function prompt({
  title = "Enter a value",
  message = "",
  label = "",
  placeholder = "",
  type = "text",
  initial = "",
  confirmText = "Save",
  cancelText = "Cancel",
} = {}) {
  return new Promise((resolve) => {
    let resolved = false;
    const done = (v) => { if (!resolved) { resolved = true; resolve(v); h.close(); } };
    const input = el("input", { type, placeholder, value: initial });
    const err = el("div", { class: "form-error hidden" });
    const submit = () => {
      if (input.value === "" && type !== "password") {
        err.textContent = "Value required";
        err.classList.remove("hidden");
        return;
      }
      done(input.value);
    };
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); submit(); }
    });
    const h = modal((body) => {
      appendChildren(body, [
        el("h2", {}, title),
        message ? el("p", { class: "modal-sub" }, message) : null,
        el("div", { class: "modal-body" },
          el("div", { class: "field" },
            label ? el("label", {}, label) : null,
            input,
            err)),
        el("div", { class: "modal-actions" },
          el("button", { class: "btn secondary", onclick: () => done(null) }, cancelText),
          el("button", { class: "btn", onclick: submit }, confirmText)),
      ]);
    }, { onClose: () => done(null) });
  });
}

// ---------- Skeleton ----------
export function skeletonTable(tbody, rows = 5, cols = 4) {
  clear(tbody);
  for (let r = 0; r < rows; r++) {
    const tr = el("tr");
    for (let c = 0; c < cols; c++) {
      tr.appendChild(el("td", {},
        el("div", { class: "skeleton line md" })));
    }
    tbody.appendChild(tr);
  }
}

export function skeletonStats(grid, count = 4) {
  clear(grid);
  for (let i = 0; i < count; i++) {
    grid.appendChild(el("div", { class: "stat" },
      el("div", { class: "skeleton line" }),
      el("div", { class: "skeleton line lg", style: "margin-top:8px;width:60%" })));
  }
}

// ---------- Empty state ----------
export function emptyState({ icon = "inbox", title, message, action } = {}) {
  const iconEl = el("div", { class: "empty-icon" });
  iconEl.innerHTML = icons[icon] || icons.inbox;
  const node = el("div", { class: "empty" },
    iconEl,
    el("h3", {}, title || "Nothing here yet"),
    message ? el("p", {}, message) : null,
    action || null);
  return node;
}

// Render an empty state inside a <tbody> as a single full-width row.
export function emptyTableRow(colspan, props) {
  const td = el("td", { colspan, style: "padding:0" }, emptyState(props));
  return el("tr", {}, td);
}

// ---------- Copy ----------
export async function copyToClipboard(text) {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
    } else {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.left = "-9999px";
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      ta.remove();
    }
    toast.success("Copied to clipboard");
    return true;
  } catch (e) {
    toast.error("Copy failed — select the text manually");
    return false;
  }
}

// Re-export a few dom helpers for ergonomic use by page scripts.
export { el, clear, $, $$, iconSpan };
