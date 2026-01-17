---
title: "[2411.04996] Mixture-of-Transformers: A Sparse and Scalable Architecture for Multi-Modal Foundation Models"
source: https://arxiv.org/abs/2411.04996
date: 2024-11-07
description: "Abstract page for arXiv paper 2411.04996: Mixture-of-Transformers: A Sparse and Scalable Architecture for Multi-Modal Foundation Models"
word_count: 717
---

# Computer Science > Computation and Language
**arXiv:2411.04996** (cs) 
Submitted on 7 Nov 2024 ([v1), last revised 8 May 2025 (this version, v2)]
# Title:Mixture-of-Transformers: A Sparse and Scalable Architecture for Multi-Modal Foundation Models
Authors:Weixin Liang, Lili Yu, Liang Luo, Srinivasan Iyer, Ning Dong, Chunting Zhou, Gargi Ghosh, Mike Lewis, Wen-tau Yih, Luke Zettlemoyer, Xi Victoria Lin
View a PDF of the paper titled Mixture-of-Transformers: A Sparse and Scalable Architecture for Multi-Modal Foundation Models, by Weixin Liang and 10 other authors
View PDF
> Abstract:The development of large language models (LLMs) has expanded to multi-modal systems capable of processing text, images, and speech within a unified framework. Training these models demands significantly larger datasets and computational resources compared to text-only LLMs. To address the scaling challenges, we introduce Mixture-of-Transformers (MoT), a sparse multi-modal transformer architecture that significantly reduces pretraining computational costs. MoT decouples non-embedding parameters of the model by modality -- including feed-forward networks, attention matrices, and layer normalization -- enabling modality-specific processing with global self-attention over the full input sequence. We evaluate MoT across multiple settings and model scales. In the Chameleon 7B setting (autoregressive text-and-image generation), MoT matches the dense baseline's performance using only 55.8\% of the FLOPs. When extended to include speech, MoT reaches speech performance comparable to the dense baseline with only 37.2\% of the FLOPs. In the Transfusion setting, where text and image are trained with different objectives, a 7B MoT model matches the image modality performance of the dense baseline with one third of the FLOPs, and a 760M MoT model outperforms a 1.4B dense baseline across key image generation metrics. System profiling further highlights MoT's practical benefits, achieving dense baseline image quality in 47.2\% of the wall-clock time and text quality in 75.6\% of the wall-clock time (measured on AWS p4de.24xlarge instances with NVIDIA A100 GPUs). 
Comments: | Accepted to TMLR 2025; 48 pages  
---|---  
Subjects: |  Computation and Language (cs.CL)  
Cite as: | arXiv:2411.04996 [cs.CL]  
(or  arXiv:2411.04996v2 [cs.CL] for this version)   
<https://doi.org/10.48550/arXiv.2411.04996> Focus to learn more arXiv-issued DOI via DataCite  
Journal reference: | Transactions on Machine Learning Research (2025), ISSN: 2835-8856  
## Submission history
From: Xi Victoria Lin [view email] **[[v1]](https://arxiv.org/abs/</abs/2411.04996v1>)** Thu, 7 Nov 2024 18:59:06 UTC (16,324 KB) **[v2]** Thu, 8 May 2025 01:53:55 UTC (28,988 KB) 
Full-text links:
## Access Paper:
View a PDF of the paper titled Mixture-of-Transformers: A Sparse and Scalable Architecture for Multi-Modal Foundation Models, by Weixin Liang and 10 other authors
  * View PDF
  * TeX Source 

view license
Current browse context: 
cs.CL
< prev") |  next >")
new |  recent | 2024-11
Change to browse by: 
cs
### References & Citations
  * NASA ADS
  * Google Scholar
  * Semantic Scholar

export BibTeX citation Loading...
## BibTeX formatted citation
×
loading...
Data provided by: 
### Bookmark
   
Bibliographic Tools
# Bibliographic and Citation Tools
Bibliographic Explorer Toggle
Bibliographic Explorer _(What is the Explorer?)_
Connected Papers Toggle
Connected Papers _(What is Connected Papers?)_
Litmaps Toggle
Litmaps _(What is Litmaps?)_
scite.ai Toggle
scite Smart Citations _(What are Smart Citations?)_
Code, Data, Media
# Code, Data and Media Associated with this Article
alphaXiv Toggle
alphaXiv _(What is alphaXiv?)_
Links to Code Toggle
CatalyzeX Code Finder for Papers _(What is CatalyzeX?)_
DagsHub Toggle
DagsHub _(What is DagsHub?)_
GotitPub Toggle
Gotit.pub _(What is GotitPub?)_
Huggingface Toggle
Hugging Face _(What is Huggingface?)_
Links to Code Toggle
Papers with Code _(What is Papers with Code?)_
ScienceCast Toggle
ScienceCast _(What is ScienceCast?)_
Demos
# Demos
Replicate Toggle
Replicate _(What is Replicate?)_
Spaces Toggle
Hugging Face Spaces _(What is Spaces?)_
Spaces Toggle
TXYZ.AI _(What is TXYZ.AI?)_
Related Papers
# Recommenders and Search Tools
Link to Influence Flower
Influence Flower _(What are Influence Flowers?)_
Core recommender toggle
CORE Recommender _(What is CORE?)_
  * Author
  * Venue
  * Institution
  * Topic

About arXivLabs 
# arXivLabs: experimental projects with community collaborators
arXivLabs is a framework that allows collaborators to develop and share new arXiv features directly on our website.
Both individuals and organizations that work with arXivLabs have embraced and accepted our values of openness, community, excellence, and user data privacy. arXiv is committed to these values and only works with partners that adhere to them.
Have an idea for a project that will add value for arXiv's community? **Learn more about arXivLabs**.
Which authors of this paper are endorsers? | Disable MathJax>) (What is MathJax?) 
