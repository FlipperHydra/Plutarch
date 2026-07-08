/* App bootstrap: opens active note state, wires all sidebars, sleep flow. */
(async () => {
  // Guard: if not active, bounce to welcome.
  try {
    const s = await api.status();
    if (s.state !== "active") { window.location.href = "/"; return; }
  } catch (_) { window.location.href = "/"; return; }

  let activeNoteId = null;
  const editor = editorMod.init();

  // -----------------------------------------------------------------
  // Busy-guard helper. Wraps an async click handler to disable the
  // triggering button (and optionally other buttons) for the duration
  // of the async work. Prevents rapid double-clicks and clarifies to
  // the user that a request is in flight.
  // -----------------------------------------------------------------
  function busyGuard(fn, ...extraIds) {
    return async function (ev) {
      const btn = ev && ev.currentTarget instanceof HTMLButtonElement
        ? ev.currentTarget : null;
      const extras = extraIds
        .map((id) => document.getElementById(id))
        .filter((el) => el instanceof HTMLButtonElement);
      const toDisable = btn ? [btn, ...extras] : extras;
      // Remember prior disabled state so we can restore correctly (e.g. the
      // Send button may be gate-disabled and must stay disabled).
      const prior = toDisable.map((el) => el.disabled);
      toDisable.forEach((el) => { el.disabled = true; });
      try {
        return await fn(ev);
      } finally {
        toDisable.forEach((el, i) => { el.disabled = prior[i]; });
      }
    };
  }

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
  document.getElementById("save-note").addEventListener(
    "click", busyGuard(saveActive));
  // NOTE: title blur does not use busyGuard because blur can fire
  // repeatedly and we specifically want the snapshot-based race protection
  // inside saveActive() itself.
  document.getElementById("note-title").addEventListener("blur", saveActive);
  document.getElementById("new-note").addEventListener(
    "click", busyGuard(async () => {
      await saveActive();
      const { id } = await api.newNote();
      await openNote(id);
    }));
  document.getElementById("export-pdf").addEventListener(
    "click", busyGuard(async () => {
      await saveActive();
      await editorMod.exportPDF(document.getElementById("note-title").value);
    }));

  async function saveActive() {
    // Snapshot the active note id at entry. If the user opens another note
    // while updateNote is in-flight, activeNoteId will change under us and
    // history_mod.refresh(activeNoteId) would highlight the new note as if
    // the old note's save just finished on it. Capture up front and use
    // the snapshot everywhere in this coroutine.
    const noteId = activeNoteId;
    if (!noteId) return;
    const title = document.getElementById("note-title").value;
    const body_html = editorMod.getHTML();
    await api.updateNote(noteId, { title, body_html });
    await history_mod.refresh(noteId);
  }

  // Sidebar toggle. Reflects state on the toggle button so the user can see
  // whether it's currently "active" (sidebar visible) or not. `aria-expanded`
  // gives screen readers the same signal.
  const toggleHistBtn = document.getElementById("toggle-history");
  toggleHistBtn.setAttribute("aria-expanded", "true");
  toggleHistBtn.classList.add("toggled");
  toggleHistBtn.setAttribute("aria-controls", "history-sidebar");
  toggleHistBtn.addEventListener("click", () => {
    const s = document.getElementById("history-sidebar");
    const nowHidden = s.classList.toggle("hidden-sidebar");
    toggleHistBtn.classList.toggle("toggled", !nowHidden);
    toggleHistBtn.setAttribute("aria-expanded", nowHidden ? "false" : "true");
  });

  // Model picker wiring
  const pill        = document.getElementById("model-pill");
  const modelPanel  = document.getElementById("model-panel");
  pill.addEventListener("click", () => modelPanel.classList.toggle("hidden"));
  document.getElementById("close-model-panel").addEventListener("click", () =>
    modelPanel.classList.add("hidden"));
  document.getElementById("model-select").addEventListener("change", () => models_mod.updateVram());
  document.getElementById("model-load-btn").addEventListener("click", () => models_mod.loadSelected());
  document.getElementById("model-pull-btn").addEventListener("click", () => models_mod.pullSelected());

  // Manual add
  const manualDlg = document.getElementById("manual-dialog");
  const manualInput = () => document.getElementById("manual-input");
  function openManualDlg() {
    manualDlg.classList.remove("hidden");
    manualDlg.setAttribute("aria-hidden", "false");
    // Focus the input on open so the user can just type.
    setTimeout(() => manualInput().focus(), 0);
  }
  function closeManualDlg() {
    manualDlg.classList.add("hidden");
    manualDlg.setAttribute("aria-hidden", "true");
  }
  document.getElementById("model-manual").addEventListener("click", openManualDlg);
  document.getElementById("manual-cancel").addEventListener("click", closeManualDlg);
  const manualSaveBtn = document.getElementById("manual-save");
  async function doManualSave() {
    const err = document.getElementById("manual-err");
    err.classList.add("hidden");
    const name = manualInput().value.trim();
    try {
      manualSaveBtn.disabled = true;
      await models_mod.manualSave(name);
      closeManualDlg();
      manualInput().value = "";
    } catch (e) {
      err.textContent = e.message; err.classList.remove("hidden");
    } finally {
      manualSaveBtn.disabled = false;
    }
  }
  manualSaveBtn.addEventListener("click", doManualSave);
  // Enter to submit, Escape to close, while the dialog is open.
  manualInput().addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); doManualSave(); }
  });

  // ---------------------------------------------------------------
  // Global modal Escape handler. Closes any visible modal in
  // reverse-open order. Kept dumb on purpose — modals don't stack
  // in this app, so the first visible one is the one to dismiss.
  // ---------------------------------------------------------------
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    // Order matters only if two are ever visible at once; today they
    // are mutually exclusive.
    const candidates = [manualDlg, document.getElementById("sleep-choice")];
    for (const el of candidates) {
      if (el && !el.classList.contains("hidden")) {
        el.classList.add("hidden");
        el.setAttribute("aria-hidden", "true");
        e.preventDefault();
        return;
      }
    }
    // Also close the model panel on Escape if it's open.
    const mp = document.getElementById("model-panel");
    if (mp && !mp.classList.contains("hidden")) {
      mp.classList.add("hidden");
      e.preventDefault();
    }
  });

  // Progress popup toggle from pill area (already double-linked to pill click)
  document.getElementById("progress-hide").addEventListener("click", () => models_mod.hideProgress());

  // Boot ordering: refresh models BEFORE wiring chat so chat.js sees the
  // correct model state on its initial paint. Otherwise the overlay flashes
  // "No Model Pulled" briefly even when one is loaded.
  await models_mod.refresh();

  // Chat wire happens here (moved from earlier in the boot).
  chat_mod.setOpenNoteHandler(async (id) => { await saveActive(); openNote(id); });
  chat_mod.wire();
  // Push the current snapshot in case wire() ran before the model refresh
  // dispatched its event (defensive).
  try {
    if (models_mod.snapshot) chat_mod.updateModelState(models_mod.snapshot());
  } catch (_) { /* non-fatal */ }

  // Tag button: on-demand tagging pass mid-session. Streams progress into
  // the model-progress popup. Skips notes already tagged (tagging_status =
  // 'done'), so Sleep afterward doesn't re-tag anything.
  //
  // Race fix: api.sse now resolves only when the whole stream ends. We
  // await it directly (no nested Promise), capture terminal state, and
  // throw after the stream so the finally-block can restore the button.
  // Mirrors the pullWithProgress() refactor from 71dfba2.
  document.getElementById("tag-btn").addEventListener("click", async () => {
    await saveActive();  // flush the current note so it's eligible
    const btn = document.getElementById("tag-btn");
    btn.disabled = true;
    models_mod.showProgress("Tagging pending notes...");
    let sawDone = false;
    let streamError = null;
    let doneEvent = null;
    try {
      await api.sse("/tags/run", {}, (ev) => {
        if (ev.type === "start") {
          if (ev.total === 0) {
            models_mod.showProgress("No notes to tag \u2014 everything is already tagged.");
          } else {
            models_mod.showProgress(`Tagging 0/${ev.total} notes...`);
          }
        } else if (ev.type === "progress") {
          models_mod.showProgress(`Tagging ${ev.processed}/${ev.total} notes...`);
        } else if (ev.type === "done") {
          sawDone = true;
          doneEvent = ev;
        } else if (ev.type === "error") {
          streamError = new Error(ev.message);
        }
      });
      if (streamError) throw streamError;
      if (!sawDone) {
        throw new Error(
          "Tagging stream ended without a done event. Some notes may still be untagged; retry."
        );
      }
      const failedNote = doneEvent.failed ? ` (${doneEvent.failed} failed)` : "";
      models_mod.showProgress(
        `Tagged ${doneEvent.processed} of ${doneEvent.total} note(s)${failedNote}. Sleep will skip these.`
      );
      setTimeout(() => models_mod.hideProgress(), 2500);
      await history_mod.refresh(activeNoteId);
    } catch (e) {
      models_mod.hideProgress();
      alert("Tagging failed: " + e.message);
    } finally {
      btn.disabled = false;
    }
  });

  // Sleep flow
  const sleepChoice = document.getElementById("sleep-choice");
  const sleepToast  = document.getElementById("sleep-toast");
  function openSleepChoice() {
    sleepChoice.classList.remove("hidden");
    sleepChoice.setAttribute("aria-hidden", "false");
    // Focus the first primary action for keyboard users.
    setTimeout(() => {
      const first = document.getElementById("sleep-use-current");
      if (first) first.focus();
    }, 0);
  }
  function closeSleepChoice() {
    sleepChoice.classList.add("hidden");
    sleepChoice.setAttribute("aria-hidden", "true");
  }
  document.getElementById("sleep-btn").addEventListener(
    "click", busyGuard(async () => {
      await saveActive();
      const s = await api.sleep({});
      if (s.state === "sleeping_no_model") {
        openSleepChoice();
      } else {
        goToSleepToast();
      }
    }));
  document.getElementById("sleep-use-current").addEventListener("click", async () => {
    closeSleepChoice();
    await api.sleep({ no_model_choice: "use_current" });
    goToSleepToast();
  });
  document.getElementById("sleep-set-default").addEventListener("click", async () => {
    const loaded = document.getElementById("model-select").value;
    closeSleepChoice();
    await api.sleep({ no_model_choice: "set_default", new_default: loaded });
    goToSleepToast();
  });
  document.getElementById("sleep-skip").addEventListener("click", async () => {
    closeSleepChoice();
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
