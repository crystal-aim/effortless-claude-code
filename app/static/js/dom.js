// Tiny DOM helpers + formatters. Kept standalone — no framework.

export function el(tag, attrs = {}, ...children) {
  const e = document.createElement(tag);
  for (const k in attrs) {
    const v = attrs[k];
    if (v === undefined || v === null || v === false) continue;
    if (k === "class" || k === "className") e.className = v;
    else if (k === "style" && typeof v === "object") Object.assign(e.style, v);
    else if (k === "dataset" && typeof v === "object") Object.assign(e.dataset, v);
    else if (k.startsWith("on") && typeof v === "function") {
      e.addEventListener(k.slice(2).toLowerCase(), v);
    } else if (k === "html") {
      e.innerHTML = v;
    } else {
      e.setAttribute(k, v === true ? "" : v);
    }
  }
  appendChildren(e, children);
  return e;
}

export function appendChildren(parent, children) {
  for (const c of children) {
    if (c == null || c === false) continue;
    if (Array.isArray(c)) appendChildren(parent, c);
    else parent.appendChild(
      typeof c === "string" || typeof c === "number"
        ? document.createTextNode(String(c))
        : c,
    );
  }
}

export function clear(node) {
  while (node && node.firstChild) node.removeChild(node.firstChild);
}

export function $(sel, root = document) { return root.querySelector(sel); }
export function $$(sel, root = document) { return Array.from(root.querySelectorAll(sel)); }

export function fmtUsd(n) {
  if (n == null) return "—";
  const v = Number(n);
  return v.toLocaleString("en-US", {
    style: "currency", currency: "USD",
    minimumFractionDigits: 2, maximumFractionDigits: 4,
  });
}

export function fmtInt(n) {
  if (n == null) return "—";
  return Number(n).toLocaleString();
}

export function fmtDate(s) {
  if (!s) return "—";
  const d = new Date(s);
  if (isNaN(d)) return s;
  return d.toLocaleString(undefined, {
    year: "numeric", month: "short", day: "2-digit",
    hour: "2-digit", minute: "2-digit",
  });
}

export function fmtRelDate(s) {
  if (!s) return "—";
  const d = new Date(s);
  if (isNaN(d)) return s;
  const diff = d - Date.now();
  const days = Math.round(diff / 86400000);
  if (Math.abs(days) < 1) return "today";
  if (days > 0) return "in " + days + "d";
  return Math.abs(days) + "d ago";
}

