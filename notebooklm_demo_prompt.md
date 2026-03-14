# NotebookLM Orchestration Demo Prompt

Please use the following prompt to instruct Simone to demonstrate the full capabilities of the `notebooklm-orchestration` skill. You can copy and paste this directly to Simone.

***

**Prompt for Simone:**

"Hi Simone! I want to do a full demonstration of our `notebooklm-orchestration` capabilities. Please execute a comprehensive end-to-end NotebookLM workflow on the topic: **'The latest information from the Russia-Ukraine war over the past five days'**.

Please perform the following steps sequentially and handle any necessary wait times or status checks:

0. **Authentication Check**: Ensure you have successfully authenticated to NotebookLM. If authentication fails, STOP immediately and notify me. Do not proceed with the workflow.
1. **Create Notebook**: Create a new NotebookLM notebook titled 'Russia-Ukraine War Recent Updates'.
2. **Deep Research**: Use the NotebookLM research tools to find at least 5-10 high-quality sources on 'The latest information from the Russia-Ukraine war over the past five days' and import them into the notebook.
3. **Artifact Generation (Studio)**: Once the sources are ingested, orchestrate the creation of the following studio artifacts from the notebook:
   - A detailed **Report** (Briefing Doc) summarizing the key findings.
   - An **Audio Overview** (Podcast) discussing the topic in a 'deep_dive' format.
   - A **Slide Deck** (Presentation) outlining the main points.
   - An **Infographic** visualizing the core concepts.
4. **Status Polling**: Poll the studio generation status until all the requested artifacts (report, audio, slide deck, infographic) are successfully completed.
5. **Download Artifacts**: Download all the generated artifacts to a local directory named `artifacts/notebooklm_demo_ru_ua/`.
6. **Sharing**: Enable public link sharing for the notebook so I can view it directly. **Important Guardrail:** The sharing tool requires explicit user confirmation (`confirm=True`). You MUST pause and use `notify_user` to ask for my permission before executing the share action.
7. **Final Summary**: Present a final summary of the execution, including the public link to the notebook, a brief summary of the research (using the notebook's describe feature), and confirmation that all artifacts are downloaded locally.

Feel free to delegate the heavy lifting to the `notebooklm-operator` subagent using our hybrid execution model. Please ensure you ask for confirmation before performing any actions that require guardrails according to your skill contract."
