/* Wake/sleep wiring and status polling.
 *
 * Wake is asynchronous on the backend: POST /wake returns immediately after
 * scheduling a background task that opens the DB and (optionally) warms the
 * default model into VRAM. The frontend polls /status until the state
 * transitions to 'active' or 'cold'.
 *
 * Polling budget: 120 seconds. Cold-loading even a small model can take
 * 30-60s on first launch; the old 12s budget consistently timed out for
 * users whose default was set to a not-yet-warmed model. The backend now
 * also short-circuits when the default isn't pulled to disk, so the common
 * bootstrap case never hits the warm-up path anyway.
 *
 * onProgress(status_snapshot) is called on each poll so callers can update
 * loader text (e.g. "loading gemma3:1b...").
 */
window.lifecycle = (() => {
  const POLL_INTERVAL_MS = 500;
  const MAX_POLLS = 240;  // 240 * 500ms = 120s

  async function wake(onProgress) {
    await api.wake();
    for (let i = 0; i < MAX_POLLS; i++) {
      let s;
      try {
        s = await api.status();
      } catch (_) {
        // Server may be momentarily unreachable during startup; keep trying.
        await new Promise(r => setTimeout(r, POLL_INTERVAL_MS));
        continue;
      }
      if (typeof onProgress === "function") onProgress(s);
      if (s.state === "active") return s;
      if (s.state === "cold" && s.last_error) throw new Error(s.last_error);
      await new Promise(r => setTimeout(r, POLL_INTERVAL_MS));
    }
    throw new Error(
      "wake timed out after 120s. Ollama may still be loading the default " +
      "model \u2014 check `ollama ps` in a terminal, or clear the default " +
      "model to skip auto-load."
    );
  }

  async function sleep(body = {}) {
    return api.sleep(body);
  }

  return { wake, sleep };
})();
