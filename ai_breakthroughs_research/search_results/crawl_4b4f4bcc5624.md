---
title: "What’s New in Copilot Studio: November 2025 Updates and Features"
source: https://www.microsoft.com/en-us/microsoft-copilot/blog/copilot-studio/whats-new-in-microsoft-copilot-studio-november-2025
date: unknown
description: "Explore the latest Copilot Studio updates from November 2025, including GPT-5 Chat, agent governance, and new tools for makers and admins."
word_count: 2586
---

Skip to content
Share 
  *  
  *  
  *  

  * Content type 
  *  Monthly Updates 

  * Topic 
  *  Agent adoption 
  *  Agentic AI 
  *  Pre-built agents 

more
In this edition of our monthly roundup, we’re highlighting a few of our biggest updates from Microsoft Ignite 2025 and walking through new capabilities available today. 
**November 2025 was a busy month for Microsoft Copilot Studio** , marked by major announcements at Microsoft Ignite 2025 and a wave of new features now rolling out to makers. It’s clear that organizations are moving beyond traditional automation and into a new era of agent-driven work. In this month’s roundup, we’re spotlighting our most significant Ignite updates and introducing powerful new capabilities you can start using today.
Find full Microsoft Ignite 2025 recap here
## Copilot Studio enhancements and new features
### From automation to outcomes: Ignite 2025 highlights
Microsoft Ignite 2025 underscored a clear trend: organizations are accelerating their shift toward agentic business transformation. Copilot Studio is the fully managed platform that enables them to build, govern, and scale AI agents across the enterprise. At Ignite, we introduced new capabilities that create a more robust, secure agent creation experience for every user – from makers to professional developers to IT administrators.
Highlights included a redesigned conversational authoring experience, natural language file generation, and a seamless one-click upgrade path from Microsoft 365 Copilot’s Agent Builder to Copilot Studio. That means business users can turn ideas into working agents faster, without waiting on development cycles and then expand when ready. This changes the game on how teams use technology to make a step change within a business process.
Makers now have even more flexibility with model choice across GPT-5 and leading third-party models, built-in agent evaluations, expanded computer use automation, and deep integration with more than 1,400 systems through Model Context Protocol, Power Platform connectors, and Microsoft Graph. In real-world terms, this removes the ‘user tax’ of context switching and managing data silos. Whether you’re looking for help with invoice processing or supplier discovery, these agents bring collective insights to help drive a process forward.
For administrators, Ignite 2025 delivered major governance updates. These included expanded analytics and insights, real-time protection powered by Microsoft Defender, and new oversight capabilities through Microsoft Entra Agent ID that gives IT teams the confidence to scale AI safely.
!Screenshot of Copilot Studio’s real-time threat detection blocking a message to an agent.
We also introduced Agent 365, the unified control plane for enterprise agents. Agent 365 centralizes governance, policy management, and monitoring. This includes new MCP servers that allow agents to schedule meetings, generate documents, send emails, and update CRM records with full compliance and audit support.
To dive deeper into all the announcements, see our full Ignite recap: **Why Microsoft Copilot Studio is the foundation for agentic business transformation**.
### GPT-5 Chat: Ready for production in Copilot Studio
GPT-5 Chat is now generally available in the European Union and United States. This means makers can confidently use this model in production scenarios for workloads that could benefit from GPT-5 Chat’s improved responsiveness, accuracy, and instruction-following. This means makers can confidently use this model in production scenarios for workloads that could benefit from GPT-5 Chat’s improved responsiveness, accuracy, and instruction-following.
You can enable GPT-5 Chat directly from an agent’s overview page. You can even set it as the primary model for scenarios like high-volume employee support or step-by-step process guidance.
!gpt-5 edit
We’ve also started rolling out the GPT-5.2 series as experimental models for U.S. customers in early release environments. These models improve performance across the board, including coding and multilingual use cases. This replaces the GPT-5.1 series, including in any agents created using GPT-5.1 models. Since these models are experimental, they’re best suited for test scenarios rather than production—but they give you an exciting preview of what’s coming next. 
!Gif showing how to choose GPT-5.2 models in a Copilot Studio agent
You can read more about model choice and how to test them out in Copilot Studio.
### Combine autonomous workflows with human judgment
One of the most important evolutions this month is human-in-the-loop (HITL), now in preview. This capability lets agents pause and ask for human input before moving forward. That may sound simple, but it fundamentally changes what organizations can trust agents to do.
With this feature, an agent can send a structured request (delivered as an Outlook form) to designated reviewers. Once the reviewer responds, the agent resumes and uses the submitted values as parameters. This provides real-time human judgment without disrupting the overall workflow.
!Screenshot showing human-in-the-loop options, highlighting the automated “request for information” that gets sent to a person for verification
HITL is especially useful when an agent needs clarification, additional context, or explicit approval to proceed. It supports scenarios such as confirming project updates, confirming procurement orders, validating financial reports, escalating complex customer support cases, resolving ambiguous data, or gathering information that only a person can provide. The result is more flexible and reliable automation that adapts to real-world conditions.
To use HITL, open the agent-building experience and select Add tool. Choose the request for information (preview) action under the Human-in-the-loop connector, then configure fields such as the title, message, assignee, and inputs. The agent will automatically trigger the request whenever the workflow calls for it. Learn more about request for information.
### Add curated Outlook and SharePoint tool groups to agents for faster setup
Makers can now streamline agent configuration by adding curated Action Groups from Outlook and SharePoint connectors, now in preview. Instead of identifying and configuring individual actions one by one, teams can bring in complete sets of related tools, such as “manage emails” or “manage files,” with a single selection. This makes it easier to equip agents with the capabilities they need to support common workflows across communication and content management.
Each Action Group contains the most relevant and reliable tools for its scenario. Shared inputs automatically apply across the group to reduce setup time and improve consistency. Makers can either specify their own values or opt to have AI dynamically fill inputs based on context. Try it both ways – each action is fully editable even after it’s added. This flexibility helps ensure agents behave predictably while still allowing customization for unique business processes.
To use Action Groups, open an agent’s **Tools** section, select **Add tool** , choose Outlook or SharePoint, and pick the tool group you want to add. This provides a faster, clearer, and more guided way to build workflow-ready agents. Learn more about Action Groups.
### SharePoint grounding: Turning content chaos into decision clarity
Any team that uses SharePoint knows that it can sometimes be tough to find exactly the nugget of information you need among all your content. Fortunately, If you’re using SharePoint as a knowledge source, your agents just got a lot smarter. We shipped an upgraded tenant graph grounding architecture that improves how agents retrieve and rank information across your organization. This translates into more precise, more context-aware responses, especially in content-heavy environments. 
On top of that, you can now filter SharePoint content using metadata like filename, owner, and last modified date. That gives you much tighter control over which documents your agents rely on when answering questions. 
!Screenshot showing how to filter a knowledge source by SP metadata inside a Copilot Studio agent
Learn more about these features and using SharePoint as a knowledge source.
## Agent Builder enhancements and new features
### Use the latest GPT-5 Chat capabilities in Agent Builder
Microsoft 365 Copilot now uses GPT-5 Chat when responding to prompts in agents created with Agent Builder. This brings immediate improvements to speed, quality, and accuracy in carrying out instructions. Organizations relying on agents built in Microsoft 365 Copilot will see immediate quality improvements in employee support, decision guidance, and informational use cases. No additional configuration or opt-in is required where GPT-5 Chat is available.
!Screenshot showing GPT-5.2 options in Agent Builder in M365 Copilot
GPT-5.2 is also now availableto use in Microsoft 365 Copilot with both web and work data. This new model series brings improved code generation and multilingual capabilities. Users with a Microsoft 365 Copilot license received priority access to GPT-5.2 on December 11th, 2025, and the series is expected to be available to all users in the coming weeks.
### Extend your agent seamlessly from Agent Builder to Copilot Studio
Makers can now seamlessly move agents built in Agent Builder (the lightweight agent-building experience inside Microsoft 365 Copilot) into the full Copilot Studio application using the new “Copy to Copilot Studio” action. This capability is generally available everywhere Agent Builder is supported.
!Microsoft Copilot with a dropdown menu open in the upper-right corner showing response modes. 
This feature allows makers to start prototypes quickly in Microsoft 365 Copilot and then expand them into fully governed, enterprise-ready Copilot Studio agents without rebuilding from scratch. The copy operation creates a version of the agent in the selected environment while preserving the original in Agent Builder. In addition to providing peace of mind, this means your users can still partake in the existing experience while the enhanced version is developed.
Once an Agent gets copied into Copilot Studio, makers gain access to a suite of richer capabilities. This includes lifecycle management, analytics, more third-party connectors, and publishing options that give agents access to channels such as the Teams app store. This helps create a healthy innovation cycle: fast at the edges, controlled at the core. Learn more about copying agents to Copilot Studio.
### Streamline employee support with the Employee Self-Service Agent
The Employee Self-Service Agent in the Microsoft 365 Copilot agent building experience is now generally available. This agent provides a centralized AI-powered experience for common employee support scenarios, including HR- and IT-related needs. The Employee Self-Service Agent helps employees quickly get answers and complete tasks such as checking leave balances, reviewing benefits, or submitting IT tickets. This agent provides a centralized AI-powered experience for common employee support scenarios, including HR- and IT-related needs. The Employee Self-Service Agent helps employees quickly get answers and complete tasks such as checking leave balances, reviewing benefits, or submitting IT tickets. 
Built for makers to configure and extend in Copilot Studio, the agent includes prebuilt connectors and workflows for systems like Workday, ServiceNow, and SAP SuccessFactors. It’s fully customizable and extensible. This allows teams to tailor responses, logic, and integrations to their own organizational processes. 
!Gif showing a conversation with the Employee Self-Service Agent
To keep employees in their flow of work, the agent can also hand off to Workday or ServiceNow agents when deeper actions are required. This means that instead of employees navigating portals or emailing multiple teams, they can simply ask for what they need. From a business lens, this reduces ticket backlogs, shortens resolution times, and improves employee sentiment. 
The Employee Self-Service Agent is designed to work within your existing Microsoft 365 security, privacy, and compliance boundaries. Expanded support for Facilities and other verticals is coming soon. Learn more in the agent’s general availability announcement blog.
### Unlock organizational intelligence: People as a knowledge source
Makers can now add People as a knowledge source in Agent Builder for declarative agents. Agents can reference live directory information, including employees’ roles, reporting relationships, team memberships, and profile details, to answer questions such as “Who is the manager for X?” or “Who is on Y team?” with current, accurate details. 
This feature, now generally available, promotes richer organizational insight across internal workflows, approvals, and employee support experiences. It’s especially valuable for onboarding, internal support, approvals, escalation paths, or any workflow where it can be difficult, but critical, to identify the correct person. By grounding agents in live directory data, makers can deliver more accurate, context-aware responses without manual upkeep or duplicated lists.
To enable People as a knowledge source, open Agent Builder, navigate to Knowledge sources, and select “Reference people in organization.” Learn more about People as a knowledge source.
### Generate polished documents, spreadsheets, and presentations
This is where AI shifts from “assistant” to “producer.” Agents built inside Microsoft 365 Copilot can now create high-quality Word documents, Excel worksheets, and PowerPoint presentations using the “Generate documents, charts, and code” skillset (formerly known as Cope Interpreter). This capability is generally available everywhere Agent Builder is supported.
These enhanced Office skills bring richer creation and formatting tools directly into your custom agents. Agents can generate structured documents, well-designed slides, and Excel files that incorporate charts, visuals, layouts, and other professional elements. This makes it easier for teams to create reports, summaries, plans, proposals, and analysis as part of an automated workflow. You can do all this using natural language.
To try out this feature, open Agent Builder and toggle on Generate documents, charts, and code. If Code Interpreter was previously enabled, the new capabilities are automatically available.
### Use OneNote pages as living knowledge
Makers can now add OneNote pages as knowledge sources in Agent Builder. Many teams rely on OneNote to capture meeting notes, brainstorming sessions, project plans, research summaries, and personal workstreams. This update, now in preview and due to roll out worldwide in December, brings all that information directly into your agents’ grounding experience.
By selecting specific OneNote pages, makers can empower agents to provide responses that reflect real project context and decision history. This is especially helpful for roles that depend on ongoing notes, such as customer success, project management, operations, or research, where critical details often live outside traditional documents.
OneNote support also reduces the need to copy content into files or recreate notes elsewhere. Makers can simply choose the pages they want to include and let Microsoft 365 Copilot agents draw from them automatically during conversations and workflows.
To add this capability: Open Agent Builder, go to Knowledge sources, choose OneNote from the file picker, and select the pages you want to include. Learn more about knowledge sources in Agent Builder.
## The bigger takeaway
The story of November isn’t just new features. It’s a shift in how work gets designed.
We’re moving toward a world where organizations don’t just automate steps—they design intelligent systems of work, where AI agents handle complexity, people apply judgment, and businesses operate with more speed, clarity, and resilience.
And we’re just getting started.
## Stay up to date on all things Copilot Studio
Check out all the updates as we ship them, as well as new features releasing in the next few months here: What’s new in Microsoft Copilot Studio.
To learn more about Microsoft Copilot Studio and how it can transform productivity within your organization, visit the Copilot Studio website or sign up for our free trial today.
!Woman with a blue collared shirt
## Nitasha Chopra
VP & COO, Microsoft Copilot Studio 
 See more articles from this author 
##  Related Posts 
  * !GPT-5.2 in Microsoft 365 Copilot
    *  News 
    * Dec 11, 2025 
    * 2 min read 
###   Available today: GPT-5.2 in Microsoft 365 Copilot 
  * !A person working on a computer
    *  News 
    * Nov 24, 2025 
    * 1 min read 
###   Announcing Claude Opus 4.5 in Microsoft Copilot Studio 
  * !Two people using Microsoft Copilot Studio collaborate in an office setting. The Copilot Studio icon is in the left bottom corner.
    *  News 
    * Nov 18, 2025 
    * 5 min read 
###   Why Microsoft Copilot Studio is the foundation for agentic business transformation 

## Try Copilot Studio
Streamline business processes across your organization by building custom agents with low-code tools and generative AI.
Try for free
Learn more
!A man looking at his phone
Notifications
