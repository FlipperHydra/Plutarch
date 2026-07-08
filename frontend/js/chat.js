/* Chat UI: streaming, tool-call disclosure, top-3 render. */
window.chat_mod = (() => {
  const log     = () => document.getElementById("chat-log");
  const input   = () => document.getElementById("chat-input");
  const sendBtn = () => document.getElementById("chat-send");
  const discl   = () => document.getElementById("tool-disclosure");
  const stepsCB = () => document.getElementById("steps-toggle");

  let onOpenNote = () => {};

  function setOpenNoteHandler(fn) { onOpenNote = fn; }

  function makeMsg(cls) {
    const el = document.createElement("div");
    el.className = "msg " + cls;
    log().appendChild(el);
    log().scrollTop = log().scrollHeight;
    return el;
  }

  function showDisclosure() { return !!discl().checked; }

  function renderTop3(cards) {
    if (!cards.length) return;
    const wrap = document.createElement("div");
    wrap.className = "top3-card";
    const head = document.createElement("div");
    head.innerHTML = "<b>Top matches</b>";
    wrap.appendChild(head);
    for (const c of cards) {
      const row = document.createElement("div");
      row.className = "top3-item";
      row.innerHTML = `
        <span class="badge">${c.score}</span>
        <div>
          <div><b></b></div>
          <div class="muted"></div>
        </div>
        <button class="ghost open-btn">Open →</button>`;
      row.querySelector("b").textContent = c.title;
      row.querySelector(".muted").textContent = c.reason;
      row.querySelector(".open-btn").addEventListener("click", () => onOpenNote(c.note_id));
      wrap.appendChild(row);
    }
    log().appendChild(wrap);
    log().scrollTop = log().scrollHeight;
  }

  async function send() {
    const text = input().value.trim();
    if (!text) return;
    input().value = "";
    sendBtn().disabled = true;

    const userMsg = makeMsg("user"); userMsg.textContent = text;
    const asstMsg = makeMsg("assistant"); asstMsg.textContent = "";
    const topCards = [];

    try {
      await api.sse("/chat/stream", { message: text }, ev => {
        if (ev.type === "token")     asstMsg.textContent += ev.text;
        else if (ev.type === "think") {
          // Chain-of-thought output is rendered only when "Show tool calls"
          // is on. The Show-steps toggle asks the model to reason; the
          // tool-disclosure toggle controls whether the user sees it.
          if (showDisclosure()) {
            const t = makeMsg("think");
            t.textContent = "[think] " + ev.text;
          }
        }
        else if (ev.type === "tool_call") {
          if (showDisclosure()) {
            const t = makeMsg("tool");
            t.textContent =
              `▶ ${ev.name}(${JSON.stringify(ev.args)})\n` +
              `↳ ${typeof ev.result === "string" ? ev.result : JSON.stringify(ev.result)}`;
          }
        }
        else if (ev.type === "top3")  topCards.push(ev.card);
        else if (ev.type === "warning") {
          const w = makeMsg("warn");
          w.textContent = "⚠ " + ev.message;
        }
        else if (ev.type === "error") { asstMsg.textContent += `\n[error] ${ev.message}`; }
        else if (ev.type === "done")  { renderTop3(topCards); }
        log().scrollTop = log().scrollHeight;
      });
    } catch (e) {
      asstMsg.textContent += `\n[error] ${e.message}`;
    } finally {
      sendBtn().disabled = false;
    }
  }

  async function persistSteps(on) {
    try {
      await fetch("/settings/show_steps_enabled", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ value: on ? "on" : "off" }),
      });
    } catch (_) { /* non-fatal */ }
  }

  async function loadStepsState() {
    try {
      const r = await fetch("/settings/show_steps_enabled");
      if (r.ok) {
        const j = await r.json();
        stepsCB().checked = j.value === "on";
      }
    } catch (_) { /* 404 = not set, default off */ }
  }

  function wire() {
    sendBtn().addEventListener("click", send);
    input().addEventListener("keydown", e => {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
    });
    stepsCB().addEventListener("change", () => persistSteps(stepsCB().checked));
    loadStepsState();
  }

  return { wire, setOpenNoteHandler, loadStepsState };
})();
