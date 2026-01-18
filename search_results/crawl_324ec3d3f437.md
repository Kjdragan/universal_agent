---
title: "The Business Case for Fine-Tuning Llama 3 Today | Shakudo"
source: https://shakudo.io/blog/business-case-fine-tuning-llama3-today
date: 2024-05-03
description: "Llama 3: The open-source LLM disrupting the AI landscape. Outperforms models 10x its size, enables cheap fine-tuning, and tops benchmarks. Discover how to harness its power for your business."
word_count: 4071
---

Latest in White Paper:The Enterprise Guide to AI Agent Readiness
[**[Event]** See Shakudo at Ai4 2025 - North America’s Largest Artificial Intelligence Industry Event](https://shakudo.io/blog/<https:/www.shakudo.io/ai4-2025>)

← Back to Blog
News
# The Business Case for Fine-Tuning Llama 3 Today
[](https://shakudo.io/blog/<https:/www.shakudo.io/sign-up>)
By: 
Shakudo Team
Updated on: 
May 3, 2024
#### Table of Contents
Shakudo is the operating system for data and AI
Shakudo is the operating system for data and AI
#### Mentioned Shakudo Ecosystem Components
No items found.
<>
## Introduction
There are hundreds of open-source LLMs already on the market and most tout best-in-class features in one metric or another. With the daily influx of new open-source models, how do you know if the most recent model from Meta, Llama 3, moves the needle for your business?
Well, Llama 3-8B surpasses models 10 times its size, such as its predecessor Llama 2-70B, and once Llama 3-405B is finished training, it is suspected to match the latest version of GPT-4. Llama 3 has brought open source on par with the best commercial LLMs. This constitutes a real shift in the current state of LLMs.
## Does Llama 3 change anything for my business?
To test this question we must first decide the criteria to evaluate Llama 3.
Andreessen Horowitz provided a rubric to this question in a recent article. Their survey of leaders in the Fortune 500 uncovered the top three considerations for open-source at the enterprise level:
  1. Control
  2. Customizability
  3. Cost

Source: <https://a16z.com/generative-ai-enterprise-2024/>
Llama 3 is an extremely competitive model in all three categories. Let’s dive into how.
### Control
Control is measured by model license and level of data security when working with the model.
Llama 3 is licensed under the “Meta LLama 3 Community License Agreement” - a license that permits almost all commercial use.
The important caveats to consider are:
  * You will need a license if your application has >700M monthly active users
  * You cannot use the outputs of the model to train competing models
  * You cannot use the Meta trademarks

For most businesses, these caveats are nothing to worry about. And if your application does support >700M MAU you can request a license from Meta. The alternative would be MIT or Apache 2.0 licensed models.
Unfortunately, there are no Apache 2.0 or MIT-licensed models within the top 10 models based on Huggingface’s LMSys Chatbot Arena Leaderboard, and the only other non-proprietary model is not for commercial use (CC-BY-NC-4.0).

Source: <https://chat.lmsys.org/?leaderboard>
This table measures performance as the Arena Elo, or “ELO” rating. It includes close to 100 models, close to 1M votes, and is widely recognized as the “ground truth” of model quality. ELO is a measure popularized in chess where competitors (LLMs) are rated based on their relative skill levels against other competitors (LLMs). This is a good measure of LLM performance as benchmarks can easily be gamed (by training on the benchmark data). The performance of the LLMs in the LMSys leaderboard are crowdsourced, where users provide one query to several LLMs and select the best answer. 

Source: Andrej Karpathy, ex-Tesla, ex-OpenAI
#### Data security
[](https://shakudo.io/blog/<https:/www.shakudo.io/sign-up>)
Another consideration for open-source over commercial is control over your data. While data security is not unique to Llama 3, it is the first open-source model to rank this high in performance benchmarks.
API providers like OpenAI and Anthropic have enterprise security offerings, but your data must be sent to their servers to be processed. Sending data to an API endpoint hosted outside your cluster can raise significant security concerns. It increases the risk of data interception during transmission, potential unauthorized access by third parties, and exposure to external vulnerabilities.
Furthermore, reliance on external endpoints introduces dependencies beyond your control, making your system susceptible to downtime or service disruptions. Maintaining data integrity and confidentiality becomes challenging when it traverses external networks. With a self-hosted Llama 3 model, you retain full control over your data. 
### Customizability
Customizability is measured by the cost of fine-tuning and the relative performance gain of fine-tuned models.
If you’ve viewed our past webinar on “How to Fine-Tune Llama 2 and Outperform ChatGPT” you might already know how small open-source models can gain huge performance boosts from domain-specific fine-tuning.
Llama 3 is the most customizable model available because of its top-tier base model performance and small parameter size, making it cheap to fine-tune. To illustrate this point, consider OpenBioLLM-70B, an open-source medical domain model by the team at Saama AI Labs, released just weeks after Llama 3 came onto the scene.
OpenBioLLM-70B is the current state-of-the-art in several biomedical tasks, beating out much larger models like Med-PaLM-2, GPT4, and Gemini-1.0. Not to mention the team also trained an 8B flavour of the model, OpenBioLLM-8B, which outperforms GPT3.5 Turbo in these tests, too.
Without further ado, here is a sample demonstrating the effectiveness of a fine-tuned Llama 3:

Source: <https://huggingface.co/aaditya/Llama3-OpenBioLLM-70B>
These models are extremely performant once fine-tuned, and fine-tuning is relatively cheap thanks to techniques like LoRA and QLoRA. Examples of fine-tuning Llama 3-8B and Llama 3-70B for just tens or hundreds of dollars are readily available online (1, 2)
Comparatively, fine-tuning with OpenAI currently requires a minimum spend of $2-3M. Anthropic, Cohere, and similar foundational model providers could be half as expensive and still put the costs of customizability for commercial models north of $1M. Not to mention OpenAI advises billions of tokens to get started.
Fine-tuning Llama 3 is cheap, and the results can lead to state-of-the-art performance. The results achieved here are unattainable for most companies through providers like OpenAI but will become commonplace in the open-source LLM landscape thanks to Llama 3.
### Cost
Cost is measured as Price/Performance. Price is the cost of 1M tokens of inference (based on standard pricing for commercial models and an average across inference providers for OS).
Model
ELO Rating
Cost per 1M tokens
ELO/Cost
GPT-4 (Turbo-2024-04-09)
1257
$15.00
83.8
Claude 3 Opus
1251
$30.00
41.7
Gemini 1.5 Pro
1248
$10.50
118.9
Gemini 1.0 Pro
1209
$ 0.75
1612
Llama3-70B
1207
$ 0.90
1341.1
Claude 3 Sonnet
1202
$ 6.00
200.3
Command R+
1192
$ 6.00
198.7
Source: ‍<https://artificialanalysis.ai/> for Cost‍<https://huggingface.co/spaces/lmsys/chatbot-arena-leaderboard> for ELO ratings
[](https://shakudo.io/blog/<https:/www.shakudo.io/sign-up>)
The top 7 ELO-rated models from our earlier analysis (only the most recent GPT4 model is included here) highlight Llama 3-70B and Gemini 1.0 Ultra as the clear price/performance leaders. 
Gemini 1.0 Pro provides 10 times more intelligence per dollar than its peers and 20 times more than the leaders Claude 3 and GPT4. Gemini 1.0 Pro is the loss leader within the group of highest-performing commercial models. With that in mind, Llama 3-70B matches the loss-leader in price/performance, while being many times smaller (parameter count), and open-source.
Once again, Llama 3-70B is at the top of the benchmark.
## Conclusion
Across all three criteria, Llama 3 excels. The Meta Llama Community license confers a high degree of control to even enterprise users, the model has achieved state-of-the-art results on domain-specific benchmarks when fine-tuned, and it is cheap - a loss leader among the leading models available, both commercial and open-source.
Now the question is - how do you get Llama 3 in-house, prepare your data for fine-tuning, and deploy Llama 3 for your internal and external business applications? None of these tasks break fresh ground like the LLM research we are witnessing, but they represent non-trivial engineering work to complete. Luckily, many open-source tools exist to help at each step of this journey.
Open-source tools like Ollama make hosting LLM inference trivial. Ollama and tools for data ingestion (Airbyte), LLM finetuning (H2O.ai), and more are available on Shakudo and deployed directly on your infrastructure. With no additional DevOps or engineering work required, Shakudo brings all the tools you need to accelerate and scale your data and AI stack. So you can start reaping the rewards of groundbreaking tech like Llama 3 in weeks not months.
🎉 Success! You're now signed up for the Shakudo newsletter.
Oops! Something went wrong while submitting the form.
## See 175+ of the Best Data & AI Tools in One Place.
Get Started
trusted by leaders

%201.svg)

Whitepaper
## Introduction
There are hundreds of open-source LLMs already on the market and most tout best-in-class features in one metric or another. With the daily influx of new open-source models, how do you know if the most recent model from Meta, Llama 3, moves the needle for your business?
Well, Llama 3-8B surpasses models 10 times its size, such as its predecessor Llama 2-70B, and once Llama 3-405B is finished training, it is suspected to match the latest version of GPT-4. Llama 3 has brought open source on par with the best commercial LLMs. This constitutes a real shift in the current state of LLMs.
## Does Llama 3 change anything for my business?
To test this question we must first decide the criteria to evaluate Llama 3.
Andreessen Horowitz provided a rubric to this question in a recent article. Their survey of leaders in the Fortune 500 uncovered the top three considerations for open-source at the enterprise level:
  1. Control
  2. Customizability
  3. Cost

Source: <https://a16z.com/generative-ai-enterprise-2024/>
Llama 3 is an extremely competitive model in all three categories. Let’s dive into how.
### Control
Control is measured by model license and level of data security when working with the model.
Llama 3 is licensed under the “Meta LLama 3 Community License Agreement” - a license that permits almost all commercial use.
The important caveats to consider are:
  * You will need a license if your application has >700M monthly active users
  * You cannot use the outputs of the model to train competing models
  * You cannot use the Meta trademarks

For most businesses, these caveats are nothing to worry about. And if your application does support >700M MAU you can request a license from Meta. The alternative would be MIT or Apache 2.0 licensed models.
Unfortunately, there are no Apache 2.0 or MIT-licensed models within the top 10 models based on Huggingface’s LMSys Chatbot Arena Leaderboard, and the only other non-proprietary model is not for commercial use (CC-BY-NC-4.0).

Source: <https://chat.lmsys.org/?leaderboard>
This table measures performance as the Arena Elo, or “ELO” rating. It includes close to 100 models, close to 1M votes, and is widely recognized as the “ground truth” of model quality. ELO is a measure popularized in chess where competitors (LLMs) are rated based on their relative skill levels against other competitors (LLMs). This is a good measure of LLM performance as benchmarks can easily be gamed (by training on the benchmark data). The performance of the LLMs in the LMSys leaderboard are crowdsourced, where users provide one query to several LLMs and select the best answer. 

Source: Andrej Karpathy, ex-Tesla, ex-OpenAI
#### Data security
Another consideration for open-source over commercial is control over your data. While data security is not unique to Llama 3, it is the first open-source model to rank this high in performance benchmarks.
API providers like OpenAI and Anthropic have enterprise security offerings, but your data must be sent to their servers to be processed. Sending data to an API endpoint hosted outside your cluster can raise significant security concerns. It increases the risk of data interception during transmission, potential unauthorized access by third parties, and exposure to external vulnerabilities.
Furthermore, reliance on external endpoints introduces dependencies beyond your control, making your system susceptible to downtime or service disruptions. Maintaining data integrity and confidentiality becomes challenging when it traverses external networks. With a self-hosted Llama 3 model, you retain full control over your data. 
### Customizability
Customizability is measured by the cost of fine-tuning and the relative performance gain of fine-tuned models.
If you’ve viewed our past webinar on “How to Fine-Tune Llama 2 and Outperform ChatGPT” you might already know how small open-source models can gain huge performance boosts from domain-specific fine-tuning.
Llama 3 is the most customizable model available because of its top-tier base model performance and small parameter size, making it cheap to fine-tune. To illustrate this point, consider OpenBioLLM-70B, an open-source medical domain model by the team at Saama AI Labs, released just weeks after Llama 3 came onto the scene.
OpenBioLLM-70B is the current state-of-the-art in several biomedical tasks, beating out much larger models like Med-PaLM-2, GPT4, and Gemini-1.0. Not to mention the team also trained an 8B flavour of the model, OpenBioLLM-8B, which outperforms GPT3.5 Turbo in these tests, too.
Without further ado, here is a sample demonstrating the effectiveness of a fine-tuned Llama 3:

Source: <https://huggingface.co/aaditya/Llama3-OpenBioLLM-70B>
These models are extremely performant once fine-tuned, and fine-tuning is relatively cheap thanks to techniques like LoRA and QLoRA. Examples of fine-tuning Llama 3-8B and Llama 3-70B for just tens or hundreds of dollars are readily available online (1, 2)
Comparatively, fine-tuning with OpenAI currently requires a minimum spend of $2-3M. Anthropic, Cohere, and similar foundational model providers could be half as expensive and still put the costs of customizability for commercial models north of $1M. Not to mention OpenAI advises billions of tokens to get started.
Fine-tuning Llama 3 is cheap, and the results can lead to state-of-the-art performance. The results achieved here are unattainable for most companies through providers like OpenAI but will become commonplace in the open-source LLM landscape thanks to Llama 3.
### Cost
Cost is measured as Price/Performance. Price is the cost of 1M tokens of inference (based on standard pricing for commercial models and an average across inference providers for OS).
Source: ‍<https://artificialanalysis.ai/> for Cost‍<https://huggingface.co/spaces/lmsys/chatbot-arena-leaderboard> for ELO ratings
The top 7 ELO-rated models from our earlier analysis (only the most recent GPT4 model is included here) highlight Llama 3-70B and Gemini 1.0 Ultra as the clear price/performance leaders. 
Gemini 1.0 Pro provides 10 times more intelligence per dollar than its peers and 20 times more than the leaders Claude 3 and GPT4. Gemini 1.0 Pro is the loss leader within the group of highest-performing commercial models. With that in mind, Llama 3-70B matches the loss-leader in price/performance, while being many times smaller (parameter count), and open-source.
Once again, Llama 3-70B is at the top of the benchmark.
## Conclusion
Across all three criteria, Llama 3 excels. The Meta Llama Community license confers a high degree of control to even enterprise users, the model has achieved state-of-the-art results on domain-specific benchmarks when fine-tuned, and it is cheap - a loss leader among the leading models available, both commercial and open-source.
Now the question is - how do you get Llama 3 in-house, prepare your data for fine-tuning, and deploy Llama 3 for your internal and external business applications? None of these tasks break fresh ground like the LLM research we are witnessing, but they represent non-trivial engineering work to complete. Luckily, many open-source tools exist to help at each step of this journey.
Open-source tools like Ollama make hosting LLM inference trivial. Ollama and tools for data ingestion (Airbyte), LLM finetuning (H2O.ai), and more are available on Shakudo and deployed directly on your infrastructure. With no additional DevOps or engineering work required, Shakudo brings all the tools you need to accelerate and scale your data and AI stack. So you can start reaping the rewards of groundbreaking tech like Llama 3 in weeks not months.
## Get the whitepaper
# The Business Case for Fine-Tuning Llama 3 Today
Thank you for filling out the form. The whitepaper you have requested is available for download below.
Download White Paper
Oops! Something went wrong while submitting the form.
## Get the whitepaper
# The Business Case for Fine-Tuning Llama 3 Today
Thank you for your interest. Click the button below to download whitepaper you have requested.
Download White Paper
# The Business Case for Fine-Tuning Llama 3 Today
Llama 3: The open-source LLM disrupting the AI landscape. Outperforms models 10x its size, enables cheap fine-tuning, and tops benchmarks. Discover how to harness its power for your business.
| Case Study
The Business Case for Fine-Tuning Llama 3 Today

#### Key results
#### About
#### industry
#### Tech Stack
No items found.
<>
## Introduction
There are hundreds of open-source LLMs already on the market and most tout best-in-class features in one metric or another. With the daily influx of new open-source models, how do you know if the most recent model from Meta, Llama 3, moves the needle for your business?
Well, Llama 3-8B surpasses models 10 times its size, such as its predecessor Llama 2-70B, and once Llama 3-405B is finished training, it is suspected to match the latest version of GPT-4. Llama 3 has brought open source on par with the best commercial LLMs. This constitutes a real shift in the current state of LLMs.
## Does Llama 3 change anything for my business?
To test this question we must first decide the criteria to evaluate Llama 3.
Andreessen Horowitz provided a rubric to this question in a recent article. Their survey of leaders in the Fortune 500 uncovered the top three considerations for open-source at the enterprise level:
  1. Control
  2. Customizability
  3. Cost

Source: <https://a16z.com/generative-ai-enterprise-2024/>
Llama 3 is an extremely competitive model in all three categories. Let’s dive into how.
### Control
Control is measured by model license and level of data security when working with the model.
Llama 3 is licensed under the “Meta LLama 3 Community License Agreement” - a license that permits almost all commercial use.
The important caveats to consider are:
  * You will need a license if your application has >700M monthly active users
  * You cannot use the outputs of the model to train competing models
  * You cannot use the Meta trademarks

For most businesses, these caveats are nothing to worry about. And if your application does support >700M MAU you can request a license from Meta. The alternative would be MIT or Apache 2.0 licensed models.
Unfortunately, there are no Apache 2.0 or MIT-licensed models within the top 10 models based on Huggingface’s LMSys Chatbot Arena Leaderboard, and the only other non-proprietary model is not for commercial use (CC-BY-NC-4.0).

Source: <https://chat.lmsys.org/?leaderboard>
This table measures performance as the Arena Elo, or “ELO” rating. It includes close to 100 models, close to 1M votes, and is widely recognized as the “ground truth” of model quality. ELO is a measure popularized in chess where competitors (LLMs) are rated based on their relative skill levels against other competitors (LLMs). This is a good measure of LLM performance as benchmarks can easily be gamed (by training on the benchmark data). The performance of the LLMs in the LMSys leaderboard are crowdsourced, where users provide one query to several LLMs and select the best answer. 

Source: Andrej Karpathy, ex-Tesla, ex-OpenAI
#### Data security
Another consideration for open-source over commercial is control over your data. While data security is not unique to Llama 3, it is the first open-source model to rank this high in performance benchmarks.
API providers like OpenAI and Anthropic have enterprise security offerings, but your data must be sent to their servers to be processed. Sending data to an API endpoint hosted outside your cluster can raise significant security concerns. It increases the risk of data interception during transmission, potential unauthorized access by third parties, and exposure to external vulnerabilities.
Furthermore, reliance on external endpoints introduces dependencies beyond your control, making your system susceptible to downtime or service disruptions. Maintaining data integrity and confidentiality becomes challenging when it traverses external networks. With a self-hosted Llama 3 model, you retain full control over your data. 
### Customizability
Customizability is measured by the cost of fine-tuning and the relative performance gain of fine-tuned models.
If you’ve viewed our past webinar on “How to Fine-Tune Llama 2 and Outperform ChatGPT” you might already know how small open-source models can gain huge performance boosts from domain-specific fine-tuning.
Llama 3 is the most customizable model available because of its top-tier base model performance and small parameter size, making it cheap to fine-tune. To illustrate this point, consider OpenBioLLM-70B, an open-source medical domain model by the team at Saama AI Labs, released just weeks after Llama 3 came onto the scene.
OpenBioLLM-70B is the current state-of-the-art in several biomedical tasks, beating out much larger models like Med-PaLM-2, GPT4, and Gemini-1.0. Not to mention the team also trained an 8B flavour of the model, OpenBioLLM-8B, which outperforms GPT3.5 Turbo in these tests, too.
Without further ado, here is a sample demonstrating the effectiveness of a fine-tuned Llama 3:

Source: <https://huggingface.co/aaditya/Llama3-OpenBioLLM-70B>
These models are extremely performant once fine-tuned, and fine-tuning is relatively cheap thanks to techniques like LoRA and QLoRA. Examples of fine-tuning Llama 3-8B and Llama 3-70B for just tens or hundreds of dollars are readily available online (1, 2)
Comparatively, fine-tuning with OpenAI currently requires a minimum spend of $2-3M. Anthropic, Cohere, and similar foundational model providers could be half as expensive and still put the costs of customizability for commercial models north of $1M. Not to mention OpenAI advises billions of tokens to get started.
Fine-tuning Llama 3 is cheap, and the results can lead to state-of-the-art performance. The results achieved here are unattainable for most companies through providers like OpenAI but will become commonplace in the open-source LLM landscape thanks to Llama 3.
### Cost
Cost is measured as Price/Performance. Price is the cost of 1M tokens of inference (based on standard pricing for commercial models and an average across inference providers for OS).
Source: ‍<https://artificialanalysis.ai/> for Cost‍<https://huggingface.co/spaces/lmsys/chatbot-arena-leaderboard> for ELO ratings
The top 7 ELO-rated models from our earlier analysis (only the most recent GPT4 model is included here) highlight Llama 3-70B and Gemini 1.0 Ultra as the clear price/performance leaders. 
Gemini 1.0 Pro provides 10 times more intelligence per dollar than its peers and 20 times more than the leaders Claude 3 and GPT4. Gemini 1.0 Pro is the loss leader within the group of highest-performing commercial models. With that in mind, Llama 3-70B matches the loss-leader in price/performance, while being many times smaller (parameter count), and open-source.
Once again, Llama 3-70B is at the top of the benchmark.
## Conclusion
Across all three criteria, Llama 3 excels. The Meta Llama Community license confers a high degree of control to even enterprise users, the model has achieved state-of-the-art results on domain-specific benchmarks when fine-tuned, and it is cheap - a loss leader among the leading models available, both commercial and open-source.
Now the question is - how do you get Llama 3 in-house, prepare your data for fine-tuning, and deploy Llama 3 for your internal and external business applications? None of these tasks break fresh ground like the LLM research we are witnessing, but they represent non-trivial engineering work to complete. Luckily, many open-source tools exist to help at each step of this journey.
Open-source tools like Ollama make hosting LLM inference trivial. Ollama and tools for data ingestion (Airbyte), LLM finetuning (H2O.ai), and more are available on Shakudo and deployed directly on your infrastructure. With no additional DevOps or engineering work required, Shakudo brings all the tools you need to accelerate and scale your data and AI stack. So you can start reaping the rewards of groundbreaking tech like Llama 3 in weeks not months.
## Explore more from Shakudo

# Ready for Enterprise AI?
"Shakudo gave us the flexibility to use the data stack components that fit our needs and evolve the stack to keep up with the industry."
Neal Gilmore
Senior Vice President, Enterprise Data & Analytics @ QuadReal
❮❯
Request a Demo

Shakudo brings the best AI tools into your VPC and operates them for you automatically, achieving a more secure, performant, and cost effective technology stack.

Email
##### Newsletter
🎉 Success! You're now signed up for the Shakudo newsletter.
Oops! Something went wrong while submitting the form.
##### Applications
Data and AI OSStack ComponentsAI AgentsMCP ProxyExtractFlowKnowledge GraphVector Database + LLMWorkflow AutomationText to SQLReverse ETL
##### Industries
Automotive & Transportation
Aerospace
Manufacturing
Healthcare & Life Sciences
Climate & Energy
Real Estate
Retail
Financial Services
##### Resources
Use Cases
Insights
White Paper
Case Study
Press
Product
Tutorial
News
WebinarGlossaryDocumentation
##### Company
AboutPartnersDGX PartnerCareersMedia Kit
##### Get Started
AI WorkshopSignupContact UsNewsletter
© 2026 Shakudo
Toronto, Canada
Contact usPrivacy PolicyTerms & ConditionsSitemap
Trusted by industry leaders

%201.svg)

See Shakudo in Action
# Watch the 3 Minute Demo
Thank you for your submission. A Shakudo expert will be in touch with you shortly. In the meantime, feel free to check out our data insights, case studies, and latest industry news that help data teams win.

Oops! Something went wrong while submitting the form.
⨉

