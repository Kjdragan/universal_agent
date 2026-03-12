# Agent-Native Project Survey

Generated on March 12, 2026 from upstream GitHub repositories and README files where available. For entries in the original list that are not actually open-source or do not expose a canonical public repo, I called that out directly instead of forcing a bad match.

## Fast Shortlist For UA

If the goal is near-term UA leverage rather than broad market awareness, the strongest candidates in this list are Open WebUI, AnythingLLM, JupyterLab, Hoppscotch, Gitea, Grafana, Mattermost, Penpot, Excalidraw, Mermaid, Jenkins, and Argo CD. They already live close to the kind of surfaces UA benefits from most: APIs, plugins, extension systems, workflow/state models, or self-hosted deployment patterns that are easier to automate safely than desktop-only creative tools.

The weaker candidates for deep UA integration are the proprietary entries in the list such as Zoom, Gamma, Beautiful.ai, and Tome, plus free-but-not-open-source products such as yEd. They may still be useful as external endpoints, but they do not give you the same repo-level control, self-hosting flexibility, or adaptation surface as the open projects in the rest of this survey.

## GitHub Repositories

### VSCodium
Repository: [VSCodium/vscodium](https://github.com/VSCodium/vscodium)

VSCodium Free/Libre Open Source Software Binaries of Visual Studio Code **This is not a fork.

For UA, the main value is turning an already mature desktop or web product into a controllable surface through generated CLIs, local automation, or MCP wrappers. These are strongest when they already expose plugins, a stable internal data model, or a well-supported desktop/runtime environment that an agent can drive without brittle UI scraping.

### WordPress
Repository: [WordPress/WordPress](https://github.com/WordPress/WordPress)

I was not able to cleanly fetch a standard `README.md` for this repository during the automation pass, but the project is the canonical upstream for WordPress. In practice, this entry is still relevant because the repository represents the product codebase used by the project maintainers.

For UA, the main value is turning an already mature desktop or web product into a controllable surface through generated CLIs, local automation, or MCP wrappers. These are strongest when they already expose plugins, a stable internal data model, or a well-supported desktop/runtime environment that an agent can drive without brittle UI scraping.

### Calibre
Repository: [kovidgoyal/calibre](https://github.com/kovidgoyal/calibre)

calibre calibre is an e-book manager. It can view, convert, edit and catalog e-books in all of the major e-book formats. It can also talk to e-book reader devices.

For UA, the main value is turning an already mature desktop or web product into a controllable surface through generated CLIs, local automation, or MCP wrappers. These are strongest when they already expose plugins, a stable internal data model, or a well-supported desktop/runtime environment that an agent can drive without brittle UI scraping.

### Zotero
Repository: [zotero/zotero](https://github.com/zotero/zotero)

Zotero Zotero is a free, easy-to-use tool to help you collect, organize, cite, and share your research sources. Please post feature requests or bug reports to the Zotero Forums. If you're having trouble with Zotero, see Getting Help.

For UA, the main value is turning an already mature desktop or web product into a controllable surface through generated CLIs, local automation, or MCP wrappers. These are strongest when they already expose plugins, a stable internal data model, or a well-supported desktop/runtime environment that an agent can drive without brittle UI scraping.

### Joplin
Repository: [laurent22/joplin](https://github.com/laurent22/joplin)

**Joplin** is a free, open source note taking and to-do application, which can handle a large number of notes organised into notebooks. The notes are searchable, can be copied, tagged and modified either from the applications directly or from your own text editor. The notes are in Markdown format.

For UA, the main value is turning an already mature desktop or web product into a controllable surface through generated CLIs, local automation, or MCP wrappers. These are strongest when they already expose plugins, a stable internal data model, or a well-supported desktop/runtime environment that an agent can drive without brittle UI scraping.

### Logseq
Repository: [logseq/logseq](https://github.com/logseq/logseq)

Logseq A privacy-first, open-source platform for knowledge management and collaboration Home Page | Blog | Documentation | Roadmap alt="Download Logseq"/> alt="forum"> alt="chat on Discord"> alt="follow on Twitter">

For UA, the main value is turning an already mature desktop or web product into a controllable surface through generated CLIs, local automation, or MCP wrappers. These are strongest when they already expose plugins, a stable internal data model, or a well-supported desktop/runtime environment that an agent can drive without brittle UI scraping.

### Penpot
Repository: [penpot/penpot](https://github.com/penpot/penpot)

I was not able to cleanly fetch a standard `README.md` for this repository during the automation pass, but the project is the canonical upstream for Penpot. In practice, this entry is still relevant because the repository represents the product codebase used by the project maintainers.

For UA, the main value is turning an already mature desktop or web product into a controllable surface through generated CLIs, local automation, or MCP wrappers. These are strongest when they already expose plugins, a stable internal data model, or a well-supported desktop/runtime environment that an agent can drive without brittle UI scraping.

### Super Productivity
Repository: [super-productivity/super-productivity](https://github.com/super-productivity/super-productivity)

An advanced todo list app with timeboxing & time tracking capabilities that supports importing tasks from your calendar, Jira, GitHub and others :globe_with_meridians: Open Web App or :computer: Download src="https://upload.wikimedia.org/wikipedia/commons/4/49/Flag_of_Ukraine.svg" alt="Ukraine Flag" width="520" height="120" /> Humanitarian Aid for Ukraine

For UA, the main value is turning an already mature desktop or web product into a controllable surface through generated CLIs, local automation, or MCP wrappers. These are strongest when they already expose plugins, a stable internal data model, or a well-supported desktop/runtime environment that an agent can drive without brittle UI scraping.

## AI/ML Platforms

### Stable Diffusion WebUI
Repository: [AUTOMATIC1111/stable-diffusion-webui](https://github.com/AUTOMATIC1111/stable-diffusion-webui)

Stable Diffusion web UI A web interface for Stable Diffusion, implemented using Gradio library. Features Detailed feature showcase with images: - Original txt2img and img2img modes - One click install and run script (but you still must install python and git) - Outpainting - Inpainting - Color Sketch - Prompt Matrix - Stable Diffusion Upscale - Attention, specify parts of text that the model should pay more attention to - a man in a ((tuxedo)) - will pay more attention to tuxedo - a man in a (tuxedo:1.21) - alternative syntax - select text and press Ctrl+Up or Ctrl+Down (or Command+Up or Command+Down if you're on a MacOS) to automatically adjust attention to selected text (code contributed by anonymous user) - Loopback, run img2img processing multiple times - X/Y/Z plot, a way to draw a 3 dimensional plot of images with different parameters

For UA, these projects matter when you want local or self-hosted generation, training, orchestration, or agent-facing inference surfaces. They are especially attractive if the product already has APIs, workflow graphs, extension systems, or reproducible config files that can be mapped into structured agent commands.

### ComfyUI
Repository: [Comfy-Org/ComfyUI](https://github.com/Comfy-Org/ComfyUI)

ComfyUI **The most powerful and modular visual AI engine and application.**

For UA, these projects matter when you want local or self-hosted generation, training, orchestration, or agent-facing inference surfaces. They are especially attractive if the product already has APIs, workflow graphs, extension systems, or reproducible config files that can be mapped into structured agent commands.

### InvokeAI
Repository: [invoke-ai/InvokeAI](https://github.com/invoke-ai/InvokeAI)

Invoke - Professional Creative AI Tools for Visual Media Invoke is a leading creative engine built to empower professionals and enthusiasts alike. Generate and create stunning visual media using the latest AI-driven technologies. Invoke offers an industry leading web-based UI, and serves as the foundation for multiple commercial products.

For UA, these projects matter when you want local or self-hosted generation, training, orchestration, or agent-facing inference surfaces. They are especially attractive if the product already has APIs, workflow graphs, extension systems, or reproducible config files that can be mapped into structured agent commands.

### Text-generation-webui
Repository: [oobabooga/text-generation-webui](https://github.com/oobabooga/text-generation-webui)

Special thanks to: Warp, built for coding with multiple AI agents Available for macOS, Linux, & Windows Text Generation Web UI A Gradio web UI for running Large Language Models locally. 100% private, offline, and free. Try the Deep Reason extension |!Image1 | !Image2 | |!Image1 | !Image2 | Features - Supports multiple local text generation backends, including llama.cpp, Transformers, ExLlamaV3, and TensorRT-LLM (the latter via its own [Dockerfile](https://github.com/

For UA, these projects matter when you want local or self-hosted generation, training, orchestration, or agent-facing inference surfaces. They are especially attractive if the product already has APIs, workflow graphs, extension systems, or reproducible config files that can be mapped into structured agent commands.

### Open WebUI
Repository: [open-webui/open-webui](https://github.com/open-webui/open-webui)

Open WebUI 👋 **Open WebUI is an extensible, feature-rich, and user-friendly self-hosted AI platform designed to operate entirely offline.** It supports various LLM runners like **Ollama** and **OpenAI-compatible APIs**, with **built-in inference engine** for RAG, making it a **powerful AI deployment solution**. Passionate about open-source AI? Join our team → > [!TIP] > **Looking for an Enterprise Plan?** – **Speak with Our Sales Team Today!** > > Get **enhanced capabilities**, including **custom theming and branding**, **Service Level Agreement (SLA) support**, **Long-Term Support (LTS) versions**, and **more!** For more information, be sure to check out our Open WebUI Documentation.

For UA, these projects matter when you want local or self-hosted generation, training, orchestration, or agent-facing inference surfaces. They are especially attractive if the product already has APIs, workflow graphs, extension systems, or reproducible config files that can be mapped into structured agent commands.

### Fooocus
Repository: [lllyasviel/Fooocus](https://github.com/lllyasviel/Fooocus)

Fooocus [>>> Click Here to Install Fooocus ). Fooocus presents a rethinking of image generator designs. The software is offline, open source, and free, while at the same time, similar to many online image generators like Midjourney, the manual tweaking is not needed, and users only need to focus on the prompts and images.

For UA, these projects matter when you want local or self-hosted generation, training, orchestration, or agent-facing inference surfaces. They are especially attractive if the product already has APIs, workflow graphs, extension systems, or reproducible config files that can be mapped into structured agent commands.

### Kohya_ss
Repository: [bmaltais/kohya_ss](https://github.com/bmaltais/kohya_ss)

Kohya's GUI This is a GUI and CLI for training diffusion models. This project provides a user-friendly Gradio-based Graphical User Interface (GUI) for Kohya's Stable Diffusion training scripts. Stable Diffusion training empowers users to customize image generation models by fine-tuning existing models, creating unique artistic styles, and training specialized models like LoRA (Low-Rank Adaptation).

For UA, these projects matter when you want local or self-hosted generation, training, orchestration, or agent-facing inference surfaces. They are especially attractive if the product already has APIs, workflow graphs, extension systems, or reproducible config files that can be mapped into structured agent commands.

### AnythingLLM
Repository: [Mintplex-Labs/anything-llm](https://github.com/Mintplex-Labs/anything-llm)

AnythingLLM: The all-in-one AI app you were looking for. Chat with your docs, use AI Agents, hyper-configurable, multi-user, & no frustrating setup required.

For UA, these projects matter when you want local or self-hosted generation, training, orchestration, or agent-facing inference surfaces. They are especially attractive if the product already has APIs, workflow graphs, extension systems, or reproducible config files that can be mapped into structured agent commands.

### SillyTavern
Repository: [SillyTavern/SillyTavern](https://github.com/SillyTavern/SillyTavern)

SillyTavern LLM Frontend for Power Users Resources - GitHub: - Docs: - Discord: - Reddit: License AGPL-3.0

For UA, these projects matter when you want local or self-hosted generation, training, orchestration, or agent-facing inference surfaces. They are especially attractive if the product already has APIs, workflow graphs, extension systems, or reproducible config files that can be mapped into structured agent commands.

## Data & Analytics

### JupyterLab
Repository: [jupyterlab/jupyterlab](https://github.com/jupyterlab/jupyterlab)

**Installation** | **Documentation** | **Contributing** | **License** | **Team** | **Getting help** | JupyterLab An extensible environment for interactive and reproducible computing, based on the Jupyter Notebook and Architecture. JupyterLab is the next-generation user interface for Project Jupyter offering all the familiar building blocks of the classic Jupyter Notebook (notebook, terminal, text editor, file browser, rich outputs, etc.) in a flexible and powerful user interface. JupyterLab can be extended using npm packages that use our public APIs.

For UA, these are useful when the agent needs to query, transform, visualize, and explain data without rebuilding an analytics stack from scratch. The best fits are tools with existing SQL, dashboard, notebook, or workflow abstractions that can be surfaced as predictable commands.

### Apache Superset
Repository: [apache/superset](https://github.com/apache/superset)

I was not able to cleanly fetch a standard `README.md` for this repository during the automation pass, but the project is the canonical upstream for Apache Superset. In practice, this entry is still relevant because the repository represents the product codebase used by the project maintainers.

For UA, these are useful when the agent needs to query, transform, visualize, and explain data without rebuilding an analytics stack from scratch. The best fits are tools with existing SQL, dashboard, notebook, or workflow abstractions that can be surfaced as predictable commands.

### Metabase
Repository: [metabase/metabase](https://github.com/metabase/metabase)

Metabase Metabase is the easy, open-source way for everyone in your company to ask questions and learn from data. Get started The easiest way to get started with Metabase is to sign up for a free trial of Metabase Cloud. You get expert support, backups, upgrades, an SMTP server, SSL certificate, SoC2 Type 2 security auditing, and more (plus your money goes toward improving a major open-source project).

For UA, these are useful when the agent needs to query, transform, visualize, and explain data without rebuilding an analytics stack from scratch. The best fits are tools with existing SQL, dashboard, notebook, or workflow abstractions that can be surfaced as predictable commands.

### Redash
Repository: [getredash/redash](https://github.com/getredash/redash)

Redash is designed to enable anyone, regardless of the level of technical sophistication, to harness the power of data big and small. SQL users leverage Redash to explore, query, visualize, and share data from any data sources. Their work in turn enables anybody in their organization to use the data.

For UA, these are useful when the agent needs to query, transform, visualize, and explain data without rebuilding an analytics stack from scratch. The best fits are tools with existing SQL, dashboard, notebook, or workflow abstractions that can be surfaced as predictable commands.

### DBeaver
Repository: [dbeaver/dbeaver](https://github.com/dbeaver/dbeaver)

DBeaver Free multi-platform database tool for developers, SQL programmers, database administrators and analysts. * Has a lot of features including schema editor, SQL editor, data editor, AI integration, ER diagrams, data export/import/migration, SQL execution plans, database administration tools, database dashboards, Spatial data viewer, proxy and SSH tunnelling, custom database drivers editor, etc. * Out of the box supports more than 100 database drivers .

For UA, these are useful when the agent needs to query, transform, visualize, and explain data without rebuilding an analytics stack from scratch. The best fits are tools with existing SQL, dashboard, notebook, or workflow abstractions that can be surfaced as predictable commands.

### KNIME
Repository: [knime/knime-analytics-platform](https://github.com/knime/knime-analytics-platform)

I was not able to cleanly fetch a standard `README.md` for this repository during the automation pass, but the project is the canonical upstream for KNIME. In practice, this entry is still relevant because the repository represents the product codebase used by the project maintainers.

For UA, these are useful when the agent needs to query, transform, visualize, and explain data without rebuilding an analytics stack from scratch. The best fits are tools with existing SQL, dashboard, notebook, or workflow abstractions that can be surfaced as predictable commands.

### Orange
Repository: [biolab/orange3](https://github.com/biolab/orange3)

Orange Data Mining [Orange] is a data mining and visualization toolbox for novice and expert alike. To explore data with Orange, one requires __no programming or in-depth mathematical knowledge__. We believe that workflow-based data science tools democratize data science by hiding complex underlying mechanics and exposing intuitive concepts.

For UA, these are useful when the agent needs to query, transform, visualize, and explain data without rebuilding an analytics stack from scratch. The best fits are tools with existing SQL, dashboard, notebook, or workflow abstractions that can be surfaced as predictable commands.

### OpenSearch Dashboards
Repository: [opensearch-project/OpenSearch-Dashboards](https://github.com/opensearch-project/OpenSearch-Dashboards)

Project Resources - Code of Conduct - License - Copyright Welcome OpenSearch Dashboards is an open-source data visualization tool designed to work with OpenSearch. OpenSearch Dashboards gives you data visualization tools to improve and automate business intelligence and support data-driven decision-making and strategic planning. We aim to be an exceptional community-driven platform and to foster open participation and collective contribution with all contributors.

For UA, these are useful when the agent needs to query, transform, visualize, and explain data without rebuilding an analytics stack from scratch. The best fits are tools with existing SQL, dashboard, notebook, or workflow abstractions that can be surfaced as predictable commands.

### Lightdash
Repository: [lightdash/lightdash](https://github.com/lightdash/lightdash)

The open-source Looker alternative. Website • Watch demo • Docs • Join Slack Community

For UA, these are useful when the agent needs to query, transform, visualize, and explain data without rebuilding an analytics stack from scratch. The best fits are tools with existing SQL, dashboard, notebook, or workflow abstractions that can be surfaced as predictable commands.

## Development Tools

### Jenkins
Repository: [jenkinsci/jenkins](https://github.com/jenkinsci/jenkins)

Table of Contents - About - What to Use Jenkins for and When to Use It - Downloads - Getting Started (Development) - Source - Contributing to Jenkins - News and Website - Governance - Adopters - License About In a nutshell, Jenkins is the leading open-source automation server. Built with Java, it provides over 2,000 plugins to support automating virtually anything, so that humans can spend their time doing things machines cannot. What to Use Jenkins for and When to Use It Use Jenkins to automate your development workflow, so you can focus on work that matters most.

For UA, these projects are natural candidates when you want agents to own build, deploy, inspect, and operations loops instead of just suggesting commands. Existing APIs, CLIs, and automation primitives make them easier to wrap safely than GUI-only tools.

### Gitea
Repository: [go-gitea/gitea](https://github.com/go-gitea/gitea)

Gitea 繁體中文 | 简体中文 Purpose The goal of this project is to make the easiest, fastest, and most painless way of setting up a self-hosted Git service. As Gitea is written in Go, it works across **all** the platforms and architectures that are supported by Go, including Linux, macOS, and Windows on x86, amd64, ARM and PowerPC architectures. This project has been forked from Gogs since November of 2016, but a lot has changed.

For UA, these projects are natural candidates when you want agents to own build, deploy, inspect, and operations loops instead of just suggesting commands. Existing APIs, CLIs, and automation primitives make them easier to wrap safely than GUI-only tools.

### Hoppscotch
Repository: [hoppscotch/hoppscotch](https://github.com/hoppscotch/hoppscotch)

src="https://avatars.githubusercontent.com/u/56705483" alt="Hoppscotch" height="64" /> Hoppscotch Open Source API Development Ecosystem Built with ❤︎ by contributors _We highly recommend you take a look at the **Hoppscotch Documentation** to learn more about the app._ **Support** **Features** ❤️ **Lightweight:** Crafted with minimalistic UI design. ⚡️ **Fast:** Send requests and get responses in real time.

For UA, these projects are natural candidates when you want agents to own build, deploy, inspect, and operations loops instead of just suggesting commands. Existing APIs, CLIs, and automation primitives make them easier to wrap safely than GUI-only tools.

### Portainer
Repository: [portainer/portainer](https://github.com/portainer/portainer)

**Portainer Community Edition** is a lightweight service delivery platform for containerized applications that can be used to manage Docker, Swarm, Kubernetes and ACI environments. It is designed to be as simple to deploy as it is to use. The application allows you to manage all your orchestrator resources (containers, images, volumes, networks and more) through a ‘smart’ GUI and/or an extensive API.

For UA, these projects are natural candidates when you want agents to own build, deploy, inspect, and operations loops instead of just suggesting commands. Existing APIs, CLIs, and automation primitives make them easier to wrap safely than GUI-only tools.

### pgAdmin
Repository: [pgadmin-org/pgadmin4](https://github.com/pgadmin-org/pgadmin4)

pgAdmin 4 pgAdmin 4 is a rewrite of the popular pgAdmin3 management tool for the PostgreSQL (http://www.postgresql.org) database. In the following documentation and examples, *$PGADMIN4_SRC/* is used to denote the top-level directory of a copy of the pgAdmin source tree, either from a tarball or a git checkout. Architecture pgAdmin 4 is written as a web application with Python(Flask) on the server side and ReactJS, HTML5 with CSS for the client side processing and UI.

For UA, these projects are natural candidates when you want agents to own build, deploy, inspect, and operations loops instead of just suggesting commands. Existing APIs, CLIs, and automation primitives make them easier to wrap safely than GUI-only tools.

### SonarQube
Repository: [SonarSource/sonarqube](https://github.com/SonarSource/sonarqube)

SonarQube Continuous Inspection SonarQube provides the capability to not only show the health of an application but also to highlight issues newly introduced. With a Quality Gate in place, you can achieve Clean Code and therefore improve code quality systematically.

For UA, these projects are natural candidates when you want agents to own build, deploy, inspect, and operations loops instead of just suggesting commands. Existing APIs, CLIs, and automation primitives make them easier to wrap safely than GUI-only tools.

### ArgoCD
Repository: [argoproj/argo-cd](https://github.com/argoproj/argo-cd)

**Releases:** **Code:** **Social:** Argo CD - Declarative Continuous Delivery for Kubernetes What is Argo CD? Argo CD is a declarative, GitOps continuous delivery tool for Kubernetes. Application definitions, configurations, and environments should be declarative and version controlled.

For UA, these projects are natural candidates when you want agents to own build, deploy, inspect, and operations loops instead of just suggesting commands. Existing APIs, CLIs, and automation primitives make them easier to wrap safely than GUI-only tools.

### OpenLens
Repository: [OpenLens/OpenLens](https://github.com/OpenLens/OpenLens)

I was not able to cleanly fetch a standard `README.md` for this repository during the automation pass, but the project is the canonical upstream for OpenLens. In practice, this entry is still relevant because the repository represents the product codebase used by the project maintainers.

For UA, these projects are natural candidates when you want agents to own build, deploy, inspect, and operations loops instead of just suggesting commands. Existing APIs, CLIs, and automation primitives make them easier to wrap safely than GUI-only tools.

### Insomnia
Repository: [Kong/insomnia](https://github.com/Kong/insomnia)

Insomnia API Client Insomnia is an open-source, cross-platform API client for GraphQL, REST, WebSockets, Server-Sent Events (SSE), gRPC and any other HTTP compatible protocol. With Insomnia you can: - **Debug APIs** using the most popular protocols and formats. **Design APIs** using the native OpenAPI editor and visual preview.

For UA, these projects are natural candidates when you want agents to own build, deploy, inspect, and operations loops instead of just suggesting commands. Existing APIs, CLIs, and automation primitives make them easier to wrap safely than GUI-only tools.

### Beekeeper Studio
Repository: [beekeeper-studio/beekeeper-studio](https://github.com/beekeeper-studio/beekeeper-studio)

🌐 ES | PT-BR | DE | FR | EL | JA | IT | KO | ID Beekeeper Studio Beekeeper Studio is a cross-platform SQL editor and database manager available for Linux, Mac, and Windows. The app provides some premium features for a reasonable cost license fee. Learn more here Most of the code in this repo is open source under the GPLv3 license.

For UA, these projects are natural candidates when you want agents to own build, deploy, inspect, and operations loops instead of just suggesting commands. Existing APIs, CLIs, and automation primitives make them easier to wrap safely than GUI-only tools.

## Creative & Media

### Blender
Repository: [blender/blender](https://github.com/blender/blender)

Blender Blender is the free and open source 3D creation suite. It supports the entirety of the 3D pipeline—modeling, rigging, animation, simulation, rendering, compositing, motion tracking and video editing. Project Pages - Main Website - Reference Manual - User Community Development - Build Instructions - Code Review & Bug Tracker - Developer Forum - Developer Documentation License Blender as a whole is licensed under the GNU General Public License, Version 3.

For UA, the opportunity is to let the agent orchestrate creative pipelines instead of just generating prompts. Projects with strong file formats, scripting hooks, batch operations, or rendering pipelines are the best fit for deterministic agent control.

### GIMP
Repository: [GNOME/gimp](https://github.com/GNOME/gimp)

I was not able to cleanly fetch a standard `README.md` for this repository during the automation pass, but the project is the canonical upstream for GIMP. In practice, this entry is still relevant because the repository represents the product codebase used by the project maintainers.

For UA, the opportunity is to let the agent orchestrate creative pipelines instead of just generating prompts. Projects with strong file formats, scripting hooks, batch operations, or rendering pipelines are the best fit for deterministic agent control.

### OBS Studio
Repository: [obsproject/obs-studio](https://github.com/obsproject/obs-studio)

image:: https://github.com/obsproject/obs-studio/actions/workflows/push.yaml/badge.svg?branch=master :alt: OBS Studio Build Status - GitHub Actions :target: https://github.com/obsproject/obs-studio/actions/workflows/push.yaml?query=branch%3Amaster .. image:: https://badges.crowdin.net/obs-studio/localized.svg :alt: OBS Studio Translation Project Progress :target: https://crowdin.com/project/obs-studio .. image:: https://img.shields.io/discord/348973006581923840.svg?label=&logo=discord&logoColor=ffffff&color=7389D8&labelColor=6A7EC2 :alt: OBS Studio Discord Server :target: https://obsproject.com/discord What is OBS Studio?

For UA, the opportunity is to let the agent orchestrate creative pipelines instead of just generating prompts. Projects with strong file formats, scripting hooks, batch operations, or rendering pipelines are the best fit for deterministic agent control.

### Audacity
Repository: [audacity/audacity](https://github.com/audacity/audacity)

Audacity **Audacity** is an easy-to-use, multi-track audio editor and recorder for Windows, macOS, GNU/Linux and other operating systems. More info can be found on https://www.audacityteam.org This repository is currently undergoing major structural change. We're currently working on Audacity 4, which means an entirely new UI and also refactorings aplenty.

For UA, the opportunity is to let the agent orchestrate creative pipelines instead of just generating prompts. Projects with strong file formats, scripting hooks, batch operations, or rendering pipelines are the best fit for deterministic agent control.

### Krita
Repository: [KDE/krita](https://github.com/KDE/krita)

CI Name | Master | Stable | Release | | Pipeline | | | | Note: Nightly builds are not covered by this table atm Krita is a free and open source digital painting application. It is for artists who want to create professional work from start to end. Krita is used by comic book artists, illustrators, concept artists, matte and texture painters and in the digital VFX industry.

For UA, the opportunity is to let the agent orchestrate creative pipelines instead of just generating prompts. Projects with strong file formats, scripting hooks, batch operations, or rendering pipelines are the best fit for deterministic agent control.

### Kdenlive
Repository: [KDE/kdenlive](https://github.com/KDE/kdenlive)

Kdenlive Kdenlive is a powerful, free and open-source video editor that brings professional-grade video editing capabilities to everyone. Whether you're creating a simple family video or working on a complex project, Kdenlive provides the tools you need to bring your vision to life. For more information about Kdenlive's features, tutorials, and community, please visit our official website.

For UA, the opportunity is to let the agent orchestrate creative pipelines instead of just generating prompts. Projects with strong file formats, scripting hooks, batch operations, or rendering pipelines are the best fit for deterministic agent control.

### Shotcut
Repository: [mltframework/shotcut](https://github.com/mltframework/shotcut)

Shotcut - a free, open source, cross-platform **video editor** - Features: https://www.shotcut.org/features/ - Roadmap: https://www.shotcut.org/roadmap/ Install Binaries are regularly built and are available at https://www.shotcut.org/download/. Contributors - Dan Dennedy > : main author - Brian Matherly > : contributor Dependencies Shotcut's direct (linked or hard runtime) dependencies are: - MLT: multimedia authoring framework - Qt 6 (6.4 minimum): application and UI framework - FFTW - FFmpeg: multimedia format and codec libraries - Frei0r: video plugins - SDL: cross-platform audio playback See https://shotcut.org/credits/ for a more complete list including indirect

For UA, the opportunity is to let the agent orchestrate creative pipelines instead of just generating prompts. Projects with strong file formats, scripting hooks, batch operations, or rendering pipelines are the best fit for deterministic agent control.

### Inkscape
Repository: [inkscape/inkscape](https://github.com/inkscape/inkscape)

Inkscape: Free and Open Source Vector Drawing Inkscape is a professional quality vector graphics software that runs on Windows, Mac OS X and Linux. It is used by design professionals and hobbyists worldwide, for creating a wide variety of graphics such as illustrations, icons, logos, diagrams, maps and web graphics. Inkscape uses the W3C open standard SVG (Scalable Vector Graphics) as its native format, and is free and open-source software.

For UA, the opportunity is to let the agent orchestrate creative pipelines instead of just generating prompts. Projects with strong file formats, scripting hooks, batch operations, or rendering pipelines are the best fit for deterministic agent control.

### Darktable
Repository: [darktable-org/darktable](https://github.com/darktable-org/darktable)

darktable is an open source photography workflow application and non-destructive raw developer - a virtual lighttable and darkroom for photographers. It manages your digital negatives in a database, lets you view them through a zoomable lighttable and enables you to develop raw images, enhance them and export them to local or remote storage. darktable is **not** a free Adobe® Lightroom® replacement.

For UA, the opportunity is to let the agent orchestrate creative pipelines instead of just generating prompts. Projects with strong file formats, scripting hooks, batch operations, or rendering pipelines are the best fit for deterministic agent control.

### LMMS
Repository: [LMMS/lmms](https://github.com/LMMS/lmms)

Cross-platform music production software Website ⦁︎ Releases ⦁︎ Developer wiki ⦁︎ User manual ⦁︎ Showcase ⦁︎ Sharing platform

For UA, the opportunity is to let the agent orchestrate creative pipelines instead of just generating prompts. Projects with strong file formats, scripting hooks, batch operations, or rendering pipelines are the best fit for deterministic agent control.

### Ardour
Repository: [Ardour/ardour](https://github.com/Ardour/ardour)

I was not able to cleanly fetch a standard `README.md` for this repository during the automation pass, but the project is the canonical upstream for Ardour. In practice, this entry is still relevant because the repository represents the product codebase used by the project maintainers.

For UA, the opportunity is to let the agent orchestrate creative pipelines instead of just generating prompts. Projects with strong file formats, scripting hooks, batch operations, or rendering pipelines are the best fit for deterministic agent control.

## Scientific Computing

### ImageJ
Repository: [imagej/ImageJ](https://github.com/imagej/ImageJ)

ImageJ ImageJ is [public domain] software for processing and analyzing scientific images. It is written in Java, which allows it to run on many different platforms. For further information, see: * The [ImageJ website], the primary home of this project.

For UA, these are interesting where a domain workflow can be reduced to repeatable steps such as import, preprocess, simulate, analyze, and export. Agent-native value is highest when the software already supports scripting, macros, project files, or reproducible pipelines.

### FreeCAD
Repository: [FreeCAD/FreeCAD](https://github.com/FreeCAD/FreeCAD)

Your own 3D Parametric Modeler Website • Documentation • Forum • Bug tracker • Git repository • Blog Overview * **Freedom to build what you want** FreeCAD is an open-source parametric 3D modeler made primarily to design real-life objects of any size. Parametric modeling allows you to easily modify your design by going back into your model history to change its parameters. * **Create 3D from 2D and back** FreeCAD lets you sketch geometry-constrained 2D shapes and use them as a base to build other objects.

For UA, these are interesting where a domain workflow can be reduced to repeatable steps such as import, preprocess, simulate, analyze, and export. Agent-native value is highest when the software already supports scripting, macros, project files, or reproducible pipelines.

### QGIS
Repository: [qgis/QGIS](https://github.com/qgis/QGIS)

QGIS is a full-featured, user-friendly, free-and-open-source (FOSS) geographical information system (GIS) that runs on Unix platforms, Windows, and MacOS. Flexible and powerful spatial data management - 2. Beautiful cartography - 3.

For UA, these are interesting where a domain workflow can be reduced to repeatable steps such as import, preprocess, simulate, analyze, and export. Agent-native value is highest when the software already supports scripting, macros, project files, or reproducible pipelines.

### ParaView
Repository: [Kitware/ParaView](https://github.com/Kitware/ParaView)

Introduction [ParaView][] is an open-source, multi-platform data analysis and visualization application based on [Visualization Toolkit (VTK)][VTK]. The first public release was announced in October 2002. Since then, the project has grown through collaborative efforts between [Kitware Inc.][Kitware], [Sandia National Laboratories][Sandia], [Los Alamos National Laboratory][LANL], [Army Research Laboratory][ARL], and various other government and commercial institutions, and academic partners.

For UA, these are interesting where a domain workflow can be reduced to repeatable steps such as import, preprocess, simulate, analyze, and export. Agent-native value is highest when the software already supports scripting, macros, project files, or reproducible pipelines.

### Gephi
Repository: [gephi/gephi](https://github.com/gephi/gephi)

Gephi - The Open Graph Viz Platform Gephi is an award-winning open-source platform for visualizing and manipulating large graphs. It runs on Windows, Mac OS X and Linux. Localization is available in English, French, Spanish, Japanese, Russian, Brazilian Portuguese, Chinese, Czech, German and Romanian.

For UA, these are interesting where a domain workflow can be reduced to repeatable steps such as import, preprocess, simulate, analyze, and export. Agent-native value is highest when the software already supports scripting, macros, project files, or reproducible pipelines.

### LibreCAD
Repository: [LibreCAD/LibreCAD](https://github.com/LibreCAD/LibreCAD)

LibreCAD → **Download** ← LibreCAD is a 2D CAD drawing tool based on the community edition of QCAD. LibreCAD uses the cross-platform framework Qt, which means it works with most operating systems. The user interface is translated in over 30 languages.

For UA, these are interesting where a domain workflow can be reduced to repeatable steps such as import, preprocess, simulate, analyze, and export. Agent-native value is highest when the software already supports scripting, macros, project files, or reproducible pipelines.

### Stellarium
Repository: [Stellarium/stellarium](https://github.com/Stellarium/stellarium)

Stellarium Stellarium is a free open source planetarium for your computer. It shows a realistic sky in 3D, just like what you see with the naked eye, binoculars or a telescope. If you are new to Stellarium, go to www.stellarium.org for loads of additional information.

For UA, these are interesting where a domain workflow can be reduced to repeatable steps such as import, preprocess, simulate, analyze, and export. Agent-native value is highest when the software already supports scripting, macros, project files, or reproducible pipelines.

### KiCad
Repository: [KiCad/kicad-source-mirror](https://github.com/KiCad/kicad-source-mirror)

KiCad README For specific documentation about building KiCad, policies and guidelines, and source code documentation see the Developer Documentation website. You may also take a look into the Wiki, the contribution guide. For general information about KiCad and information about contributing to the documentation and libraries, see our Website and our Forum.

For UA, these are interesting where a domain workflow can be reduced to repeatable steps such as import, preprocess, simulate, analyze, and export. Agent-native value is highest when the software already supports scripting, macros, project files, or reproducible pipelines.

### JASP
Repository: [jasp-stats/jasp-desktop](https://github.com/jasp-stats/jasp-desktop)

**JASP** is a cross-platform software that allows you to conduct statistical analyses in seconds, and without having to learn programming or risking a programming mistake. It aims to be a complete statistical package for both Bayesian and Frequentist statistical methods, that is easy to use and familiar to users of SPSS. Explore our introductory guides on how to use JASP.

For UA, these are interesting where a domain workflow can be reduced to repeatable steps such as import, preprocess, simulate, analyze, and export. Agent-native value is highest when the software already supports scripting, macros, project files, or reproducible pipelines.

### Jamovi
Repository: [jamovi/jamovi](https://github.com/jamovi/jamovi)

jamovi jamovi is a free and open statistics package, which is easy to use, and designed to be familiar to users of SPSS. It provides a spreadsheet editor, and a range of statistical analyses. jamovi can provide R syntax for each analysis that is run, and additional analyses for jamovi can be developed using the R language.

For UA, these are interesting where a domain workflow can be reduced to repeatable steps such as import, preprocess, simulate, analyze, and export. Agent-native value is highest when the software already supports scripting, macros, project files, or reproducible pipelines.

## Enterprise & Office

### Nextcloud
Repository: [nextcloud/server](https://github.com/nextcloud/server)

Nextcloud Server ☁ **A safe home for all your data.** Why is this so awesome? 🤩 * 📁 **Access your Data** You can store your files, contacts, calendars, and more on a server of your choosing. * 🔄 **Sync your Data** You keep your files, contacts, calendars, and more synchronized amongst your devices.

For UA, these tools become valuable when the agent can operate against shared operational systems instead of isolated artifacts. Products with roles, workflows, APIs, and document or record models are the strongest candidates for reliable multi-step agent execution.

### GitLab
Repository: [gitlabhq/gitlabhq](https://github.com/gitlabhq/gitlabhq)

GitLab Canonical source The canonical source of GitLab where all development takes place is hosted on GitLab.com. If you wish to clone a copy of GitLab without proprietary code, you can use the read-only mirror of GitLab located at https://gitlab.com/gitlab-org/gitlab-foss/. However, please do not submit any issues and/or merge requests to that project.

For UA, these tools become valuable when the agent can operate against shared operational systems instead of isolated artifacts. Products with roles, workflows, APIs, and document or record models are the strongest candidates for reliable multi-step agent execution.

### Grafana
Repository: [grafana/grafana](https://github.com/grafana/grafana)

The open-source platform for monitoring and observability Grafana allows you to query, visualize, alert on and understand your metrics no matter where they are stored. Create, explore, and share dashboards with your team and foster a data-driven culture: - **Visualizations:** Fast and flexible client side graphs with a multitude of options. Panel plugins offer many different ways to visualize metrics and logs.

For UA, these tools become valuable when the agent can operate against shared operational systems instead of isolated artifacts. Products with roles, workflows, APIs, and document or record models are the strongest candidates for reliable multi-step agent execution.

### Mattermost
Repository: [mattermost/mattermost](https://github.com/mattermost/mattermost)

Mattermost is an open core, self-hosted collaboration platform that offers chat, workflow automation, voice calling, screen sharing, and AI integration. This repo is the primary source for core development on the Mattermost platform; it's written in Go and React, runs as a single Linux binary, and relies on PostgreSQL. A new compiled version is released under an MIT license every month on the 16th.

For UA, these tools become valuable when the agent can operate against shared operational systems instead of isolated artifacts. Products with roles, workflows, APIs, and document or record models are the strongest candidates for reliable multi-step agent execution.

### LibreOffice
Repository: [LibreOffice/core](https://github.com/LibreOffice/core)

LibreOffice LibreOffice is an integrated office suite based on copyleft licenses and compatible with most document formats and standards. Libreoffice is backed by The Document Foundation, which represents a large independent community of enterprises, developers and other volunteers moved by the common goal of bringing to the market the best software for personal productivity. A quick overview of the LibreOffice code structure.

For UA, these tools become valuable when the agent can operate against shared operational systems instead of isolated artifacts. Products with roles, workflows, APIs, and document or record models are the strongest candidates for reliable multi-step agent execution.

### AppFlowy
Repository: [AppFlowy-IO/AppFlowy](https://github.com/AppFlowy-IO/AppFlowy)

AppFlowy ⭐️ The Open Source Alternative To Notion ⭐️ AppFlowy is the AI workspace where you achieve more without losing control of your data Website •

For UA, these tools become valuable when the agent can operate against shared operational systems instead of isolated artifacts. Products with roles, workflows, APIs, and document or record models are the strongest candidates for reliable multi-step agent execution.

### NocoDB
Repository: [nocodb/nocodb](https://github.com/nocodb/nocodb)

NocoDB is the fastest and easiest way to build databases online. Website • Discord • Community • Twitter • Reddit • Documentation

For UA, these tools become valuable when the agent can operate against shared operational systems instead of isolated artifacts. Products with roles, workflows, APIs, and document or record models are the strongest candidates for reliable multi-step agent execution.

### Odoo (Community)
Repository: [odoo/odoo](https://github.com/odoo/odoo)

Odoo Odoo is a suite of web based open source business apps. The main Odoo Apps include an Open Source CRM, Website Builder, eCommerce, Warehouse Management, Project Management, Billing & Accounting, Point of Sale, Human Resources, Marketing, Manufacturing, ... Odoo Apps can be used as stand-alone applications, but they also integrate seamlessly so you get a full-featured Open Source ERP when you install several Apps.

For UA, these tools become valuable when the agent can operate against shared operational systems instead of isolated artifacts. Products with roles, workflows, APIs, and document or record models are the strongest candidates for reliable multi-step agent execution.

### Plane
Repository: [makeplane/plane](https://github.com/makeplane/plane)

Modern project management for all teams Website • Forum • Twitter • Documentation src="https://media.docs.plane.so/GitHub-readme/github-top.webp" alt="Plane Screens" width="100%" /> Meet Plane, an open-source project management tool to track issues, run ~sprints~ cycles, and manage product roadmaps without the chaos of managing the tool itself. 🧘‍♀️ > Plane is evolving every day. Your suggestions, ideas, and reported bugs help us immensely.

For UA, these tools become valuable when the agent can operate against shared operational systems instead of isolated artifacts. Products with roles, workflows, APIs, and document or record models are the strongest candidates for reliable multi-step agent execution.

### ERPNext
Repository: [frappe/erpnext](https://github.com/frappe/erpnext)

ERPNext Powerful, Intuitive and Open-Source ERP Live Demo Website Documentation ERPNext 100% Open-Source ERP system to help you run your business. Motivation Running a business is a complex task - handling invoices, tracking stock, managing personnel and even more ad-hoc activities. In a market where software is sold separately to manage each of these tasks, ERPNext does all of the above and more, for free.

For UA, these tools become valuable when the agent can operate against shared operational systems instead of isolated artifacts. Products with roles, workflows, APIs, and document or record models are the strongest candidates for reliable multi-step agent execution.

## Communication & Collaboration

### Zoom
Repository: No canonical public GitHub repository identified

No public open-source Zoom meeting server or client repository appears to exist for the core product; Zoom publishes SDK samples and open-source attributions, but the platform itself is proprietary.

No public open-source Zoom meeting server or client repository appears to exist for the core product; Zoom publishes SDK samples and open-source attributions, but the platform itself is proprietary.

### Jitsi Meet
Repository: [jitsi/jitsi-meet](https://github.com/jitsi/jitsi-meet)

Jitsi Meet Jitsi Meet is a set of Open Source projects which empower users to use and deploy video conferencing platforms with state-of-the-art video quality and features. Amongst others here are the main features Jitsi Meet offers: * Support for all current browsers * Mobile applications * Web and native SDKs for integration * HD audio and video * Content sharing * Raise hand and reactions * Chat with private conversations * Polls * Virtual backgrounds And many more! Using Jitsi Meet Using Jitsi Meet is straightforward, as it's browser based.

For UA, the practical question is whether the system can be driven through stable APIs to schedule meetings, manage participants, capture transcripts, and route follow-up work. Open platforms here are much more attractive than closed SaaS products because the integration surface is materially wider.

### BigBlueButton
Repository: [bigbluebutton/bigbluebutton](https://github.com/bigbluebutton/bigbluebutton)

BigBlueButton BigBlueButton is an open-source virtual classroom designed to help teachers teach and learners learn. BigBlueButton supports real-time sharing of audio, video, slides (with whiteboard annotations), chat, and the screen. Instructors can engage remote students with polling, emojis, multi-user whiteboards, shared notes, and breakout rooms.

For UA, the practical question is whether the system can be driven through stable APIs to schedule meetings, manage participants, capture transcripts, and route follow-up work. Open platforms here are much more attractive than closed SaaS products because the integration surface is materially wider.

### Mattermost
Repository: [mattermost/mattermost](https://github.com/mattermost/mattermost)

Mattermost is an open core, self-hosted collaboration platform that offers chat, workflow automation, voice calling, screen sharing, and AI integration. This repo is the primary source for core development on the Mattermost platform; it's written in Go and React, runs as a single Linux binary, and relies on PostgreSQL. A new compiled version is released under an MIT license every month on the 16th.

For UA, the practical question is whether the system can be driven through stable APIs to schedule meetings, manage participants, capture transcripts, and route follow-up work. Open platforms here are much more attractive than closed SaaS products because the integration surface is materially wider.

## Diagramming & Visualization

### Draw.io (diagrams.net)
Repository: [jgraph/drawio](https://github.com/jgraph/drawio)

draw.io About draw.io is a configurable diagramming and whiteboarding application, jointly owned and developed by draw.io Ltd (previously named JGraph) and draw.io AG. We also run a production deployment at https://app.diagrams.net. License The source code in this repository is licensed under the Apache License 2.0.

For UA, these are useful when an agent needs to generate or maintain diagrams as durable project artifacts rather than screenshots. Declarative formats, text-based specs, or import/export pipelines make these much easier to automate than purely manual drawing tools.

### Mermaid
Repository: [mermaid-js/mermaid](https://github.com/mermaid-js/mermaid)

Mermaid Generate diagrams from markdown-like text. 📖 Documentation | 🚀 Getting Started | 🌐 CDN | 🙌 Join Us 简体中文 Try Live Editor previews of future releases: Develop | Next

For UA, these are useful when an agent needs to generate or maintain diagrams as durable project artifacts rather than screenshots. Declarative formats, text-based specs, or import/export pipelines make these much easier to automate than purely manual drawing tools.

### PlantUML
Repository: [plantuml/plantuml](https://github.com/plantuml/plantuml)

🌱 PlantUML Generate UML diagrams from textual descriptions. ℹ️ About PlantUML is a component that allows you to create various UML diagrams through simple textual descriptions. From sequence diagrams to deployment diagrams and beyond, PlantUML provides an easy way to create visual representations of complex systems.

For UA, these are useful when an agent needs to generate or maintain diagrams as durable project artifacts rather than screenshots. Declarative formats, text-based specs, or import/export pipelines make these much easier to automate than purely manual drawing tools.

### Excalidraw
Repository: [excalidraw/excalidraw](https://github.com/excalidraw/excalidraw)

Excalidraw Editor | Blog | Documentation | Excalidraw+ An open source virtual hand-drawn style whiteboard. Collaborative and end-to-end encrypted.

For UA, these are useful when an agent needs to generate or maintain diagrams as durable project artifacts rather than screenshots. Declarative formats, text-based specs, or import/export pipelines make these much easier to automate than purely manual drawing tools.

### yEd
Repository: No canonical public GitHub repository identified

yEd is a free desktop graph editor from yWorks, but it is not maintained as an open-source GitHub project. That makes it a weaker candidate for deep UA integration than text-native diagram tools such as Mermaid or PlantUML.

yEd is a free desktop graph editor from yWorks, but it is not maintained as an open-source GitHub project. That makes it a weaker candidate for deep UA integration than text-native diagram tools such as Mermaid or PlantUML.

## AI Content Generation

### AnyGen
Repository: No canonical public GitHub repository identified

I did not find a clear canonical open-source GitHub repository for the product listed as AnyGen. Treat this as unresolved or likely non-open-source until a specific repository is identified.

I did not find a clear canonical open-source GitHub repository for the product listed as AnyGen. Treat this as unresolved or likely non-open-source until a specific repository is identified.

### Gamma
Repository: No canonical public GitHub repository identified

Gamma is primarily a proprietary AI presentation/document product. I did not find a canonical open-source repository for the core product.

Gamma is primarily a proprietary AI presentation/document product. I did not find a canonical open-source repository for the core product.

### Beautiful.ai
Repository: No canonical public GitHub repository identified

Beautiful.ai is a proprietary presentation platform and does not appear to expose the core product as an open-source GitHub repository.

Beautiful.ai is a proprietary presentation platform and does not appear to expose the core product as an open-source GitHub repository.

### Tome
Repository: No canonical public GitHub repository identified

Tome the presentation product is proprietary, but there is a separate open-source project named Tome in the local-LLM space. Because your list grouped it with AI deliverable SaaS tools, I treated the listed item as the proprietary presentation product rather than the unrelated OSS repo.

Tome the presentation product is proprietary, but there is a separate open-source project named Tome in the local-LLM space. Because your list grouped it with AI deliverable SaaS tools, I treated the listed item as the proprietary presentation product rather than the unrelated OSS repo.
