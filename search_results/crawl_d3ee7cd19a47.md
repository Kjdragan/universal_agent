---
title: "Can I finetune llama3.3 using axolotl? · axolotl-ai-cloud/axolotl · Discussion #2283 · GitHub"
source: https://github.com/axolotl-ai-cloud/axolotl/discussions/2283
date: unknown
description: "Can I finetune llama3.3 using axolotl?"
word_count: 1110
---

Skip to content
You signed in with another tab or window. Reload to refresh your session. You signed out in another tab or window. Reload to refresh your session. You switched accounts on another tab or window. Reload to refresh your session. Dismiss alert
{{ message }}
 axolotl-ai-cloud  / **axolotl ** Public
  * ###  Uh oh! 
There was an error while loading. Please reload this page.
  *  Notifications  You must be signed in to change notification settings
  *  Fork 1.2k 
  *  Star  11.1k 

#  Can I finetune llama3.3 using axolotl?  #2283
Closed  Unanswered
 hahmad2008   asked this question in Q&A
 Can I finetune llama3.3 using axolotl?  #2283
 
Jan 23, 2025 · 2 comments · 12 replies 
Return to top
Discussion options
###  Uh oh! 
There was an error while loading. Please reload this page.

#  {{title}} 
Something went wrong. 
###  Uh oh! 
There was an error while loading. Please reload this page.
Quote reply
## 
  Jan 23, 2025 
- 
could you please provide any example for the config file to finetune llama3.3 on instruction dataset? model: `meta-llama/Llama-3.3-70B-Instruct`  
---  
Beta Was this translation helpful? Give feedback.
1 You must be logged in to vote
👍 1 👀 1
All reactions
  * 👍 1
  * 👀 1

##  Replies:  2 comments  · 12 replies 
Comment options
###  Uh oh! 
There was an error while loading. Please reload this page.

#  {{title}} 
Something went wrong. 
###  Uh oh! 
There was an error while loading. Please reload this page.
Quote reply
### 
  Jan 24, 2025 
Maintainer 
- 
Hey, you could use the existing llama3 configs and point to that new model <https://github.com/axolotl-ai-cloud/axolotl/blob/main/examples/llama-3/lora-8b.yml>  
---  
Beta Was this translation helpful? Give feedback.
1 You must be logged in to vote
All reactions
0 replies 
Comment options
###  Uh oh! 
There was an error while loading. Please reload this page.

#  {{title}} 
Something went wrong. 
###  Uh oh! 
There was an error while loading. Please reload this page.
Quote reply
edited
###  Uh oh! 
There was an error while loading. Please reload this page.

#  {{editor}}'s edit 
{{actor}} deleted this content . 
#  {{editor}}'s edit 
Something went wrong. 
###  Uh oh! 
There was an error while loading. Please reload this page.
### 
  Jan 27, 2025 
Author 
- 
Thanks @NanoCode012 . can we finetune a quantized model? AWQ version? what the config need to be set?  
---  
Beta Was this translation helpful? Give feedback.
1 You must be logged in to vote
All reactions
12 replies 

Comment options
###  Uh oh! 
There was an error while loading. Please reload this page.

#  {{title}} 
Something went wrong. 
###  Uh oh! 
There was an error while loading. Please reload this page.
Quote reply
#### 
hahmad2008  Jan 29, 2025 
Author 
- 
model config.yaml: ```
base_model: unsloth/Meta-Llama-3.1-70B-bnb-4bit
base_model_config: unsloth/Meta-Llama-3.1-70B-bnb-4bit
gptq: true
model_type: LlamaForCausalLM
tokenizer_type: LlamaTokenizer
trust_remote_code: null
is_falcon_derived_model: null
is_llama_derived_model: null
is_mistral_derived_model: null
load_in_8bit: false
load_in_4bit: true
strict: false
push_dataset_to_hub: null
chat_template: null
datasets:
- path: mhenrichsen/alpaca_2k_test
 type: alpaca
dataset_prepared_path: prepared-dataset
val_set_size: 0.01
adapter: qlora
lora_model_dir: null
sequence_len: 512
sample_packing: true
eval_sample_packing: false
lora_r: 8
lora_alpha: 16
lora_dropout: 0.05
lora_target_modules: null
lora_target_linear: true
lora_fan_in_fan_out: null
wandb_project: null
wandb_entity: null
wandb_watch: null
wandb_run_id: null
wandb_log_model: null
output_dir: model-finetuned
gradient_accumulation_steps: 1
micro_batch_size: 2
num_epochs: 1
optimizer: adamw_bnb_8bit
torchdistx_path: null
lr_scheduler: cosine
learning_rate: 0.0002
train_on_inputs: false
group_by_length: false
bf16: false
fp16: true
tf32: false
gradient_checkpointing: true
early_stopping_patience: null
resume_from_checkpoint: null
local_rank: null
logging_steps: 1
xformers_attention: null
flash_attention: true
gptq_groupsize: null
gptq_model_v1: null
warmup_steps: 10
eval_steps: 100
save_steps: 100
debug: null
deepspeed: null
weight_decay: 0.0
fsdp: null
fsdp_config: null
special_tokens:
 bos_token: <s>
 eos_token: </s>
 unk_token: <unk>
tokens: null

```
  
---  
Beta Was this translation helpful? Give feedback.
All reactions

Comment options
###  Uh oh! 
There was an error while loading. Please reload this page.

#  {{title}} 
Something went wrong. 
###  Uh oh! 
There was an error while loading. Please reload this page.
Quote reply
#### 
hahmad2008  Jan 29, 2025 
Author 
- 
@NanoCode012 when I change it to lora I get this error: ```
ValueError: model_config.quantization_config is not set or quant_method is not set to gptq. Please make sure to point to a GPTQ model.

```
  
---  
Beta Was this translation helpful? Give feedback.
All reactions

Comment options
###  Uh oh! 
There was an error while loading. Please reload this page.

#  {{title}} 
Something went wrong. 
###  Uh oh! 
There was an error while loading. Please reload this page.
Quote reply
#### 
NanoCode012  Jan 30, 2025 
Maintainer 
- 
Please set `load_in_4bit: true`, turn off gptq. Use `adapter: qlora`. I thought we removed that validation check.  
---  
Beta Was this translation helpful? Give feedback.
All reactions

Comment options
###  Uh oh! 
There was an error while loading. Please reload this page.

#  {{title}} 
Something went wrong. 
###  Uh oh! 
There was an error while loading. Please reload this page.
Quote reply
edited
###  Uh oh! 
There was an error while loading. Please reload this page.

#  {{editor}}'s edit 
{{actor}} deleted this content . 
#  {{editor}}'s edit 
Something went wrong. 
###  Uh oh! 
There was an error while loading. Please reload this page.
#### 
lynkz-matt-psaltis  Feb 15, 2025 
- 
Setting load in 4bit in the config is ignored by:  axolotl/src/axolotl/cli/merge_lora.py Line 72 in a98526e |  load_in_4bit=False,   
---  
Does anyone have a solid example of merging awq base with qlora?  
Beta Was this translation helpful? Give feedback.
All reactions

Comment options
###  Uh oh! 
There was an error while loading. Please reload this page.

#  {{title}} 
Something went wrong. 
###  Uh oh! 
There was an error while loading. Please reload this page.
Quote reply
#### 
NanoCode012  Mar 12, 2025 
Maintainer 
- 
@lynkz-matt-psaltis , sorry for late reply. AWQ is a different thing. I'm not aware it can be tuned on, only loaded for inference.  
---  
Beta Was this translation helpful? Give feedback.
❤️ 1
All reactions
  * ❤️ 1

Sign up for free **to join this conversation on GitHub**. Already have an account? Sign in to comment
Category 
 🙏 Q&A 
Labels 
None yet 
3 participants 
     
Heading
Bold
Italic
Quote
Code
Link
Numbered list
Unordered list
Task list
Attach files
Mention
Reference
Menu
  * Heading 
  * Bold 
  * Italic 
  * Quote 
  * Code 
  * Link 
  * Numbered list 
  * Unordered list 
  * Task list 
  * Attach files 
  * Mention 
  * Reference 

#  Select a reply 
Loading
###  Uh oh! 
There was an error while loading. Please reload this page.
 Create a new saved reply 
👍 1 reacted with thumbs up emoji 👎 1 reacted with thumbs down emoji 😄 1 reacted with laugh emoji 🎉 1 reacted with hooray emoji 😕 1 reacted with confused emoji ❤️ 1 reacted with heart emoji 🚀 1 reacted with rocket emoji 👀 1 reacted with eyes emoji
You can’t perform that action at this time. 
