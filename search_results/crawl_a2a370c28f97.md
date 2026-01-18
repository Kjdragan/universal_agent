---
title: "How to Fine-Tune LLaMA 3 for Customer Support Tasks"
source: https://predibase.com/blog/tutorial-how-to-fine-tune-and-serve-llama-3-for-automated-customer-support
date: 2024-04-30
description: "Step-by-step guide to fine-tuning Llama 3 8B for automated customer support: Learn how to train Llama-3 Instruct on your data, optimize classification prompts, and adapt from pre-training. Includes co"
word_count: 2116
---

Introducing Agent Cloud from Rubrik + Predibase.Learn More
# How to Fine-Tune LLaMA 3 for Customer Support Tasks
April 30, 2024 · 5 min read
!Blog Llama 3 fine-tune
!Chloe LeungChloe Leung](https://predibase.com/blog/</author/chloe-leung>)
[](https://predibase.com/blog/<https:/twitter.com/intent/tweet/?text=How%20to%20Fine-Tune%20LLaMA%203%20for%20Customer%20Support%20Tasks&url=https://predibase.com/blog/tutorial-how-to-fine-tune-and-serve-llama-3-for-automated-customer-support>)[](https://predibase.com/blog/<https:/www.facebook.com/sharer/sharer.php?u=https://predibase.com/blog/tutorial-how-to-fine-tune-and-serve-llama-3-for-automated-customer-support>)[](https://predibase.com/blog/<https:/www.linkedin.com/shareArticle?mini=true&url=https://predibase.com/blog/tutorial-how-to-fine-tune-and-serve-llama-3-for-automated-customer-support>)
Meta Llama 3 is the next generation of state-of-the-art open-source LLM and is now available on Predibase for fine-tuning and inference—try it for free with _$25 in free credits_. 
In this tutorial, we provide a detailed walkthrough of fine-tuning and serving Llama 3 for a customer support use case using Predibase’s new fine-tuning stack. You’ll learn how to easily and efficiently fine-tune and serve open-source LLMs that perform on par with much larger commercial models for task specific use cases. 
Fine-tuning LLaMA 3 models, especially LLaMA-3 Instruct and Meta-LLaMA-3-8B, unlocks powerful capabilities for domain-specific tasks like automated customer support. Whether you're working with classification prompts, fine-tuning the LLaMA 3 base model, or handling reserved special tokens, this guide will walk you through the process step-by-step.
_Colab Notebook Tutorial: Fine-tuning Llama-3 for Customer Support_
## Fine-tuning Use Case: Automating Customer Support 
Customer support such as AI virtual assistant presents significant potential for LLM automation. In this specific scenario, our dataset comprises complaints directed to a financial institution regarding individual products. Based on the original customer complaint narratives, the model is expected to perform a few tasks:
  * Intent classification: Classify both the relevant **Product** and **Issue** raised by the customer
  * Text generation: **Generate company response** in a polite email format to the complaint

Eventually, we want the LLM to generate a structured JSON output that contains the Product, Issue and Company Response.
## Environment Setup
The first step is to sign into Predibase, we need to install the Python SDK and use the API token:
```
pip install predibase
from predibase import Predibase, FinetuningConfig, DeploymentConfig
pb = Predibase(api_token = "{Insert API Token}")
```

Copy
Signing into Predibase and installing the Python SDK
## Dataset Preparation
Predibase supports instruction fine-tuning and we must structure your dataset to follow **_“prompt”_** and **_“completion”_** format:
  * prompt: the fully materialized input to the model with the prompt template
  * completion: the desired response

(Please refer to the documentation for more details on data preparation: _https://docs.predibase.com/user-guide/fine-tuning/prepare-data_)
### Prompt
We construct the prompt to give instructions to the Llama-3 LLM, and insert the value of the raw complaint narrative into the prompt for each row, as indicated by the notation: _Complaint: {Complaint}_.
```
prompt = """You are a support agent for a public financial company and a customer has raised a complaint.
Generate a structured JSON output with the following fields "product", "issue", and "generatedCompanyResponse".
Here is an example structure:
{{
 "product": "...",
 "issue": "...",
 "generatedCompanyResponse": "..."
}}
The value for "generatedCompanyResponse" should be a polite response to the following complaint.
### Complaint: {Complaint}
### Structured JSON Output:
"""
```

Copy
Input Prompt
### Dataset Overview
  * Raw dataset: _link_
  * Processed dataset: _link_

The dataset used for fine-tuning consists of 1,500 rows and ~1.5M tokens:

!With Predibase we visualize our fine-tuning dataset for customer complaints
With Predibase we visualize our fine-tuning dataset for customer complaints
## Benchmarking with Llama 3 Base Model
For benchmarking, we can prompt the Llama-3-8B model via serverless endpoints with a sample prompt:
```
sample_prompt = """You are a support agent for a public financial company and a customer has raised a complaint.
Generate a structured JSON output with the following fields "product", "issue", and "generatedCompanyResponse".
Here is an example structure:
{{
 "product": "...",
 "issue": "...",
 "generatedCompanyResponse": "..."
}}
The value for "generatedCompanyResponse" should be a polite response to the following complaint.
### Complaint: Been receiving multiple calls per day from XXXX, which leaves a prerecorded message to call regarding a debt. It also gives an option to
speak to a representative. I opted to do this to find out what this is about. The operator asked for a relative, who has never lived with
us yet our number was the one they had on file. I told them that the person they seek does not live here and asked to be removed from their
caller list. The operator said he could not do this without a new number being given. He also said they are not required to stop calling.

This is simply harassment by proxy.
### Structured JSON Output:
"""
```

Copy
```
lorax_client = pb.deployments.client("llama-3-8b")
print(lorax_client.generate(sample_prompt).generated_text)
```

Copy
Prompting base Llama 3 generates a response as follows:
```
{{
 "product": "Financial Services",
 "issue": "Harassment by proxy",
 "generatedCompanyResponse": "We are sorry to hear about your experience. We take these matters very seriously and will look into this immediately. We will also make sure that this does not happen again. Thank you for bringing this to our attention."
}}
```

Copy
Base model response
We can also prompt a base model from the Predibase UI:

!Prompting Llama-3 from the Predibase UI
Prompting Llama-3 from the Predibase UI
The out-of-the-box Llama 3 model doesn’t perform well in this task although it does generate a response in JSON format. The related product is debt collection, and the issue raised is false representation of the customer. The model fails to correctly classify the product or issue, nor does it generate the response in an email format.
## Efficiently and Easily Fine-tune Llama 3
Let’s see if fine-tuning can do the magic for us. There’re a few steps to create a fine-tuned model (“adapter”) on Predibase:
**Dataset Upload**
```
# Upload a dataset
dataset = pb.datasets.from_file("dataset_path", name="dataset name")
```

Copy
**Create an Adapter Repo**
```
# Create an adapter repository
repo = pb.repos.create(name="repo name", description="repo description", exists_ok=True)
```

Copy
### Kick-off a Llama 3 Fine-tuning Job with Predibase
Epochs, adapter rank and learning rate are hyperparameters available to configure. We’re using the default setting here:
```
# Create an adapter
adapter = pb.adapters.create(
  config=FinetuningConfig(
    base_model="llama-3-8b",
    epochs=3,
    rank=16,
    learning_rate=0.0002,
  ),
  dataset=dataset,
  repo=repo,
  description="adapter description"
)
```

Copy
Once the job is kicked off, we can see the live training progress including checkpoints and corresponding metrics (loss):

!Model training metrics in Predibase
Model training metrics in Predibase

!Tracking Llama-3 fine-tuning job in the Predibase UI
Tracking Llama-3 fine-tuning job in the Predibase UI
Once training is finished, we can get the adapter information:
```
# format: repo_name/version_number
adapter = pb.adapters.get("repo_name/adapter_version")
```

Copy
Retrieving a finished Llama 3 adapter in Predibase
And the same on the Adapters page:

!Viewing our fine-tuned Llama-3 adapter in the Predibase UI
Viewing our fine-tuned Llama-3 adapter in the Predibase UI
## Test Adapter and Run Inference
Once the adapter status is “ready”, we can run inference instantly via serverless endpoints:
```
print(lorax_client.generate(sample_prompt, adapter_id="repo_name/adapter_version").generated_text)
```

Copy
You can see that our fine-tuned Llama 3 adapter generates a much better customer service response compared to the base model:
```
{
"product": "Debt collection",
"issue": "False statements or representation",
"generatedCompanyResponse": "Dear [Customer],
Thank you for bringing this matter to our attention. We apologize for any inconvenience you have experienced regarding the calls you have been receiving from XXXX. We understand that receiving multiple calls per day can be frustrating, especially when they are related to a debt.
We would like to assure you that we take your concerns seriously and will investigate this matter further. We will reach out to XXXX to address the issue of the incorrect number being on file and the lack of options to be removed from their caller list. Additionally, we will review their compliance with the regulations regarding harassment by proxy.
Please be assured that we will do everything in our power to resolve this matter promptly and to your satisfaction. We appreciate your patience and understanding as we work towards a resolution.
If you have any additional information or concerns, please do not hesitate to contact us. We are here to assist you and ensure that your rights are protected.
Thank you for bringing this to our attention.
Sincerely,
[Your Name]
[Company Name]"
}
```

Copy
Improved response from our fine-tuned Llama-3 model
and here is that same response within the Predibase UI:

!Response from our fine-tuned Llama-3 adapter in the UI 
Response from our fine-tuned Llama-3 adapter in the UI 
We see that the fine-tuned adapter successfully completes the task by automatically and correctly classifying the product and issues that were raised in the complaint and drafting a high-quality email response to the customer.
We also offer options to run inference on dedicated deployments; please refer to the documentation for more details: _https://docs.predibase.com/user-guide/inference/dedicated_deployments_.
## Start Fine-tuning Llama 3 on Your Own Use Case
Congratulations on completing the end-to-end tutorial where you fine-tuned Llama 3 to automate customer support routing and generate immediate email response.
Predibase’s low-code capabilities in fine-tuning and serving LLMs offers unparalleled benefits:
  * **Enhanced efficiency:** up to 10x faster training which significantly reduces training latency
  * **Real-time Inference:** instant access to the latest Llama 3 model via high-throughput serverless endpoints so you can easily build production applications with the latest models
  * **Cost-effective:** extremely affordable while maintaining scalability & performance, fine-tuning on Llama 3 with ~1.5M tokens only cost $1

**Get started fine-tuning your own LLMs on Predibase with $25 in free credits:**<https://predibase.com/free-trial>.
## **Frequently Asked Questions**
### **How do I fine-tune Llama-3 Instruct for customer support tasks?**
To fine-tune Llama-3 Instruct for customer support, format your dataset with examples of user queries and ideal agent responses. Use Predibase’s LoRA adapters to optimize for domain-specific language while retaining the model’s general knowledge. 
### **How to train Llama 3 on your own data?**
Training Llama 3 on custom data requires a structured dataset (e.g., JSONL), parameter-efficient methods like LoRA, and tools like Predibase to automate GPU provisioning and hyperparameter tuning. Follow our step-by-step tutorial above to deploy your model
### **What’s the difference between pretraining and fine-tuning Llama-3 Instruct?**
Pretraining involves training Llama-3 from scratch on vast text corpora, while fine-tuning adapts the pretrained model to specific tasks (e.g., customer support) using smaller, domain-specific datasets. Predibase simplifies fine-tuning without altering the base model’s core capabilities.
### **What’s the best way to fine-tune Llama 3 8B?**
The Llama 3 8B model excels in customer support when fine-tuned with LoRA adapters and a curated dataset of 500+ support dialogues. Predibase’s automated hyperparameter optimization ensures faster convergence and higher accuracy compared to manual setups.
### **What is the recommended prompt format for Llama 3?**
The recommended prompt format for Llama 3 includes:
  * **System Message** : Sets the context or behavior of the model.
  * **User Message** : The input or query from the user.
  * **Assistant Message** : The model's response.

Each message is enclosed within special tokens to define roles and structure. Llama 3 uses a new tokenizer with a 128K vocabulary, so legacy Llama 2 special tokens (e.g., `[INST]`) aren’t required. Instead, leverage Llama 3’s native `<|begin_of_text|>` and `<|end_of_text|>` tokens for structured prompts.
### **What’s the best model to fine-tune—LLaMA 3 Instruct or Base?**
Use **LLaMA 3 Instruct** for natural language tasks. Use **LLaMA 3 Base** for complete customization when pretraining on structured prompts or special formats.
### **Can I fine-tune Meta-LLaMA-3-8B for classification?**
Yes! You can fine-tune Meta-LLaMA-3-8B for classification prompts, dialogue generation, summarization, or custom workflows.
### **How is LLaMA 3 fine-tuning different from LLaMA 2?**
LLaMA 3 uses a different tokenizer and prompt format. It’s generally more instruction-aligned out of the box and requires different treatment of special tokens.
### **What’s the difference between LLaMA 2 and LLaMA 3?**
LLaMA 3 offers major improvements over LLaMA 2 in accuracy, reasoning ability, and instruction-following. It's trained on more diverse data and optimized for longer context windows, making it better suited for real-world use cases like automated customer support.
### **Is LLaMA 3 better for fine-tuning than LLaMA 2?**
Yes. LLaMA 3 introduces architectural improvements that make it more responsive to fine-tuning. Whether you're customizing for domain-specific language or tone in customer support, LLaMA 3 adapts faster and generalizes better than LLaMA 2.
### **How does serving performance compare between LLaMA 2 and LLaMA 3?**
LLaMA 3 is larger and more powerful, which can impact inference speed. However, with the right serving stack (like Predibase’s TurboLoRA + FP8), you can achieve low-latency, cost-efficient serving that outperforms most LLaMA 2 deployments—even at scale.
### **Can I still use LLaMA 2 if I’m just getting started?**
Absolutely. LLaMA 2 remains a solid entry point for lightweight or resource-constrained deployments. But if you're building production-grade AI for customer support, LLaMA 3 will give you better performance, accuracy, and ROI.
## Related Articles 
  * 
## Not Your Average VPC: Secure AI in Your Private Cloud with Direct Ingress
Read Article
  * 
## Real-World LLM Inference Benchmarks: How Predibase Built the Fastest Stack
Read Article
  * 
## Next-Gen Inference Engine for Fine-Tuned SLMs
Read Article

## Join Our Community!
Join now
