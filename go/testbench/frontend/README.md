# Editor assets

CodeMirror 6 provides the three editors: baseline, current code, and read-only
preview. Language parsers, line numbers, selection, search, undo and the jump
marker are bundled locally. The running stand needs no Node.js or CDN.

Rebuild the committed browser bundle after changing `code-editor.js`:

```sh
npm ci
npm run build
```

This regenerates `../web/code-editor.js` and `../web/editor-licenses.txt`.
Dependencies are pinned in `package-lock.json`; `node_modules` is ignored.
Keep the generated bundle and dependency license notices in the same commit.

The Go server embeds these files. The `/editor` response creates a fresh style
nonce and passes it through CodeMirror's `EditorView.cspNonce` facet, preserving
the strict script policy. No `unsafe-inline` policy exception is required.

Application code uses the adapter's document/selection API. Offsets inside
CodeMirror use UTF-16; `editor.js` converts the prefix to UTF-8 bytes for native
requests. Native jump row/column values use the editor's zero-based positions;
the UI labels show one-based values. The line and exact column are decorated
after an explicit Move click. Editing or selecting elsewhere clears the marker.
`setDocument` resets undo history when loading a different source, while
`replaceText` applies a suggestion as an undoable transaction. Downloads always
read the current editor document, including undo/redo and manual edits.

References: [basic setup](https://codemirror.net/examples/basic/),
[state and view API](https://codemirror.net/docs/ref/),
[read-only editors](https://codemirror.net/examples/readonly/).
