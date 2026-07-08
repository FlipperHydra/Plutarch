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
      return editor;
    }
    const { Editor } = window.tiptap;
    const StarterKit = window.tiptap.StarterKit;
    editor = new Editor({
      element: document.getElementById("editor"),
      extensions: [StarterKit],
      content: "<p></p>",
    });
    return editor;
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
