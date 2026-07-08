/* Left history sidebar: notes grouped by modified time. */
window.history_mod = (() => {
  let onOpen = () => {};

  function setOpenHandler(fn) { onOpen = fn; }

  function group(dateStr) {
    const now = new Date();
    const then = new Date(dateStr.replace(" ", "T"));
    const days = Math.floor((now - then) / 86400000);
    if (days <= 0) return "Today";
    if (days === 1) return "Yesterday";
    if (days <= 7) return "This week";
    return "Older";
  }

  async function refresh(activeId) {
    const list = document.getElementById("history-list");
    list.innerHTML = "";
    const notes = await api.listNotes();
    let currentGroup = null;
    for (const n of notes) {
      const g = group(n.modified_at);
      if (g !== currentGroup) {
        const gh = document.createElement("div");
        gh.className = "history-group";
        gh.textContent = g;
        list.appendChild(gh);
        currentGroup = g;
      }
      const row = document.createElement("div");
      row.className = "history-row" + (n.id === activeId ? " active" : "");
      row.innerHTML = `<div class="h-title"></div><div class="h-time"></div>`;
      row.querySelector(".h-title").textContent = n.title || "Untitled";
      row.querySelector(".h-time").textContent = n.modified_at;
      row.addEventListener("click", () => onOpen(n.id));
      list.appendChild(row);
    }
  }

  return { refresh, setOpenHandler };
})();
