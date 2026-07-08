/* App bootstrap: opens active note state, wires all sidebars, sleep flow. */
(async () => {
  // Guard: if not active, bounce to welcome.
  try {
    const s = await api.status();
    if (s.state !== "active") { window.location.href = "/"; return; }
  } catch (_) { window.location.href = "/"; return; }

  let activeNoteId = null;
  const editor = editorMod.init();

  // History sidebar
  history_mod.setOpenHandler(openNote);
  await history_mod.refresh();

  // If no notes yet, create one to start editing.
  const notes = await api.listNotes();
  if (!notes.length) {
    const { id } = await api.newNote();
    await openNote(id);
  } else {
    await openNote(notes[0].id);
  }

  async function openNote(id) {
    const n = await api.getNote(id);
    document.getElementById("note-title").value = n.title || "";
    editorMod.setHTML(n.body_html || "<p></p>");
    activeNoteId = id;
    await history_mod.refresh(id);
  }

  // Save (manual). Also debounced on title blur.
  document.getElementById("save-note").addEventListener("click", saveActive);
  document.getElementById("note-title").addEventListener("blur", saveActive);
  document.getElementById("new-note").addEventListener("click", async () => {
    await saveActive();
    const { id } = await api.newNote();
    await openNote(id);
  });
  document.getElementById("export-pdf").addEventListener("click", async () => {
    await saveActive();
    await editorMod.exportPDF(document.getElementById("note-title").value);
  });

  async function saveActive() {
    if (!activeNoteId) return;
    const title = document.getElementById("note-title").value;
    const body_html = editorMod.getHTML();
    await api.updateNote(activeNoteId, { title, body_html });
    await history_mod.refresh(activeNoteId);
  }

  // Chat
  chat_mod.setOpenNoteHandler(async (id) => { await saveActive(); openNote(id); });
  chat_mod.wire();

  // Sidebar toggle
  document.getElementById("toggle-history").addEventListener("click", () => {
    const s = document.getElementById("history-sidebar");
    s.classList.toggle("hidden-sidebar");
  });

  // Model picker wiring
  const pill        = document.getElementById("model-pill");
  const modelPanel  = document.getElementById("model-panel");
  pill.addEventListener("click", () => modelPanel.classList.toggle("hidden"));
  document.getElementById("close-model-panel").addEventListener("click", () =>
    modelPanel.classList.add("hidden"));
  document.getElementById("model-select").addEventListener("change", () => models_mod.updateVram());
  document.getElementById("model-load-btn").addEventListener("click", () => models_mod.loadSelected());

  // Manual add
  const manualDlg = document.getElementById("manual-dialog");
  document.getElementById("model-manual").addEventListener("click", () => manualDlg.classList.remove("hidden"));
  document.getElementById("manual-cancel").addEventListener("click", () => manualDlg.classList.add("hidden"));
  document.getElementById("manual-save").addEventListener("click", async () => {
    const err = document.getElementById("manual-err");
    err.classList.add("hidden");
    const name = document.getElementById("manual-input").value.trim();
    try {
      await models_mod.manualSave(name);
      manualDlg.classList.add("hidden");
    } catch (e) {
      err.textContent = e.message; err.classList.remove("hidden");
    }
  });

  // Progress popup toggle from pill area (already double-linked to pill click)
  document.getElementById("progress-hide").addEventListener("click", () => models_mod.hideProgress());

  await models_mod.refresh();

  // Sleep flow
  const sleepChoice = document.getElementById("sleep-choice");
  const sleepToast  = document.getElementById("sleep-toast");
  document.getElementById("sleep-btn").addEventListener("click", async () => {
    await saveActive();
    const s = await api.sleep({});
    if (s.state === "sleeping_no_model") {
      sleepChoice.classList.remove("hidden");
    } else {
      goToSleepToast();
    }
  });
  document.getElementById("sleep-use-current").addEventListener("click", async () => {
    sleepChoice.classList.add("hidden");
    await api.sleep({ no_model_choice: "use_current" });
    goToSleepToast();
  });
  document.getElementById("sleep-set-default").addEventListener("click", async () => {
    const loaded = document.getElementById("model-select").value;
    sleepChoice.classList.add("hidden");
    await api.sleep({ no_model_choice: "set_default", new_default: loaded });
    goToSleepToast();
  });
  document.getElementById("sleep-skip").addEventListener("click", async () => {
    sleepChoice.classList.add("hidden");
    await api.sleep({ no_model_choice: "skip" });
    goToSleepToast();
  });
  document.getElementById("sleep-force").addEventListener("click", () => {
    // Force stop = just navigate away; server will finish or leave orphans.
    window.location.href = "/";
  });

  async function goToSleepToast() {
    sleepToast.classList.remove("hidden");
    // Poll until cold, then bounce to welcome.
    while (true) {
      await new Promise(r => setTimeout(r, 1000));
      try {
        const s = await api.status();
        if (s.state === "cold") { window.location.href = "/"; return; }
      } catch (_) { /* server may be closing */ }
    }
  }
})();
