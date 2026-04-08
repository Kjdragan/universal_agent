scan discord for eventts. notify me.  if i cant attentd (or iven if i do? watch events and summarize and deliver summary to me.

get up top speed on autoresearch and program.md file with it

whisper dfeployment

create a /btw  feature so i can have fast response dide channel

devin as the code pr check?

unsloth studio

google turboquant kv cache compression

gemini 3.1 flash create a tab in our UA with a bunch of sliders that can show video, hear audio, etcetera, and then I can just have like a conversation with an AI there that is real time with a webcam.

voxtral tts emotional tts

home kits outside of the apps... example runn sonos speakers

LiteParse new from llamaparse

CMUX

investigate Browserbase pay browser service

stripe machine to machinepayments research

Take a look at our GitHub action with regards to the OpenClaw research process and see if we can generalize this so we can have this kind of up-to-date latest release analysis for whatever library we want to examine

research on businesses for agents as the customer (what are the pain points?)

Airport terminal tv display

biomcp

Done: amazing game graph of claude activities

claude code organiser repo for moving/copying globsal local mcps and skills!

ultra has deepthink capabilities.  Hiow can i use?

improved scraping, update use of crawkl4ai, schema investigation for particular sites such as hacker newsrather than blanket scrapes,.  When scraping, make sure skill porcesses pipeline tio get it in good for for model to then injest.

weights and biases hackathon resuklts and repos

explore agentforge repo from hackathon... already forked

Services for agens dirtectory ()angent native/first)

google notebooklm knopwledge/process base plus gems = agentic analysis system for any process.

DONE: omc  install

start using gemini deep research in notebooklm daily: automate?

build a nemoclaw as an extra agent to use in our system if infrence is free

be more amnitious re what you can do...

feeds into idea of "work to give agents more agency"

So if prototyping is free, how do we get AI to automate the evaluation of prototypes? I we can generate lots of code programs, but we don't use them to check to see if they work properly and therefore improve them. So the signal of a well-developed code with documentation, etc., is gone now. because anyone can develop code in minutes with full professional looking code. How do we automate the testing of the prototype?

use the phrase red/green TDD as a part of the prompt for code generation  

"""Build a Python function to extract headers from a markdown string. Use red/green TDD."""

If you have a starting template for each project, it will improve the development. For example, just having a template that you start with that has a one plus one test will encourage the agents to build tests. So we should start to develop a starting template on our projects.

i Simone. I want you to build out a system for us that will be helpful for us in your activities and our universal agent system take a look at this file consider it and Then properly delegate it to Cody, your coding agent, because it is the one that will have to work on this because it has the authority to work on repos. And fix our code.Make sure you pass along the full context of what I've included so that Codie knows it all. I want this to be a great system improvement for our Universal Agent project and its capabilities:  # LLM Wiki  A pattern for building personal knowledge bases using LLMs.  This is an idea file, it is designed to be copy pasted to your own LLM Agent (e.g. OpenAI Codex, Claude Code, OpenCode / Pi, or etc.). Its goal is to communicate the high level idea, but your agent will build out the specifics in collaboration with you.  ## The core idea  Most people's experience with LLMs and documents looks like RAG: you upload a collection of files, the LLM retrieves relevant chunks at query time, and generates an answer. This works, but the LLM is rediscovering knowledge from scratch on every question. There's no accumulation. Ask a subtle question that requires synthesizing five documents, and the LLM has to find and piece together the relevant fragments every time. Nothing is built up. NotebookLM, ChatGPT file uploads, and most RAG systems work this way.  The idea here is different. Instead of just retrieving from raw documents at query time, the LLM incrementally builds and maintains a persistent wiki — a structured, interlinked collection of markdown files that sits between you and the raw sources. When you add a new source, the LLM doesn't just index it for later retrieval. It reads it, extracts the key information, and integrates it into the existing wiki — updating entity pages, revising topic summaries, noting where new data contradicts old claims, strengthening or challenging the evolving synthesis. The knowledge is compiled once and then kept current, not re-derived on every query.  This is the key difference: the wiki is a persistent, compounding artifact. The cross-references are already there. The contradictions have already been flagged. The synthesis already reflects everything you've read. The wiki keeps getting richer with every source you add and every question you ask.  You never (or rarely) write the wiki yourself — the LLM writes and maintains all of it. You're in charge of sourcing, exploration, and asking the right questions. The LLM does all the grunt work — the summarizing, cross-referencing, filing, and bookkeeping that makes a knowledge base actually useful over time. In practice, I have the LLM agent open on one side and Obsidian open on the other. The LLM makes edits based on our conversation, and I browse the results in real time — following links, checking the graph view, reading the updated pages. Obsidian is the IDE; the LLM is the programmer; the wiki is the codebase.  This can apply to a lot of different contexts. A few examples:  - Personal: tracking your own goals, health, psychology, self-improvement — filing journal entries, articles, podcast notes, and building up a structured picture of yourself over time. - Research: going deep on a topic over weeks or months — reading papers, articles, reports, and incrementally building a comprehensive wiki with an evolving thesis. - Reading a book: filing each chapter as you go, building out pages for characters, themes, plot threads, and how they connect. By the end you have a rich companion wiki. Think of fan wikis like Tolkien Gateway — thousands of interlinked pages covering characters, places, events, languages, built by a community of volunteers over years. You could build something like that personally as you read, with the LLM doing all the cross-referencing and maintenance. - Business/team: an internal wiki maintained by LLMs, fed by Slack threads, meeting transcripts, project documents, customer calls. Possibly with humans in the loop reviewing updates. The wiki stays current because the LLM does the maintenance that no one on the team wants to do. - Competitive analysis, due diligence, trip planning, course notes, hobby deep-dives — anything where you're accumulating knowledge over time and want it organized rather than scattered.  ## Architecture  There are three layers:  Raw sources — your curated collection of source documents. Articles, papers, images, data files. These are immutable — the LLM reads from them but never modifies them. This is your source of truth.  The wiki — a directory of LLM-generated markdown files. Summaries, entity pages, concept pages, comparisons, an overview, a synthesis. The LLM owns this layer entirely. It creates pages, updates them when new sources arrive, maintains cross-references, and keeps everything consistent. You read it; the LLM writes it.  The schema — a document (e.g. CLAUDE.md for Claude Code or AGENTS.md for Codex) that tells the LLM how the wiki is structured, what the conventions are, and what workflows to follow when ingesting sources, answering questions, or maintaining the wiki. This is the key configuration file — it's what makes the LLM a disciplined wiki maintainer rather than a generic chatbot. You and the LLM co-evolve this over time as you figure out what works for your domain.  ## Operations  Ingest. You drop a new source into the raw collection and tell the LLM to process it. An example flow: the LLM reads the source, discusses key takeaways with you, writes a summary page in the wiki, updates the index, updates relevant entity and concept pages across the wiki, and appends an entry to the log. A single source might touch 10-15 wiki pages. Personally I prefer to ingest sources one at a time and stay involved — I read the summaries, check the updates, and guide the LLM on what to emphasize. But you could also batch-ingest many sources at once with less supervision. It's up to you to develop the workflow that fits your style and document it in the schema for future sessions.  Query. You ask questions against the wiki. The LLM searches for relevant pages, reads them, and synthesizes an answer with citations. Answers can take different forms depending on the question — a markdown page, a comparison table, a slide deck (Marp), a chart (matplotlib), a canvas. The important insight: good answers can be filed back into the wiki as new pages. A comparison you asked for, an analysis, a connection you discovered — these are valuable and shouldn't disappear into chat history. This way your explorations compound in the knowledge base just like ingested sources do.  Lint. Periodically, ask the LLM to health-check the wiki. Look for: contradictions between pages, stale claims that newer sources have superseded, orphan pages with no inbound links, important concepts mentioned but lacking their own page, missing cross-references, data gaps that could be filled with a web search. The LLM is good at suggesting new questions to investigate and new sources to look for. This keeps the wiki healthy as it grows.  ## Indexing and logging  Two special files help the LLM (and you) navigate the wiki as it grows. They serve different purposes:  index.md is content-oriented. It's a catalog of everything in the wiki — each page listed with a link, a one-line summary, and optionally metadata like date or source count. Organized by category (entities, concepts, sources, etc.). The LLM updates it on every ingest. When answering a query, the LLM reads the index first to find relevant pages, then drills into them. This works surprisingly well at moderate scale (~100 sources, ~hundreds of pages) and avoids the need for embedding-based RAG infrastructure.  log.md is chronological. It's an append-only record of what happened and when — ingests, queries, lint passes. A useful tip: if each entry starts with a consistent prefix (e.g. ## [2026-04-02] ingest | Article Title), the log becomes parseable with simple unix tools — grep "^## [" log.md | tail -5 gives you the last 5 entries. The log gives you a timeline of the wiki's evolution and helps the LLM understand what's been done recently.  ## Optional: CLI tools  At some point you may want to build small tools that help the LLM operate on the wiki more efficiently. A search engine over the wiki pages is the most obvious one — at small scale the index file is enough, but as the wiki grows you want proper search. qmd is a good option: it's a local search engine for markdown files with hybrid BM25/vector search and LLM re-ranking, all on-device. It has both a CLI (so the LLM can shell out to it) and an MCP server (so the LLM can use it as a native tool). You could also build something simpler yourself — the LLM can help you vibe-code a naive search script as the need arises.  ## Tips and tricks  - Obsidian Web Clipper is a browser extension that converts web articles to markdown. Very useful for quickly getting sources into your raw collection. - Download images locally. In Obsidian Settings → Files and links, set "Attachment folder path" to a fixed directory (e.g. raw/assets/). Then in Settings → Hotkeys, search for "Download" to find "Download attachments for current file" and bind it to a hotkey (e.g. Ctrl+Shift+D). After clipping an article, hit the hotkey and all images get downloaded to local disk. This is optional but useful — it lets the LLM view and reference images directly instead of relying on URLs that may break. Note that LLMs can't natively read markdown with inline images in one pass — the workaround is to have the LLM read the text first, then view some or all of the referenced images separately to gain additional context. It's a bit clunky but works well enough. - Obsidian's graph view is the best way to see the shape of your wiki — what's connected to what, which pages are hubs, which are orphans. - Marp is a markdown-based slide deck format. Obsidian has a plugin for it. Useful for generating presentations directly from wiki content. - Dataview is an Obsidian plugin that runs queries over page frontmatter. If your LLM adds YAML frontmatter to wiki pages (tags, dates, source counts), Dataview can generate dynamic tables and lists. - The wiki is just a git repo of markdown files. You get version history, branching, and collaboration for free.  ## Why this works  The tedious part of maintaining a knowledge base is not the reading or the thinking — it's the bookkeeping. Updating cross-references, keeping summaries current, noting when new data contradicts old claims, maintaining consistency across dozens of pages. Humans abandon wikis because the maintenance burden grows faster than the value. LLMs don't get bored, don't forget to update a cross-reference, and can touch 15 files in one pass. The wiki stays maintained because the cost of maintenance is near zero.  The human's job is to curate sources, direct the analysis, ask good questions, and think about what it all means. The LLM's job is everything else.  The idea is related in spirit to Vannevar Bush's Memex (1945) — a personal, curated knowledge store with associative trails between documents. Bush's vision was closer to this than to what the web became: private, actively curated, with the connections between documents as valuable as the documents themselves. The part he couldn't solve was who does the maintenance. The LLM handles that.

the article i want you to add to our knowledge base to start is on the internet: "Harness Engineering: leveraging Codex in an agent-first world"

# discord Bot Details

Application ID
1491525297714237630
Public Key
fb3a0c54b7a171044e90c5b37aa8fa9cdfda1182d53a964506f00ef961aaf695

reset token
[REDACTED: Token should be managed via Infisical / environment variables]
