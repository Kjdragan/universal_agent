# Open Knowledge Format (OKF) — Research Survey

**Date of survey:** 2026-08-03
**Subject:** Google Cloud's "Open Knowledge Format" (OKF), described to us as a Google framework competing with RAG, currently at v0.2.
**Verdict on identification:** **Confirmed.** The project exists, is genuinely from Google Cloud, and "Open Knowledge Format" is its exact official name. v0.2 is real and is the current spec version.

---

## Executive summary

**Open Knowledge Format (OKF)** is an open, vendor-neutral **specification** — not a product, SDK, database, or serving system — published by **Google Cloud's Data Cloud engineering organization** for representing organizational knowledge as a directory tree of plain Markdown files with YAML frontmatter ([SPEC.md](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md), [Google Cloud Blog, 2026-06-12](https://cloud.google.com/blog/products/data-analytics/how-the-open-knowledge-format-can-improve-data-sharing)). The spec describes itself as "intentionally minimal: a directory of markdown files with YAML frontmatter. There is no schema registry, no central authority, and no required tooling. If you can `cat` a file, you can read OKF; if you can `git clone` a repo, you can ship it." ([SPEC.md, preamble](https://raw.githubusercontent.com/GoogleCloudPlatform/knowledge-catalog/main/okf/SPEC.md)). It ships under Apache 2.0 in the [GoogleCloudPlatform/knowledge-catalog](https://github.com/GoogleCloudPlatform/knowledge-catalog) repository, under the `okf/` directory ([license file](https://raw.githubusercontent.com/GoogleCloudPlatform/knowledge-catalog/main/okf/LICENSE.md); [GitHub API repo metadata](https://api.github.com/repos/GoogleCloudPlatform/knowledge-catalog)). v0.1 was announced 2026-06-12 and v0.2 on 2026-07-24, the latter adding first-class **provenance, trust, freshness, lifecycle, and attestation** signals to frontmatter ([Google Cloud Blog, 2026-07-24](https://cloud.google.com/blog/products/data-analytics/okf-v0-2-adds-trust-signals); [SPEC.md §13](https://raw.githubusercontent.com/GoogleCloudPlatform/knowledge-catalog/main/okf/SPEC.md)).

**The single most important correction to the brief:** the "competes with / replaces RAG" framing is **not Google's**. It comes entirely from third-party commentary. Google's own primary sources — the v0.1 announcement, the v0.2 announcement, `SPEC.md`, and `okf/README.md` — **never mention RAG, retrieval-augmented generation, vector databases, embeddings, or chunking at all**. See [§3](#3-the-rag-positioning--an-important-correction) for the verification evidence.

---

## 1. Official name, creator, and ownership

| Field | Value | Source |
|---|---|---|
| Official name | **Open Knowledge Format**, abbreviated **OKF** | [SPEC.md title line](https://raw.githubusercontent.com/GoogleCloudPlatform/knowledge-catalog/main/okf/SPEC.md) |
| Creator | **Google Cloud** — Data Cloud engineering org | [Google Cloud Blog, 2026-06-12](https://cloud.google.com/blog/products/data-analytics/how-the-open-knowledge-format-can-improve-data-sharing) |
| Named authors | **Sam McVeety**, Tech Lead, Data Analytics, Engineering, Data Cloud; **Amir Hormati**, Tech Lead, BigQuery, Engineering, Data Cloud | Bylines on both the [v0.1](https://cloud.google.com/blog/products/data-analytics/how-the-open-knowledge-format-can-improve-data-sharing) and [v0.2](https://cloud.google.com/blog/products/data-analytics/okf-v0-2-adds-trust-signals) blog posts |
| Home repository | `github.com/GoogleCloudPlatform/knowledge-catalog`, subdirectory `okf/` | [Repo contents API](https://api.github.com/repos/GoogleCloudPlatform/knowledge-catalog/contents/) |
| License | Apache License 2.0 | [okf/LICENSE.md](https://raw.githubusercontent.com/GoogleCloudPlatform/knowledge-catalog/main/okf/LICENSE.md); [GitHub API `license.spdx_id: Apache-2.0`](https://api.github.com/repos/GoogleCloudPlatform/knowledge-catalog) |

"Google" is genuinely the creator and backer — this is not a community project with a Google-sounding name. It lives in the official `GoogleCloudPlatform` GitHub org, is announced on the official Google Cloud blog under the Data Analytics product category, and is authored by named Google Cloud engineering tech leads ([v0.1 blog](https://cloud.google.com/blog/products/data-analytics/how-the-open-knowledge-format-can-improve-data-sharing); [v0.2 blog](https://cloud.google.com/blog/products/data-analytics/okf-v0-2-adds-trust-signals)).

Note the repository is broader than OKF: its GitHub description is "Google Cloud Knowledge Catalog Tools and Samples" and its homepage points at `cloud.google.com/products/knowledge-catalog` ([GitHub API repo metadata](https://api.github.com/repos/GoogleCloudPlatform/knowledge-catalog)). The `okf/README.md` nonetheless states: "**This repository is primarily about the Open Knowledge Format (OKF)**" ([okf/README.md](https://raw.githubusercontent.com/GoogleCloudPlatform/knowledge-catalog/main/okf/README.md)).

---

## 2. What it is and how it works

### 2.1 The core idea

OKF is a **data format / specification**, not a storage engine, index, protocol, or SDK. The spec is explicit that prescribing infrastructure is a **non-goal**: its stated non-goals include "Prescribing storage, serving, or query infrastructure" and "Defining a fixed taxonomy of concept types" ([SPEC.md §1, Non-goals](https://raw.githubusercontent.com/GoogleCloudPlatform/knowledge-catalog/main/okf/SPEC.md)).

The motivating problem, per the v0.1 announcement, is fragmented enterprise context: "As foundation models continue to improve, the lack of relevant context often limits what they can do, especially as they are used to build agentic systems." Knowledge sits scattered across metadata catalogs with proprietary APIs, wikis, shared drives, code comments, and engineers' heads, so "When an AI agent needs to answer 'How do I compute weekly active users from our event stream?' it has to assemble the answer from these scattered, mutually incompatible surfaces." ([Google Cloud Blog, 2026-06-12](https://cloud.google.com/blog/products/data-analytics/how-the-open-knowledge-format-can-improve-data-sharing)).

Google frames v0.1 as "an open specification that formalizes the LLM-wiki pattern into a portable, interoperable format. This is a vendor-neutral, agent- and human-friendly standard for representing the metadata, context, and curated knowledge that modern AI systems need." ([same source](https://cloud.google.com/blog/products/data-analytics/how-the-open-knowledge-format-can-improve-data-sharing)).

The design criteria in the spec are that knowledge representation should be **Readable** by humans without tooling, **Parseable** by agents without bespoke SDKs, **Diffable** in version control, and **Portable** across tools, organizations, and time ([SPEC.md §1](https://raw.githubusercontent.com/GoogleCloudPlatform/knowledge-catalog/main/okf/SPEC.md)).

The v0.2 motivation adds a second concern — agent-authored corpora need trust signals: "Increasingly, a knowledge corpus is not authored once and then read: it is **continuously written and maintained by agents**." The spec enumerates the five questions a consumer needs answered: provenance ("What was this created from, and how was it verified?"), trust ("How much should I trust it?"), freshness ("Is it still true?"), lifecycle ("Is it the current version?"), and attestation ("Was this number produced the way we said it must be?") ([SPEC.md §1](https://raw.githubusercontent.com/GoogleCloudPlatform/knowledge-catalog/main/okf/SPEC.md)).

### 2.2 Stated benefits

From `okf/README.md` ([source](https://raw.githubusercontent.com/GoogleCloudPlatform/knowledge-catalog/main/okf/README.md)), OKF's plain-files choice is claimed to unlock:

- **Human- and agent-readable** — "No SDK or query language stands between a reader and the content. An engineer can `cat` a concept; an LLM can ingest it verbatim into context."
- **Version-controllable out of the box** — "Bundles live in git. Pull requests, line-by-line diffs, blame, and review workflows just work."
- **Portable and lock-in free** — "A bundle is a directory. Ship it as a tarball, host it in any repo, mount it from any filesystem."
- **Mixes structured and unstructured data deliberately** — frontmatter for queryable/filterable fields, markdown body for prose, schemas, and example queries.
- **Trust, provenance, and freshness are first-class** (new in v0.2).
- **Minimally opinionated, freely extensible** — bundles may carry arbitrary extra frontmatter keys and body sections without breaking consumers.
- **Composes with existing tooling** — "Notion, Obsidian, MkDocs, Hugo, Jekyll — already speak markdown plus YAML frontmatter."
- **Progressive disclosure built in** — auto-generated `index.md` files let an agent navigate one level at a time "instead of loading the entire bundle into context."
- **Graph-shaped, not just tree-shaped** — concepts cross-link via normal markdown links.

The README's stated interoperability goal: "**Anyone can produce** OKF — humans authoring by hand, agents built on any framework (Google ADK, LangChain, custom), export pipelines from existing catalogs (Dataplex, Unity Catalog, Collibra, …), or scripts walking a database. **Anyone can serve and consume** OKF — a static file server, a knowledge-management UI (Obsidian, Notion, MkDocs), an LLM loading files into context, a search index, or a graph viewer." ([okf/README.md](https://raw.githubusercontent.com/GoogleCloudPlatform/knowledge-catalog/main/okf/README.md)).

Google's positioning statement on ownership: "Format, not platform. OKF is not tied to any specific cloud, database, model provider, or agent framework. It will never require a proprietary account or SDK to read, write, or serve. We're publishing it as an open standard because the value of a knowledge format comes from how many parties speak it, not from who owns it." ([Google Cloud Blog, 2026-06-12](https://cloud.google.com/blog/products/data-analytics/how-the-open-knowledge-format-can-improve-data-sharing)).

---

## 3. The RAG positioning — an important correction

**Kevin's description ("competing with / an alternative to RAG") reflects how the tech press covered OKF, not how Google positioned it.**

### 3.1 Verification: Google's own sources never mention RAG

I grepped the two authoritative repository documents for RAG-adjacent terminology:

```
grep -inE 'RAG|retrieval|vector|embedding|chunk'  okf/SPEC.md   okf/README.md
```

- `okf/SPEC.md` (1,003 lines): only two hits, neither about RAG — line 63 "Prescribing storage, serving, or query infrastructure" (a non-goal) and line 213 "structure aids both human reading and agent retrieval" (generic use of the word). **No occurrence of "RAG", "vector", "embedding", or "chunk".** ([SPEC.md raw](https://raw.githubusercontent.com/GoogleCloudPlatform/knowledge-catalog/main/okf/SPEC.md))
- `okf/README.md`: **zero hits** for any of those terms. ([okf/README.md raw](https://raw.githubusercontent.com/GoogleCloudPlatform/knowledge-catalog/main/okf/README.md))

Fetching the two Google Cloud blog announcements produced the same result:

- v0.1 announcement (2026-06-12): does not reference RAG, vector search, or embeddings. ([source](https://cloud.google.com/blog/products/data-analytics/how-the-open-knowledge-format-can-improve-data-sharing))
- v0.2 announcement (2026-07-24): does not mention retrieval-augmented generation. ([source](https://cloud.google.com/blog/products/data-analytics/okf-v0-2-adds-trust-signals))
- The related Google Cloud Knowledge Catalog product announcement (2026-04-22) likewise does not mention RAG, vector search, embeddings, or grounding; it describes "high-precision semantic search" using a "hybrid search stack" and "advanced query-rewriting and machine-learning technologies." ([source](https://cloud.google.com/blog/products/data-analytics/introducing-the-google-cloud-knowledge-catalog))

### 3.2 Where the RAG framing actually comes from

The RAG-competitor framing originates with third-party analysts and content marketers, and it is not uniform:

- **Strong "replacement" framing** appears in headlines like "Beyond RAG: How Google's Open Knowledge Format (OKF) is Replacing the Vector Database" ([Medium](https://secret-dev.medium.com/beyond-rag-how-googles-open-knowledge-format-okf-is-replacing-the-vector-database-2ffb5bc2f8eb)) and "Google Just Open-Sourced a Format That Makes RAG Optional" ([Medium](https://medium.com/@patriwala/google-just-open-sourced-a-format-that-makes-rag-optional-inside-okf-396d28865e6e)).
- **Complementary framing** is the more careful reading. Analytics Vidhya states outright: "The future is not OKF versus RAG. It is OKF plus RAG, working together as complementary layers in a single knowledge architecture" — and this is presented as the author's own analysis, not attributed to Google. ([Analytics Vidhya](https://www.analyticsvidhya.com/blog/2026/07/open-knowledge-format-okf/))
- Google's own framing, when described neutrally by coverage, is a **spec for curated context**, e.g. MarkTechPost's headline: "Google Cloud Introduces Open Knowledge Format (OKF): A Vendor-Neutral Markdown Spec for Giving AI Agents Curated Context." ([MarkTechPost](https://www.marktechpost.com/2026/06/16/google-cloud-introduces-open-knowledge-format-okf-a-vendor-neutral-markdown-spec-for-giving-ai-agents-curated-context/)) *(Note: this URL returned HTTP 503 when I attempted a direct fetch; the title is from search-result metadata, and I have not verified its body text.)*

### 3.3 The substantive technical contrast (as argued by third parties)

The comparison analysts draw, which is a fair *reading* of the format even though Google does not make it:

| Dimension | OKF | RAG |
|---|---|---|
| Best for | Curated organizational knowledge | Large unstructured document collections |
| Retrieval | Deterministic navigation via explicit links | Semantic similarity search |
| Infrastructure | File system + Git | Embeddings + vector database |
| Scalability | Moderate | Excellent |
| Explainability | High | Moderate |
| Version control | Native Git support | Requires re-indexing |

(Table as published by [Analytics Vidhya](https://www.analyticsvidhya.com/blog/2026/07/open-knowledge-format-okf/).)

The mechanism behind the contrast is real and traceable to the spec: because a bundle is a link-connected tree of small markdown files with `index.md` progressive disclosure, an agent can **navigate deterministically** to the right concept and load it verbatim into context, rather than embedding a corpus and retrieving probabilistically-ranked chunks ([SPEC.md §3 and okf/README.md "Progressive disclosure built in"](https://raw.githubusercontent.com/GoogleCloudPlatform/knowledge-catalog/main/okf/README.md)). But the spec never claims this displaces RAG, and it explicitly declines to prescribe any retrieval infrastructure at all ([SPEC.md §1, Non-goals](https://raw.githubusercontent.com/GoogleCloudPlatform/knowledge-catalog/main/okf/SPEC.md)).

### 3.4 The "LLM wiki" lineage

Google's v0.1 blog says OKF "formalizes the LLM-wiki pattern" ([source](https://cloud.google.com/blog/products/data-analytics/how-the-open-knowledge-format-can-improve-data-sharing)). Third-party coverage attributes that pattern to Andrej Karpathy, citing his analogy "Obsidian is the IDE. The LLM is the programmer. The wiki is the codebase." ([Analytics Vidhya](https://www.analyticsvidhya.com/blog/2026/07/open-knowledge-format-okf/)). **I did not find a Karpathy attribution in any Google primary source I fetched** — `SPEC.md` contains no occurrence of "Karpathy" (grep confirmed; the only "wiki" hits are `wiki.acme` placeholder URLs in the worked example). Treat the Karpathy credit as third-party attribution.

---

## 4. Version history

| Version | Date | Evidence |
|---|---|---|
| **v0.1** | 2026-06-12 | Announcement blog published 2026-06-12 ([source](https://cloud.google.com/blog/products/data-analytics/how-the-open-knowledge-format-can-improve-data-sharing)); first `okf/SPEC.md` commit dated `2026-06-12T05:02:31Z`, message "Import Open Knowledge Format reference enrichment agent (#28)" ([commit history API](https://api.github.com/repos/GoogleCloudPlatform/knowledge-catalog/commits?path=okf/SPEC.md)) |
| **v0.2** | 2026-07-24 | Announcement blog published 2026-07-24 ([source](https://cloud.google.com/blog/products/data-analytics/okf-v0-2-adds-trust-signals)); commit `2026-07-24T16:45:07Z` "okf: migrate format and tooling to Open Knowledge Format v0.2 (#227)", followed by `2026-07-24T16:45:43Z` "Update SPEC.md" ([commit history API](https://api.github.com/repos/GoogleCloudPlatform/knowledge-catalog/commits?path=okf/SPEC.md)) |

**v0.2 is confirmed current as of 2026-08-03.** `SPEC.md` line 3 reads "**Version 0.2**", and the `okf/README.md` links to "Read the Open Knowledge Format v0.2 specification" ([SPEC.md](https://raw.githubusercontent.com/GoogleCloudPlatform/knowledge-catalog/main/okf/SPEC.md); [okf/README.md](https://raw.githubusercontent.com/GoogleCloudPlatform/knowledge-catalog/main/okf/README.md)). The `okf/SPEC.md` file has had only three commits total, the last on 2026-07-24 — no v0.3 work has landed.

**Caveat on release mechanics:** the repository publishes **no GitHub Releases and no git tags** ([releases API](https://api.github.com/repos/GoogleCloudPlatform/knowledge-catalog/releases) and [tags API](https://api.github.com/repos/GoogleCloudPlatform/knowledge-catalog/tags) both return empty). Versioning is expressed only inside the spec text and in bundle frontmatter. There is no machine-readable release channel to subscribe to.

### 4.1 What changed in v0.2

Per [SPEC.md §13](https://raw.githubusercontent.com/GoogleCloudPlatform/knowledge-catalog/main/okf/SPEC.md), v0.2 "supersedes OKF v0.1 and is a minor version bump … except for two deliberate breaking changes."

**Breaking changes (§13.1):**
1. **`timestamp` is superseded by `generated.at`.** Last content change is now recorded as `generated: { by, at }`. "Consumers MAY fall back to a legacy `timestamp` when `generated` is absent."
2. **The body `# Citations` list is superseded by `sources`.** Provenance moves from the markdown body into frontmatter. "Consumers SHOULD read `sources` and MAY still parse a legacy `# Citations` body list for v0.1 documents."

**Additive changes (§13.2):**
- New frontmatter families: `sources` (with per-source credibility signals `author`, `usage_count`, `last_modified`, plus the `usage_window` sibling); `generated`; `verified`; `status`; `stale_after`.
- New concept type `Attested Computation` and its keys `runtime`, `parameters`, `computation`, `executor`, `attester`.
- New conventional body heading `# Computation`.
- The actor convention for `generated.by` and `verified[].by`.

Everything else — bundle structure, reserved filenames, the required `type`, recommended `title`/`description`/`resource`/`tags`, cross-linking, index files, log files, permissive conformance — "is carried forward unchanged" ([SPEC.md §13.2](https://raw.githubusercontent.com/GoogleCloudPlatform/knowledge-catalog/main/okf/SPEC.md)).

Google's own summary of the shift: "v0.1 already kept metadata in frontmatter: `type`, `title`, `description`, `resource`, `tags`. Those fields describe a concept: what it is and what it points at. v0.2 adds a second kind of frontmatter field, the kind you use to decide something about a concept before you read it." ([Google Cloud Blog, 2026-07-24](https://cloud.google.com/blog/products/data-analytics/okf-v0-2-adds-trust-signals)). The same post stresses compatibility: "every new field is opt-in, custom keys are still preserved rather than rejected, and a bundle that adopts none of the additions is exactly as valid as it was under v0.1."

---

## 5. Format and architecture details

All content in this section is from [SPEC.md v0.2](https://raw.githubusercontent.com/GoogleCloudPlatform/knowledge-catalog/main/okf/SPEC.md) unless otherwise noted.

### 5.1 Terminology (§2)

- **Knowledge Bundle** (or **bundle**): "A self-contained, hierarchical collection of knowledge documents. The unit of distribution."
- **Concept**: "A single unit of knowledge within a bundle, represented as one markdown document." May describe a tangible asset (a table, an API), an abstract idea (a metric, a business process), or anything in between.
- **Concept ID**: "The path of the concept's file within the bundle, with the `.md` suffix removed."
- **Frontmatter**: A YAML metadata block delimited by `---` at the top of a markdown file. **Body**: everything after it.
- **Link**: A standard markdown link from one concept to another, expressing relationships beyond the implicit parent/child hierarchy.
- **Source**: A material a concept derives from, recorded in `sources`.
- **Credibility signal**: "An objective, per-source fact (`author`, `usage_count`, `last_modified`) used to infer trust; OKF records the signals, not a verdict."
- **Trust tier**: A level derived from `verified` — unverified, machine-confirmed, or human-reviewed.
- **Attested Computation**: A concept "carrying a sanctioned way to compute a value, so a consumer can confirm the value was produced by running it."
- **Executor** / **Receipt** / **Attester**: run instructions returning a receipt; the evidence a run returns (a runtime artifact, not stored in the bundle); and deterministic, no-LLM code that inspects a receipt and returns a verdict.

### 5.2 Bundle structure (§3)

"A bundle is a directory tree of markdown files. The directory structure is independent of the domain: producers organize concepts however makes sense for the knowledge being captured."

```
path/to/bundle/
  index.md                      # Optional. Directory listing for progressive disclosure.
  log.md                        # Optional. Chronological history of updates.
  <concept>.md                  # A concept at the bundle root.
  <subdirectory>/               # Subdirectories organize concepts into groups.
```

Reserved filenames are `index.md` and `log.md`; all other `.md` files are concept documents. Distribution is via git repositories (recommended), tarballs, zip archives, or subdirectories within larger repositories.

### 5.3 Frontmatter fields

**Required (the only universally mandatory field):**

| Field | Meaning |
|---|---|
| `type` | "A short string identifying the kind of concept." Examples given: `BigQuery Table`, `API Endpoint`, `Metric`, `Playbook`, `Attested Computation`. |

**Recommended:**

| Field | Meaning |
|---|---|
| `title` | Human-readable display name |
| `description` | Single-sentence summary |
| `resource` | Canonical URI for the underlying asset |
| `tags` | YAML list for cross-cutting categorization |

**Provenance — `sources` (§5.1).** Each entry may carry:

| Key | Meaning |
|---|---|
| `resource` (required within entry) | Artifact URI or scope descriptor |
| `id` | Stable key for attribution |
| `title` | Human-readable label |
| `author` | Producer identity (authority signal) |
| `usage_count` | Exercise frequency (adoption signal) |
| `last_modified` | Source change date (recency signal) |
| `usage_window` | Sibling field; date range framing usage counts |

**Trust — `generated` and `verified` (§5.2, §5.3).**

- `generated: { by, at }` — `by` (required within) is an actor identifier; `at` is an ISO 8601 datetime of the last meaningful change.
- `verified: [ { by, at } ]` — a list of verification events confirming content against `sources` or `resource`.

**Lifecycle.**

- `status` — `draft`, `stable` (default), or `deprecated`.
- `stale_after` — absolute date (`YYYY-MM-DD`) after which content is considered stale.

### 5.4 Actor convention (§7)

Three formats for identity fields (`generated.by`, `verified[].by`):

- `<producer>/<version>` for agents — e.g. `reference_agent/gemini-2.5-pro`
- `human:<id>` for people — e.g. `human:ahormati`
- `process:<id>` for automated processes — e.g. `process:finance-nightly`

### 5.5 Trust tiers (§5.3)

Derived, not declared:

1. No `verified` key → **unverified**
2. Machine actors only → **machine-confirmed**
3. Any `human:<id>` actor → **human-reviewed**

Google's framing: "Tiers are advisory signals, not access control, but they let a consumer say 'only surface human-reviewed metrics in the executive dashboard' as a frontmatter filter." ([Google Cloud Blog, 2026-07-24](https://cloud.google.com/blog/products/data-analytics/okf-v0-2-adds-trust-signals))

### 5.6 Cross-linking model

- Markdown links express relationships; the *kind* of relationship is conveyed by surrounding prose rather than a typed edge vocabulary.
- Two link forms: absolute bundle-relative (beginning with `/`, recommended) and relative paths.
- Path-valued fields accept absolute URLs, bundle-relative paths, or relative paths.
- **Broken links are tolerated** — "they may represent not-yet-written knowledge."

### 5.7 Attested Computation (§10)

A concept type for computations a consumer can verify were produced the sanctioned way. Contract fields:

- `runtime` (required for this type) — defines parameter semantics; documented values include `bigquery`, `postgres`, `dbt`, `python`, `Looker`.
- `parameters` — list of typed, named holes: `{ name, type, required }`.
- `computation` — optional path to a computation file; if absent, the body's `# Computation` fence holds it.
- `executor` — `{ resource, receipt }`: `resource` names run instructions, `receipt` declares the fields a run returns.
- `attester` — `{ resource }`: deterministic (no-LLM) check code that inspects a receipt and returns a verdict.

A worked example from the spec's Appendix A — a BigQuery revenue metric, human-verified, with credibility-signalled sources:

```markdown
---
type: Attested Computation
title: Revenue for fiscal year
description: Recognized revenue for a fiscal year, per Finance's definition.
tags: [finance, revenue]
status: stable
runtime: bigquery
parameters:
  - { name: year, type: integer, required: true }
executor:
  resource: references/skills/run-on-bq.md
  receipt: [job_id, executed_sql, result]
attester:
  resource: references/attesters/sql-equality.py
generated: { by: reference_agent/gemini-2.5-pro, at: 2026-06-28T14:00:00Z }
verified: { by: human:ahormati, at: 2026-06-25T09:00:00Z }
stale_after: 2026-12-31
sources:
  - id: rev-policy
    resource: https://wiki.acme/finance/revenue-recognition
    title: Revenue recognition policy
    author: team:finance-fpa
    last_modified: 2026-04-02
usage_window: { from: 2026-06-01, to: 2026-06-30 }
---

# Computation

    SELECT SUM(amount) AS revenue
    FROM finance.recognized_revenue
    WHERE fiscal_year = @year
```

### 5.8 Conformance rules (§ conformance)

A conformant bundle must satisfy three rules:

1. Every non-reserved `.md` file contains parseable YAML frontmatter.
2. Every frontmatter block contains a non-empty `type` field.
3. Reserved filenames follow their specified structures when present.

Crucially, the spec constrains *consumers* too — they **must not reject** documents for missing optional families, unknown types, unknown keys, broken links, or missing index files. This permissiveness is what makes v0.1 bundles readable by v0.2 consumers.

### 5.9 Versioning rules (§12)

Versioning follows `<major>.<minor>`: minor bumps introduce backward-compatible additions, major bumps allow breaking changes. A bundle may declare its target version with `okf_version: "0.2"` — **in the bundle-root `index.md` frontmatter only**.

### 5.10 Explicit non-goals (§1)

- Defining a fixed taxonomy of concept types.
- Prescribing storage, serving, or query infrastructure.
- Replacing domain-specific schemas (Avro, Protobuf, OpenAPI, and so on) — "OKF *references* them; it does not subsume them."
- Specifying a packaging or invocation standard for executor/attester code — "OKF fixes the interface, not the packaging."

---

## 6. Adoption and ecosystem signals

**Repository metrics** (fetched from the [GitHub API](https://api.github.com/repos/GoogleCloudPlatform/knowledge-catalog) on 2026-08-03):

| Metric | Value |
|---|---|
| Repository created | 2026-05-04 |
| Last push | 2026-08-01 |
| Stars | 8,225 |
| Forks | 697 |
| Open issues | 161 |
| License | Apache-2.0 |
| Archived | No |

That is substantial traction for a spec repository roughly three months old, and the open-issue count plus recent push activity indicate it is actively maintained rather than a drop-and-abandon publication.

**Third-party ecosystem.** A GitHub repository search for "okf open knowledge format" returns **252 repositories** ([search API](https://api.github.com/search/repositories?q=okf+open+knowledge+format&sort=stars)). The most-starred, as of 2026-08-03:

| Stars | Repository | Description |
|---|---|---|
| 224 | [`scaccogatto/okf-skills`](https://github.com/scaccogatto/okf-skills) | "The OKF toolkit for Claude Code — author, maintain, validate & visualize Open Knowledge Format bundles. Plugin, agent skills, and a GitHub Action." (MIT, created 2026-06-14, pushed 2026-08-03) |
| 117 | `serradura/okf-gem` | "Open Knowledge Format for coding agents. Author, validate, lint, search, and visualize…" |
| 112 | `coleam00/cole-medin-ai-coding` | "An Open Knowledge Format (OKF) bundle of Cole Medin's best AI-coding videos" |
| 86 | `OWOX/models` | "OWOX Model Canvas — visual editor for data models in the Open Knowledge Format (OKF)" |
| 75 | `coleam00/cole-medin-knowledge-base` | "An OKF knowledge base + Karpathy-style LLM wiki" |
| 62 | `0dust/OKFy` | "Turn docs into agent-readable knowledge bundles using Open Knowledge Format (OKF)" |
| 56 | `sniperunder123/okf-knowledge` | "A portable Claude Code skill (`/okf`) to create, read, maintain & visualize OKF" |
| 43 | `longsizhuo/okf-frontmatter` | "skill: maintain repo docs under the Open Knowledge Format" |
| 35 | `openknowledge-sh/openknowledge` | "CLI tool for managing Open Knowledge Format (OKF) bundles." |
| 29 | `xSAVIKx/okf-skills` | "Open Knowledge format agentic skills" |

(All rows from the same [search API result](https://api.github.com/search/repositories?q=okf+open+knowledge+format&sort=stars); the `okf-skills` detail line is from its [repo API record](https://api.github.com/repos/scaccogatto/okf-skills).)

Note how much of this ecosystem is **agent-skill tooling** rather than data-platform tooling — several of the top repos are Claude Code skills/plugins for authoring and validating bundles. That is a meaningful signal about where OKF is actually being picked up.

**Google-side product context.** The repository sits alongside Google Cloud Knowledge Catalog, announced 2026-04-22 as "an evolution of Dataplex into a dynamic, always-on context engine" providing "deep business semantics and data relationships," unified metadata, and "high-precision retrieval for agents" ([Google Cloud Blog](https://cloud.google.com/blog/products/data-analytics/introducing-the-google-cloud-knowledge-catalog)). **Unverified claim:** search-result summaries stated that a "BigQuery Knowledge Catalog integration ingests OKF natively," but I could **not** confirm this on the Knowledge Catalog product blog page itself — that page does not directly mention OKF. Treat native Knowledge Catalog↔OKF ingestion as plausible but unconfirmed by a primary source I fetched.

**Standards-body status:** none observed. OKF is published unilaterally by Google Cloud under Apache 2.0; I found no evidence of submission to a neutral standards organization.

---

## 7. Getting started

All commands from [`okf/README.md`](https://raw.githubusercontent.com/GoogleCloudPlatform/knowledge-catalog/main/okf/README.md).

Important framing from that README: the bundled agent is "a **proof of concept** demonstrating *one* way to produce OKF bundles automatically. The format itself is the contribution; this agent and the visualizer exist to make the format tangible at both ends — production and consumption." **There is nothing to install to simply author OKF by hand** — it is markdown files in a directory.

### 7.1 Install (for the reference tooling)

```bash
python3.13 -m venv .venv
.venv/bin/pip install --index-url https://pypi.org/simple/ -e .[dev]
```

### 7.2 Credentials (reference agent only)

- **BigQuery:** `gcloud auth application-default login` plus a billing project (`gcloud config set project <id>`). Public datasets are readable, but the caller's project is billed for query bytes.
- **Gemini:** set `GEMINI_API_KEY` (AI Studio) **or** use Vertex AI via `GOOGLE_GENAI_USE_VERTEXAI=true`, `GOOGLE_CLOUD_PROJECT=<id>`, `GOOGLE_CLOUD_LOCATION=<region>`.

### 7.3 Produce a bundle

```bash
.venv/bin/python -m reference_agent enrich \
    --source bq \
    --dataset <project>.<dataset> \
    --web-seed-file <path/to/seeds.txt> \
    --out ./bundles/<name>
```

The reference agent runs two passes. The **BQ pass** writes one OKF doc per concept the source advertises, from BigQuery metadata alone. The **web pass** runs the LLM as its own crawler: it fetches seed URLs via a `fetch_url` tool and decides which outbound links to follow based on whether they look like authoritative documentation for existing concepts. For each page it either (a) enriches existing concept docs, (b) mints a standalone `references/<slug>` doc, or (c) skips. A hard `--web-max-pages` cap and a same-domain allowed-hosts filter (`--web-allowed-host`) are enforced inside the tool "so the agent cannot overrun." Use `--no-web` to skip the web pass, and `--concept <type>/<name>` to iterate on one concept.

### 7.4 Visualize a bundle

```bash
.venv/bin/python -m reference_agent visualize --bundle ./bundles/<name>
```

This writes a **self-contained interactive HTML file** (`viz.html`) — one file, no backend. It renders a force-directed graph of every concept (nodes colored by type, edges from markdown cross-links), a detail panel showing frontmatter plus rendered body with internal links rewired to navigate in-viewer, a "Cited by" backlinks list computed from the reverse link graph, a search box matching title/concept id/tags, a type filter, and switchable layouts (cose / concentric / breadth-first / circle / grid). Flags: `--bundle` (required), `--out` (default `<bundle>/viz.html`), `--name`.

Implementation note from the README: the HTML embeds the bundle as a JSON blob and uses [Cytoscape.js](https://js.cytoscape.org/) for the graph and [marked](https://marked.js.org/) for markdown rendering, "both loaded from a CDN." (Relevant if you need a genuinely offline artifact — the graph libs are not inlined.)

### 7.5 Tests

```bash
.venv/bin/pytest
```

### 7.6 Ready-made example bundles

Four bundles are checked into `bundles/` ([okf/README.md](https://raw.githubusercontent.com/GoogleCloudPlatform/knowledge-catalog/main/okf/README.md)); the [`okf/samples/`](https://api.github.com/repos/GoogleCloudPlatform/knowledge-catalog/contents/okf/samples) directory pairs each with a reproducible recipe (seed URLs plus the exact `enrich` command):

- `bundles/ga4/` — GA4 Google Merchandise Store e-commerce dataset
- `bundles/stackoverflow/` — Stack Overflow public dataset; exercises multi-concept enrichment from cross-cutting docs pages
- `bundles/crypto_bitcoin/` — Bitcoin blocks/transactions from `bitcoin-etl`; exercises cross-table foreign-key relationships in prose
- `bundles/acme_retail/` — Acme Retail

*(The `samples/` API listing returned three directories — `crypto_bitcoin`, `ga4_merch_store`, `stackoverflow` — so `acme_retail` appears as a bundle without a matching sample recipe.)*

---

## 8. Assessment and caveats

1. **The name and creator check out exactly.** "Open Knowledge Format" is the literal official name; Google Cloud is genuinely the creator.
2. **v0.2 is real, current, and well-documented**, but there are no git tags or GitHub Releases — versioning lives only in spec text ([releases API](https://api.github.com/repos/GoogleCloudPlatform/knowledge-catalog/releases), [tags API](https://api.github.com/repos/GoogleCloudPlatform/knowledge-catalog/tags)).
3. **The RAG framing is media framing, not Google's.** Anyone evaluating OKF on the premise that "Google says this replaces RAG" is working from a claim Google did not make. The spec explicitly declines to prescribe retrieval infrastructure.
4. **It is a format, deliberately not a system.** There is no index, no server, no query language, and no runtime in scope. Any "does it beat RAG" benchmark comparison is a category error unless you also specify the consumer that reads the bundle.
5. **The interesting v0.2 idea is arguably attestation, not storage.** `Attested Computation` — shipping the sanctioned SQL/dbt computation plus a deterministic, no-LLM attester that validates a run receipt — is a mechanism for making agent-reported numbers checkable. That has no RAG analogue at all and is underplayed in the popular coverage.
6. **Unverified item flagged above:** native Knowledge Catalog/BigQuery ingestion of OKF appeared in search summaries but not in any Google page I fetched.

---

## Sources

### Primary — Google

- Google Cloud Blog, "How the Open Knowledge Format can improve data sharing" (v0.1 announcement, 2026-06-12) — https://cloud.google.com/blog/products/data-analytics/how-the-open-knowledge-format-can-improve-data-sharing
- Google Cloud Blog, OKF v0.2 trust signals announcement (2026-07-24) — https://cloud.google.com/blog/products/data-analytics/okf-v0-2-adds-trust-signals
- Google Cloud Blog, "Introducing the Google Cloud Knowledge Catalog" (2026-04-22) — https://cloud.google.com/blog/products/data-analytics/introducing-the-google-cloud-knowledge-catalog
- OKF v0.2 specification, `okf/SPEC.md` — https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md (raw: https://raw.githubusercontent.com/GoogleCloudPlatform/knowledge-catalog/main/okf/SPEC.md)
- `okf/README.md` — https://raw.githubusercontent.com/GoogleCloudPlatform/knowledge-catalog/main/okf/README.md
- `okf/LICENSE.md` (Apache 2.0) — https://raw.githubusercontent.com/GoogleCloudPlatform/knowledge-catalog/main/okf/LICENSE.md
- OKF directory index — https://github.com/GoogleCloudPlatform/knowledge-catalog/tree/main/okf
- Repository root — https://github.com/GoogleCloudPlatform/knowledge-catalog

### Primary — GitHub API (fetched 2026-08-03)

- Repo metadata (stars, forks, license, dates) — https://api.github.com/repos/GoogleCloudPlatform/knowledge-catalog
- `okf/` directory contents — https://api.github.com/repos/GoogleCloudPlatform/knowledge-catalog/contents/okf
- Repo root contents — https://api.github.com/repos/GoogleCloudPlatform/knowledge-catalog/contents/
- `okf/samples/` contents — https://api.github.com/repos/GoogleCloudPlatform/knowledge-catalog/contents/okf/samples
- `okf/SPEC.md` commit history — https://api.github.com/repos/GoogleCloudPlatform/knowledge-catalog/commits?path=okf/SPEC.md
- Releases (empty) — https://api.github.com/repos/GoogleCloudPlatform/knowledge-catalog/releases
- Tags (empty) — https://api.github.com/repos/GoogleCloudPlatform/knowledge-catalog/tags
- Ecosystem repository search — https://api.github.com/search/repositories?q=okf+open+knowledge+format&sort=stars
- `scaccogatto/okf-skills` metadata — https://api.github.com/repos/scaccogatto/okf-skills

### Secondary — used only to characterize third-party RAG framing

- Analytics Vidhya, "OKF: Redefining Knowledge Bases for AI Agents" — https://www.analyticsvidhya.com/blog/2026/07/open-knowledge-format-okf/
- MarkTechPost, "Google Cloud Introduces Open Knowledge Format (OKF)…" *(title from search metadata; direct fetch returned HTTP 503, body unverified)* — https://www.marktechpost.com/2026/06/16/google-cloud-introduces-open-knowledge-format-okf-a-vendor-neutral-markdown-spec-for-giving-ai-agents-curated-context/
- Medium (Secret Dev), "Beyond RAG: How Google's OKF is Replacing the Vector Database" *(cited as an example of replacement framing; not fetched)* — https://secret-dev.medium.com/beyond-rag-how-googles-open-knowledge-format-okf-is-replacing-the-vector-database-2ffb5bc2f8eb
- Medium (Amit Patriwala), "Google Just Open-Sourced a Format That Makes RAG Optional" *(cited as an example of replacement framing; not fetched)* — https://medium.com/@patriwala/google-just-open-sourced-a-format-that-makes-rag-optional-inside-okf-396d28865e6e
- HPE Community, "Open Knowledge Format vs. RAG" *(direct fetch returned HTTP 403; content unverified)* — https://community.hpe.com/t5/ai-unlocked/open-knowledge-format-vs-rag-rethinking-how-ai-agents-get-their/ba-p/7270244

### Third-party tooling referenced

- `scaccogatto/okf-skills` — https://github.com/scaccogatto/okf-skills
