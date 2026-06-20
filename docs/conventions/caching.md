# Caching learned patterns & docs/ layout

Where durable findings go, so we don't re-query for them.

## Caching learned patterns (avoid repeat queries)

When I learn a durable pattern, persist it instead of re-querying:

- **Short cross-session facts** → my memory files.
- **Reference material** (API snippets, code patterns, gotchas) → `docs/`,
  organized by category subfolder (see below).

Every cached doc/reference entry must stamp **library + version** and **date**,
so a stale cache is self-evident. Entry shape: what was asked, the answer/
pattern, the version, the date, and the Context7 source if applicable.

Check `docs/reference/` before issuing a Context7 query for something I may have
already cached.

## docs/ layout (cache organized by category)

```
docs/
  planning/    design notes, open-question resolutions, scope decisions
  impl/        implementation notes, architecture-as-built
  reference/   cached Context7 / library patterns & API snippets (version+date stamped)
  conventions/ how we work together: workflow & interaction preferences
```
