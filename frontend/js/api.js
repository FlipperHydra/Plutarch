/* Tiny fetch wrapper + SSE helper. */
window.api = (() => {
  const BASE = "";

  async function req(path, opts = {}) {
    const r = await fetch(BASE + path, {
      headers: { "Content-Type": "application/json" },
      ...opts,
    });
    if (!r.ok) {
      let msg = `${r.status} ${r.statusText}`;
      try { const j = await r.json(); if (j.detail) msg = j.detail; } catch (_) {}
      throw new Error(msg);
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

    // Server-sent-events helper — pass an onEvent(event) callback.
    // Returns { close() } to abort mid-stream.
    async sse(path, body, onEvent) {
      const controller = new AbortController();
      const r = await fetch(BASE + path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body || {}),
        signal: controller.signal,
      });
      if (!r.ok || !r.body) {
        let msg = `${r.status} ${r.statusText}`;
        try { const j = await r.json(); if (j.detail) msg = j.detail; } catch (_) {}
        throw new Error(msg);
      }
      const reader = r.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      (async () => {
        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          buf += dec.decode(value, { stream: true });
          let idx;
          while ((idx = buf.indexOf("\n\n")) >= 0) {
            const raw = buf.slice(0, idx).trim();
            buf = buf.slice(idx + 2);
            if (raw.startsWith("data:")) {
              try { onEvent(JSON.parse(raw.slice(5).trim())); } catch (_) {}
            }
          }
        }
      })();
      return { close: () => controller.abort() };
    },
  };
  return obj;
})();
