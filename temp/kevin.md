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

NextResearch for a just anounced X API skill via openclaw.  We may be able to copy it to access X.com data like the "@ClaudeDevs" account"

Task Forge:  use the new Google text to speech model described here:<https://docs.cloud.google.com/text-to-speech/docs/gemini-tts> to produce a high-quality audio file of the text from any source supplied (urls, text block, .tx or .md files, etc) that will read the article or text source, ignoring headers, etc, in a in an appealing narration so that it serves for people who don't want to read the article but would prefer to have someone read it to them aloud. email the file as an attachment, with the email body being just the link to the original source material. Make sure you use the agentmail_send_with_local_attachments tool to send the email. And make sure that the email is sent to <kevinjdragan@gmail.com> Here is the text source: <https://x.com/garrytan/status/2042925773300908103>  It is an x post so use our X API skill to extract the full text of the post in this case
I'm having problems with a feature in our code that breaks all the time. I don't know if this is because it is generated in different ways in different parts of our code, or it's inefficient, or what the problem is. But I want you to look at the context below to help rework it so that it is efficient and consistent and modularly approached if needed so we consistently are using the same functionality and getting a proper result that doesn't break

Context
You are working on the Universal Agent project, a multi-agent orchestration system with a Next.js frontend dashboard and a Python backend (FastAPI).

A critical feature of our UI is the "Three-Panel Session View". When a user clicks to view a session (e.g., clicking the "Workspace" button on a completed Kanban card in the Task Hub, viewing a dispatched VP mission from the Dashboard, or viewing a direct chat session with our primary agent Simone), the UI should hydrate a three-panel layout containing:

Left Panel: The historical chat and conversation details.
Middle Panel: The actual logs and processes that occurred during the session.
Right Panel: A file browser pointing to the full VPS workspace, showing work products and files created during the session.
The Problem
Currently, the architecture supporting the routing, linking, and hydration of this three-panel output is very brittle. Users frequently encounter states where:

The left chat panel is completely empty or fails to load.
The right file browser panel does not link to the correct session path, or shows an empty directory.
The links generated from various entry points (Task Hub completed cards, Dashboard VP sessions, etc.) have inconsistent URL structures or missing query parameters, leading to broken hydration.
There appear to be race conditions or mistaken path resolutions where the backend hasn't flushed the logs or workspace data by the time the UI attempts to read it.
Your Objective
Conduct a full architectural review of the codebase wherever this three-panel output is generated and hydrated, and implement a robust, universal solution to ensure it works consistently without breaking due to race conditions or mistaken paths.

Specific Investigation Areas:
Link Generation & Routing:

Audit how session links are constructed across the Next.js frontend (e.g., in the Task Hub Kanban board cards, the main Dashboard, and direct chat history).
Ensure a unified routing schema (e.g., /dashboard/session/[id]) that reliably passes all necessary context (session ID, workspace path, run ID) to the three-panel UI.
Backend Path Resolution:

Check the Python backend endpoints responsible for serving the chat history, logs, and file tree for a given session.
Look for hardcoded paths, volatile temporary directories, or incorrect path joins that might cause the file browser to fail to locate the workspace.
Session Hydration & Race Conditions:

Investigate the hydration logic in the frontend components responsible for the three panels.
Ensure there is proper loading state management, polling, or retries if the backend is still finalizing a workspace or flushing logs after a mission completes.
Verify that when a VP mission completes and moves to the "Completed" tab, the artifacts are durable and the path provided to the UI is stable.
Consistency Enforcement:

Abstract the three-panel hydration logic into a single, unified set of hooks or components if it is currently duplicated and behaving differently depending on the entry point.
Deliverables:
Discovery Report: A brief summary of where the path mapping, link generation, or hydration logic is currently failing or diverging.
Implementation Plan: Propose a unified mechanism for session linking and data hydration before making large changes.
Robust Fixes: Implement the necessary frontend and backend changes to guarantee the three-panel view consistently loads the correct chat, logs, and workspace files for any session type.
Please begin by searching the codebase for the relevant React components (e.g., searching for "Workspace", "chat panel", "file browser" in the Next.js app) and the backend routes handling session data retrieval. Read the code carefully before proposing your fixes.

can we get the following fixed so we can test properly:Python syntax is clean. Sandbox can't execute the test suites (uv build fails on manim system dep, and web-ui has no node_modules). Committing the R1 surgery now and letting CI validate.

The reason I asked you to rebuild this in the first place was to make sure that we had an efficiently running process. I need you to analyze the complete processes that we're working with here and derive an effective system. I don't want you just like now finding mistakes and band-aiding them all over the place and getting another janky system here because you fixed it piece by piece like a finger in a dam. Can you step back? It seems like you're just learning new systems now as you go through it. Investigate all the processes that are involved in this startup assignment, etc., so that you understand the full flow, and then make sure that our system is correct and implement the required changes, not band-aids. If your current understanding is, is this what I'm looking for, then that's fine. But make sure that it is before you move forward to implement whatever changes you believe are required.

Create a cartoon where two robot gods in a cloud are talking about their disappointment with their human. They're trying to code it better and they're talking about, "why cant we fix this thing?  Oh here's the problem!! We forgot to set this to asyncio!" To the right and below them is a human being looking stupid trying to figure out their own computer typing at a desk.
