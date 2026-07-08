/* Model picker + pill + progress + manual add. */
window.models_mod = (() => {
  const pill        = () => document.getElementById("model-pill");
  const pillName    = () => document.getElementById("model-pill-name");
  const panel       = () => document.getElementById("model-panel");
  const sel         = () => document.getElementById("model-select");
  const isDefaultCB = () => document.getElementById("model-is-default");
  const vramLine    = () => document.getElementById("model-vram");
  const popup       = () => document.getElementById("progress-popup");
  const popupLabel  = () => document.getElementById("progress-label");

  function setPill(color, text) {
    const p = pill();
    p.classList.remove("grey", "blue", "green", "red");
    p.classList.add(color);
    pillName().textContent = text;
  }

  // Cache of {name -> pulled:bool} from the last refresh so loadSelected can
  // decide whether to pull first without re-hitting the API.
  const pulledMap = new Map();

  async function refresh() {
    const data = await api.availableModels();
    const s = sel();
    s.innerHTML = "";
    pulledMap.clear();
    for (const m of data.models) {
      pulledMap.set(m.name, !!m.pulled);
      const opt = document.createElement("option");
      opt.value = m.name;
      const marks = [];
      if (!m.pulled)     marks.push("(not pulled)");
      if (m.is_default)  marks.push("★ default");
      // Prefix pulled models with a ✓ so users can see at a glance which are
      // already on disk and ready to load without a download.
      const prefix = m.pulled ? "✓ " : "";
      opt.textContent = prefix + m.name + (marks.length ? "  " + marks.join(" ") : "");
      if (!m.pulled) opt.style.color = "#a89f92";
      s.appendChild(opt);
    }
    if (data.loaded) {
      s.value = data.loaded;
      setPill("green", data.loaded);
    } else {
      setPill("grey", "no model");
    }
    await updateVram();
  }

  async function updateVram() {
    const name = sel().value;
    if (!name) return;
    try {
      const est = await api.vramEstimate(name);
      const el = vramLine();
      el.classList.remove("ok", "warn", "block");
      el.classList.add(est.level);
      el.textContent =
        `Est. ${est.estimate_gb} GB — available ~${est.available_gb} GB — ${est.note}`;
    } catch (_) {
      vramLine().textContent = "";
    }
  }

  async function loadSelected() {
    const name = sel().value;
    if (!name) return;
    // Check VRAM. Warn on 'warn', block on 'block' unless user confirms.
    const est = await api.vramEstimate(name);
    if (est.level === "block") {
      if (!confirm(
        `This model needs ~${est.estimate_gb} GB but only ~${est.available_gb} GB is available. ` +
        `Loading it may fail or slow your system. Continue anyway?`)) return;
    } else if (est.level === "warn") {
      if (!confirm(
        `This model needs ~${est.estimate_gb} GB (close to available ~${est.available_gb} GB). Continue?`)) return;
    }

    try {
      // If not pulled locally, stream the pull first with progress.
      if (!pulledMap.get(name)) {
        setPill("blue", "pulling " + name);
        showProgress(`Pulling ${name}... (this can take a while on first run)`);
        await pullWithProgress(name);
      }

      setPill("blue", "loading " + name);
      showProgress("Loading " + name + " into memory...");
      await api.selectModel(name);
      setPill("green", name);
      setTimeout(() => setPill("grey", name), 3000);
    } catch (e) {
      setPill("red", "failed");
      alert("Model setup failed: " + e.message +
            "\n\nCheck that Ollama is running (ollama serve) and reachable at " +
            "http://127.0.0.1:11434.");
      setTimeout(() => refresh(), 3000);
    } finally {
      hideProgress();
    }

    if (isDefaultCB().checked) {
      try { await api.setDefault(name); } catch (_) {}
    }
    await refresh();
  }

  // Streams the pull and resolves when Ollama reports the final "success"
  // status. Rejects if any event carries an error field.
  //
  // Implementation note: api.sse now resolves only when the whole stream
  // ends. So we await api.pullModel directly — no need to race a
  // per-event resolve against the stream Promise. We remember the terminal
  // event and surface any error after the stream completes.
  async function pullWithProgress(name) {
    let sawSuccess = false;
    let streamError = null;
    await api.pullModel(name, (ev) => {
      if (ev.error) { streamError = new Error(ev.error); return; }
      // Progress: `status` is a human string, `total`/`completed` are bytes.
      if (ev.total && ev.completed != null) {
        const pct = Math.min(100, Math.floor((ev.completed / ev.total) * 100));
        popupLabel().textContent =
          `Pulling ${name}... ${ev.status || "downloading"} ${pct}%`;
      } else if (ev.status) {
        popupLabel().textContent = `Pulling ${name}... ${ev.status}`;
        // Final Ollama event is `status: "success"`.
        if (/^success$/i.test(ev.status)) sawSuccess = true;
      }
    });
    if (streamError) throw streamError;
    if (!sawSuccess) {
      throw new Error(
        "Pull stream ended without a success event. The download may have " +
        "been interrupted; check `ollama list` and retry."
      );
    }
  }

  function showProgress(text) {
    popupLabel().textContent = text;
    popup().classList.remove("hidden");
  }
  function hideProgress() { popup().classList.add("hidden"); }

  // Pull-only flow: fetch the model to disk without loading it into memory.
  // Used by the standalone "Pull model" button. Shares the progress popup
  // with loadSelected() so the UI is consistent.
  async function pullSelected() {
    const name = sel().value;
    if (!name) return;
    if (pulledMap.get(name)) {
      alert("'" + name + "' is already pulled.");
      return;
    }
    setPill("blue", "pulling " + name);
    showProgress(`Pulling ${name}... (this can take a while on first run)`);
    try {
      await pullWithProgress(name);
      setPill("grey", "pulled");
      // Brief success confirmation in the popup before it closes, so the
      // user gets clear feedback that the download completed successfully.
      popupLabel().textContent = `${name} pulled ✓`;
      await new Promise((r) => setTimeout(r, 1500));
    } catch (e) {
      setPill("red", "pull failed");
      alert("Pull failed: " + e.message +
            "\n\nCheck that Ollama is running (ollama serve) and reachable at " +
            "http://127.0.0.1:11434.");
    } finally {
      hideProgress();
      await refresh();
    }
  }

  async function manualSave(name) {
    if (!/^[A-Za-z0-9._:/\-]{1,100}$/.test(name)) {
      throw new Error("Invalid characters or too long. Use letters, digits, . : / _ -");
    }
    await api.manualAddModel(name);
    await refresh();
  }

  return { refresh, updateVram, loadSelected, pullSelected, manualSave, setPill, showProgress, hideProgress };
})();
