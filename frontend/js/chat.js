/* Chat UI: streaming, step disclosure, top-3 render, model-gating overlay.
 *
 * Two toggles, kept intentionally orthogonal:
 *   - #thinking-toggle       -> persists settings.thinking_enabled
 *                              (backend injects CoT prompt when "on")
 *   - #show-steps-toggle     -> persists settings.show_steps_enabled
 *                              (client renders think + tool events when "on")
 * Both default off.
 *
 * Model gating (Q1=C flow):
 *   - Zero models pulled to disk    -> overlay + Send disabled
 *   - Models pulled but none loaded -> overlay + Send disabled
 *   - Model loaded                  -> normal chat
 * State is refreshed on `plutarch:model` custom events dispatched by
 * models.js after any change (refresh, pull, load, manual-add). No polling.
 */
window.chat_mod = (() => {
  const log      = () => document.getElementById("chat-log");
  const input    = () => document.getElementById("chat-input");
  const sendBtn  = () => document.getElementById("chat-send");
  const thinkCB  = () => document.getElementById("thinking-toggle");
  const stepsCB  = () => document.getElementById("show-steps-toggle");

  let onOpenNote = () => {};

  function setOpenNoteHandler(fn) { onOpenNote = fn; }

  // ---- Model-gating overlay -------------------------------------------
  // Current known snapshot of models state. Populated by updateModelState().
  let modelState = { loaded: "", pulledCount: 0 };

  function chatLogEl() { return document.getElementById("chat-log"); }
  function overlayEl() { return document.getElementById("chat-overlay"); }

  function renderOverlay() {
    const host = chatLogEl();
    if (!host) return;
    let ov = overlayEl();

    // Decide the current gating state.
    const loaded = !!modelState.loaded;
    const anyPulled = modelState.pulledCount > 0;

    if (loaded) {
      // Normal chat — tear down the overlay if present.
      if (ov) ov.remove();
      setSendEnabled(true, "");
      return;
    }

    // Need an overlay. Create if missing.
    if (!ov) {
      ov = document.createElement("div");
      ov.id = "chat-overlay";
      ov.className = "chat-overlay";
      const box = document.createElement("div");
      box.className = "chat-overlay-box";
      box.innerHTML = `
        <h4></h4>
        <p></p>
        <button class="ghost overlay-cta">Open model panel</button>`;
      box.querySelector(".overlay-cta").addEventListener("click", openModelPanel);
      ov.appendChild(box);
      host.appendChild(ov);
    }

    const h = ov.querySelector("h4");
    const p = ov.querySelector("p");
    if (!anyPulled) {
      // Exact copy as specified by the product spec.
      ov.classList.add("no-pulled");
      ov.classList.remove("no-loaded");
      h.textContent = "Warning No Model Pulled";
      p.textContent = "If no model is pulled chat based interactions will be left unavailable.";
      setSendEnabled(false, "No model pulled \u2014 pull one from the model panel to chat.");
    } else {
      // Models exist on disk but none loaded (Q1=C third state).
      ov.classList.add("no-loaded");
      ov.classList.remove("no-pulled");
      h.textContent = "No Model Loaded";
      p.textContent = "Load a pulled model from the model panel to start chatting.";
      setSendEnabled(false, "No model loaded \u2014 open the model panel to load one.");
    }
  }

  function openModelPanel() {
    const panel = document.getElementById("model-panel");
    if (panel) panel.classList.remove("hidden");
  }

  function setSendEnabled(enabled, tooltip) {
    const btn = sendBtn();
    const ta = input();
    if (!btn || !ta) return;
    // Do not override the transient disabled state set during an in-flight
    // send — the send() coroutine handles its own re-enable. We only clear
    // the persistent gating flag.
    btn.dataset.gatedDisabled = enabled ? "" : "1";
    ta.dataset.gatedDisabled = enabled ? "" : "1";
    if (!enabled) {
      btn.disabled = true;
      ta.disabled = true;
    } else if (!btn.dataset.sending) {
      btn.disabled = false;
      ta.disabled = false;
    }
    btn.title = tooltip || "";
    ta.title = tooltip || "";
  }

  /**
   * Public entry for models.js (or main.js) to push model-state updates.
   * `snapshot` = { loaded: string, pulledCount: number }.
   * Also invoked implicitly via a window-level custom event handler.
   */
  function updateModelState(snapshot) {
    modelState = {
      loaded: (snapshot && snapshot.loaded) || "",
      pulledCount: (snapshot && Number(snapshot.pulledCount)) || 0,
    };
    renderOverlay();
  }

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
    // Gating guard: if the overlay is up, refuse to send. This is a
    // belt-and-braces check on top of the disabled Send button.
    if (sendBtn().dataset.gatedDisabled === "1") return;
    const text = input().value.trim();
    if (!text) return;
    input().value = "";
    sendBtn().disabled = true;
    sendBtn().dataset.sending = "1";

    const userMsg = makeMsg("user"); userMsg.textContent = text;
    // The assistant may produce text over multiple tool-agent rounds. We
    // keep one "current" assistant bubble that grows as `token` events
    // stream in, and open a fresh bubble whenever a tool call interrupts
    // so the pre- and post-tool text render as distinct messages instead
    // of concatenating in one confusing bubble.
    let asstMsg = makeMsg("assistant"); asstMsg.textContent = "";
    let sawTokenInBubble = false;
    const topCards = [];
    const t0 = performance.now();
    let frameCount = 0;

    try {
      await api.sse("/chat/stream", { message: text }, ev => {
        frameCount++;
        if (ev.type === "token") {
          asstMsg.textContent += ev.text;
          sawTokenInBubble = true;
        }
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
          // Start a fresh assistant bubble for whatever the model says
          // AFTER consuming this tool result.
          if (sawTokenInBubble) {
            asstMsg = makeMsg("assistant"); asstMsg.textContent = "";
            sawTokenInBubble = false;
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
      // Diagnostic breadcrumb — helps confirm the stream actually delivered
      // events end-to-end. Empty bubbles + zero frames = SSE never arrived;
      // empty bubble + frames > 0 = model produced no visible content.
      const dtMs = Math.round(performance.now() - t0);
      console.info(`[chat] stream done: ${frameCount} frames in ${dtMs}ms`);
      if (!sawTokenInBubble && !asstMsg.textContent) {
        asstMsg.textContent =
          "(no visible output — the model may have replied with only a tool call " +
          "or hidden reasoning. Try turning on Show steps.)";
        asstMsg.classList.add("muted");
      }
    } catch (e) {
      asstMsg.textContent += `\n[error] ${e.message}`;
      console.error("[chat] stream failed:", e);
    } finally {
      delete sendBtn().dataset.sending;
      // Only re-enable if the gating overlay isn't currently blocking.
      if (sendBtn().dataset.gatedDisabled !== "1") {
        sendBtn().disabled = false;
      }
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

    // Subscribe to model-state changes. `models.js` dispatches this after
    // refresh / load / pull / manual-add so we don't need to poll.
    window.addEventListener("plutarch:model", (ev) => {
      updateModelState(ev.detail || {});
    });
    // Initial paint: render whatever state we already have. main.js calls
    // updateModelState() explicitly once models_mod.refresh() completes
    // during boot, but this covers the case where the event fires first.
    renderOverlay();
  }

  return { wire, setOpenNoteHandler, loadToggleStates, updateModelState };
})();
