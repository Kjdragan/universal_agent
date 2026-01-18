---
title: "Tutorial: How to Finetune Llama-3 and Use In Ollama | Unsloth Documentation"
source: https://docs.unsloth.ai/get-started/fine-tuning-llms-guide/tutorial-how-to-finetune-llama-3-and-use-in-ollama
date: unknown
description: "Beginner's Guide for creating a customized personal assistant (like ChatGPT) to run locally on Ollama"
word_count: 3194
---

By the end of this tutorial, you will create a custom chatbot by **finetuning Llama-3** with **Unsloth** arrow-up-right for free. It can run locally via **Ollama** arrow-up-right on your PC, or in a free GPU instance through **Google Colab** arrow-up-right-Ollama.ipynb>). You will be able to interact with the chatbot interactively like below:

**Unsloth** makes finetuning much easier, and can automatically export the finetuned model to **Ollama** with integrated automatic `Modelfile` creation! If you need help, you can join our Discord server: <https://discord.com/invite/unsloth>arrow-up-right
circle-exclamation
**If you’d like to copy or save the code, everything is available in our** **Ollama Colab notebook** arrow-up-right-Ollama.ipynb>)**. You can use it directly there or adapt it for your local setup:** **https://colab.research.google.com/github/unslothai/notebooks/blob/main/nb/Llama3_(8B)-Ollama.ipynb** arrow-up-right-Ollama.ipynb>)
## 
hashtag
1. What is Unsloth?
Unslotharrow-up-right makes finetuning LLMs like Llama-3, Mistral, Phi-3 and Gemma 2x faster, use 70% less memory, and with no degradation in accuracy! We will be using Google Colab which provides a free GPU during this tutorial. You can access our free notebooks below:
  * Ollama Llama-3 Alpacaarrow-up-right-Ollama.ipynb>) (notebook which we will be using)
  * CSV/Excel Ollama Guidearrow-up-right

#### 
hashtag
 _**You will also need to login into your Google account!**_

## 
 hashtag
2. What is Ollama?
Ollama arrow-up-rightallows you to run language models from your own computer in a quick and simple way! It quietly launches a program which can run a language model like Llama-3 in the background. If you suddenly want to ask the language model a question, you can simply submit a request to Ollama, and it'll quickly return the results to you! We'll be using Ollama as our inference engine!

## 
hashtag
3. Install Unsloth

If you have never used a Colab notebook, a quick primer on the notebook itself:
  1. **Play Button at each "cell".** Click on this to run that cell's code. You must not skip any cells and you must run every cell in chronological order. If you encounter any errors, simply rerun the cell you did not run before. Another option is to click CTRL + ENTER if you don't want to click the play button.
  2. **Runtime Button in the top toolbar.** You can also use this button and hit "Run all" to run the entire notebook in 1 go. This will skip all the customization steps, and can be a good first try.
  3. **Connect / Reconnect T4 button.** You can click here for more advanced system statistics.

The first installation cell looks like below: Remember to click the PLAY button in the brackets [ ]. We grab our open source Github package, and install some other packages.

## 
hashtag
4. Selecting a model to finetune
Let's now select a model for finetuning! We defaulted to Llama-3 from Meta / Facebook which was trained on a whopping 15 trillion "tokens". Assume a token is like 1 English word. That's approximately 350,000 thick Encyclopedias worth! Other popular models include Mistral, Phi-3 (trained using GPT-4 output) and Gemma from Google (13 trillion tokens!).
Unsloth supports these models and more! In fact, simply type a model from the Hugging Face model hub to see if it works! We'll error out if it doesn't work.

There are 3 other settings which you can toggle:
  1. Copy```
max_seq_length = 2048
```

This determines the context length of the model. Gemini for example has over 1 million context length, whilst Llama-3 has 8192 context length. We allow you to select ANY number - but we recommend setting it 2048 for testing purposes. Unsloth also supports very long context finetuning, and we show we can provide 4x longer context lengths than the best.
  2. Copy```
dtype = None
```

Keep this as None, but you can select torch.float16 or torch.bfloat16 for newer GPUs.
  3. Copy```
load_in_4bit = True
```

We do finetuning in 4 bit quantization. This reduces memory usage by 4x, allowing us to actually do finetuning in a free 16GB memory GPU. 4 bit quantization essentially converts weights into a limited set of numbers to reduce memory usage. A drawback of this is there is a 1-2% accuracy degradation. Set this to False on larger GPUs like H100s if you want that tiny extra accuracy.

If you run the cell, you will get some print outs of the Unsloth version, which model you are using, how much memory your GPU has, and some other statistics. Ignore this for now.
## 
hashtag
5. Parameters for finetuning

Now to customize your finetune, you can edit the numbers above, but you can ignore it, since we already select quite reasonable numbers.
The goal is to change these numbers to increase accuracy, but also **counteract over-fitting**. Over-fitting is when you make the language model memorize a dataset, and not be able to answer novel new questions. We want to a final model to answer unseen questions, and not do memorization.
  1. Copy```
r = 16, # Choose any number > 0 ! Suggested 8, 16, 32, 64, 128
```

The rank of the finetuning process. A larger number uses more memory and will be slower, but can increase accuracy on harder tasks. We normally suggest numbers like 8 (for fast finetunes), and up to 128. Too large numbers can causing over-fitting, damaging your model's quality.
  2. Copy```
target_modules = ["q_proj", "k_proj", "v_proj", "o_proj",
         "gate_proj", "up_proj", "down_proj",],
```

We select all modules to finetune. You can remove some to reduce memory usage and make training faster, but we highly do not suggest this. Just train on all modules!
  3. Copy```
lora_alpha = 16,
```

The scaling factor for finetuning. A larger number will make the finetune learn more about your dataset, but can promote over-fitting. We suggest this to equal to the rank `r`, or double it.
  4. Copy```
lora_dropout = 0, # Supports any, but = 0 is optimized
```

Leave this as 0 for faster training! Can reduce over-fitting, but not that much.
  5. Copy```
bias = "none",  # Supports any, but = "none" is optimized
```

Leave this as 0 for faster and less over-fit training!
  6. Copy```
use_gradient_checkpointing = "unsloth", # True or "unsloth" for very long context
```

Options include `True`, `False` and `"unsloth"`. We suggest `"unsloth"` since we reduce memory usage by an extra 30% and support extremely long context finetunes.You can read up here: <https://unsloth.ai/blog/long-context>arrow-up-right for more details.
  7. Copy```
random_state = 3407,
```

The number to determine deterministic runs. Training and finetuning needs random numbers, so setting this number makes experiments reproducible.
  8. Copy```
use_rslora = False, # We support rank stabilized LoRA
```

Advanced feature to set the `lora_alpha = 16` automatically. You can use this if you want!
  9. Copy```
loftq_config = None, # And LoftQ
```

Advanced feature to initialize the LoRA matrices to the top r singular vectors of the weights. Can improve accuracy somewhat, but can make memory usage explode at the start.

## 
hashtag
6. Alpaca Dataset

We will now use the Alpaca Dataset created by calling GPT-4 itself. It is a list of 52,000 instructions and outputs which was very popular when Llama-1 was released, since it made finetuning a base LLM be competitive with ChatGPT itself.
You can access the GPT4 version of the Alpaca dataset here: <https://huggingface.co/datasets/vicgalle/alpaca-gpt4>arrow-up-right. An older first version of the dataset is here: <https://github.com/tatsu-lab/stanford_alpaca>arrow-up-right. Below shows some examples of the dataset:

You can see there are 3 columns in each row - an instruction, and input and an output. We essentially combine each row into 1 large prompt like below. We then use this to finetune the language model, and this made it very similar to ChatGPT. We call this process **supervised instruction finetuning**.

## 
hashtag
7. Multiple columns for finetuning
But a big issue is for ChatGPT style assistants, we only allow 1 instruction / 1 prompt, and not multiple columns / inputs. For example in ChatGPT, you can see we must submit 1 prompt, and not multiple prompts.

This essentially means we have to "merge" multiple columns into 1 large prompt for finetuning to actually function!
For example the very famous Titanic dataset has many many columns. Your job was to predict whether a passenger has survived or died based on their age, passenger class, fare price etc. We can't simply pass this into ChatGPT, but rather, we have to "merge" this information into 1 large prompt.

For example, if we ask ChatGPT with our "merged" single prompt which includes all the information for that passenger, we can then ask it to guess or predict whether the passenger has died or survived.

Other finetuning libraries require you to manually prepare your dataset for finetuning, by merging all your columns into 1 prompt. In Unsloth, we simply provide the function called `to_sharegpt` which does this in 1 go!
To access the Titanic finetuning notebook or if you want to upload a CSV or Excel file, go here: <https://colab.research.google.com/drive/1VYkncZMfGFkeCEgN2IzbZIKEDkyQuJAS?usp=sharing>arrow-up-right

Now this is a bit more complicated, since we allow a lot of customization, but there are a few points:
  * You must enclose all columns in curly braces `{}`. These are the column names in the actual CSV / Excel file.
  * Optional text components must be enclosed in `[[]]`. For example if the column "input" is empty, the merging function will not show the text and skip this. This is useful for datasets with missing values.
  * Select the output or target / prediction column in `output_column_name`. For the Alpaca dataset, this will be `output`.

For example in the Titanic dataset, we can create a large merged prompt format like below, where each column / piece of text becomes optional.

For example, pretend the dataset looks like this with a lot of missing data:
Embarked
Age
Fare
S
23
18
7.25
Then, we do not want the result to be:
  1. The passenger embarked from S. Their age is 23. Their fare is **EMPTY**.
  2. The passenger embarked from **EMPTY**. Their age is 18. Their fare is $7.25.

Instead by optionally enclosing columns using `[[]]`, we can exclude this information entirely.
  1. [[The passenger embarked from S.]] [[Their age is 23.]] [[Their fare is **EMPTY**.]]
  2. [[The passenger embarked from **EMPTY**.]] [[Their age is 18.]] [[Their fare is $7.25.]]

becomes:
  1. The passenger embarked from S. Their age is 23.
  2. Their age is 18. Their fare is $7.25.

## 
hashtag
8. Multi turn conversations
A bit issue if you didn't notice is the Alpaca dataset is single turn, whilst remember using ChatGPT was interactive and you can talk to it in multiple turns. For example, the left is what we want, but the right which is the Alpaca dataset only provides singular conversations. We want the finetuned language model to somehow learn how to do multi turn conversations just like ChatGPT.

So we introduced the `conversation_extension` parameter, which essentially selects some random rows in your single turn dataset, and merges them into 1 conversation! For example, if you set it to 3, we randomly select 3 rows and merge them into 1! Setting them too long can make training slower, but could make your chatbot and final finetune much better!

Then set `output_column_name` to the prediction / output column. For the Alpaca dataset dataset, it would be the output column.
We then use the `standardize_sharegpt` function to just make the dataset in a correct format for finetuning! Always call this!

## 
hashtag
9. Customizable Chat Templates
We can now specify the chat template for finetuning itself. The very famous Alpaca format is below:

But remember we said this was a bad idea because ChatGPT style finetunes require only 1 prompt? Since we successfully merged all dataset columns into 1 using Unsloth, we essentially can create the below style chat template with 1 input column (instruction) and 1 output:

We just require you must put a `{INPUT}` field for the instruction and an `{OUTPUT}` field for the model's output field. We in fact allow an optional `{SYSTEM}` field as well which is useful to customize a system prompt just like in ChatGPT. For example, below are some cool examples which you can customize the chat template to be:

For the ChatML format used in OpenAI models:

Or you can use the Llama-3 template itself (which only functions by using the instruct version of Llama-3): We in fact allow an optional `{SYSTEM}` field as well which is useful to customize a system prompt just like in ChatGPT.

Or in the Titanic prediction task where you had to predict if a passenger died or survived in this Colab notebook which includes CSV and Excel uploading: <https://colab.research.google.com/drive/1VYkncZMfGFkeCEgN2IzbZIKEDkyQuJAS?usp=sharing>arrow-up-right

## 
hashtag
10. Train the model
Let's train the model now! We normally suggest people to not edit the below, unless if you want to finetune for longer steps or want to train on large batch sizes.

We do not normally suggest changing the parameters above, but to elaborate on some of them:
  1. Copy```
per_device_train_batch_size = 2,
```

Increase the batch size if you want to utilize the memory of your GPU more. Also increase this to make training more smooth and make the process not over-fit. We normally do not suggest this, since this might make training actually slower due to padding issues. We normally instead ask you to increase `gradient_accumulation_steps` which just does more passes over the dataset.
  2. Copy```
gradient_accumulation_steps = 4,
```

Equivalent to increasing the batch size above itself, but does not impact memory consumption! We normally suggest people increasing this if you want smoother training loss curves.
  3. Copy```
max_steps = 60, # num_train_epochs = 1,
```

We set steps to 60 for faster training. For full training runs which can take hours, instead comment out `max_steps`, and replace it with `num_train_epochs = 1`. Setting it to 1 means 1 full pass over your dataset. We normally suggest 1 to 3 passes, and no more, otherwise you will over-fit your finetune.
  4. Copy```
learning_rate = 2e-4,
```

Reduce the learning rate if you want to make the finetuning process slower, but also converge to a higher accuracy result most likely. We normally suggest 2e-4, 1e-4, 5e-5, 2e-5 as numbers to try.

You’ll see a log of numbers during training. This is the training loss, which shows how well the model is learning from your dataset. For many cases, a loss around 0.5 to 1.0 is a good sign, but it depends on your dataset and task. If the loss is not going down, you might need to adjust your settings. If the loss goes to 0, that could mean overfitting, so it's important to check validation too.
## 
hashtag
11. Inference / running the model

Now let's run the model after we completed the training process! You can edit the yellow underlined part! In fact, because we created a multi turn chatbot, we can now also call the model as if it saw some conversations in the past like below:

Reminder Unsloth itself provides **2x faster inference** natively as well, so always do not forget to call `FastLanguageModel.for_inference(model)`. If you want the model to output longer responses, set `max_new_tokens = 128` to some larger number like 256 or 1024. Notice you will have to wait longer for the result as well!
## 
hashtag
12. Saving the model
We can now save the finetuned model as a small 100MB file called a LoRA adapter like below. You can instead push to the Hugging Face hub as well if you want to upload your model! Remember to get a Hugging Face token via <https://huggingface.co/settings/tokens>arrow-up-right and add your token!

After saving the model, we can again use Unsloth to run the model itself! Use `FastLanguageModel` again to call it for inference!

## 
hashtag
13. Exporting to Ollama
Finally we can export our finetuned model to Ollama itself! First we have to install Ollama in the Colab notebook:

Then we export the finetuned model we have to llama.cpp's GGUF formats like below:

Reminder to convert `False` to `True` for 1 row, and not change every row to `True`, or else you'll be waiting for a very time! We normally suggest the first row getting set to `True`, so we can export the finetuned model quickly to `Q8_0` format (8 bit quantization). We also allow you to export to a whole list of quantization methods as well, with a popular one being `q4_k_m`.
Head over to <https://github.com/ggerganov/llama.cpp>arrow-up-right to learn more about GGUF. We also have some manual instructions of how to export to GGUF if you want here: <https://github.com/unslothai/unsloth/wiki#manually-saving-to-gguf>arrow-up-right
You will see a long list of text like below - please wait 5 to 10 minutes!!

And finally at the very end, it'll look like below:

Then, we have to run Ollama itself in the background. We use `subprocess` because Colab doesn't like asynchronous calls, but normally one just runs `ollama serve` in the terminal / command prompt.

## 
hashtag
14. Automatic `Modelfile` creation
The trick Unsloth provides is we automatically create a `Modelfile` which Ollama requires! This is a just a list of settings and includes the chat template which we used for the finetune process! You can also print the `Modelfile` generated like below:

We then ask Ollama to create a model which is Ollama compatible, by using the `Modelfile`

## 
hashtag
15. Ollama Inference
And we can now call the model for inference if you want to do call the Ollama server itself which is running on your own local machine / in the free Colab notebook in the background. Remember you can edit the yellow underlined part.

## 
hashtag
16. Interactive ChatGPT style
But to actually run the finetuned model like a ChatGPT, we have to do a bit more! First click the terminal icon and a Terminal will pop up. It's on the left sidebar.

Then, you might have to press ENTER twice to remove some weird output in the Terminal window. Wait a few seconds and type `ollama run unsloth_model` then hit ENTER.

And finally, you can interact with the finetuned model just like an actual ChatGPT! Hit CTRL + D to exit the system, and hit ENTER to converse with the chatbot!

## 
hashtag
You've done it!
You've successfully finetuned a language model and exported it to Ollama with Unsloth 2x faster and with 70% less VRAM! And all this for free in a Google Colab notebook!
If you want to learn how to do reward modelling, do continued pretraining, export to vLLM or GGUF, do text completion, or learn more about finetuning tips and tricks, head over to our Githubarrow-up-right.
If you need any help on finetuning, you can also join our Discord server herearrow-up-right. If you want help with Ollama, you can also join their server herearrow-up-right.
And finally, we want to thank you for reading and following this far! We hope this made you understand some of the nuts and bolts behind finetuning language models, and we hope this was useful!
To access our Alpaca dataset example click herearrow-up-right, and our CSV / Excel finetuning guide is herearrow-up-right.
PreviousWhat Model Should I Use?chevron-leftNextReinforcement Learning Guidechevron-right
Last updated 2 months ago
Was this helpful?
This site uses cookies to deliver its service and to analyze traffic. By browsing this site, you accept the privacy policy.
close
AcceptReject
