// Theme: reads localStorage, falls back to system preference, exposes toggle().
// Dispatches a `theme:change` CustomEvent on <html> so charts can redraw.

const KEY = "theme";

export function getTheme() {
  return document.documentElement.dataset.theme || "dark";
}

export function setTheme(next) {
  const t = next === "light" ? "light" : "dark";
  document.documentElement.dataset.theme = t;
  try { localStorage.setItem(KEY, t); } catch {}
  document.documentElement.dispatchEvent(
    new CustomEvent("theme:change", { detail: { theme: t } }),
  );
}

export function toggleTheme() {
  setTheme(getTheme() === "light" ? "dark" : "light");
}

// Read CSS custom property values at runtime (for Chart.js etc.).
export function cssVar(name) {
  return getComputedStyle(document.documentElement)
    .getPropertyValue(name)
    .trim();
}

// Read a whole bundle of chart-relevant tokens.
export function chartPalette() {
  return {
    accent: cssVar("--accent"),
    axis: cssVar("--chart-axis"),
    grid: cssVar("--chart-grid"),
    fill: cssVar("--chart-fill"),
    text: cssVar("--text"),
  };
}

// Re-fire on system preference change if user never set an explicit theme.
if (typeof window !== "undefined" && window.matchMedia) {
  const mq = window.matchMedia("(prefers-color-scheme: light)");
  mq.addEventListener?.("change", (e) => {
    try {
      if (localStorage.getItem(KEY)) return; // user chose; don't override
    } catch {}
    document.documentElement.dataset.theme = e.matches ? "light" : "dark";
    document.documentElement.dispatchEvent(
      new CustomEvent("theme:change", {
        detail: { theme: document.documentElement.dataset.theme },
      }),
    );
  });
}
