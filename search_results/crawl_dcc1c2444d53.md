---
title: "Fine-tuning | How-to guides"
source: https://www.llama.com/docs/how-to-guides/fine-tuning
date: unknown
description: "Learn how to fine-tune Llama models using various methods, including LoRA, QLoRA, and reinforcement learning, to improve performance on specific tasks and adapt to domain-specific knowledge. Fine-tune"
word_count: 2905
---

### 
Table Of Contents
Overview
ModelsLlama 4Llama Guard 4Llama 3.3Llama 3.2Llama 3.1Llama Guard 3Llama Prompt Guard 2Other models
Getting the ModelsMetaHugging FaceKaggle1B/3B Partners405B Partners
Running LlamaLinuxWindowsMacCloud
Deployment (New)Private cloud deploymentProduction deployment pipelinesInfrastructure migrationVersioningAccelerator managementAutoscalingRegulated industry self-hostingSecurity in productionCost projection and optimizationComparing costsA/B testing
How-To GuidesPrompt Engineering (Updated)Fine-tuning (Updated)Quantization (Updated)Distillation (New)Evaluations (New)ValidationVision CapabilitiesResponsible Use
Integration GuidesLangChainLlamalndex
Community SupportResources
Overview
ModelsLlama 4Llama Guard 4Llama 3.3Llama 3.2Llama 3.1Llama Guard 3Llama Prompt Guard 2Other models
Getting the ModelsMetaHugging FaceKaggle1B/3B Partners405B Partners
Running LlamaLinuxWindowsMacCloud
Deployment (New)Private cloud deploymentProduction deployment pipelinesInfrastructure migrationVersioningAccelerator managementAutoscalingRegulated industry self-hostingSecurity in productionCost projection and optimizationComparing costsA/B testing
How-To GuidesPrompt Engineering (Updated)Fine-tuning (Updated)Quantization (Updated)Distillation (New)Evaluations (New)ValidationVision CapabilitiesResponsible Use
Integration GuidesLangChainLlamalndex
Community SupportResources
How-to guides
# Fine-tuning
## What is fine-tuning?
Fine tuning enables you to take a pre-trained model and adapt it to perform better for a specific use case by training it on your own data. Large language models like Llama are trained on enormous quantities of text and other data. However, despite such massive datasets, there are often gaps in the model's knowledge. For example, the model may have never seen specific data that is normally locked behind a paywall, or private data internal to your company or organization. In other cases, the model may have seen relevant data but not enough of it.
While you can control the base model using instructions and examples in your prompts, this approach limits the number of examples you can provide due to the model's max context window, potentially resulting in inconsistent outputs when the model has not seen enough data. By fine-tuning, you train Llama directly on larger, task-specific datasets, eliminating the need for repeated examples in prompts and enhancing the model's consistency and accuracy. Because Llama is an open-weight model, you have full control over the fine-tuning process compared to API-only offerings.
Fine-tuning offers several advantages over using the base model directly:
  * **Reduced token usage** : When using a base model, you will need to include instructions in the form of a prompt as well as potentially examples of how to perform the task. Fine-tuning allows you to "bake in" the instructions and examples into the model itself, reducing the number of tokens needed to generate a response. Once fine-tuned, a model will not need a lengthy prompt or in-context examples for the task it has been tuned for, greatly reducing the input tokens required.
  * **Domain-specific knowledge** : By providing an additional dataset, you can teach the model knowledge specific to your use case. This might include data that was not included in the original training set (e.g., unique words, phrases, terms).
  * **Improved performance** : Few-shot prompting requires providing multiple examples with each request. The number of examples you can provide is limited by the input context window of the model. With fine-tuning, you can provide far more examples than can be included in a prompt alone, allowing the model to learn from more data and achieve better performance on your specific task.

## When to fine-tune Llama
Fine-tuning is a powerful technique that improves model performance on specific tasks. However, it requires an investment in data collection and evaluation to use effectively. Before fine-tuning a model you may want to try some other techniques to improve performance on your task such as
  * Prompt optimization
  * Few-shot learning
  * Using a more powerful model
  * Using tool calling or RAG

If these techniques don't provide the quality you need, or if you achieve your desired quality but with increased latency or cost, fine-tuning may be the right approach. We recommend fine-tuning when you need to:
  * Improve the model's performance on a specific task beyond what's possible with prompting (e.g., for a specific use case)
  * Add domain-specific knowledge to the model (e.g., for a specific industry)
  * Reduce the number of tokens needed to generate a response (e.g., for cost savings)
  * Reduce the latency of the model's responses (e.g., for real-time applications)
  * Modify the model to output in a specific non-JSON format, such as YAML.

Conversely, there are situations where fine-tuning may not be the best solution:
  * If you specifically need JSON formatted output, use **structured output**. For other output formats, consider outputting JSON and then using a simple tool or library to convert it to the structure you need.
  * For factual accuracy or up-to-date knowledge, consider using **tool calling** to integrate knowledge from other data sources.

## Fine-tuning overview
Fine-tuning works by training an existing pre-trained model on a specific task with your own data. The process works as follows:
  1. Prepare a dataset of examples that demonstrate your specific task
  2. Take an existing pre-trained model and train it on your dataset
  3. Evaluate the new fine-tuned model on a separate testing dataset
  4. Use the new fine-tuned model for inference

#### How much data is needed?
Generally, you can get started with as few as a couple dozen examples, but a good starting point is at least 50, with an eventual goal of 100-200 examples. For fine-tuning, data quality is more important than quantity. Your data should be both accurate and as diverse as possible.
In some cases, you may be able to generate "synthetic" data using another LLM instead of curating the dataset by hand. Meta offers a synthetic data toolkit for this technique, which is explored further in the distillation guide.
## Fine-tuning methods
### Full parameter fine-tuning
The most straightforward way to fine-tune a model is to fine-tune all of the model's parameters. The full model is loaded into memory and the weights are adjusted by showing the model example sentences. However, this method has trade-offs:
  * While the model is not trained for as long in fine-tuning, it still requires large amounts of VRAM (as much as the pre-training) and substantial compute.
  * Fine-tuning the entire model can risk overfitting to the training data, and consequently, the model may forget knowledge or become worse on tasks not specifically included in the fine-tuning training data.

### Parameter efficient fine-tuning
Parameter efficient fine-tuning (PEFT) is a technique that allows you to fine-tune only a subset of the model's parameters, both reducing the amount of compute required for each example as well as reducing the VRAM required since gradients are only held in memory for the fine-tuned parameters.
There are two important PEFT methods: LoRA (Low Rank Adaptation) and QLoRA (Quantized LoRA), both of which are explored in more detail below. With a consumer-grade GPU, you can fine-tune a Llama 3.1 8B model with LoRA, and a Llama 3.3 70B model with QLoRA.
Typically, you should try LoRA, or if resources are extremely limited, QLoRA, first, and after the fine-tuning is done, evaluate the performance. Only consider full fine-tuning when the performance is not desirable.
#### Low-rank adaptation (LoRA)
LoRA#Low-rank_adaptation>) is a PEFT technique that reduces the number of trainable parameters by adding low-rank matrices to the network and only training those. LoRA results in a significant reduction in the number of trainable parameters, making it more efficient in terms of both compute and memory requirements.
The advantages of LoRA include:
  * **Reduced memory requirements** : LoRA requires less memory to store the gradients and optimizer states, as only the low-rank matrices need to be updated.
  * **Faster training** : LoRA reduces the computational cost of fine-tuning, as the number of operations required to update the model's weights is significantly reduced.
  * **Improved performance** : LoRA can achieve comparable or even better performance than full fine-tuning, as the low-rank matrices can effectively capture the task-specific knowledge without losing the original model's knowledge.

The disadvantage is that LoRA may struggle to fully adapt the model to tasks that require large changes in the internal representations, for example for substantially different domains or complex reasoning tasks.
#### Quantized low-rank adaptation (QLoRA)
QLoRA is an extension of LoRA that further reduces the memory requirements by quantizing the model's weights and activations. QLoRA uses a combination of quantization and low-rank adaptation to achieve even more efficient fine-tuning.
The advantages of QLoRA include:
  * **Even lower memory requirements** : QLoRA reduces the memory requirements even further by quantizing the model's weights and activations, making it possible to fine-tune larger models on limited hardware.
  * **Increased efficiency** : QLoRA achieves a better trade-off between memory requirements and performance, making it an attractive option for fine-tuning large models on resource-constrained devices.

Compared to LoRA, QLoRA may result in lower model quality because the quantization applied will result in lower numerical precision. You can read more about how quantization works and the trade-offs in our quantization guide.
### Reinforcement learning from human feedback (RLHF)
Sometimes it's difficult to specify the exact "correct answer" you want from a model. For example, teaching a model to align with human values, or to make nuanced decisions. In these cases, it may be infeasible to collect a dataset of examples that are "correct" for all possible inputs.
Unlike the previous methods, RLHF does not attempt to fine-tune the model to match a specific "correct" output. Instead, it is a mechanism for guiding the model to produce outputs that are preferred by a human. Since it's much easier for a human to specify a preference than write the "correct" output, RLHF enables much larger datasets to be used for post-training and improves output quality.
The RLHF process typically involves the following steps:
  1. Collecting human feedback: Human evaluators are asked to rate or compare the outputs of the language model based on certain criteria, such as coherence, relevance, or overall quality.
  2. Training a reward model: The human feedback is used to train a reward model that predicts the human ratings or preferences. This reward model is typically a smaller model that is trained to capture the patterns and preferences expressed by the human evaluators.
  3. Fine-tuning the language model: The language model is fine-tuned using reinforcement learning, with the reward model providing the reward signal. The goal is to maximize the expected cumulative reward, which corresponds to generating outputs that are preferred by humans.

As RLHF has become an essential part of the post-training process, many libraries have added support for it. Some libraries also offer support for direct-preference optimization (DPO), which is a variant of preference tuning that does not require reinforcement learning and instead frames the problem as a supervised learning task.
### Reinforcement learning from verifiable rewards (RLVR)
While RLHF is a powerful technique, it requires a significant amount of human feedback to be effective. Instead of using a human-provided reward signal, RLVR uses objective, verifiable rewards to guide the model's behavior. This enables models to excel especially on logic, mathematical, or coding tasks. For example, RLVR can be used to train a model to write code by providing a reward when the written code passes automated tests. No human input is required; instead, the model is guided by a verification process (running the tests) that is automatic and fast.
While RLVR is a new technique, many libraries already offer reinforcement-learning support and thus can be use to fine-tune Llama with RLVR with only minor changes. Examples of supported RL algorithms include Proximal Policy Optimization (PPO) and Group Relative Policy Optimization (GRPO).
## How to approach fine-tuning
In general, you should use **LoRA** as the go-to first option for fine-tuning. For the most common tasks this will be the best trade-off of training requirements and resulting model quality. There are cases where LoRA may not be the best option. For example:
  1. If you need to incorporate external knowledge or tools, try **tool calling or RAG** instead.
  2. If you need to align the model to human preferences or logic/math tasks, use a **reinforcement learning method**.
  3. If you have substantial compute resources available and need to make major modifications to the base model, use **full fine-tuning**.
  4. If you have extremely limited compute, use **QLoRA**.

The table below summarizes the specific methods.
Method| Best Whenâ€¦  
---|---  
Full| You have compute and need total control or major language/domain shift  
LoRA| You have moderate resources and want efficient tuning  
QLoRA| You have limited resources (low VRAM) and want to fine-tune large models  
RLHF| You want to match output to human preferences  
RLVR| You want to optimize based on verifier or bespoke reward models  
## Experiment tracking
When evaluating different fine-tuning methods, experiment tracking ensures reproducibility, maintains a structured version history, allows for easy collaboration, and aids in identifying optimal training configurations. 
With different combinations of iterations, hyperparameters, and model versions to try, tools like Weights & Biases (W&B) become indispensable. With its seamless integration into multiple frameworks, W&B provides a comprehensive dashboard to visualize metrics, compare runs, and manage model checkpoints. 
Making use of W&B is often as simple as adding a single argument to your training script. See Llama Cookbook for a fine-tuning example that integrates with W&B.
## Fine-tuning libraries
There are a variety of both Meta maintained and third-party libraries for fine-tuning Llama.
### Managed fine-tuning
The easiest way to fine-tune a model is to use a managed fine-tuning service, which does not require you to manage any infrastructure or tune any hyperparameters. 
Meta offers a managed fine-tuning service for Llama, which is available as part of Llama API. This service abstracts most of the fine-tuning process, requiring you only to provide a dataset for training and evaluation. After your model has been fine-tuned, you can download the fine-tuned model and run it on your own hardware or infrastructure.
### PyTorch torchtune
PyTorch's torchtune library provides first-party support for a wide variety of fine-tuning methods, including full fine-tuning, PEFT methods like LoRA and QLoRA, and reinforcement learning methods like RLHF and RLVR. It also includes out-of-the-box support for the most popular open source models, including Llama. 
torchtune supports the end-to-end fine-tuning lifecycle including:
  * Downloading model checkpoints and datasets
  * Training recipes for fine-tuning Llama3 using full fine-tuning, LoRA, and QLoRA
  * Support for single-GPU fine-tuning capable of running on consumer-grade GPUs with24GB of VRAM
  * Scaling fine-tuning to multiple GPUs using PyTorch FSDP
  * Log metrics and model checkpoints during training using Weights & Biases
  * Evaluation of fine-tuned models using EleutherAI’s LM Evaluation Harness
  * Post-training quantization of fine-tuned models via TorchAO
  * Interoperability with inference engines including ExecuTorch

To install torchtune simply run the pip install command
```
pip install torchtune

```

Follow the instructions on the Hugging Face meta-llama repository to ensure you have access to Llama model weights. Once you have confirmed access, you can run the following command to download the weights to your local machine. This will also download the tokenizer model and a responsible use guide.
```
tune download meta-llama/Meta-Llama-3-8B \
 --output-dir <checkpoint_dir> \
 --hf-token <ACCESS TOKEN>

```

Set your environment variable HF_TOKEN or pass in --hf-token to the command in order to validate your access. You can find your token at https://huggingface.co/settings/tokens
The basic command for a single-device LoRA fine-tune of Llama3 is
```
tune run lora_finetune_single_device --config llama3/8B_lora_single_device

```

torchtune contains built-in recipes for:
  * Full fine-tuning on single device and on multiple devices with FSDP
  * LoRA fine-tuning on single device and on multiple devices with FSDP.
  * QLoRA fine-tuning on single device, with a QLoRA specific configuration

You can find more information on fine-tuning Llama models by reading the torchtune Getting Started guide.
### Third party libraries
#### Hugging Face peft
The Hugging Face `peft`[](https://www.llama.com/docs/how-to-guides/<https:/github.com/huggingface/peft>) library provides support for a wide variety of PEFT methods, including LoRA and QLoRA. It also includes out-of-the-box support for the most popular open source models, including Llama. If you are already using the `Transformers` library, you can use peft to fine-tune models with only minor changes.
The Llama Cookbook repo highlights the use of PEFT as a recommended fine-tuning method, as it reduces the hardware requirements and prevents catastrophic forgetting. For specific cases, full parameter fine-tuning can still be valid, and different strategies can be used to still prevent modifying the model too much. Additionally, fine-tuning can be done in single gpu or multi-gpu with FSDP.
In order to run the recipes, follow the steps below:
  1. Create a conda environment with pytorch and additional dependencies
  2. Install `llama-cookbook` from PyPI: 
  3. Download the desired model from hf, either using git-lfs or using the llama download script. 
  4. With everything configured, run the following command:

```
python -m llama_recipes.finetuning \
 --use_peft --peft_method lora --quantization \
 --model_name ../llama/models_hf/7B \
 --output_dir ../llama/models_ft/7B-peft \
 --batch_size_training2 --gradient_accumulation_steps2

```

#### Axolotl
Axolotl provides fine-tuning support for Llama and includes support for RLHF and RLVR. It also includes support for a variety of PEFT methods, including LoRA and QLoRA. The library is designed to be easily configurable and scalable, with support for distributed training and cloud-ready training.
#### Unsloth
Unsloth is a fine-tuning library that includes fused Triton kernels, aiming to accelerate fine-tuning on consumer GPUs. It has wide support for transformer-based models, including text-to-speech models and diffusion models. It supports Llama with PEFT methods like LoRA and QLoRA.
## Additional resources
  * **Fine-tuning guide** : For a complete guide showing how to fine-tune Llama models using torchtune, see the fine-tuning tutorial.

Was this page helpful?
Yes
No
On this page
Fine-tuning
What is fine-tuning?
When to fine-tune Llama
Fine-tuning overview
Fine-tuning methods
Full parameter fine-tuning
Parameter efficient fine-tuning
Reinforcement learning from human feedback (RLHF)>)
Reinforcement learning from verifiable rewards (RLVR)>)
How to approach fine-tuning
Experiment tracking
Fine-tuning libraries
Managed fine-tuning
PyTorch torchtune
Third party libraries
Additional resources
