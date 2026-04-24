// App shell renderer: sidebar + topbar. Mounts into <body class="app"> with a
// <div id="nav"></div> placeholder. Pages pass page title and active-link key.

import { el, clear, $, appendChildren } from "./dom.js";
import { toggleTheme, getTheme } from "./theme.js";
import { logout } from "./api.js";
import { icons, toast } from "./ui.js";

function svg(src) {
  const s = document.createElement("span");
  s.className = "icon";
  s.innerHTML = src;
  return s;
}

function navLink({ href, label, iconName, active, onclick }) {
  const a = el("a", { href, class: "nav-link" + (active ? " active" : "") },
    svg(icons[iconName] || icons.home),
    el("span", {}, label));
  if (onclick) a.addEventListener("click", onclick);
  return a;
}

export function renderShell({ me, active, pageTitle, actions = [] }) {
  const bodyClassEl = document.body;
  bodyClassEl.classList.add("app");

  // Build sidebar
  const brand = el("div", { class: "sidebar-brand" },
    el("span", { class: "logo" }, "C"),
    el("span", {}, "Croxy"));

  const links = el("div", { class: "nav-links" });
  links.appendChild(el("div", { class: "sidebar-section-label" }, "Workspace"));
  links.appendChild(navLink({
    href: "/ui/admin", label: "Overview", iconName: "home",
    active: active === "admin-overview",
  }));
  links.appendChild(navLink({
    href: "/ui/admin#keys", label: "Virtual keys", iconName: "key",
    active: active === "admin-keys",
  }));
  links.appendChild(navLink({
    href: "/ui/admin#provider", label: "Provider", iconName: "shield",
    active: active === "admin-provider",
  }));
  links.appendChild(navLink({
    href: "/ui/admin#mlx", label: "MLX Inference", iconName: "cpu",
    active: active === "admin-mlx",
  }));
  links.appendChild(navLink({
    href: "/ui/admin#ecc", label: "Claude Setup", iconName: "plus",
    active: active === "admin-ecc",
  }));

  const footer = el("div", { class: "sidebar-footer stack" },
    el("div", { class: "row", style: "gap:8px" },
      el("div", {
        class: "avatar",
        style: "width:32px;height:32px;border-radius:50%;background:var(--accent-soft);color:var(--accent);font-weight:700;display:grid;place-items:center;flex:none"
      },
        (me?.email || "?").slice(0, 1).toUpperCase()),
      el("div", { style: "min-width:0;flex:1 1 auto" },
        el("div", { class: "truncate", style: "font-weight:600" }, me?.email || ""),
        el("div", { class: "text-xs muted" }, me?.role || ""))),
    el("button", {
      class: "btn secondary small",
      id: "changePwBtn",
      style: "width:100%;gap:6px",
    }, "Change password"),
    el("button", {
      class: "btn secondary small",
      onclick: () => logout(),
      style: "width:100%;gap:6px",
    }, svg(icons.logout), "Log out"));

  const sidebar = el("aside", { class: "sidebar", id: "sidebar", "aria-label": "Primary" },
    brand, links, footer);

  const backdrop = el("div", { class: "sidebar-backdrop", id: "sidebar-backdrop" });

  // Build topbar
  const hamburger = el("button", {
    class: "hamburger", "aria-label": "Open navigation",
    onclick: () => openDrawer(),
  });
  hamburger.innerHTML = icons.menu;

  const titleEl = el("h1", { class: "page-title" }, pageTitle || "");

  const themeBtn = el("button", {
    class: "theme-toggle",
    title: "Toggle theme",
    "aria-label": "Toggle theme",
    onclick: () => {
      toggleTheme();
      updateThemeIcon();
    },
  });
  function updateThemeIcon() {
    themeBtn.innerHTML = getTheme() === "light" ? icons.moon : icons.sun;
  }
  updateThemeIcon();
  document.documentElement.addEventListener("theme:change", updateThemeIcon);

  const topbar = el("header", { class: "topbar" },
    hamburger,
    titleEl,
    el("div", { class: "topbar-actions" },
      ...actions,
      themeBtn));

  // Mount: replace any pre-existing #nav placeholder and insert shell children.
  const nav = $("#nav");
  if (nav) nav.remove();

  document.body.insertBefore(sidebar, document.body.firstChild);
  document.body.insertBefore(backdrop, sidebar.nextSibling);
  document.body.insertBefore(topbar, backdrop.nextSibling);

  // Drawer behavior on mobile/tablet.
  backdrop.addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeDrawer();
  });

  return { sidebar, backdrop, topbar, titleEl };
}

export function openDrawer() {
  $("#sidebar")?.classList.add("open");
  $("#sidebar-backdrop")?.classList.add("show");
}

export function closeDrawer() {
  $("#sidebar")?.classList.remove("open");
  $("#sidebar-backdrop")?.classList.remove("show");
}
