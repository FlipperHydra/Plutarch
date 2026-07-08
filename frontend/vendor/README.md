# Frontend vendor bundles

Two files are expected in this directory. They are NOT committed because
they are third-party bundles pinned by URL below.

## 1. `tiptap-bundle.umd.js`

TipTap v2 UMD bundle exposing `window.tiptap.Editor` and
`window.tiptap.StarterKit`. If you skip this file, the editor falls back
to a plain contenteditable box.

Suggested source:
    https://unpkg.com/@tiptap/core@2.6.6/dist/tiptap-core.umd.js
    https://unpkg.com/@tiptap/starter-kit@2.6.6/dist/tiptap-starter-kit.umd.js

Concatenate the two files (in that order) into `tiptap-bundle.umd.js`, or
build a proper bundle with your preferred bundler.

## 2. `html2pdf.bundle.min.js`

Provides `window.html2pdf`. Used only for the PDF-export button.

Suggested source:
    https://unpkg.com/html2pdf.js@0.10.2/dist/html2pdf.bundle.min.js

If missing, PDF export falls back to a print dialog.
