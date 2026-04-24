// Claude Setup panel orchestrator — top-level switcher between:
//   • ECC (everything-claude-code): auto-install agents/skills/MCP/hooks
//   • ACC (awesome-claude-code):    catalog browser with external links
//
// Builds the #panel-ecc shell once, then delegates to ecc.js / acc.js
// based on the active source tab.

import { el, clear, $ } from "./dom.js";
import { loadEcc } from "./ecc.js";
import { loadAcc } from "./acc.js";

let built = false;
let active = "ecc";

export async function loadSetup() {
  buildShell();
  showSource(active);
}

function buildShell() {
  if (built) return;
  const panel = $("#panel-ecc");
  if (!panel) return;
  clear(panel);

  const tabs = el("div", { class: "row", style: "gap:8px;margin-bottom:var(--space-4);flex-wrap:wrap" },
    tabBtn("ecc", "Everything-CC (auto-install)"),
    tabBtn("acc", "Awesome-CC (link catalog)"),
  );
  panel.appendChild(tabs);
  panel.appendChild(el("div", { id: "setupEccPane" }));
  panel.appendChild(el("div", { id: "setupAccPane", class: "hidden" }));

  built = true;
}

function tabBtn(key, label) {
  return el("button", {
    class: "btn small" + (active === key ? "" : " secondary"),
    id: `setupSource_${key}`,
    onclick: () => showSource(key),
  }, label);
}

function showSource(key) {
  active = key;
  $("#setupSource_ecc")?.classList.toggle("secondary", key !== "ecc");
  $("#setupSource_acc")?.classList.toggle("secondary", key !== "acc");
  $("#setupEccPane")?.classList.toggle("hidden", key !== "ecc");
  $("#setupAccPane")?.classList.toggle("hidden", key !== "acc");
  if (key === "ecc") loadEcc();
  if (key === "acc") loadAcc();
}
