[← Back to the README](../../README.md)

# Documentation guide

This is the documentation guide for the Rust code-navigation and documentation-search service. The README covers the quick start; these pages go deeper into how the system works and how to extend it. Each page is self-contained and links back here.

## Pages

- [Architecture](architecture.md) — the big picture: how a request flows through the system, and the key design ideas (readiness, the response format, how documentation search works).
- [Tools / API reference](tools.md) — every tool the server offers, its inputs, and the exact responses it returns.
- [Configuration](configuration.md) — every setting and environment variable, with defaults and what each one does.
- [Development setup](development.md) — how to set up the development container, run the server, and run the two kinds of tests.
- [Components](components.md) — a guided, module-by-module tour of the source code.
- [Dependencies](dependencies.md) — the main libraries and external tools the project relies on, and why.
- [Agentic coding](agentic-coding.md) — how this project is built with IBM Bob: the delivery lifecycle, the build conventions, and the Bob configuration.

## Where to start

- **New users** — begin with the README quick start.
- **Wiring the server into a client** — go to [Tools / API reference](tools.md) and [Configuration](configuration.md).
- **Contributors** — go to [Development setup](development.md) and [Components](components.md).
- **Understanding how the project is built (agentic workflow)** — go to [Agentic coding](agentic-coding.md).
