---
name: oversized-file-chunked-reading
description: >
  Recover when a Read is rejected for being too large, by locating the relevant region with grep/rg
  first and then issuing a targeted Read with offset and limit instead of retrying the full read.
  Use whenever a Read fails with "File content ... exceeds maximum allowed tokens (25000). Use offset
  and limit parameters to read specific portions of the file, or search for specific content instead
  of reading the entire file", or with "File content (262.5KB) exceeds maximum allowed size (256KB).
  Use offset and limit parameters...", or any "exceeds maximum allowed size", ">256KB", "file too
  large to read", "read rejected", "25000 token limit", or "256KB limit" message. Use when you need
  one function or region out of a huge source file, or need to scan a big log/JSONL/bundle where only
  matching lines matter, or wonder "how do I read a giant file" or "how do I chunk through a big
  file". Also use when you want to read a file in chunks, chunk the file, do a chunked read, read the
  file in pieces, page through a big file, or do a windowed / partial read — even before any rejection
  has been hit. Also use when you need to use the offset and limit parameters (offset/limit) to read
  part of a file, e.g. "use offset and limit to read just part of this file" or "do a windowed read
  with offset and limit". Also use for non-error line-range intents: "read just part of a huge file",
  "read a specific section/range of a large file", "read lines N to M of a big file", or "extract a
  region from a giant file". NOT for transforming a file's meaning into a summary (use summarize), NOT for searching GIF
  libraries (gifgrep), NOT for reading external-repo docs (zread-dependency-docs), and NOT for mining
  transcripts for skill gaps (technical-skill-finder).
user-invocable: true
risk: safe
source: "Derived from the UA skill-gap finder backlog (issue #796) -- oversized-file-chunked-reading."
---

# Oversized File Chunked Reading

When a `Read` is rejected for being too large, **do not retry the same full Read** — it fails
identically and burns a turn. Locate the region you need, then read a targeted chunk. There are two
hard ceilings on the Read tool:

- **Token ceiling (25000 tokens)** — hit by large but normal source files. The rejection reads:
  `File content (...) exceeds maximum allowed tokens (25000). Use offset and limit parameters to read
  specific portions of the file, or search for specific content instead of reading the entire file.`
- **Byte ceiling (256KB raw bytes)** — hit by minified, bundled, JSONL, or log files. The rejection
  reads: `File content (262.5KB) exceeds maximum allowed size (256KB). Use offset and limit
  parameters...` (also seen well into the MB range for JSONL/log files).

The one rule: **locate, then read.** Never re-issue the full Read, and never guess a blind large
limit hoping to dodge the cap — find real line numbers first.

## The recovery recipe

1. **Locate** the relevant region with `rg`/`grep` to get line numbers:
   ```bash
   rg -n 'def list_cron_jobs' src/universal_agent/gateway_server.py
   # → 3338:    def list_cron_jobs(self) -> list[Any]:
   ```
2. **Read a targeted chunk** with `offset` a few lines above the first match and a bounded `limit`
   (~100–200 lines, well under both ceilings). `offset` is a 1-based start line; `limit` is the
   number of lines:
   ```
   Read(file_path=".../gateway_server.py", offset=3320, limit=110)
   ```
3. **Iterate** if the region spans more than one window, or if you need to scan the whole file:
   compute the next `offset` from the `cat -n` line numbers in the output and step sequential windows
   (`offset 1 limit 200`, then `offset 201 limit 200`, ...) until done.

## Concrete example sequence

A full Read of `gateway_server.py` (35566 lines) is rejected: `exceeds maximum allowed tokens
(25000)`. Locate the symbol, then read a bounded window around its real line number:

```bash
rg -n 'def list_cron_jobs' src/universal_agent/gateway_server.py
# → 3338:    def list_cron_jobs(self) -> list[Any]:
```
```
Read(file_path=".../gateway_server.py", offset=3320, limit=110)   # the list_cron_jobs region, with margin
```
Then repeat for the next symbol you need — find its line number, read a bounded window around it:
```bash
rg -n 'def _emit_cron_event' src/universal_agent/gateway_server.py
# → 7931:def _emit_cron_event(payload: dict) -> None:
```
```
Read(file_path=".../gateway_server.py", offset=7915, limit=130)
```
The pattern is always the same: full Read rejected → grep for the symbol → read one bounded,
line-numbered window per region. Several small chunks, never one full read.

## Picking offset and limit

- Start `offset` a few lines **above** the match so you get surrounding context.
- Keep `limit` around 100–200 lines — comfortably under both the token and byte ceilings.
- For the **256KB byte cap** on JSONL / log / minified blobs, prefer filtering with `rg`, `grep`,
  `jq`, `head`, or `tail` over reading raw windows of an unstructured file. Pull the few matching
  lines out on the command line rather than paging through megabytes:
  ```bash
  rg -n 'ERROR' big.log | head
  jq -c 'select(.event=="failure")' events.jsonl | head
  ```

## When to use

- Any Read rejected with `exceeds maximum allowed tokens (25000)` or `exceeds maximum allowed size
  (256KB)`.
- You need one function or region out of a huge source file.
- You want to read a file in chunks / page through a big file in pieces / do a windowed or partial
  read — even before any rejection, e.g. "read lines N to M" or "use offset and limit to read just
  part of this file".
- You're scanning a large log / JSONL / bundle and only the matching lines matter.

## When NOT to use

- The file fits — just read it normally.
- You want the file's **meaning**, not a slice (a summary/digest) → use `summarize`.
- Searching external-repo documentation → use `zread-dependency-docs`.
- Searching GIF libraries → use `gifgrep`.
- Mining transcripts specifically for skill gaps → use `technical-skill-finder`.

## NEVER

- NEVER retry the identical full Read after a size/token rejection — it fails the same way and wastes
  a turn.
- NEVER guess a blind large `limit` hoping to slip under the cap. Grep first to get real line
  numbers, then read a bounded window.
- NEVER route around the ceiling by pasting a giant file into context by other means.
- NEVER read a multi-MB minified / bundled / JSONL / log file in raw windows when `rg`/`jq`/`grep`
  can filter it down to the few relevant lines first.
