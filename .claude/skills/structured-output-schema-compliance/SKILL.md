---
name: structured-output-schema-compliance
description: >
  Emit JSON that conforms exactly to the StructuredOutput tool's required schema so a workflow
  subagent's return is accepted, not rejected. Use when a subagent or agent must call the
  StructuredOutput tool exactly once with a role-specific JSON schema (or return any
  schema-validated envelope a parent will json.loads) and the output keeps getting rejected.
  Trigger phrases: "output does not match required schema", "must have required property",
  "structured output", "StructuredOutput tool", "emit conforming JSON", "schema validation
  failed", "return only JSON", "strip markdown code fences from JSON", "self-validate output
  keys", "InputValidationError schema", "my output keeps getting rejected", "subagent schema
  mismatch", "missing required key in structured output", "wrap output in json code fence",
  "JSON does not match the required schema", "must be number", "must be array", "must be of
  type", "wrong JSON type in structured output", "value has the wrong type for schema",
  "confidence must be a number", "evidence must be an array", "subagent returned nothing",
  "subagent returned no output", "orchestrator got an empty result", "agent answered in text
  instead of calling the tool", "StructuredOutput tool was never called", "must NOT have
  additional properties", "unexpected additional property", "extra key in structured output",
  "remove additionalProperties from output", "renamed schema key rejected", "dimension headline
  conclusion evidence schema", "verdict findings lens summary schema", "evidence recommended_fix
  confidence schema", "claim verdict supporting_evidence schema", "summary findings schema",
  "summary evidence root_cause_contribution schema". NOT for: configuring a provider's
  structured-output API or responseSchema (use gemini-api-dev), MCP tool input schemas (use
  mcp-builder), or verifying the work itself is correct (use verification-before-completion) —
  this skill is only about the return-envelope contract.
user-invocable: true
risk: safe
source: "Derived from the UA skill-gap finder backlog (issue #796) -- structured-output-schema-compliance."
---

# Structured Output Schema Compliance

UA workflow subagents are spawned by the orchestrator and told: **"You MUST call the
`StructuredOutput` tool exactly once"** with a role-specific JSON schema. The orchestrator script
reads **only** that tool call — everything else you emit (prose, reasoning, a text answer) is
discarded. The single highest-frequency failure in the backlog is the validator rejecting that
call: **188 schema-mismatch failures across 8 schema types** in the transcript corpus.

The one rule: **the StructuredOutput tool's input schema is the authority.** Read it, conform to it
exactly, and return nothing but conforming JSON. The schema is never fixed — it is dictated
per-spawn by the orchestrator. Do not assume a shape from memory; read the shape you were given.

## The validator wording to recognize

When the call is rejected the message is literal and names every missing key:

```
Output does not match required schema: root: must have required property 'dimension',
root: must have required property 'headline', root: must have required property 'conclusion',
root: must have required property 'evidence', root: must have required property 'confidence'
```

Each `must have required property '<key>'` clause maps to exactly one missing key. Fix it
surgically: add the named key(s) and re-call. Do not restructure the whole object blindly.

## The canonical recipe

A six-step discipline. Run it every time you are about to return a structured result.

1. **Identify the required schema.** Read the `StructuredOutput` tool's input schema and the
   spawn prompt's schema block. Note **every** `required` key and its declared type
   (array vs string vs number/object). That list is your contract.

2. **Emit strictly-conforming JSON.** Every required key present, each value the correct JSON
   type. No renamed keys, no extra keys unless `additionalProperties` allows them.

3. **Return ONLY the structured call.** No surrounding prose, no "Here is my output:", no trailing
   commentary. The script discards everything outside the tool call, so a preamble doesn't help and
   a malformed wrapper hurts.

4. **Tolerate and strip markdown code fences.** When YOU read inbound JSON (e.g. upstream output),
   strip a leading ```` ```json ```` / trailing ```` ``` ```` before `json.loads`. When you EMIT,
   pass the raw object to the tool — never a fenced string.

5. **Self-validate before returning.** Walk the required-key list against your object; check each
   type. If a key is unknown to you, do **not** invent a value — re-read the schema.

6. **On rejection, fix surgically.** Read the `must have required property 'X'` message literally,
   add exactly the named key(s) with correct types, and re-call. Don't rebuild from scratch.

## Real recurring schemas (recognize, but always defer to the live spawn schema)

These are the shapes that actually appear in UA transcripts. Treat them as recognition aids — the
authoritative schema is always the one in your spawn, not this list.

| Subagent role | Required keys | Corpus frequency |
|---|---|---|
| research-brief / dimension | `dimension, headline, conclusion, evidence, confidence` | 148× (dominant) |
| diagnosis | `evidence, recommended_fix, confidence` | 14× |
| fact-check / verdict | `claim, verdict, confidence, supporting_evidence` | 6× |
| audit / review | `summary, findings` | 4× |
| root-cause | `summary, evidence, root_cause_contribution, confidence` | 2× |
| migration-audit | `stale_assertions, breaks_on_migration, ...` | 2× |

Confirmed **conforming** sibling schemas from successful tool calls (proof the schema is
per-spawn): lens-finding `{findings, lens, summary, verdict}` and
doc-drift `{broken_pointers, drift_fixes, edited, file, summary}`. Single-key rejections also occur
(`'notes'`, `'summary'`, `'recommendation'`, `'real_root_cause'`) — the surgical case: add exactly
the one named key.

## Pre-return checklist

Copy-pasteable; tick every box before you return.

```
[ ] Did I actually CALL StructuredOutput (not answer in a plain text response)?
[ ] Is every required key present?
[ ] Is each value the right JSON type (arrays as arrays, confidence as a number not a string)?
[ ] Is there NO prose or markdown outside the JSON object?
[ ] Is there NO leftover ```json fence wrapping the object?
[ ] Did I avoid inventing or renaming any key not in the schema?
```

## Before / after

**(A) Omitted required key + prose preamble**

```
BEFORE  →  rejected: must have required property 'dimension'
  text:  "Here's the dimension analysis:"
  call:  StructuredOutput({ headline, conclusion, evidence, confidence })

AFTER   →  accepted
  call:  StructuredOutput({ dimension, headline, conclusion, evidence, confidence })
  (no surrounding text)
```

**(B) JSON emitted as a fenced string instead of an object**

```
BEFORE  →  wrong type / not parsed
  call:  StructuredOutput("```json\n{\"summary\": ...}\n```")

AFTER   →  accepted
  call:  StructuredOutput({ summary: ..., findings: [...] })
  (pass the bare object; strip fences only when READING inbound JSON)
```

## Root causes behind rejections

1. A required key is omitted (most common — `must have required property 'dimension'`).
2. Prose/explanation is returned alongside or instead of the JSON.
3. JSON is wrapped in a ```` ```json ```` fence and the wrong layer parses it.
4. A value has the wrong type (string where an array or number was expected).
5. The agent answers in a text response and **never calls the StructuredOutput tool** — the
   orchestrator reads only the tool call, so this silently fails the whole subagent.

## When to use

- A subagent must call `StructuredOutput` exactly once with a role-specific schema.
- Any schema-validated return envelope a parent agent will `json.loads`.
- The output keeps getting rejected with "Output does not match required schema".

## When NOT to use

- Configuring a **provider's** structured-output API / `responseSchema` → use `gemini-api-dev`.
- Defining an **MCP tool's** input schema → use `mcp-builder`.
- Verifying the **work itself** is correct (running test/verification commands before a success
  claim) → use `verification-before-completion`. That is verify-the-work; this is
  verify-the-return-envelope. They are complementary, not the same.

## NEVER

- NEVER add explanatory prose, a preamble, or trailing commentary around the JSON — it is discarded
  at best and breaks parsing at worst.
- NEVER omit a required key, and NEVER guess a key that is not in the schema.
- NEVER answer in a plain text response when a structured tool call is required.
- NEVER emit the JSON as a fenced string when the tool expects an object.
- NEVER ignore the `must have required property` message — it names the exact fix.
