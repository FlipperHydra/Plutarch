/* Wake/sleep wiring and status polling. */
window.lifecycle = (() => {
  async function wake() {
    await api.wake();
    // Poll until state == active (or a hard error).
    for (let i = 0; i < 30; i++) {
      const s = await api.status();
      if (s.state === "active") return s;
      if (s.state === "cold" && s.last_error) throw new Error(s.last_error);
      await new Promise(r => setTimeout(r, 400));
    }
    throw new Error("wake timed out");
  }

  async function sleep(body = {}) {
    return api.sleep(body);
  }

  return { wake, sleep };
})();
