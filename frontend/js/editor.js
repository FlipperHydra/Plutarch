/* TipTap editor init + PDF export via pdfmake-quality-ish html2pdf.
   NOTE on Issue 13: we chose pdfmake (option c) but ship html2pdf as the
   working v1 fallback since pdfmake requires a rebuild of the DOM tree.
   html2pdf gives you a usable exporter today; upgrading is a drop-in swap. */
window.editorMod = (() => {
  let editor = null;

  function init() {
    if (!window.tiptap) {
      const el = document.getElementById("editor");
      el.contentEditable = "true";
      el.innerHTML = "<p>TipTap failed to load. Basic contenteditable fallback active.</p>";
      editor = {
        getHTML: () => el.innerHTML,
        commands: { setContent: (h) => { el.innerHTML = h || ""; } },
      };
      wireToolbar();  // best-effort — buttons are no-ops without TipTap
      return editor;
    }
    const { Editor } = window.tiptap;
    const StarterKit = window.tiptap.StarterKit;
    editor = new Editor({
      element: document.getElementById("editor"),
      extensions: [StarterKit],
      content: "<p></p>",
    });
    wireToolbar();
    return editor;
  }

  // Toolbar wiring. Uses TipTap's chain API when available, falls back to
  // document.execCommand for the contenteditable fallback so users still get
  // basic Bold / Italic even if TipTap failed to load.
  //
  // Each button maps to a `data-cmd` on the element; the same names appear
  // in the HTML so this list is the source of truth.
  function wireToolbar() {
    const bar = document.getElementById("editor-toolbar");
    if (!bar) return;
    const btns = bar.querySelectorAll("button[data-cmd]");
    btns.forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.preventDefault();
        runCommand(btn.dataset.cmd);
        // Reflect the current state on the next tick so active marks show.
        setTimeout(updateToolbarActive, 0);
      });
    });
    // Keep active state in sync with the current selection.
    if (editor && typeof editor.on === "function") {
      editor.on("selectionUpdate", updateToolbarActive);
      editor.on("transaction", updateToolbarActive);
    }
  }

  function runCommand(cmd) {
    if (!editor) return;
    // TipTap path.
    if (typeof editor.chain === "function") {
      const c = editor.chain().focus();
      switch (cmd) {
        case "bold":         c.toggleBold().run(); break;
        case "italic":       c.toggleItalic().run(); break;
        case "h1":           c.toggleHeading({ level: 1 }).run(); break;
        case "h2":           c.toggleHeading({ level: 2 }).run(); break;
        case "bulletList":   c.toggleBulletList().run(); break;
        case "orderedList":  c.toggleOrderedList().run(); break;
        case "blockquote":   c.toggleBlockquote().run(); break;
      }
      return;
    }
    // Fallback for the no-TipTap contenteditable path. Limited to what
    // execCommand still ships; heading/list handling is approximate.
    const map = {
      bold: "bold", italic: "italic",
      h1: ["formatBlock", "H1"], h2: ["formatBlock", "H2"],
      bulletList: "insertUnorderedList",
      orderedList: "insertOrderedList",
      blockquote: ["formatBlock", "BLOCKQUOTE"],
    };
    const spec = map[cmd];
    if (!spec) return;
    if (Array.isArray(spec)) document.execCommand(spec[0], false, spec[1]);
    else document.execCommand(spec);
  }

  function updateToolbarActive() {
    if (!editor || typeof editor.isActive !== "function") return;
    const bar = document.getElementById("editor-toolbar");
    if (!bar) return;
    const checks = {
      bold:        () => editor.isActive("bold"),
      italic:      () => editor.isActive("italic"),
      h1:          () => editor.isActive("heading", { level: 1 }),
      h2:          () => editor.isActive("heading", { level: 2 }),
      bulletList:  () => editor.isActive("bulletList"),
      orderedList: () => editor.isActive("orderedList"),
      blockquote:  () => editor.isActive("blockquote"),
    };
    bar.querySelectorAll("button[data-cmd]").forEach((b) => {
      const fn = checks[b.dataset.cmd];
      const on = fn ? !!fn() : false;
      b.classList.toggle("active", on);
      b.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }

  function getHTML()   { return editor ? editor.getHTML() : ""; }
  function setHTML(h)  { if (editor) editor.commands.setContent(h || "<p></p>"); }

  async function exportPDF(title) {
    const html = getHTML();
    const wrap = document.createElement("div");
    wrap.style.padding = "24px";
    wrap.style.fontFamily = "'Times New Roman', Georgia, serif";
    wrap.innerHTML = `<h1>${escapeHTML(title || "Untitled")}</h1>` + html;
    if (window.html2pdf) {
      await window.html2pdf().from(wrap).set({
        margin: 12,
        filename: (title || "note") + ".pdf",
        html2canvas: { scale: 2 },
        jsPDF: { unit: "mm", format: "letter", orientation: "portrait" },
      }).save();
    } else {
      // Fallback: open a print dialog with the content.
      const w = window.open("", "_blank");
      w.document.write(wrap.outerHTML);
      w.document.close();
      w.print();
    }
  }

  function escapeHTML(s) {
    return String(s).replace(/[&<>"']/g, c => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  return { init, getHTML, setHTML, exportPDF };
})();
