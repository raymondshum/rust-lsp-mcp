# ty + VSCode editor setup

**Library:** ty (astral-sh/ty) **Version:** 0.0.18 **Date cached:** 2026-06-19
**Source (Context7):** https://github.com/astral-sh/ty/blob/main/docs/editors.md,
https://github.com/astral-sh/ty/blob/main/docs/reference/editor-settings.md

## Question

How do you set up ty in VSCode, and how do you stop Pyright/Pylance from
conflicting with ty's language server?

## Answer

The Astral team maintains an **official ty VSCode extension**. It **automatically
disables the Python extension's (Pylance/Pyright) language server** to prevent
conflicts, so ty's own language server becomes the active type checker with **no
manual setting required** for the common case (ty as the language server).

### Only if you want the opposite split (ty = type-checking only, keep Pylance)

Override to keep Pylance for completion/hover and use ty only for type checking:

```jsonc
{
  "python.languageServer": "Pylance",
  "ty.disableLanguageServices": true
}
```

### Other relevant settings

- `"ty.disableLanguageServices": true` — turn off ty completion/hover/go-to so ty
  is type-checking only.
- `"ty.showSyntaxErrors": false` — suppress ty syntax-error diagnostics when
  another language server is also reporting them.

### Whole-project diagnostics (problems for all files, not just open ones)

ty's editor diagnostic scope is set by `ty.diagnosticMode`. Options: `off`,
`openFilesOnly` (default), `workspace` (all files in the project). For VSCode:

```json
{
  "ty.diagnosticMode": "workspace"
}
```

Source: https://github.com/astral-sh/ty/blob/main/docs/reference/editor-settings.md,
https://github.com/astral-sh/ty/blob/main/docs/features/language-server.md

## Decision for this project

We want ty to be the language server and Pylance/Pyright disabled — which is the
**default behavior of the ty extension**. So: install the ty VSCode extension (and
ty as a uv dependency); no manual override needed. Set
`"ty.diagnosticMode": "workspace"` in committed VSCode settings so problems surface
for every file, not just open ones. Verify against current docs if the ty version
changes.
