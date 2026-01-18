---
title: "Examples & Use Cases"
source: https://run.house/examples/fine-tune-llama-3-with-lora
date: unknown
description: "Explore a variety of ways to use Kubetorch for common ML workloads on your own Kubernetes clusters."
word_count: 656
---

Opens in a new window Opens an external website Opens an external website in a new window
Close this dialog
This website utilizes technologies such as cookies to enable essential site functionality, as well as for analytics, personalization, and targeted advertising. To learn more, view the following link:  Privacy Policy
Close Cookie Preferences
  * Kubetorch Examples
  * Hello, World
    * Training: PyTorch DDP
    * Inference: vLLM
  * Training
    * MNIST Torchvision
    * Automated Re-Training (Airflow)
    * Supervised Fine Tuning (Llama3)
    * Ray (Tune - HPO)
    * Ray (Train, Data - DLRM)
    * Lightning (ImageNet)
    * TensorFlow
    * XGBoost on GPU
    * Pytorch DDP (Resnet)
  * Fault Tolerance
    * Dynamic Training Rescaling
    * Training Pod Preemption Recovery
    * Find Batch Size
    * Fail to Larger Compute
  * Reinforcement Learning
    * Async GRPO 
    * Basic Sync GRPO 
    * VERL Training
    * TRL with a Code Sandbox
    * Evaluation Sandboxes
  * Inference
    * DeepSeek - vLLM
    * OpenAI OSS - Transformers
    * Triton Inference Server
    * Batch Embeddings
    * RAG App (Composite AI System)

# Kubetorch Examples
Kubetorch streamlines machine learning workloads on Kubernetes by eliminating the traditional barriers between research and production. It provides a unified Python-first interface that scales seamlessly from local development to production clusters.
Our examples demonstrate how Kubetorch can be used in the ML development lifecycle across different use cases. If you'd like to see specific examples not covered here, feel free to send us a ping. 
## Training
In the research and development phase, Kubetorch enables fast, <2 second iteration loops for your code updates, at any scale. Even if you aren't working on extremely large models, the ability to scale to multi-node is extremely valuable for speeding up training via data parallelism or parallelized hyper-parameter optimization.
There is also no gap between research and reproducible production training. The code that you ran locally slots in as-is into CI or orchestrators (or whatever "production" is), compared to the standard multi-week process translating a research notebook into Airflow and Docker.
## Fault-Tolerance
Kubetorch gives you direct programmatic control over compute and execution, and preserves the execution environment in the face of a fault, making it easy for you to create control flows that overcome common errors like node preemption and CUDA OOM errors. This eliminates the manual intervention and over-provisioning typical in traditional approaches.
## Reinforcement Learning
RL workloads require heterogeneous compute and images for training, inference, and evaluation components. Existing frameworks struggle with compute heterogeneity (Slurm) or image heterogeneity (Ray). Kubetorch lets you define component-specific resource allocation (image, compute, distribution type) and deploy them to Kubernetes, and can be directly orchestrated (asynchronously) from a single driver.
## Inference / Batch Processing
Kubetorch enables a range of online and offline inference mechanisms, with a simple-to-use API for Pythonic deployment with features like autoscaling and scale-to-zero built in. As with training, Kubetorch provides 2-second iteration cycles for inference services, replacing the 15-30 minute redeploy cycles of YAML-based approaches. For composite inference applications like RAG, teams can independently and quickly iterate on each component via identical-to-production services.
## Installation
We are currently under a private beta. If you are interested in trying it out, shoot us a quick note at team@run.house**,** and we will share the required deployment resources with you.
### Hello, World
  * Training: PyTorch DDP
  * Inference: vLLM

### Training
  * MNIST Torchvision
  * Automated Re-Training (Airflow)
  * Supervised Fine Tuning (Llama3)
  * Ray (Tune - HPO)
  * Ray (Train, Data - DLRM)
  * Lightning (ImageNet)
  * TensorFlow
  * XGBoost on GPU
  * Pytorch DDP (Resnet)

### Fault Tolerance
  * Dynamic Training Rescaling
  * Training Pod Preemption Recovery
  * Find Batch Size
  * Fail to Larger Compute

### Reinforcement Learning
  * Async GRPO 
  * Basic Sync GRPO 
  * VERL Training
  * TRL with a Code Sandbox
  * Evaluation Sandboxes

### Inference
  * DeepSeek - vLLM
  * OpenAI OSS - Transformers
  * Triton Inference Server
  * Batch Embeddings
  * RAG App (Composite AI System)

