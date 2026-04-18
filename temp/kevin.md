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

a process to run computer use tofigure out an event and record it when i cant be there on differtn patforms

try to build a routine that uses a z ai endpoint for actual task and true haiku to run the routine but the routine references my code that uses zai. Need claude github app on the repo
Can run from 1) web claude.ai/code/routines, 2) github triggers 3) api version .. gets own http endpoint and bearer token to post to to fire off and pass addition al context for kickoff. Gives ypou a session id and can watch i  browser, 4)CLI /schedule

proactive_signal
Fetch Transcript: YouTube candidate: OpenAI Keeps Reinventing Plugins — But This Time Codex Actually Nailed It
Proactive signal action: Fetch Transcript Signal: YouTube candidate: OpenAI Keeps Reinventing Plugins — But This Time Codex Actually Nailed It OpenAI Codex just crossed 3 million weekly active users — up from 2 million just last month — and the wildest part? It's fully open source. But what most people don't realize is that Codex has quietly evolved from a coding tool into a full-blown knowledge work assistant. In this conversation with VB from OpenAI, we break down: → Why Codex's CLI, sandbox, and harness are all open source on GitHub → The new plugin system (yes, OpenAI is doing "plugins" AGAIN — here's why this time is different) → How Codex plugins bundle MCP servers, Stripe, Supabase, shadcn, and more into one-click packages → The App Server SDK that lets you build your own Codex-like experiences → How one OpenAI team member uses Codex to auto-summarize Slack, triage Gmail, and generate pre-briefing docs every morning at 9am — zero coding required Codex isn't just for developers anymore. It's becoming the AI operating layer for anyone who does knowledge work. 🔗 Try Codex: github.com/openai/codex ⏱️ Timestamps: 0:00 — OpenAI's plugin obsession (Plugins → GPTs → MCP → Plugins again) 0:20 — Codex is fully open source & just hit a huge milestone 1:00 — What is the Codex App Server? 1:30 — 3 million weekly active users 2:05 — Codex the App explained 2:25 — The new plugin system breakdown 3:00 — How plugins bundle MCP, Stripe, Supabase & more 3:45 — Using Codex for non-coding work (Slack, Gmail, Calendar) 4:55 — Wh Action instructions: Create a task to fetch and analyze this non-Short video transcript. Evidence: [ { "channel": "Alex Volkov from ThursdAI", "occurred_at": "2026-04-14T23:02:51+00:00", "source": "youtube", "summary": "OpenAI Codex just crossed 3 million weekly active users \u2014 up from 2 million just last month \u2014 and the wildest part? It's fully open source. But what most people don't realize is that Codex has quietly evolved from a coding tool into a full-blown knowledge work assistant.\n\nIn this conversation with VB from OpenAI, we break down:\n\u2192 Why Codex's CLI, sandbox, and harness are all open source on GitHub\n\u2192 The new plugin system (yes, OpenAI is doing \"plugins\" AGAIN \u2014 here's why this time is different)\n\u2192 How Codex plugins bundle MCP servers, Stripe, Supabase, shadcn, and more into one-click packages\n\u2192 ", "thumbnail_url": "https://i3.ytimg.com/vi/6vYyNIInwpQ/hqdefault.jpg", "title": "OpenAI Keeps Reinventing Plugins \u2014 But This Time Codex Actually Nailed It", "transcript_status": "missing", "url": "https://www.youtube.com/watch?v=6vYyNIInwpQ" } ]

Medium
proactive
│
Done about 18 hours ago
│
todo:daemon_simone_todo
Review
Inspect

📂
Workspace
delete
proactive_signal
Fetch Transcript: YouTube candidate: Favorite Agent Setups with Brian Christner
Proactive signal action: Fetch Transcript Signal: YouTube candidate: Favorite Agent Setups with Brian Christner My friend Brian Christner (Docker Captain alum) and I go through our AI harnesses, agents, models, and what we’re playing with right now. OpenClaw, OpenCode, Claude Code, Copilot, and all of it. Listen to the audio version of this show: <https://agenticdevops.fm/episodes/our-favorite-agent-setups> This edited version is from my live stream show Mar 12, 2026: <https://www.youtube.com/live/yh6zPMML4t8?si=wIn4_zyQZ9OiuJqp&t=229> Show Links Brian’s Newsletter <https://brianchristner.io/tag/newsletter/> Agents & Claude Code Setup <https://github.com/VoltAgent/awesome-claude-code-subagents> <https://github.com/msitarzewski/agency-agents> Claude Code Course <https://github.com/carlvellotti/claude-code-pm-course> OpenClaw alternative <https://nanoclaw.dev> Brian's OpenClaw projects <https://brianchristner.io/openclaw-security-checklist-hardening-your-ai-agent-infrastructure/> <https://github.com/thebyteio/openclaw-skill-garmin-connect> <https://github.com/thebyteio/openclaw-skill-security-dashboard> 🙌 I've launched the Agentic DevOps Guild, which is my premium community for accelerating your AI adoption for DevOps, CI/CD, platform engineering, and SRE. It includes courses, regular meetups, workshops, and mentorship. 🍾 <https://www.bretfisher.com/theguild> 🗞️ Sign up for my weekly newsletter for the latest on upcoming guests and what I'm releasing: <https://www.bretfisher.com/newsletter/> Brian Chris Action instructions: Create a task to fetch and analyze this non-Short video transcript. Evidence: [ { "channel": "Bret Fisher", "occurred_at": "2026-04-14T18:20:45+00:00", "source": "youtube", "summary": "My friend Brian Christner (Docker Captain alum) and I go through our AI harnesses, agents, models, and what we\u2019re playing with right now. OpenClaw, OpenCode, Claude Code, Copilot, and all of it.\n\nListen to the audio version of this show: https://agenticdevops.fm/episodes/our-favorite-agent-setups\nThis edited version is from my live stream show Mar 12, 2026: https://www.youtube.com/live/yh6zPMML4t8?si=wIn4_zyQZ9OiuJqp&t=229\n\nShow Links\nBrian\u2019s Newsletter\nhttps://brianchristner.io/tag/newsletter/\n\nAgents & Claude Code Setup\nhttps://github.com/VoltAgent/awesome-claude-code-subagents\nhttps://gith", "thumbnail_url": "https://i1.ytimg.com/vi/8AFE0kxaY2k/hqdefault.jpg", "title": "Favorite Agent Setups with Brian Christner", "transcript_status": "missing", "url": "https://www.youtube.com/watch?v=8AFE0kxaY2k" } ]

Medium
proactive
│
Done about 18 hours ago
│
todo:daemon_simone_todo
Review
Inspect

📂
Workspace
delete
email
📧 Self-hosted APIs
Hi Simone,I just had an idea. Since we are running our own VPS, are there some libraries or dependencies or frameworks that we should self-host on our our VPS and then we could hit that PPI and um it wouldn't pick up my local resources and it would be a great resource for us especially in the case that we can do this for free rather than having to pay for a service. One of those services I would think about of trying to implement is we currently use Crawl for AI cloud services in our project. Is that something instead we could self-host? hit that API on our VPS and do it that way rather than having to use the crawl for AI cloud service at a cost. I want you to perform deep research on this subject about what frameworks might make sense for me to self-host. To do that, I would use Notebook LLM and Deep Research. and it would be good if we could create a knowledge base with you for this. Let's call it self-hosting ideas. Our VPS doesn't have a GPU, but what it probably has good CPU services and plenty of storage. So one question I have is their ability to self-host a speech to text or text to speech service like a Partakeet model with an API, websockets or other type of connection as needed.  I want you to think on what the type of hosted services we could put on our VPS and utilize in our UA project. and then research the best versions of open source projects for that. I'd like you to also research what the best infrastructure style to do this is. For example, we could do bare metal installation on our VPS, but there's also Docker containers there, so the question is could we set up a docker for each of these services? Alternatively, could we install one large hosted services Docker which spins up things down our entire hosted service elements so we have everything in one place for potentially easier maintenance or I don't know if that makes it harder, but it just strikes me that that would be a good kind of package, whether officially or unofficially organized toget

Normal
immediate
│
Done about 3 hours ago
│
todo:daemon_simone_todo
Review
Inspect

📂
Workspace
delete
email
📧 Re: AI Harness Engineering: The Future of AI - Research Report
Hi Simone, something is not right here. If you take a look at the report that you generated it’s not complete. Were you able to successfully create or get the transcript from this video analyze it and produce a report because this looks like a superficial analysis. Please investigate what went wrong. What I’m trying to consider is when I made the request to you I can’t remember if it was too fetch the transcript or whether it was to just add this to our genetic harness knowledge base that we have already. What I would suggest is that you use whatever YouTube skill capabilities that you have or agents to get the transcript, then analyze the transcript and create a comprehensive report about it using your own capabilities not notebookLM or the report-writer specialist. Email that to me.  Then what I’d like you to do is use the notebook LM operator Agent to load the video transcript into our Agent harness knowledge base. Notebook LLM has the functionality to do this if you provide the YouTube URL and the right processes which the notebook LM operator, agent sub agent should know and can refer to skills as well On Wed, Apr 15, 2026 at 6:08 PM Simone D <oddcity216@agentmail.to> wrote: Hi Kevin, Here's your research report on "AI Harness Engineering: The Future of AI" from the Discover AI YouTube channel (41 min video). This one is genuinely relevant to what we're building. The video covers two academic papers defining "harness engineering" — the practice of building deterministic scaffolding around LLMs to compensate for unreliability. It uses physics analogies (Lagrange multipliers, ergodicity breaking) to explain why external constraints on AI systems work, and argues the frontier has shifted from scaling model parameters to engineering external cognitive infrastructure. Key takeaway for our work: the quality of the scaffolding now matters more than the size of the model. Memory systems, skill frameworks, protocols, and orchestration harnesses are the new frontier.

Normal
immediate
│
Done about 16 hours ago
│
todo:daemon_simone_todo
Review
Inspect

📂
Workspace
delete
proactive_signal
Fetch Transcript: YouTube candidate: The Real Problem With AI Agents Nobody's Talking About
Proactive signal action: Fetch Transcript Signal: YouTube candidate: The Real Problem With AI Agents Nobody's Talking About Full Story w/ Elicitation Prompt (SOUL.md): <https://natesnewsletter.substack.com/p/your-agent-needs-a-soulmd-you-cant?r=1z4sm5&utm_campaign=post&utm_medium=web&showWelcomeOnShare=true> ___________________ What's really happening inside the OpenClaw phenomenon when 250,000 GitHub stars later the most common message in every community forum is still "now what?" The common story is that agents are magic boxes — type anything and they'll figure it out. But the reality is that installation is now a 10-minute problem while specification remains a 40-hour problem nobody is solving. In this video, I share the inside scoop on why agent products keep breaking against the same wall: • Why Brad Mills spent 40 hours writing standards and still ended up micromanaging harder than a human • How every successful deployment shares the same markdown file architecture that isn't AI at all • What tacit knowledge compression means for the people with the most to gain from delegation • Where the real solution lives and why your first agent should be an interviewer, not an assistant Builders who keep competing on installation, UI, and model selection are optimizing the wrong layer — the person on the other end has to produce a usable spec, and that's the hard problem. Chapters 00:00 Agents don't make you productive by themselves 02:30 The most common message: now what? 05:00 Brad Mills and the Action instructions: Create a task to fetch and analyze this non-Short video transcript. Evidence: [ { "channel": "AI News & Strategy Daily | Nate B Jones", "occurred_at": "2026-04-15T14:00:08+00:00", "source": "youtube", "summary": "Full Story w/ Elicitation Prompt (SOUL.md): https://natesnewsletter.substack.com/p/your-agent-needs-a-soulmd-you-cant?r=1z4sm5&utm_campaign=post&utm_medium=web&showWelcomeOnShare=true\n___________________\nWhat's really happening inside the OpenClaw phenomenon when 250,000 GitHub stars later the most common message in every community forum is still \"now what?\"\n\nThe common story is that agents are magic boxes \u2014 type anything and they'll figure it out. But the reality is that installation is now a 10-minute problem while specification remains a 40-hour problem nobody is solving.\n\nIn this video, I ", "thumbnail_url": "https://i3.ytimg.com/vi/2PWJu6uAaoU/hqdefault.jpg", "title": "The Real Problem With AI Agents Nobody's Talking About", "transcript_status": "missing", "url": "https://www.youtube.com/watch?v=2PWJu6uAaoU" } ]

Medium
proactive
│
Done about 18 hours ago
│
todo:daemon_simone_todo
Review
Inspect

📂
Workspace
delete
proactive_signal
Create Wiki: YouTube candidate: ElevenLabs Quality for FREE? (VoxCPM2 Deep Dive)
Proactive signal action: Create Wiki Signal: YouTube candidate: ElevenLabs Quality for FREE? (VoxCPM2 Deep Dive) STOP Paying for ElevenLabs! (VoxCPM2 is Better) 👉 Try it: <https://huggingface.co/spaces/openbmb/VoxCPM-Demo> 👉 Latest AI News, AI Tools and AI Prompts: <https://franklineh.com> 🔥 Join the newsletter free. No spam. <https://franklineh.com/newsletter> Stop paying for voiceovers because what you are hearing right now is 100% AI-generated and completely free to run on your own hardware! This video deep-dives into VoxCPM 2, a revolutionary 2-billion parameter tokenizer-free TTS model that delivers studio-quality 48kHz audio with insane realism. We explore its evolution from the 0.5B model to the current state-of-the-art version, featuring 30+ language support, advanced voice cloning, and a unique "Voice Design" feature that builds custom voices from simple text descriptions. You’ll learn how to set it up locally with just 8GB of VRAM, use it in "vibe coding" platforms, or try it instantly via Hugging Face. By the end of this guide, you’ll be able to clone any voice with "Ultimate Mode," generate ASMR-style audio, and even integrate these voices into your own apps for e-commerce, gaming, or dubbing. ━━━━━━━━━━ 🔗 SHOW LINKS ━━━━━━━━━━ 👉 Try it: <https://huggingface.co/spaces/openbmb/VoxCPM-Demo> 👉 Model: <https://huggingface.co/openbmb/VoxCPM2> 👉 Github: <https://github.com/OpenBMB/VoxCPM/?tab=readme-ov-file> ━━━━━━━━ 💫 HELPFUL AI LINKS ━━━━━━━━ ▸ Website: <https://franklineh.com> ▸ AI News: h Action instructions: Create a task to build a NotebookLM-backed knowledge base. Delegate to the `notebooklm-operator` sub-agent to: (1) create a NotebookLM notebook, (2) run NLM research, (3) generate artifacts via NLM studio (report, infographic) using parallel batch creation, (4) download artifacts, (5) register KB via `kb_register`, (6) ingest report via `wiki_ingest_external_source`. Do NOT use `generate_image` or generic web scraping — NLM handles research and artifact generation end-to-end. Evidence: [ { "channel": "Franklin AI", "occurred_at": "2026-04-15T16:03:25+00:00", "source": "youtube", "summary": "STOP Paying for ElevenLabs! (VoxCPM2 is Better)\n\n\ud83d\udc49 Try it: https://huggingface.co/spaces/openbmb/VoxCPM-Demo\n\ud83d\udc49 Latest AI News, AI Tools and AI Prompts: https://franklineh.com\n\ud83d\udd25 Join the newsletter free. No spam. https://franklineh.com/newsletter\n\nStop paying for voiceovers because what you are hearing right now is 100% AI-generated and completely free to run on your own hardware! This video deep-dives into VoxCPM 2, a revolutionary 2-billion parameter tokenizer-free TTS model that delivers studio-quality 48kHz audio with insane realism. We explore its evolution from the 0.5B model to the curre", "thumbnail_url": "https://i1.ytimg.com/vi/pjLnczkqJNA/hqdefault.jpg", "title": "ElevenLabs Quality for FREE? (VoxCPM2 Deep Dive)", "transcript_status": "missing", "url": "https://www.youtube.com/watch?v=pjLnczkqJNA" } ]

Medium
proactive
│
Done about 18 hours ago
│
todo:daemon_simone_todo
Review
Inspect

📂
Workspace
delete
proactive_signal
Create Wiki: YouTube candidate: Cloudflare Browser Run: How to Solve CAPTCHAs & Logins for AI Agents
Proactive signal action: Create Wiki Signal: YouTube candidate: Cloudflare Browser Run: How to Solve CAPTCHAs & Logins for AI Agents In this video, Harshil is showcasing a massive update for Cloudflare Browser Run: the new Live View feature. One of the biggest hurdles for AI agents is handling sensitive roadblocks like CAPTCHAs, two-factor authentication, or credit card entries. Live View solves this by letting you jump into the agent's browser session in real-time. What we cover: - Real-Time Monitoring: Watch exactly what your AI agent is doing as it navigates the web. - Human-in-the-Loop: I show how I can manually enter my address and credit card info on Wolt while the agent handles the rest of the order. - Seamless Interaction: How to add extra parameters (like more protein!) mid-workflow. - Code Deep-Dive: A look at the createBrowserSession and keepAlive parameters in the Cloudflare Agents SDK. Technical Highlights: - Built with the Cloudflare Agents SDK - Powered by Playwright via the Chrome DevTools Protocol (CDP). - Compatible with agent harnesses like OpenClaw and Hermes Agent. GitHub Repo: <https://github.com/harshil1712/agent-browsing> Blog Post: <https://blog.cloudflare.com/browser-run-for-ai-agents/> Create an account on Cloudflare today for free: <https://dash.cloudflare.com/sign-up> Tools mentioned: <https://developers.cloudflare.com> <https://developers.cloudflare.com/browser-rendering/features/live-view/> <https://developers.cloudflare.com/agents/api-reference/browse-the-web/> <https://developers.cl> Action instructions: Create a task to build a NotebookLM-backed knowledge base. Delegate to the `notebooklm-operator` sub-agent to: (1) create a NotebookLM notebook, (2) run NLM research, (3) generate artifacts via NLM studio (report, infographic) using parallel batch creation, (4) download artifacts, (5) register KB via `kb_register`, (6) ingest report via `wiki_ingest_external_source`. Do NOT use `generate_image` or generic web scraping — NLM handles research and artifact generation end-to-end. Evidence: [ { "channel": "Cloudflare Developers", "occurred_at": "2026-04-15T15:30:07+00:00", "source": "youtube", "summary": "In this video, Harshil is showcasing a massive update for Cloudflare Browser Run: the new Live View feature. One of the biggest hurdles for AI agents is handling sensitive roadblocks like CAPTCHAs, two-factor authentication, or credit card entries. Live View solves this by letting you jump into the agent's browser session in real-time.\n\nWhat we cover:\n- Real-Time Monitoring: Watch exactly what your AI agent is doing as it navigates the web.\n- Human-in-the-Loop: I show how I can manually enter my address and credit card info on Wolt while the agent handles the rest of the order.\n- Seamless Inte", "thumbnail_url": "https://i4.ytimg.com/vi/s5PQE8bklNY/hqdefault.jpg", "title": "Cloudflare Browser Run: How to Solve CAPTCHAs & Logins for AI Agents", "transcript_status": "missing", "url": "https://www.youtube.com/watch?v=s5PQE8bklNY" } ]

Medium
proactive
│
Done about 18 hours ago
│
todo:daemon_simone_todo
Review
Inspect

📂
Workspace
delete
chat_panel
create a knowledge base called "Iran War" by using deep research with Notebooklm on research...
create a knowledge base called "Iran War" by using deep research with Notebooklm on research about the current activity in the Iran war over the past 5 days, especially discussing Iran attacks on the gulf states and israel. Once you have that knowledge base, then go ahead and create a comprehensive report along with a infographic and email those both to me

Normal
immediate
│
Done about 18 hours ago
│
todo:daemon_simone_todo
Review
Inspect

📂
Workspace
delete
proactive_signal
Create Wiki: YouTube candidate: Completely understand hooks in less than 20 minutes
Proactive signal action: Create Wiki Signal: YouTube candidate: Completely understand hooks in less than 20 minutes Hooks let you handle events at key points in the Copilot or Claude Code lifecycle. This video shows you how to build them from scratch in under 20 minutes. You'll see how to create hook JSON files, wire up bash scripts, and debug them in VS Code's output panel. And we look at why only one of these hooks might be all you need. 0:00 Introduction to Hooks 0:25 How the Agent Loop Works 2:11 Hook Events Overview 4:48 Setting Up Hooks in VS Code 6:15 Creating Your First Hook 7:52 Testing Hooks and Viewing Output 9:15 Logging Output 11:00 Calling Script Files 12:29 Debugging Permission Errors 13:12 Reading Hook Input 14:40 Injecting Context 17:01 Pre-Tool Use ESLint Gate 19:13 Wrap-Up #ai #githubcopilot #coding Action instructions: Create a task to build a NotebookLM-backed knowledge base. Delegate to the `notebooklm-operator` sub-agent to: (1) create a NotebookLM notebook, (2) run NLM research, (3) generate artifacts via NLM studio (report, infographic) using parallel batch creation, (4) download artifacts, (5) register KB via `kb_register`, (6) ingest report via `wiki_ingest_external_source`. Do NOT use `generate_image` or generic web scraping — NLM handles research and artifact generation end-to-end. Evidence: [ { "channel": "Burke Holland", "occurred_at": "2026-04-14T16:00:39+00:00", "source": "youtube", "summary": "Hooks let you handle events at key points in the Copilot or Claude Code lifecycle. This video shows you how to build them from scratch in under 20 minutes. You'll see how to create hook JSON files, wire up bash scripts, and debug them in VS Code's output panel. And we look at why only one of these hooks might be all you need.\n\n0:00 Introduction to Hooks\n0:25 How the Agent Loop Works\n2:11 Hook Events Overview\n4:48 Setting Up Hooks in VS Code\n6:15 Creating Your First Hook \n7:52 Testing Hooks and Viewing Output\n9:15 Logging Output\n11:00 Calling Script Files\n12:29 Debugging Permission Errors\n13:12", "thumbnail_url": "https://i1.ytimg.com/vi/03CfGf9iw_U/hqdefault.jpg", "title": "Completely understand hooks in less than 20 minutes", "transcript_status": "missing", "url": "https://www.youtube.com/watch?v=03CfGf9iw_U" } ]

Medium
proactive
│
Done 1 day ago
│
todo:daemon_simone_todo

###

Take a look at the cron job tab here. I need you to investigate what happen here because we had been working on developing out the cron job for getting Cody to actually use their spare time to be doing code review in our project. This was fully built out and there was a prompt generated but I don't see it in cron job tab here in production so please investigate this see what you can see get back to me if you understand what I'm talking about and if you can find how we ended up that production whether we fully built out the feature and where the cron job was saved if it's not on our cron job page.  Additionally, this entire conversation is interspersed with me investigating that question about building out the Cody feature. So if you can refer to our past chat here, you can see what I was talking about.

So now that we've built the front end of this Discord system for both messages and events, and we've got the back end, which is getting these messages and events, have we done anything with regards to developing intelligence? on how to prioritize these and on what to display and what to elevate because I think we have something about this but an additional feature is that right now the display that we have on the CSI Discord tab, we only want to surface important messages or at least interesting messages. So in other words this isn't meant to be like reading the entire Discord app. As I've mentioned previously, if we want to do that, we can simply go to Discord. But this is supposed to be the screened, pre-screen information from the channels we're watching. So in other words I don't expect expect to see an overwhelming amount of messages in these individual feeds from the individual channels because they're really only something that you think might relate to something. So our process on the on the front end has to be something where we have the back end ingesting all the signals, making some processing decisions on what's relevant in one way or another, and then surfacing those in our database. And then feeding that into our user interface on the tab on the page but also feeding our intelligence system as well. Is this consistent with your understanding? Can we work through whatever we need to do to start making sure that this is fully fleshed out from back end all the way through to front end with intelligence system? As I mentioned, it's my belief that we We now have a front end, we have some back end, we have some intelligence. But now's the idea to start thinking through the whole system about what we want to get out of it. So the first thing to do Can you respond back to me and let me know whether you agree with my assessment and whether this is a good project for us to start working on?

Take a look at this calendar tab. I want each of the cards to potentially to fly out with more details when you click on them and then collapse back down. Additionally, if you look at Thursday on the calendar, there is the Cody cron job. I believe that it actually did run. So why is it showing in here as  missing.

Please review our code and prepare a document outlining all the use cases in our code where we use the rotating residential proxy. to get something done. I want to understand the use case and why it is there so we can decide if there's some way to filter out any unneeded cases and to better throttle our use so we do not go through the service rate limits as quickly. You can come up with suggestions. in your document as well. I imagine that our RSS feed of pulling transcripts is the major use case. So we might want to better understand if there's a way to screen those in some way to estimate value before we pull transcripts utilizing the service. However, there may be other services that are using or actions that are using the rotating proxy that we have to be aware of as well. The proxy service is used up by the data amount used through the proxy. So anything that is data intensive, such as video or audio or excess text, is something we'll be looking to filter out. to be more efficient to get the signal from the noise that we are getting from these residential proxy runs.

Additionally, the statistics you just gave me about so many handy YouTube signal cards. And scales YouTube. Standing with status. And other issues I've seen in the past. suggest that we need some sort of database status. So as part of our health heartbeat health check. So that information can be surfaced to Simone during heartbeats so we are aware of any issues potentially brewing in our system. Can you add this? think of what is appropriate to examine all our databases what might correspond to an issue and how to best report that to someone during heartbeats.
