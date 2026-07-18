// Workspace switcher — flip the whole UI between built dossiers (companies).
import { esc } from "./fmt.js";

async function init() {
  let list;
  try {
    const r = await fetch("/api/workspaces");
    if (!r.ok) return;
    list = await r.json();
  } catch { return; }
  if (!Array.isArray(list) || list.length === 0) return;

  const cur = list.find((w) => w.current) || list[0];
  // Brand subtitle shows the actual company of the active dossier.
  const sub = document.getElementById("brand-sub");
  if (sub && cur.company) sub.textContent = `${cur.company} · ${cur.dossier}`;
  if (list.length < 2) return;   // nothing to switch between

  const sel = document.createElement("select");
  sel.id = "ws-select";
  sel.title = "Dossier / company workspace";
  sel.innerHTML = list.map((w) => `
    <option value="${esc(w.id)}"${w.current ? " selected" : ""}>
      ${esc(w.company)} — ${esc(w.dossier)}${w.n_findings != null
        ? ` (${w.n_findings})` : ""}
    </option>`).join("");
  sel.addEventListener("change", async () => {
    sel.disabled = true;
    try {
      const r = await fetch("/api/workspace", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: sel.value }),
      });
      if (r.ok) { location.reload(); return; }
      alert(`Switch failed: HTTP ${r.status}`);
    } catch (e) {
      alert(`Switch failed: ${e}`);
    }
    sel.disabled = false;
  });

  const right = document.querySelector(".topbar-right");
  if (right) right.insertBefore(sel, right.firstChild);

  // app.js rewrites the brand subtitle on locale toggle — restore the
  // active company label afterwards.
  document.addEventListener("click", (ev) => {
    if (ev.target.closest("#lang-toggle button") && sub && cur.company)
      setTimeout(() => { sub.textContent = `${cur.company} · ${cur.dossier}`; }, 0);
  });
}

init();
