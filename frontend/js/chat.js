/* Chat UI: streaming, step disclosure, top-3 render.
 *
 * Two toggles, kept intentionally orthogonal:
 *   - #thinking-toggle       -> persists settings.thinking_enabled
 *                              (backend injects CoT prompt when "on")
 *   - #show-steps-toggle     -> persists settings.show_steps_enabled
 *                              (client renders think + tool events when "on")
 * Both default off.
 */
window.chat_mod = (() => {
  const log      = () => document.getElementById("chat-log");
  const input    = () => document.getElementById("chat-input");
  const sendBtn  = () => document.getElementById("chat-send");
  const thinkCB  = () => document.getElementById("thinking-toggle");
  const stepsCB  = () => document.getElementById("show-steps-toggle");

  let onOpenNote = () => {};

  function setOpenNoteHandler(fn) { onOpenNote = fn; }

  function makeMsg(cls) {
    const el = document.createElement("div");
    el.className = "msg " + cls;
    log().appendChild(el);
    log().scrollTop = log().scrollHeight;
    return el;
  }

  // Master visibility switch — gates both think blocks AND tool_call
  // events. If this is off, the chat shows only the model's answer.
  function stepsVisible() { return !!stepsCB().checked; }

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
          if (stepsVisible()) {
            const t = makeMsg("think");
            t.textContent = "[think] " + ev.text;
          }
        }
        else if (ev.type === "tool_call") {
          if (stepsVisible()) {
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

  async function persistSetting(key, on) {
    try {
      await fetch(`/settings/${key}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ value: on ? "on" : "off" }),
      });
    } catch (_) { /* non-fatal */ }
  }

  async function loadToggleStates() {
    // Read persisted state for both toggles. 404 or error => leave unchecked
    // (matches the default-off behaviour).
    for (const [key, cb] of [
      ["thinking_enabled", thinkCB],
      ["show_steps_enabled", stepsCB],
    ]) {
      try {
        const r = await fetch(`/settings/${key}`);
        if (r.ok) {
          const j = await r.json();
          cb().checked = j.value === "on";
        }
      } catch (_) { /* leave default */ }
    }
  }

  function wire() {
    sendBtn().addEventListener("click", send);
    input().addEventListener("keydown", e => {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
    });
    thinkCB().addEventListener("change",
      () => persistSetting("thinking_enabled", thinkCB().checked));
    stepsCB().addEventListener("change",
      () => persistSetting("show_steps_enabled", stepsCB().checked));
    loadToggleStates();
  }

  return { wire, setOpenNoteHandler, loadToggleStates };
})();
