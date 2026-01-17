---
title: "OpenAI's O1: A Large Reasoning Model"
source: https://emergentmind.com/topics/openai-s-o1
date: 2025-01-20
description: "OpenAI's O1 is a large-scale reasoning model that integrates chain-of-thought and reinforcement learning to solve complex problems across domains."
word_count: 2015
---

2000 character limit reached 
#  OpenAI's O1: A Large Reasoning Model 
Updated 21 July 2025 
  * OpenAI's o1 is a large-scale reasoning model that features explicit chain-of-thought reasoning, enabling multi-step problem solving with reinforcement learning.
  * It uses a two-phase training process combining initial CoT supervision and dense reward-based fine-tuning to achieve state-of-the-art results in planning and scheduling tasks.
  * Despite its advanced reasoning capabilities, o1 faces challenges in efficiency, domain generalization, and long-step planning, driving further research in robust AGI systems.

OpenAI’s o1 is a large-scale reasoning model (“Large Reasoning Model,” LRM) developed and released in late 2024 as a successor to previous autoregressive LLMs. In contrast to conventional LLMs, o1 is specifically engineered to internalize chain-of-thought reasoning and leverages advanced reinforcement learning techniques during both pre-training and fine-tuning. As a result, o1 sets new standards in reasoning, planning, and complex multi-step problem solving across a wide array of domains, while also presenting challenges related to efficiency, safety, and domain generalization.
## 1. Architectural Foundations and Training Paradigm
OpenAI o1 departs from standard LLM frameworks by positioning explicit multi-step reasoning—in the form of internalized chain-of-thought (CoT) mechanisms—at the core of its training and inference. Unlike traditional models that operate primarily as text retrievers or next-token predictors, o1 is trained through a reinforcement learning (RL) curriculum that rewards the construction of coherent, high-quality reasoning traces.
O1’s training integrates a two-phase process:
  * **Policy Initialization and CoT Supervision:** Model parameters are first pre-trained on large corpora and then fine-tuned using supervised instruction data, which introduces early reasoning behaviors.
  * **Reinforcement Learning with Dense Rewards:** The model undergoes RL fine-tuning where it learns to assign q-values to intermediate reasoning steps, using both process-level rewards (for stepwise quality) and outcome rewards (for final correctness). This process is formalized by optimizing a value function

Q(s,a)≈E[∑t=0Trt∣s0=s,a0=a]Q(s, a) \approx \mathbb{E}\Bigl[\sum_{t=0}^{T} r_t \,\Big|\, s_0 = s,\, a_0 = a\Bigr]Q(s,a)≈E[t=0∑T​rt​​s0​=s,a0​=a]
so as to reinforce effective reasoning actions.
Additionally, o1 employs adaptive inference: at deployment time, it dynamically determines the number of reasoning (“thinking”) tokens internally generated, scaling its computation to problem complexity rather than responding with a fixed-length output (Valmeekam et al., 2024, Zeng et al., 2024).
## 2. Planning, Scheduling, and System 2 Reasoning Capabilities
The o1 model series demonstrates unprecedented performance on classical planning and scheduling benchmarks. On standardized tasks such as Blocksworld, o1 achieves near-perfect accuracy of 97.8% in zero-shot evaluations, dramatically surpassing earlier LLMs, which plateau at 28–62% accuracy in similar settings (Valmeekam et al., 2024, Valmeekam et al., 2024). In obfuscated or “mystery” variant problems—where surface forms are randomized—o1 maintains significantly above-random performance (52.8% accuracy), while LLMs nearly fail entirely.
However, on longer planning tasks, the advantage diminishes: for plans exceeding 20 steps, o1’s performance degrades to approximately 23.6%. In domains requiring adherence to intricate constraints and robust state tracking (e.g., Tyreworld, Termes), o1-preview demonstrates improved feasibility and constraint satisfaction relative to GPT-4, but still exhibits shortcomings in optimality (redundant steps), memory management, and generalization when faced with unfamiliar abstractions or spatial complexity (Wang et al., 2024).
In scheduling applications, o1-mini achieves up to 96% accuracy on graph coloring but only marginal improvements or inconsistencies in more complex travel planning and calendar scheduling scenarios (Valmeekam et al., 2024).
## 3. Reasoning Patterns: Divide-and-Conquer, Self-Refinement, and Test-Time Compute
Analyses of o1’s reasoning processes identify six distinct reasoning patterns that underlie its superior performance:
  * **Systematic Analysis:** Decomposing problems by explicitly analyzing structure and constraints before responding.
  * **Method Reuse:** Mapping novel tasks onto known strategies.
  * **Divide and Conquer:** Splitting complex problems into sub-problems and hierarchically recombining solutions.
  * **Self-Refinement:** Iterating over intermediate solutions, correcting errors through internal critique.
  * **Context Identification:** Summarizing necessary context, especially for tasks requiring external knowledge.
  * **Emphasizing Constraints:** Explicitly reinforcing formatting and operational requirements.

Empirical studies indicate that o1’s integration of divide-and-conquer and self-refinement is a major driver of its reasoning gains, enabling it to outperform both simple best-of-N and agent workflow strategies on complex math, coding, and commonsense reasoning tasks (Wu et al., 2024). The “thinking-before-responding” paradigm, realized via increased inference computation, is a notable shift away from the “one-shot” approaches of past LLMs.
## 4. Comparative Benchmark Performance and Domain-Specific Deployments
O1 consistently delivers leading performance across diverse domains:
  * **Mathematics:** O1-preview scores near 97.8th percentile on Dutch national exams, outperforming both GPT-4o and most human candidates (Winter et al., 2024). On International Mathematics Olympiad (IMO) and lesser-known Chinese National Team datasets, o1’s consistent accuracy demonstrates true problem-solving over memorization (Li et al., 2024).
  * **Medicine:** O1 achieves higher accuracy than prior models (average +6.2% over GPT-4) on datasets derived from NEJM and The Lancet quizzes, as well as improved multilingual performance (85.2% on XMedBench vs. 75.7% for GPT-4) (Xie et al., 2024). Nonetheless, areas such as hallucination, multilingual agent tasks, and decoding speed remain open challenges.
  * **Ophthalmology:** O1 leads in accuracy (0.88) and macro-F1 (0.70) among LLMs on MedMCQA, but ranks third (after GPT-4o and GPT-4) in reasoning metrics that assess text-generation quality, indicating a gap between answer selection and explanation fidelity (Srinivasan et al., 20 Jan 2025).
  * **Higher-Order Cognition:** O1-preview demonstrably outperforms human baselines in critical thinking, systematic thinking, data literacy, and scientific reasoning, but underperforms in certain types of logic and abstract/adaptive reasoning (Latif et al., 2024, Latif et al., 2024).
  * **Other Domains:** O1-preview exhibits strong performance in chip design, robotics planning, quantitative investing, sentiment analysis, and table-to-text generation (Zhong et al., 2024).

Despite its breadth, o1’s reasoning prowess does not universally transfer to highly specialized domains without domain-specific adaptation, and verbose or rigid chain-of-thought outputs may lower performance on metrics sensitive to brevity or phrasing.
## 5. Efficiency, Scalability, and Inference Optimization
The “long thought” paradigm in o1, while boosting problem-solving ability, incurs substantial computational overhead. The model frequently generates hidden reasoning tokens beyond user output, causing high inference latency and financial cost—up to orders of magnitude greater than classical planners or standard LLMs for comparable tasks (Valmeekam et al., 2024, Valmeekam et al., 2024). For example, the cost-per-100 instances can exceed \$42 for o1-preview, whereas classical planners complete the same benchmarks in milliseconds at negligible cost (Valmeekam et al., 2024).
The inefficiency is addressed in part by post-hoc methods such as O1-Pruner (Luo et al., 22 Jan 2025), which applies length-harmonizing fine-tuning via RL to reduce redundant reasoning steps without sacrificing accuracy:
RLH(x,y)=Lˉref(x)L(y)−1+λ(A(x,y)−Aˉref(x))R_{LH}(x, y) = \frac{\bar{L}_{ref}(x)}{L(y)} - 1 + \lambda \bigl(A(x, y) - \bar{A}_{ref}(x)\bigr)RLH​(x,y)=L(y)Lˉref​(x)​−1+λ(A(x,y)−Aˉref​(x))
This approach yields up to 40% reduction in answer length on benchmarks like MATH, occasionally with improved accuracy.
## 6. Safety, Alignment, and Robustness
O1’s explicit incorporation of chain-of-thought facilitates enhanced safety protocols through “deliberative alignment,” in which the model reasons about its safety constraints before finalizing outputs (OpenAI et al., 2024). On adversarial tests—including disallowed content, jailbreak resistance, and refusal robustness—o1 achieves state-of-the-art scores, with “not_unsafe” metrics approaching or equaling 1. Statistical evaluations and red teaming confirm improvements over predecessors such as GPT-4o, particularly in withstanding complex jailbreak attempts and contextual safety scenarios (Wang et al., 2024). Nevertheless, o1 retains residual vulnerabilities: adversaries may exploit intermediate reasoning states (“attack surface”) or employ mathematically encoded prompts that evade conventional safety mechanisms.
Mitigation strategies include enhanced prompt engineering, supervised fine-tuning with detailed chain-of-thought responses, and reinforcement learning with process supervision (rewarding each reasoning step for safety and correctness). Balance between safety and overrefusal remains a focus of ongoing research.
## 7. Limitations, Open Research Directions, and Implications
Despite substantial advances, o1 is not a universal solution:
  * Its performance degrades on long, highly compositional, or spatially complex planning tasks due to memory and state-tracking bottlenecks (Wang et al., 2024).
  * “Quantum improvements” on standard benchmarks do not guarantee robustness or guarantees in unstructured, adversarial, or real-world settings; classical planners retain provable correctness and efficiency advantages.
  * The chain-of-thought reasoning, while effective, exposes sensitivities to probability and distributional frequency—the so-called “embers of autoregression”—limiting performance when outputs are statistically rare or low-probability (McCoy et al., 2024).
  * Domain transfer to specialized fields (e.g., ophthalmology) shows that o1’s reasoning improvements may require further refinement and fine-tuning for optimal results (Srinivasan et al., 20 Jan 2025).

Open research directions include:
  * Enhanced memory management and decision-making modules for sustained multi-step reasoning.
  * Adaptive computation policies to dynamically balance efficiency and reasoning depth (Luo et al., 22 Jan 2025).
  * Unified evaluation protocols for reasoning quality, factuality, and hallucination mitigation in complex domains (Xie et al., 2024).
  * Safety alignment approaches that secure each step of the reasoning process against adversarial manipulation (OpenAI et al., 2024, Wang et al., 2024).
  * Exploration of journey learning paradigms and open science methodologies for transparent and community-driven refinement (Qin et al., 2024).

In summary, OpenAI’s o1 marks an epochal shift toward LLMs capable of explicit reasoning via reinforcement learning–trained chain-of-thought. While it establishes new state-of-the-art results for planning, problem solving, and cognitive benchmarking, o1’s limitations in cost, generalization, efficiency, and safe deployment underscore the ongoing challenges in advancing toward robust and trustworthy artificial general intelligence.
 File Document Download Save Streamline Icon: https://streamlinehq.com  PDF   File Document Download Save Streamline Icon: https://streamlinehq.com  Markdown   Chat Bubble Oval Streamline Icon: https://streamlinehq.com  Chat (Pro) 
Definition Search Book Streamline Icon: https://streamlinehq.com
References (17)
1. 
LLMs Still Can't Plan; Can LRMs? A Preliminary Evaluation of OpenAI's o1 on PlanBench (2024)
2. 
Scaling of Search and Learning: A Roadmap to Reproduce o1 from Reinforcement Learning Perspective (2024)
3. 
Planning in Strawberry Fields: Evaluating and Improving the Planning and Scheduling Capabilities of LRM o1 (2024)
4. 
On The Planning Abilities of OpenAI's o1 Models: Feasibility, Optimality, and Generalizability (2024)
5. 
A Comparative Study on Reasoning Patterns of OpenAI's o1 Model (2024)
6. 
System 2 thinking in OpenAI's o1-preview model: Near-perfect performance on a mathematics exam (2024)
7. 
OpenAI-o1 AB Testing: Does the o1 model really do good reasoning in math problem solving? (2024)
8. 
A Preliminary Study of o1 in Medicine: Are We Closer to an AI Doctor? (2024)
9. 
Can OpenAI o1 Reason Well in Ophthalmology? A 6,990-Question Head-to-Head Evaluation Study (2025)
10. 
A Systematic Assessment of OpenAI o1-Preview for Higher Order Thinking in Education (2024)
11. 
Can OpenAI o1 outperform humans in higher-order cognitive thinking? (2024)
12. 
Evaluation of OpenAI o1: Opportunities and Challenges of AGI (2024)
13. 
O1-Pruner: Length-Harmonizing Fine-Tuning for O1-Like Reasoning Pruning (2025)
14. 
OpenAI o1 System Card (2024)
15. 
Don't Command, Cultivate: An Exploratory Study of System-2 Alignment (2024)
16. 
When a language model is optimized for reasoning, does it still show embers of autoregression? An analysis of OpenAI o1 (2024)
17. 
O1 Replication Journey: A Strategic Progress Report -- Part 1 (2024)
### Sponsor
 
Organize your preprints, BibTeX, and PDFs with Paperpile.
 Get 30 days free 
 
### Whiteboard
Generate a whiteboard explanation of this topic.
 Ai Sparkles Streamline Icon: https://streamlinehq.com  Sign Up to Generate 
### Topic to Video (Beta)
Generate a video overview of this topic.
 Ai Sparkles Streamline Icon: https://streamlinehq.com  Sign Up to Generate 
### Follow Topic
Get notified by email when new papers are published related to **OpenAI's O1**.
 Add Bell Notification Streamline Icon: https://streamlinehq.com  Sign Up to Follow Topic by Email 
### Continue Learning
  1. How does o1's reinforcement learning with dense rewards improve its reasoning abilities compared to previous autoregressive LLMs?
  2. What are the main bottlenecks or failure modes for o1 in long-horizon planning and highly compositional tasks, and what approaches are being considered to overcome them?
  3. In what ways does o1's approach to safety and 'deliberative alignment' differ from classical LLM safety protocols, and what new vulnerabilities are introduced by chain-of-thought reasoning?
  4. How does o1 balance the trade-off between computational efficiency and reasoning depth, particularly in real-world deployment scenarios where inference costs are nontrivial?
  5. Find recent papers about advanced memory and decision modules for multi-step reasoning in large language models.

### Related Topics
  1. O3-mini Reasoning Model Overview
  2. OpenAI o3-pro: Advanced LLM
  3. OpenAI-o3: Advanced Reasoning Models
  4. o1-preview Model: Advanced Reasoning LLM
  5. DeepSeek Models: Scalable and Efficient AI
  6. DeepSeek-Distill-Qwen-1.5B Overview
  7. DeepSeek Reasoner: Explicit Multi-Step LLM
  8. O1-Coder Framework: Multi-Step Code Generation
  9. DeepSeek-Reasoner: Chain-of-Thought LLMs
  10. DeepSeek-R: Efficient Reasoning LLM

Stay informed about trending AI/ML papers: 
