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

  async function refresh() {
    const data = await api.availableModels();
    const s = sel();
    s.innerHTML = "";
    for (const m of data.models) {
      const opt = document.createElement("option");
      opt.value = m.name;
      const marks = [];
      if (!m.pulled)     marks.push("(not pulled)");
      if (m.is_default)  marks.push("★ default");
      opt.textContent = m.name + (marks.length ? "  " + marks.join(" ") : "");
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

    setPill("blue", "loading " + name);
    showProgress("Loading " + name + "...");
    try {
      await api.selectModel(name);
      setPill("green", name);
      setTimeout(() => setPill("grey", name), 3000);
    } catch (e) {
      setPill("red", "load failed");
      alert("Load failed: " + e.message);
      setTimeout(() => refresh(), 3000);
    } finally {
      hideProgress();
    }

    if (isDefaultCB().checked) {
      try { await api.setDefault(name); } catch (_) {}
    }
    await refresh();
  }

  function showProgress(text) {
    popupLabel().textContent = text;
    popup().classList.remove("hidden");
  }
  function hideProgress() { popup().classList.add("hidden"); }

  async function manualSave(name) {
    if (!/^[A-Za-z0-9._:/\-]{1,100}$/.test(name)) {
      throw new Error("Invalid characters or too long. Use letters, digits, . : / _ -");
    }
    await api.manualAddModel(name);
    await refresh();
  }

  return { refresh, updateVram, loadSelected, manualSave, setPill, showProgress, hideProgress };
})();
