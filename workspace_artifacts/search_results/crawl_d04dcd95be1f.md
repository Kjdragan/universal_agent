---
title: "Fine-Tuning Llama 3 with LoRA: Step-by-Step Guide"
source: https://neptune.ai/blog/fine-tuning-llama-3-with-lora
date: unknown
description: "You can apply the key ideas of this 'Google Collab-friendly' approach to many other base models and tasks."
word_count: 4960
---

 **📣 BIG NEWS:** **Neptune is joining OpenAI!** → Read the message from our CEO 📣 
###  TL;DR 

The models of the Llama 3 family are powerful LLMs created by Meta based on an advanced tokenizer and Grouped-query Attention.

Fine-tuning LLMs like Llama 3 is necessary to apply them to novel tasks but is computationally expensive and requires extensive resources.

Low-rank adaptation (LoRA) is a technique to reduce the amount of parameters modified during fine-tuning. LoRA is based on the idea that an LLM’s intrinsic dimension is substantially smaller than the dimension of its tensors, which allows them to be approximated with lower-dimensional ones.

With LoRA and a number of additional optimizations, it is possible to fine-tune a quantized version of Llama3 8B with the limited resources of Google Colab.
Llama 3 is a family of large language models (LLMs) developed by Meta. These models have demonstrated exceptional performance on benchmarks for language modeling, general question answering, code generation, and mathematical reasoning, surpassing recently introduced models such as Google’s Gemini (with its smaller variants named Gemma), Mistral, and Anthropic’s Claude 3.
There are two main versions, Llama 3 8B and Llama 3 70B, which are available as base models as well as in instruction-tuned versions. Due to their superior performance, many data scientists and organizations consider integrating them into their projects and products, especially as Meta provides the Llama models free of charge and permits their commercial use. Everyone is allowed to use and modify the models, although some restrictions apply (the most important one is that you need a special license from Meta if your service has more than 700 million monthly users).
Several critical questions beyond licensing need to be addressed before downstream users can adopt Llama 3. Are they sufficiently effective? What hardware and resources are required to use and train Llama 3 models? Which libraries and training techniques should be employed for efficient and fast results? We will explore these challenges and provide an example of fine-tuning the Llama 3 8B Instruct model utilizing the neptune.ai experiment tracker.
## The Llama 3 architecture
Meta chose a decoder-only transformer architecture for Llama 3. Compared to the previous Llama 2 family, the main innovation is the adoption of Grouped-query Attention (GQA) instead of traditional Multi-head Attention and novel Multi-query Attention (MQA).
GQA, which is also used in the Gemini and Mixtral model family, leads to models with fewer parameters while maintaining the speed of MQA and the informativeness of MHA. Let’s unpack the difference between these three types of attention mechanisms.
### Deep dive: multi-head, multi-query, and grouped-query attention
Self-attention, the key component of transformer-based models, assumes that for every token, we will have the vectors _q_ (Query), _k_ (Key), and _v_ (Value). Together, they form _Q_ , _K_ , and _V_ matrices, respectively. Then, the attention is defined as:
!Grouped-query attention
where the scaling factor _d_ _k_ is the dimension of the vectors.
In Multi-head Attention (MHA), several self-attention “heads” are computed in parallel and concatenated:
!Multi-head
where _head_ _i_ is the _i_ -th attention head, and _W_ _O_ is a trainable matrix (feed-forward layer).
!Multi-head Attention \(MHA\) consists of several independent self-attention heads.Multi-head Attention (MHA) consists of several independent self-attention heads. | Modified based on: source
In MHA, the heads are independent and do not share parameters. Thus, MHA leads to large models. To reduce the number of parameters, Multi-query Attention (MQA) shares keys and values across heads by using the same Key and Value matrices for each query. The intuition behind this is: “I will create the keys and values so that they will provide answers to each query.”
!In Multi-query Attention \(MQA\), the values and keys are shared across attention heads.In Multi-query Attention (MQA), the values and keys are shared across attention heads. | Modified based on: source
While this approach decreases the number of parameters substantially, Multi-query Attention is inefficient for large models and can lead to quality degradation and training instability. In other words, we could say that “it’s hard to create good key and value matrices that provide good answers to multiple queries.”Considering the benefits and drawbacks of MHA and MQA, Joshua Ainslie et al. designed Grouped-query Attention (GQA).
!Grouped-query Attention \(GQA\) is a compromise between Multi-head and Multi-query Attention: A subset of attention heads shares common keys and values.Grouped-query Attention (GQA) is a compromise between Multi-head and Multi-query Attention: A subset of attention heads shares common keys and values. | Modified based on: source
The intuition behind Grouped-query Attention is “if we cannot find such good key and value matrices to answer _all_ queries, we can still find common key and value matrices that work well enough for _small groups_ of queries.”
As the researchers explain in their paper introducing Grouped-query Attention: “[…] MQA can lead to quality degradation. […] We show that uptrained GQA achieves quality close to multi-head attention with comparable speed to MQA.”
!Comparison of attention methods. Multi-head Attention \(left\) has separate values, keys, and queries for each attention head. Multi-query Attention \(right\) shares values and keys across all attention heads. Grouped-query Attention \(center\) shares values and keys for groups of attention heads. This interpolation between Multi-head and Multi-query Attention limits the number of parameters while maintaining reasonable attention performance.Comparison of attention methods. Multi-head Attention (left) has separate values, keys, and queries for each attention head. Multi-query Attention (right) shares values and keys across all attention heads. Grouped-query Attention (center) shares values and keys for groups of attention heads. This interpolation between Multi-head and Multi-query Attention limits the number of parameters while maintaining reasonable attention performance. | Modified based on: source
!Inference time and average performance of T5 Large and XXL models with Multi-head
Attention \(MHA\) and T5-XXL models with Multi-query \(MQA\) and Grouped-query Attention \(GQA\) on several benchmark datasets \(Summarization: CNN/DailyMail, arXiv, PubMed, MediaSum, and MultiNews; Translation: WMT 2014; Question answering: TriviaQA\).
We see that inference times for the T5-XXL model with MQA and GQA are comparable to the smaller T5-Large model with MHA, while there is little difference in the performance of the T5-XXL models with MQA and GQA. Inference time and average performance of T5 Large and XXL models with Multi-head Attention (MHA) and T5-XXL models with Multi-query (MQA) and Grouped-query Attention (GQA) on several benchmark datasets (Summarization: CNN/DailyMail, arXiv, PubMed, MediaSum, and MultiNews; Translation: WMT 2014; Question answering: TriviaQA).We see that inference times for the T5-XXL model with MQA and GQA are comparable to the smaller T5-Large model with MHA, while there is little difference in the performance of the T5-XXL models with MQA and GQA. | Source
### Efficient language encoding through extremely large vocabulary
The Llama 3 family models use a tokenizer with a vocabulary of 128K tokens instead of the 32K tokens used for the previous Llama 2 generation. This expansion helps to encode the language more efficiently and aids the model’s multilingual abilities.However, while a larger tokenizer is one factor that leads to substantially improved model performance, the cost of this improvement is that input and output matrices get larger.
### How did Meta train Llama 3?
The Llama 3 training data is seven times larger than what Meta used for training Llama 2. It includes four times more source code.
For pre-training, Meta combined four types of parallelization, an approach they dubbed “4D parallelism”: data, model, pipeline, and context. This parallelism helped distribute computations across many GPUs efficiently, maximizing their utilization.
The fine-tuning phase happened in an innovative way. The Llama team combined rejection sampling, proximal policy optimization, and direct preference optimization. Meta claims some of the model’s extraordinary abilities come from this stage.
  ](https://neptune.ai/blog/</blog/optimizing-gpu-usage-during-model-training-with-neptune>)
### Llama 3 performance
Llama 3 models show outstanding performance in understanding and generating human language. This is evident from the scores achieved on the Massive Multitask Language Understanding (MMLU) benchmark that evaluates language generation proficiency using a set of scenarios with similar conditions for all tasks, as well as the performance on the General-Purpose Question Answering (GPQA) benchmark.
Llama 3 also demonstrates enhanced coding abilities compared to previous and competing models. This is underscored by the results obtained on the HumanEval benchmark, which focuses on generating code for compiler-driven programming languages.
Last but not least, Llama 3 produces impressive results in mathematical reasoning, beating Gemma, Mistral, and Mixtral on the GSM-8K and MATH benchmarks. Both focus on mathematical reasoning, with GSM-8K emphasizing grade-school level problems and MATH targeting more advanced mathematics.
All benchmark results are summarized in the official Llama 3 model card.
## Hands-on guide: resource-efficient fine-tuning of Llama 3 on Google Colab
Fine-tuning Llama 3 8B is challenging, as it requires considerable computational resources. Based on my personal experience, at least 24 GB VRAM (such as that provided by an NVIDIA RTX 4090) is needed. This is a significant obstacle, as many of us do not have access to such hardware.
In addition to the memory required for loading the model, the training dataset also consumes a considerable amount of memory. We also need space to load a validation dataset to evaluate the model throughout the training process.
In this tutorial, we’ll explore strategies to fine-tune Llama 3 with limited resources. We’ll apply techniques like LoRA and sample packing to make training work within the constraints of Google Colab’s free tier.
💡 You can find the complete tutorial code in this Colab notebook.
### General overview of the task and approach
Nowadays, many businesses have an FAQ page on their website that answers common questions. Nevertheless, customers reach out with individual questions directly – either because the FAQ does not cover them or they did not find a satisfactory answer. Having human customer service agents answer these questions can quickly become expensive, and customer satisfaction is affected negatively if responses take too much time.
In this tutorial, we’ll fine-tune Llama 3 to take the role of a customer service agent’s helper. The model will be able to compare a given question to previously answered questions so that customer service agents can use existing answers to respond to a customer’s inquiry. 
For this, we’ll train the model on a classification task. We’ll provide instructions and a pair of questions and task the model to assess whether the two questions are similar.
We’ll use the Llama 3 8B model, which is sufficient for this task despite being the smallest Llama 3 model. We will use Hugging Face’s TRL library and the Unsloth framework>), which enables highly efficient fine-tuning without consuming excessive GPU memory.
We’ll conduct the following steps:
  * First, we’ll create a training dataset based on the Quora Question Pairs dataset.
  * Next, we’ll load and prepare the model. As supervised fine-tuning is computationally expensive, we will load the model in its BitsandBytes quantized version and employ the LoRA technique to reduce the number of parameters we’ll adapt during training.
  * Then, we’ll perform instruction-based fine-tuning, providing a detailed prompt describing what exactly the model should do.
  * Finally, we’ll thoroughly evaluate the fine-tuned model and compare it against different baselines.

### Technical prerequisites and requirements 
For this tutorial, we will use Google Colab as our working environment, which gives us access to a Nvidia T4 GPU with 15 GB VRAM. All you need for accessing Colab is a free Google account.
We will use neptune.ai to track our model training. Neptune enables seamless integration of our training process with our interface using just a few lines of code, allowing us to monitor the process and results from anywhere.
Please note that this article references a **deprecated version of Neptune**.
For information on the latest version with improved features and functionality, please visit our website.
### Setting up and loading the model 
We begin by installing the necessary dependencies:
```
!pip install "unsloth[colab-new] @ git+https://github.com/unslothai/unsloth.git"
!pip install --no-deps xformers trl peft accelerate bitsandbytes
!pip install neptune
!pip install scikit-learn
```

Copied!
Copy
Next, we’ll load the base model. We’ll use the BitsAndBytes 4-bit quantized version of Llama 3 8B, which reduces the memory footprint significantly while keeping the model’s outstanding performance.
To load the model through Unsloth, we define the parameters and use the FastLanguageModel.from_pretrained() classmethod:
```
model_parameters = {
  'model_name' : 'unsloth/llama-3-8b-bnb-4bit',
  'model_dtype' : None ,
  'model_load_in_4bit' : True
}
from unsloth import FastLanguageModel
import torch
model, tokenizer = FastLanguageModel.from_pretrained(
  model_name = model_parameters['model_name'],
  max_seq_length = model_parameters['model_max_seq_length'],
  dtype = model_parameters['model_dtype'],
  load_in_4bit = model_parameters['model_load_in_4bit'],
)
```

Copied!
Copy
### Reducing resource consumption through LoRA
#### What is LoRA?
Fine-tuning an LLM requires loading billions of parameters and training data into memory and iteratively updating each parameter through a sequence of GPU operations.
LoRA (Low-Rank Adaptation) is a fine-tuning technique that allows us to fine-tune an LLM, changing significantly fewer parameters than the original LLM. Its creators were inspired by the theory of LLMs’ intrinsic dimension. The theory posits that during the adaptation to a specific task, LLMs possess a low “intrinsic dimension” – in other words, LLMs only use a subset of parameters for a specific task and could thus be represented by a projection to a lower-dimensional space without loss of performance.
Building on this concept and low-rank decomposition of matrices, Edward J. Hu et al. proposed the LoRA fine-tuning method. They suggest we can efficiently adapt LLMs to specific tasks by adding low-rank matrices to the pre-trained weights (rather than modifying the pre-trained weights).
Mathematically, we will represent the weights of the fine-tuned model as:
W = (W0 + ∆W) = (W0 + BA)
where _W_ _0_ are the original weights and A and B are matrices whose product has the same dimension as _W_ _0_. During backpropagation, we update only the smaller _B_ and _A_ matrices and leave the original _W_ _0_ matrix untouched.
To understand how this leads to a reduction in the number of parameters we need to update despite _dim(W_ _0_ _) = dim(BA)_ , let’s take a look at the following visualization:
!Reducing resource consumption through LoRA
Here, _r << m, n _is the rank of the approximation. While there are m * n entries in the original weight matrix _W_ _0_ , the lower-rank approximation only requires _m * r + r * n_ entries. If, for example, _m_ = 500, _n_ = 500, and _r_ = 2, this means we need to update only 2,000 instead of 250,000 parameters.
If the forward pass of the original pre-trained LLM is:
foriginal(x) = W0 * x,
the fine-tuned LLM’s forward pass can be written as:
flora(x) = W0 * x + (α/r)*∆W*x = W0*x + (α/r)*BAx
where _x_ is the input sequence and:
  * _W_ _0_ is the original pre-trained weight matrix,
  * _∆W_ is the fine-tuned correction,
  * _B_ and _A_ represent a low-rank decomposition of the _∆W_ matrix, where 
    * _A_ is an _n_ x _r_ matrix
    * _B_ is an _r_ x _m_ matrix
    * _m_ and _n_ are the original weight matrix’ dimensions
    * _r << n_, _m_ is the lower rank
  * α is a scaling factor that controls how much the new updates from the low-rank matrices affect the original model weights.

  ](https://neptune.ai/blog/</blog/llm-fine-tuning-and-model-selection-with-neptune-transformers>)
#### Configuring the LoRA adapter
To initialize LoRA for our Llama 3 model, we need to specify several parameters:
```
lora_parameters = {
  'lora_r': 16,
  'target_modules': ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj",],
  'lora_alpha': 16,
  'lora_dropout': 0, 
  'lora_bias': "none",
  'lora_use_gradient_checkpointing': "unsloth",
  'lora_random_state': 42,
}
```

Copied!
Copy
Here, lora_r represents the low-rank dimension, and target_modules are the model’s parameters that can be approximated through LoRA. lora_alpha is the numerator of the scaling factor for ∆W (α/r).
We also set the LoRA dropout to 0, as we do not have any threat of overfitting. The bias is deactivated to keep things simple. We also configure Unsloth’s gradient checkpointing to save gradients. Those parameters are suggested in this Unsloth example notebook to yield outstanding performance.
With this configuration, we can instantiate the model:
```
model = FastLanguageModel.get_peft_model(
  model,
  r = lora_parameters['lora_r'],
  target_modules = lora_parameters['target_modules'],
  lora_alpha = lora_parameters['lora_alpha'],
  lora_dropout = lora_parameters['lora_dropout'],
  bias = lora_parameters['lora_bias'],
  use_gradient_checkpointing =  lora_parameters['lora_use_gradient_checkpointing'],
  random_state = lora_parameters['lora_random_state'],
)
```

Copied!
Copy
### Dataset preprocessing
The Quora Question Pairs (QQP) dataset comprises over 400,000 question pairs. Each question pair is annotated with a binary value indicating whether the two questions are paraphrases of each other.
We will not use all 400,000 data points. Instead, we’ll randomly sample 1,000 data points from the original training data for our fine-tuning phase and 200 data points from the original validation data. This allows us to stay within the memory and compute time restrictions of the Colab environment. (You can find and download the complete training and validation data from my Hugging Face repository.)
Instead of just using the original data points, I added explanations for why pairs of questions are similar or different. This helps the model learn more than just matching a question pair to a “yes” or “no” label. To automate this process, I’ve passed the question pair to GPT-4 and instructed it to explain their (dis)similarity.
Having an explanation for each question pair allows us to conduct instruction-based fine-tuning. For this, we craft a prompt that details the model’s training task (e.g., what label it should predict, how the prediction should be formatted, and that it should explain its classification). This approach helps to avoid hallucinations and prevents the LLM from discarding information it acquired during pre-training.
Here is the prompt template I used:
```
prompt = """Below is an instruction that describes a task, paired with an input that provides further context. Write a response that appropriately completes the request.
Instruction:
You are given 2 questions and you need to compare them and understand are they semantically similar or not, by providing explanation and after that label. 0 means dissimilar and 1 means similar.
Question 1: {{question_1}}
Question 2: {{question_2}}
Explanation: {{expandlab}}
"""
```

Copied!
Copy
To generate the prompt from the raw data and the template, we need to create a formatting function:
```
from datasets import load_dataset
dataset = load_dataset('borismartirosyan/glue-qqp-sampled-explanation')
EOS_TOKEN = tokenizer.eos_token
def formatting_prompts_func(examples):
  question_1 = examples["question1"]
  question_2 = examples["question2"]
  explanations = examples["explanation"]
  labels = examples["label"]
  texts = []
  for q1, q2, exp, labl in zip(question_1, question_2, explanations, labels):
    # Must add EOS_TOKEN, otherwise your generation will go on forever! BOS token will be added automatically
    text = prompt.replace('{{question_1}}', q1).replace('{{question_2}}', q2).replace("{{expandlab}}", exp+' label: ' + labl) + EOS_TOKEN
    texts.append(text)
  return { "text": texts, }
dataset = dataset.map(formatting_prompts_func, batched = True)
```

Copied!
Copy
This results in prompts that look like this:
“Below is an instruction that describes a task, paired with an input that provides further context. Write a response that appropriately completes the request.
**Instruction:** You are given 2 questions and you need to compare them and understand are they semantically similar or not, by providing explanation and after that label. 0 means dissimilar and 1 means similar.
**Question 1:** How much noise does one bar of the iPhone volume slider make in decibels?
**Question 2:** What are some social impacts of The Agricultural Revolution and what are some examples?
**Explanation:** The questions ‘How much noise does one bar of the iPhone volume slider make in decibels?’ and ‘What are some social impacts of The Agricultural Revolution and what are some examples?’ are considered dissimilar because they address different topics, concepts, or inquiries.
**Label:** 0<|end_of_text|>”
  ](https://neptune.ai/blog/</blog/prompt-engineering-strategies>)
### Setting up the neptune.ai experiment tracker
Tracking machine-learning experiments is essential to optimize model performance and resource utilization. neptune.ai is a versatile tool that enables researchers, data scientists, and ML engineers to collect and analyze metadata. It’s optimized for tracking foundation model training.
If you have not worked with Neptune before, sign up for an account first. Then, create a project and find your API credentials. Within our Colab notebook, we export these values as environment variables to be picked up by the Neptune client later on:
```
import os
os.environ["NEPTUNE_PROJECT"] = "YOUR_PROJECT"
os.environ["NEPTUNE_API_TOKEN"] = "YOUR_API_KEY"
```

Copied!
Copy
### Monitoring and configuring the fine-tuning
When it comes to tracking training progress, I prefer to keep an eye on the validation and training losses. We can do this directly in our Colab notebook and persist this data to our Neptune project for later analysis and comparison.
Let’s set this up by configuring the TrainingArguments that we’ll pass to the Supervised Fine-tuning Trainer provided by the TRL library:
  * eval_strategy defines when we’ll evaluate our model. We’ll set this to “steps.” (An alternative value would be “epoch.”) Setting eval_steps to 10 leads to an evaluation being carried out every tenth step.
  * logging_strategy and logging_steps follow the same pattern and define when and how often we’ll log training metadata.
  * The save_strategy specifies when we’ll save a model checkpoint. Here, we choose “epoch” to persist a checkpoint after each training epoch.

  ](https://neptune.ai/blog/</blog/ml-experiment-tracking>)
We also have to configure our training procedure:
  * per_device_train_batch_size defines the batch size per GPU. As we are in Google Colab, where we only have one GPU, this is equivalent to the total training batch size.
  * num_train_epochs specifies the number of training epochs.

  * Through the optim parameter, we select the optimizer to use. In our case, “adamw_8bit” is a good choice. 8-bit optimizers reduce the required memory by 75% compared to standard 32-bit optimizers.
  * Through the fp16 and bf16 parameters, we activate 16-bit mixed-precision training. (See the Hugging Face documentation for details.)
  * The warmup_steps parameter controls how many steps we’ll take at the beginning of the training to ramp up the learning rate from 0 to the desired learning rate specified through the learning_rate parameter. We’ll further use a cosine learning rate scheduler, which I strongly recommend for transformer models. While it is common to see official implementations opting for a linear scheduler, cosine annealing is preferable because it facilitates faster convergence.
  * weight_decay defines the amount of regularization of weights with the L2 norm.

```
from trl import SFTTrainer, DataCollatorForCompletionOnlyLM
from transformers import TrainingArguments
from unsloth import is_bfloat16_supported
import neptune

training_arguments = {
  # Tracking parameters
  'eval_strategy' : "steps",
  'eval_steps': 10,
  'logging_strategy' : "steps",
  'logging_steps': 1,
  'save_strategy' : "epoch",
  # Training parameters
  'per_device_train_batch_size' : 2,
  'num_train_epochs' : 2
  'optim' : "adamw_8bit",
  'fp16' : not is_bfloat16_supported(),
  'bf16' : is_bfloat16_supported(),
  'warmup_steps' : 5,
  'learning_rate' : 2e-4,
  'lr_scheduler_type' : "cosine",
  'weight_decay' : 0.01,
  'seed' : 3407,
  'output_dir' : "outputs",
}
```

Copied!
Copy
After defining the lora, model, and training parameters, we log them to Neptune. For this, we initialize a new Run object, merge the separate parameter dictionaries, and assign the resulting parameters dictionary to the “parameters” key:
```
run = neptune.init_run()
params = {**lora_parameters, **model_parameters, **training_arguments}
run["parameters"] = params
```

Copied!
Copy
This pattern ensures that all parameters are consistently logged without any risk of forgetting updates or copy-and-paste errors.
### Launching a training run
Finally, we can pass all parameters to the TRL Supervised Fine-tuning Trainer and kick-off our first training run:
```
trainer = SFTTrainer(
  model = model,
  tokenizer = tokenizer,
  train_dataset = dataset['train'],
  eval_dataset = dataset['validation'],
  dataset_text_field = "text",
  max_seq_length = model_parameters['model_max_seq_length'],
  dataset_num_proc = 2,
  packing = False, 
  args = TrainingArguments(
	**training_arguments
  ),
)
trainer.model.print_trainable_parameters()
trainer.train()
```

Copied!
Copy
Here, I’d like to draw your attention to the packing parameter. Packing is a process where many different sample sequences of varying lengths get combined into one batch while staying within a specified maximum sequence length permitted by the model.
### Monitoring training progress
While the training is progressing, we observe the training and validation loss. If everything goes well, both will decrease, indicating that the model is getting better at identifying similar questions. At some point, we expect the validation loss to stop decreasing along with the training loss as the model begins to overfit our training samples.
!Example plot of training and validation loss over training epochs. Approximately starting from the 5th epoch, the validation loss no longer decreases with the training loss – the model starts to overfit.Example plot of training and validation loss over training epochs. Approximately starting from the 5th epoch, the validation loss no longer decreases with the training loss – the model starts to overfit.
However, in practice, it’s rarely as clear cut. Our case is a great example of this. When I ran the training, the training and validation loss curves looked like this:
!Training an validation loss vs. steps
The validation loss never increases but becomes steady after about 50 steps. We should stop there, as we can reasonably assume we’ll overfit if we go beyond that.
Track months-long model training with more confidence. Use neptune.ai forking feature to iterate faster and optimize the usage of GPU resources. 
With Neptune, users can visualize forked training out of the box. This means you can:
  * Test multiple configs at the same time. Stop the runs that don’t improve accuracy. And continue from the most accurate last step. 
  * Restart failed training sessions from any previous step. The training history is inherited, and the entire experiment is visible on a single chart. 

  !zoom Full screen preview 
  * 
Check the documentation
  * 
Play with an interactive example project
  * 
Get in touch to go through a custom demo with our engineering team

### Evaluating the fine-tuned model
To understand how well the fine-tuned model is performing and if it is ready to be integrated into our customer service application, it’s not sufficient to just try a few requests. Instead, we have to conduct a more thorough evaluation.
To quantitatively assess the model, we need to write a function to extract the label from the model’s output. Even with the best instruction prompts and extensive fine-tuning, an LLM can produce improper answers. In our case, this could be an incorrect label (e.g., “similar” instead of “1”) or additional text.
This is what a full evaluation loop looks like:
```
from tqdm import tqdm
# Enable the inference acceleration
FastLanguageModel.for_inference(trainer.model)
trainer.model.to('cuda')
predicted_classes = []
for dp in tqdm(trainer.eval_dataset):
  dp = tokenizer.decode(dp['input_ids'])
  dp = tokenizer(dp, add_special_tokens=False, return_tensors='pt')['input_ids'][0].to('cuda')
  dp = dp.unsqueeze(0)
  outputs = model.generate(dp, max_new_tokens = 400, use_cache = True)
  possible_label = tokenizer.decode(outputs[0]).split('label:')[-1].replace('<|end_of_text|>', '').replace('<|begin_of_text|>', '').replace('\n', '').replace('://', '').strip()
  if len(possible_label) == 1:
   predicted_classes.append(possible_label)
  else:
   predicted_classes.append(tokenizer.decode(outputs[0]))
y_pred = predicted_classes
y_true = [x['text'].split("label:")[-1].replace('\n<|end_of_text|>', '').strip() for x in dataset['validation']]
```

Copied!
Copy
Now, we have two lists, y_pred and y_true, that contain the predicted and the ground truth labels, respectively.
To analyze this data, I prefer generating a scikit-learn classification report and a confusion matrix:
```
from sklearn.metrics import classification_report, ConfusionMatrixDisplay
print(classification_report(y_true, y_pred))
print(ConfusionMatrixDisplay.from_predictions(y_true, y_pred, xticks_rotation=20))
```

Copied!
Copy
The classification report shows us the precision, recall, f1-score, and accuracy for the two classes:
!classification report
Let’s step through this report together:
  * From the support column, we see that we have a well-balanced dataset consisting of 101 dissimilar and 99 similar question pairs.
  * We have a great accuracy score of 99%, which tells us that our predictions are nearly perfect. This is also reflected by the precision and recall scores, which are close to the best possible value of 1.00.
  * The macro average is computed by taking the mean across classes, while the weighted average takes the number of instances per class into account.

In summary, we find that our fine-tuned model performs really well on the evaluation set.
The confusion matrix visualizes this data:
!confusion matrix
The first column shows that out of 101 samples from the “0” class (dissimilar questions), 100 were correctly classified, and only one pair was mistakenly classified as similar. The second column shows that out of 99 samples from the “1” class (similar questions), all 99 were correctly classified. 
We can log these tables and figures to Neptune by adding them to the Run object:
```
fig = ConfusionMatrixDisplay.from_predictions(y_true, y_pred, xticks_rotation=20)
run['confusion_matrix'] = fig
```

Copied!
Copy
  ](https://neptune.ai/blog/</blog/llm-guardrails>)
### Comparing the fine-tuned model with baseline Llama 3 8B and GPT-4o
So far, we’ve evaluated the performance of the fine-tuned Llama 3 8B model. Now, we’re going to assess how much better the fine-tuned version performs compared to the base model. This will reveal the effectiveness of our fine-tuning approach. We’re also going to compare against the much larger OpenAI GPT-4o to determine if we’re limited by our model’s size.
Using the same prompt and dataset, the pre-trained Llama 3 8B achieved 63% accuracy, while GPT-4o reached 69.5%. This zero-shot performance is significantly below the 99% accuracy of our fine-tuned model, which indicates that our training has been very effective. 
While conducting the evaluation, I noticed that GPT-4o sometimes provided answers that were factually incorrect. This shows that even the most advanced and largest models still struggle with general knowledge tasks and instructions, making fine-tuning a smaller model a first-choice approach.
## Conclusions and next steps
In this tutorial, we’ve explored an approach to fine-tune an LLM with limited resources. By utilizing quantization, we reduced the memory footprint of the Llama 3 8B model. Applying LoRA allowed us to reduce the number of trainable parameters in the model without sacrificing accuracy. Finally, instruction-based prompts with LLM-generated explanations helped speed up the training further by maximizing the model’s learning.
You can apply the key ideas of this “Google Collab-friendly” approach to many other base models and tasks. Often, you’ll find that you don’t need large GPUs and long training times to reach a production-ready performance. Even if you do have access to vast cloud resources, reducing the cost and duration of model training is vital to project success.
##  Was the article useful? 
!yes Yes  !no No 
 Suggest changes 
####  Check out our  **product resources** and  **related articles** below: 
       
###  Explore more content topics: 
 Computer Vision   General   LLMOps   ML Model Development   ML Tools   MLOps   Natural Language Processing   Paper Reflections   Reinforcement Learning   Tabular Data   Time Series 

×
This website uses cookies 
To provide the best experiences, we use technologies like cookies to store and/or access device information. Find out more in our   privacy policy 
Strictly necessary 
Performance 
Targeting 
Functionality 
Unclassified 
OK, I get it 
