/* Tiny fetch wrapper + SSE helper. */
window.api = (() => {
  const BASE = "";

  async function req(path, opts = {}) {
    const r = await fetch(BASE + path, {
      headers: { "Content-Type": "application/json" },
      ...opts,
    });
    if (!r.ok) {
      // FastAPI wraps HTTPException.detail into `{detail: ...}`. Detail can
      // be a plain string (traditional case) or a structured object (used by
      // /models/select 409 to include the requested/available diagnostic).
      // Format both usefully; also attach the parsed detail to the error so
      // callers can render actionable UI if they want.
      let msg = `${r.status} ${r.statusText}`;
      let detail = null;
      try {
        const j = await r.json();
        if (j.detail !== undefined) {
          detail = j.detail;
          if (typeof detail === "string") {
            msg = detail;
          } else if (detail && typeof detail === "object") {
            // Prefer the `error` field if the object exposes one; otherwise
            // stringify so the user still sees the payload.
            msg = detail.error || detail.message || JSON.stringify(detail);
          }
        }
      } catch (_) { /* body was not JSON */ }
      const err = new Error(msg);
      err.status = r.status;
      err.detail = detail;
      throw err;
    }
    if (r.status === 204) return null;
    const ct = r.headers.get("content-type") || "";
    return ct.includes("application/json") ? r.json() : r.text();
  }

  const obj = {
    status:    ()          => req("/status"),
    wake:      ()          => req("/wake",  { method: "POST", body: "{}" }),
    sleep:     (body = {}) => req("/sleep", { method: "POST", body: JSON.stringify(body) }),

    listNotes: ()          => req("/notes"),
    getNote:   (id)        => req(`/notes/${id}`),
    newNote:   ()          => req("/notes", { method: "POST", body: JSON.stringify({ title: "Untitled", body_html: "" }) }),
    updateNote: (id, patch) => req(`/notes/${id}`, { method: "PUT", body: JSON.stringify(patch) }),
    deleteNote: (id)       => req(`/notes/${id}`, { method: "DELETE" }),

    availableModels:  ()               => req("/models/available"),
    selectModel:      (name)           => req("/models/select", { method: "POST", body: JSON.stringify({ name }) }),
    // Streams Ollama pull progress. onEvent receives { status, total, completed, ... } or { error }.
    pullModel:        (name, onEvent)  => obj.sse("/models/pull", { name }, onEvent),
    setDefault:       (name)           => req("/models/default",{ method: "POST", body: JSON.stringify({ name }) }),
    manualAddModel:   (name)           => req("/models/manual-add", { method: "POST", body: JSON.stringify({ name }) }),
    vramEstimate:     (name, ctx)      => req(`/models/vram?model=${encodeURIComponent(name)}${ctx?`&ctx=${ctx}`:""}`),

    listTags:         ()               => req("/tags"),

    chatHistory:      ()               => req("/chat/history"),

    // Server-sent-events helper.
    //
    // Behaviour contract:
    //   * Returns a Promise that resolves only when the stream ends cleanly.
    //     (Previously this returned early with a background reader — that
    //     let callers proceed while events were still arriving and made
    //     reader-loop errors invisible, which manifested as empty chat bubbles.)
    //   * Rejects if the initial response is non-2xx, the response has no
    //     body, or the reader throws mid-stream.
    //   * `onEvent(parsedJson)` is called for every complete `data:` frame.
    //     Handles both `\n\n` (LF) and `\r\n\r\n` (CRLF) frame boundaries so
    //     Windows/proxy line endings don't break parsing.
    //   * Frames that aren't valid JSON are logged and skipped rather than
    //     silently dropped.
    //   * Callers that need mid-stream cancellation can pass an AbortSignal
    //     via `opts.signal`.
    async sse(path, body, onEvent, opts = {}) {
      const controller = new AbortController();
      // Chain any externally-supplied signal so callers can abort us.
      if (opts.signal) {
        if (opts.signal.aborted) controller.abort();
        else opts.signal.addEventListener("abort", () => controller.abort(), { once: true });
      }
      const r = await fetch(BASE + path, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          // Ask any intermediate proxy not to buffer. Not honoured by every
          // proxy but harmless when it isn't.
          "Accept": "text/event-stream",
        },
        body: JSON.stringify(body || {}),
        signal: controller.signal,
      });
      if (!r.ok || !r.body) {
        let msg = `${r.status} ${r.statusText}`;
        try { const j = await r.json(); if (j.detail) msg = j.detail; } catch (_) {}
        throw new Error(msg);
      }
      const reader = r.body.getReader();
      const dec = new TextDecoder("utf-8");
      let buf = "";

      function drain() {
        // Match either LF-LF or CRLF-CRLF as a frame separator. We walk the
        // buffer looking for the earliest occurrence of either.
        while (true) {
          const iLF   = buf.indexOf("\n\n");
          const iCRLF = buf.indexOf("\r\n\r\n");
          let idx, sep;
          if (iLF === -1 && iCRLF === -1) return;
          if (iCRLF !== -1 && (iLF === -1 || iCRLF < iLF)) { idx = iCRLF; sep = 4; }
          else                                             { idx = iLF;   sep = 2; }
          const raw = buf.slice(0, idx);
          buf = buf.slice(idx + sep);
          // An SSE frame can have multiple lines; we care about `data:` lines.
          // Join multi-line `data:` payloads with "\n" per the SSE spec.
          const lines = raw.split(/\r?\n/);
          const dataLines = [];
          for (const line of lines) {
            if (line.startsWith("data:")) dataLines.push(line.slice(5).replace(/^ /, ""));
          }
          if (!dataLines.length) continue;
          const payload = dataLines.join("\n");
          try { onEvent(JSON.parse(payload)); }
          catch (e) { console.warn("[sse] non-JSON frame skipped:", payload, e); }
        }
      }

      try {
        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          buf += dec.decode(value, { stream: true });
          drain();
        }
        // Flush any decoder tail + any final frame that didn't get a
        // trailing blank line (permissive parse).
        buf += dec.decode();
        if (buf.trim()) drain();
      } finally {
        try { reader.releaseLock(); } catch (_) {}
      }

      // Return an object with close() so existing callers that stored the
      // handle (models.js pullWithProgress) keep working.
      return { close: () => controller.abort() };
    },
  };
  return obj;
})();
