# LLM Wiki Runtime Guidance

The LLM wiki system has two vault modes:

- external knowledge vault
- internal memory vault

Important boundaries:

- External raw sources are immutable.
- Internal memory wiki is derived from canonical memory, session, checkpoint, and run evidence.
- Do not use the internal wiki as the source of truth for resumability or runtime state.
- Keep `index.md`, `log.md`, and `overview.md` current.
- Preserve provenance refs on every managed page.
