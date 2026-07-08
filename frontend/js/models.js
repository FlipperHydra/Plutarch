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

  // Last-known loaded model name, updated by refresh(). Used together with
  // pulledMap to compute the snapshot dispatched via `plutarch:model`.
  let loadedName = "";

  // Dispatch a `plutarch:model` custom event carrying the current snapshot
  // so chat.js (and any other subscriber) can react without polling.
  // Called at the end of any function that changes model state.
  function dispatchModelState() {
    let pulledCount = 0;
    for (const v of pulledMap.values()) if (v === true) pulledCount++;
    window.dispatchEvent(new CustomEvent("plutarch:model", {
      detail: { loaded: loadedName || "", pulledCount },
    }));
  }

  async function refresh() {
    const data = await api.availableModels();
    const s = sel();
    s.innerHTML = "";
    pulledMap.clear();

    // Group entries by `source` so the picker cleanly separates "what
    // Ollama sees on your system" from curated recommendations and your
    // own manual adds. This mirrors the backend's enumeration order and
    // uses native <optgroup> for accessibility (screen readers announce
    // the group label; no extra ARIA needed).
    const groups = {
      system:      { label: "Detected on system (Ollama)", opts: [] },
      recommended: { label: "Recommended (\u22644B, not pulled)", opts: [] },
      custom:      { label: "Custom (manual add)", opts: [] },
    };
    for (const m of data.models) {
      pulledMap.set(m.name, !!m.pulled);
      const opt = document.createElement("option");
      opt.value = m.name;
      const marks = [];
      if (!m.pulled)     marks.push("(not pulled)");
      if (m.is_default)  marks.push("\u2605 default");
      // Prefix pulled models with a check so users can see at a glance
      // which are already on disk and ready to load without a download.
      const prefix = m.pulled ? "\u2713 " : "";
      opt.textContent = prefix + m.name + (marks.length ? "  " + marks.join(" ") : "");
      if (!m.pulled) opt.style.color = "#a89f92";
      const src = groups[m.source] ? m.source : "custom";
      groups[src].opts.push(opt);
    }
    for (const key of ["system", "recommended", "custom"]) {
      const g = groups[key];
      if (!g.opts.length) continue;
      const og = document.createElement("optgroup");
      og.label = g.label;
      for (const opt of g.opts) og.appendChild(opt);
      s.appendChild(og);
    }

    // Surface Ollama connectivity as a subtle tooltip on the picker so
    // "no models" vs "daemon unreachable" is distinguishable without an
    // extra UI element. Empty string clears any prior tooltip.
    s.title = data.list_error
      ? `Ollama list error: ${data.list_error}`
      : "";

    if (data.loaded) {
      s.value = data.loaded;
      setPill("green", data.loaded);
      loadedName = data.loaded;
    } else {
      setPill("grey", "no model");
      loadedName = "";
    }
    await updateVram();
    dispatchModelState();
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
      // Special-case the 409 "not pulled" diagnostic: show what Ollama
      // actually reports so the user can tell whether it's a tag mismatch,
      // a daemon-unreachable, or a genuinely-missing model.
      let extra = "";
      if (e && e.status === 409 && e.detail && typeof e.detail === "object") {
        const avail = Array.isArray(e.detail.available) ? e.detail.available : [];
        const listErr = e.detail.list_error || "";
        const host = e.detail.ollama_host || "(unknown)";
        const hostLine = `\n\nPlutarch is querying Ollama at: ${host}`;
        if (listErr) {
          extra = `\n\nOllama returned an error while listing models: ${listErr}` +
                  `\nIs the daemon running? Try \`ollama list\` in a terminal.` +
                  hostLine;
        } else if (avail.length === 0) {
          // The daemon replied but reported zero models. If `ollama list`
          // in your terminal shows models, Plutarch is almost certainly
          // pointed at a different daemon (common on Docker installs).
          extra = "\n\nOllama reports no models on disk. Try `ollama list`." +
                  hostLine +
                  "\nIf your terminal `ollama list` shows models but this " +
                  "alert does not, set the OLLAMA_HOST env var to the " +
                  "daemon your terminal uses (e.g. " +
                  "http://host.docker.internal:11434 on Docker Desktop).";
        } else {
          extra = `\n\nOllama reports these on disk:\n  \u2022 ` + avail.join("\n  \u2022 ") +
                  `\n\nRequested (normalized): ${e.detail.requested_normalized || ""}` +
                  hostLine;
        }
      }
      alert("Model setup failed: " + e.message + extra +
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
    // refresh() dispatches, but be explicit in case the API race meant
    // `data.loaded` hadn't updated server-side yet.
    dispatchModelState();
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
      dispatchModelState();
    }
  }

  async function manualSave(name) {
    if (!/^[A-Za-z0-9._:/\-]{1,100}$/.test(name)) {
      throw new Error("Invalid characters or too long. Use letters, digits, . : / _ -");
    }
    await api.manualAddModel(name);
    await refresh();
    dispatchModelState();
  }

  // Read-only snapshot for callers that need to paint before an async refresh
  // completes (e.g. main.js initial chat overlay paint).
  function snapshot() {
    let pulledCount = 0;
    for (const v of pulledMap.values()) if (v === true) pulledCount++;
    return { loaded: loadedName || "", pulledCount };
  }

  return { refresh, updateVram, loadSelected, pullSelected, manualSave, setPill, showProgress, hideProgress, snapshot };
})();
