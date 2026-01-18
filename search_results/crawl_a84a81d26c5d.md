---
title: "Fine-Tuning Llama-2: Tailoring Models to Unique Applications"
source: https://anyscale.com/blog/fine-tuning-llama-2-a-comprehensive-case-study-for-tailoring-models-to-unique-applications
date: 2023-08-11
description: "We examine the Llama-2 models under 3 real-world use cases and show that fine-tuning yields significant accuracy improvements."
word_count: 5929
---

HomeBlogBlog Detail
# Fine-Tuning Llama-2: A Comprehensive Case Study for Tailoring Models to Unique Applications
By Kourosh Hakhamaneshi and Rehaan Ahmad| August 11, 2023
 _In this blog, we provide a thorough analysis and a practical guide for fine-tuning. We examine the Llama-2 models under three real-world use cases, and show that fine-tuning yields significant accuracy improvements across the board (in some niche cases, better than GPT-4). Experiments were carried out with this_ __script__ _._
Large open language models have made significant progress in recent months, paving the way for commercially viable solutions that are suitable for enterprise applications. Notable among these are the Llama-2 and Falcon models. While powerful generalist language models like GPT-4 and Claude-2 provide quick access and rapid turnaround for projects, they often end up being an overkill for the requirements of many applications.
As an example, if the goal is to summarize support tickets and categorize issues into predetermined buckets, there's no need for a model capable of generating prose in the style of Shakespeare. Setting security concerns aside, employing GPT-4 for such tasks is akin to using a space shuttle for a cross-town commute. To support this claim, we study fine-tuning the Llama-2 model of various sizes on three tasks: 
  1. **Functional representations extracted from unstructured text (****_ViGGO_****)**
  2. **SQL generation (****_SQL-create-context_****)**
  3. **Grade-school math question-answering (****_GSM8k_****)**

We specifically show how on some tasks (e.g. SQL Gen or Functional Representation) we can fine-tune small Llama-2 models to become even better than GPT-4. At the same time, there are tasks like math reasoning and understanding that OSS models are just behind even after significant gains obtained by fine-tuning.
!Llama 2 performanceLlama 2 performance
 _The performance gain of Llama-2 models obtained via fine-tuning on each task. The darker shade for each of the colors indicate the performance of the Llama-2-chat models with a baseline prompt. The purple shows the performance of GPT-4 with the same prompt. The stacked bar plots show the performance gain from fine-tuning the Llama-2 base models. In Functional representation and SQL gen tasks with fine-tuning we can achieve better performance than GPT-4 while on some other task like math reasoning, fine-tuned models, while improving over the base models, are still not able to reach GPT-4’s performance levels._
In particular we show that with the Llama-13b variant we observed an increase in accuracy from, 58% to 98% on functional representations, 42% to 89% on SQL generation, and 28% to 47% on GSM. All of these experiments are done using Anyscale fine-tuning and serving platforms as offered as part of _Anyscale Endpoints_. 
In addition to providing more quantitative results, this blog post will present a technical deep-dive into how you can leverage Llama-2 models for specialized tasks. We will discuss the correct problem formulation, the setup of evaluation pipelines, and much more. We will compare methods such as prompt-engineering & few-shot prompting with fine-tuning, providing concrete pros and cons of each method along the way.
Fine-tuning these models is not a straightforward task. However, _Ray_ and _Anyscale_ offer unique capabilities that make this process faster, cheaper, and more manageable. Our mission is to enable enterprises to harness the latest advancements in AI as swiftly as possible.
We hope that the details covered in this post can help others elicit more value from their LLMs through an emphasis on data quality and evaluation procedures. 
## LinkFine-Tuning Basics[](https://anyscale.com/blog/<#fine-tuning-basics>)
For all three tasks, we use standard full parameter fine-tuning techniques. Models are fine-tuned for next-token prediction, and all parameters in the model are subject to gradient updates. While there certainly are other techniques to train LLMs, such as freezing select transformer blocks and LoRA, to keep a narrow scope we keep the training technique itself constant from task to task. 
Performing full parameter fine-tuning on models of this scale is no easy task. However, our lives can be made easier if we use the right combination of libraries. The script we used to produce the results in this blog post can be found _here_. Built on top of  _Ray Train_,  _Ray Data_, _Deepspeed_, and  _Accelerate_, this script allows you to easily run any of the Llama-2 7B, 13B, or 70B models. We will go over a couple high-level details about the script in the following subsections, but we suggest you checkout the script itself for details on how to run it. 
### LinkGeneral Training Flow[](https://anyscale.com/blog/<#general-training-flow>)
Training these large scale models is very difficult without scaling your workload across multiple nodes. Our script centers around a singular training function in which gradient updates on the model actually occur:
```
1deftraining_function(kwargs: dict):2print("training_function called")
3    …
4for epoch inrange(num_epochs):
5        …
6        model.train()
7        …
8
```

The key here is that this training function is run on each of the individual worker processes, possibly distributed across multiple machines. Within Ray Train, we use the _TorchTrainer_ class which acts as a process dispatcher and scales this training loop across our cluster. We can let TorchTrainer know how many worker processes we want to use and how many resources would each process need:
```
1scaling_config=air.ScalingConfig(
2   ...
3   num_workers=args.num_devices,
4   use_gpu=True,
5   resources_per_worker={"GPU": 1},
6),
7
```

From here, the main challenge is figuring out how to split the work across our individual training functions. Intuitively, there are two ways to "split" the work when training a model: one could shard the model, gradients, and optimizer states across workers, and also shard the data across them. On the data side, Ray Train helps us manage the data ingestion and dataset sharding across the training loops. At the top of training loop, a worker can access the shard of the dataset delegated to it via:
```
1train_ds = session.get_dataset_shard("train")
2valid_ds = session.get_dataset_shard("valid")
3
```

Model sharding is done through DeepSpeed. DeepSpeed defines a strategy for how to split the model across nodes and when to offload compute and memory from GPU to CPU (we use ZeRO stage 3 with optimizer state offloading). Note that because different chunks of the model are delegated to different workers, if we want to access the model in its entirety on any one node (for example, if we want to checkpoint it), we would need to “unwrap” the model:
```
1unwrapped_model = accelerator.unwrap_model(model)
2unwrapped_model.save_pretrained(
3    ckpt_path_epoch,
4    is_main_process=accelerator.is_main_process,
5    save_function=accelerator.save,
6    safe_serialization=True,
7    state_dict=accelerator.get_state_dict(model),
8)
9
```

### LinkSpecial Tokens[](https://anyscale.com/blog/<#special-tokens>)
To perform fine-tuning effectively, data needs to be structured appropriately. Rather than having to prompt a task by describing it as instructions to the LLM, we can simply encode this in plain text by utilizing “special tokens”:
Before:
```
1{"text": "You are to solve the following math question. Please write 
2out your reasoning ... etc ... {question}\n{answer}"}
3
```

After:
```
1{"text": "<START_Q>{question}<END_Q><START_A>{answer}<END_A>}
2
```

The special tokens allow us to easily encode the structure of our task, as well as providing a signal for when a model should stop producing output. With the example above, we can define “<END_A>” to be the stopping token. This will guarantee that the model will stop producing output when it is done with the task as opposed to waiting for it to output an end-of-sentence token. 
The Llama models tokenizer, by default, outputs 32000 unique token IDs. After adding the four special tokens above to the tokenizer, it will instead output 32004 unique IDs – “<START_Q>” will have an ID of 32000, “<END_Q>” will have an ID of 32001, and so forth. In our script, these special tokens are added like so: 
```
1tokenizer = AutoTokenizer.from_pretrained(pretrained_path, ...)
2tokenizer.add_tokens(special_tokens, special_tokens=True)
3# this will make new learnable parameters for specialized tokens4model.resize_token_embeddings(len(tokenizer))
5
```

### LinkCompute Details[](https://anyscale.com/blog/<#compute-details>)
For the 7B and 13B models, we used 16xA10Gs, and for the 70B model, we used 32xA10Gs (across 4x g5.48xlarge instances). When using Ray, there's no need to secure A100s to perform full-parameter fine-tuning on these models! The process is simply repeated for each task. Figures below show an example run based on a context length of 512, with a total of 3.7M effective tokens per epoch on GSM8k dataset. 
We ran the training for a maximum of 10 epochs and selected the best checkpoint according to the minimum perplexity score on the validation set.
!Llama 2 learning curveLlama 2 learning curve
 _The learning curves obtained from a full-parameter fine-tuning Llama-2 model of different sizes. From these plots you can clearly see when the training starts to overfit the data. Perplexity graphs are good indicators of when to stop the training._
##  LinkFunctional Representation of Unstructured Text (ViGGO)[](https://anyscale.com/blog/<#functional-representation-of-unstructured-text-\(viggo\)>)
The first task we examine is based on the _ViGGO_ dataset. It is an English data-to-text generation dataset with the data centering around video game opinions. The original task involves converting a “functional representation” (a set of attribute-values) into coherent text that incorporates those attributes. However, we will reverse this task: transforming unstructured text into a structured and parsable “functional representation”. This representation condenses the information present in the text and can be used for indexing and other downstream applications. While the domain is just video games, this general problem is one that many enterprises are keen to solve. 
### LinkExample Data Point[](https://anyscale.com/blog/<#example-data-point>)
Let's examine an example from this task to understand the level of difficulty it can present for an LLM:
!Text and Representation TableText and Representation Table
Given a target sentence the model has to construct the underlying meaning representation of the input sentence as a single function with attributes and attribute values. This function should describe the target string accurately and must be one of the following:
```
1['inform', 'request', 'give_opinion', 'confirm', 'verify_attribute',
2'suggest', 'request_explanation', 'recommend', 'request_attribute']
3
```

The attributes must be one of the following:
```
1['name', 'release_year', 'esrb', 'genres', 'platforms', 'available_on_steam',
2'has_linux_release', 'has_mac_release', 'specifier', 'rating', 'player_perspective',
3'has_multiplayer', 'developer', 'exp_release_date']
4
```

Let's prompt a few models to see if they can get anywhere close to our intention. Here is the prompt we used:
```
1Given a target sentence construct the underlying meaning representation
2of the input sentence as a single function with attributes and attribute
3values. This function should describe the target string accurately and the
4function must be one of the following ['inform', 'request', 'give_opinion',
5'confirm', 'verify_attribute', 'suggest', 'request_explanation',
6'recommend', 'request_attribute'] .
7
8The attributes must be one of the following:
9['name', 'exp_release_date', 'release_year', 'developer', 'esrb', 'rating',
10'genres', 'player_perspective', 'has_multiplayer', 'platforms',
11'available_on_steam', 'has_linux_release', 'has_mac_release', 'specifier']
12The order your list the attributes within the function must follow the
13order listed above. For example the 'name' attribute must always come 
14before the 'exp_release_date' attribute, and so forth.
15
16For each attribute, fill in the corresponding value of the attribute 
17within brackets. A couple of examples are below. Note: you are to output
18the string after "Output: ". Do not include "Output: " in your answer.
19
20Example 1)
21Sentence: Dirt: Showdown from 2012 is a sport racing game for the
22PlayStation, Xbox, PC rated E 10+ (for Everyone 10 and Older). 
23It's not available on Steam, Linux, or Mac.
24Output: inform(name[Dirt: Showdown], release_year[2012], 
25esrb[E10+ (for Everyone 10 and Older)], genres[driving/racing, sport],
26platforms[PlayStation, Xbox, PC], available_on_steam[no], 
27has_linux_release[no], has_mac_release[no])
28
29Example 2) 
30Sentence: Were there even any terrible games in 2014?
31Output: request(release_year[2014], specifier[terrible])
32
33Example 3)
34Sentence: Adventure games that combine platforming and puzzles 
35can be frustrating to play, but the side view perspective is 
36perfect for them. That's why I enjoyed playing Little Nightmares.
37Output: give_opinion(name[Little Nightmares], rating[good],
38genres[adventure, platformer, puzzle], player_perspective[side view])
39
40Example 4)
41Sentence: Since we're on the subject of games developed by Telltale 
42Games, I'm wondering, have you played The Wolf Among Us?
43Output: recommend(name[The Wolf Among Us], developer[Telltale Games])
44
45Example 5) 
46Sentence: Layers of Fear, the indie first person point-and-click adventure game?
47Output: confirm(name[Layers of Fear], genres[adventure, indie,
48point-and-click], player_perspective[first person])	
49
50Example 6) 
51Sentence: I bet you like it when you can play games on Steam, like 
52Worms: Reloaded, right?	
53Output: suggest(name[Worms: Reloaded], available_on_steam[yes])
54
55Example 7)
56Sentence: I recall you saying that you really enjoyed The Legend 
57of Zelda: Ocarina of Time. Are you typically a big fan of games
58on Nintendo rated E (for Everyone)?	
59Output: verify_attribute(name[The Legend of Zelda: Ocarina of Time],
60esrb[E (for Everyone)], rating[excellent], platforms[Nintendo])
61
62Example 8)
63Sentence: So what is it about the games that were released in 200564that you find so excellent?	
65Output: request_explanation(release_year[2005], rating[excellent])
66
67Example 9)
68Sentence: Do you think Mac is a better gaming platform than others?
69Output: request_attribute(has_mac_release[])
70
71Give the output for the following sentence:
72{input}
73
```

**Input Query:** What's a really fast-paced game with multiplayer that you like to play? 
**Expected Output:** request(has_multiplayer[yes], specifier[fast-paced])
!Llama 2 ModelsLlama 2 Models
As observed, these models do not align well with our intended output. This particular task is not one that can be easily accomplished through prompt-engineering alone. Also notice the length of the input context being passed in for these models – this large input makes inference time for producing an output significantly longer than the input text itself. With all this in mind, we are interested in exploring how far we can push the limits of fine-tuning on this task.
### LinkWhy Might Fine-Tuning Be Promising?[](https://anyscale.com/blog/<#why-might-fine-tuning-be-promising?>)
In one of our previous blog posts, we discussed the idea that "_fine-tuning is for form, not facts_". So, does it make sense to expect fine-tuned models to outperform other methods such as prompt engineering or few-shot prompting on this particular task?
The answer to this question isn't straightforward and requires experimentation. However, there are a couple of key insightful questions that can guide you in formulating a hypothesis on whether fine-tuning could add substantial value for your specific use case:
  1. **New Concepts:** Can we assume that the base model has encountered the concepts within this task (concepts related to video games, etc) in its pre-training data, or is this an entirely new concept? If it is a completely new concept (or fact), the chances of the model learning it through small-scale fine-tuning are quite low.
  2. **Promising few-shot:** Do you observe improvements when you employ few-shot prompting? This technique involves showing the model a few examples of inputs and outputs, then asking it to complete the answer following the same pattern. If you notice significant improvements, fine-tuning could potentially offer even better results. This is because fine-tuning allows you to incorporate far more examples into the model's internal neural network weights, rather than being constrained by context length and consuming tokens for the prompt prefix.
  3. **Token budget:** Even if prompt-engineering is working for you, you must provide the usually lengthy prompts as input for **every** request. This approach can quickly consume your token budget. In the long run, it might be more cost-effective to fine-tune a niche model specifically for that task, thereby saving money.

This particular task revolves around pattern recognition, necessitating a basic grasp of language and underlying concepts but not demanding intricate logical reasoning. More importantly, this task is grounded, meaning all required "facts" for its output are already embedded in the input. It is evident that a lengthier input prompt incorporating examples aids the model's comprehension of our intent, and that's a good indicator that even fine-tuning smaller Llama-2 models could significantly enhance performance in addressing this task.
### LinkEvaluation[](https://anyscale.com/blog/<#evaluation>)
Evaluating this task can be done from a few angles. While this task is deterministic enough to warrant checking for an exact character match, this would not be a fair metric for the non-fine-tuned models. Instead, we first check if the output function is predicted correctly. From there, we also check if the attribute types are correct. The attribute types within the function follow a strict precedence and so we check that the model output adheres to this ordering. This is mentioned in the prompt for instruction-following models (i.e. GPT, llama-2-chat), so these models are expected to output attributes following this rule. This is a hard guideline to pick up from just a few examples and the model has to pay attention to the specific rule and understand the meaning behind it. 
To speed up evaluation, we utilized Ray's batch inference API for scaling up inference in conjunction with Anyscale's Aviary for serving our customized LLMs. Utilizing these two components allowed us to chain LLM generation with postprocessing and distribute it across many machines. Investing time in a robust evaluation framework is extremely important, as it forms the foundation of any model development process.
### LinkResults[](https://anyscale.com/blog/<#results>)
!Viggo DatasetViggo Dataset
 _Dark colors present chat model performance using the mentioned prompt. For GPT-4, we report both evaluations numbers: with and without attribute order importance. Fine-tuned models consistently achieve >90% success rate in both evaluations methods, never diverging from the precedence rule._
Both the 7b and 13b models significantly improve in accuracy with fine-tuning. While GPT-4’s accuracy significantly drops when attribute precedence is considered, the outputs of the fine-tuned models always follow precedence and accuracy remains unchanged with this added evaluation constraint.
### LinkTakeaways[](https://anyscale.com/blog/<#takeaways>)
The ViGGO dataset highlights the strongest aspects of fine-tuning, and the results clearly back it up. When requiring structured form, fine-tuning can provide reliable and efficient means to accomplish your task. This task also shows that requiring a “structured form” does not just mean matching a simple regex or JSON format, tasks that perhaps can be accomplished with libraries like _guidance_. With ViGGO, an LLM needs to determine whether an argument should be included or not, as well as ensuring that the order of the included arguments follows precedence. 
There is also the argument of efficiency. Besides the fact that significantly more input tokens were required for the general models, the fine-tuned results were achieved with only the 7b & 13b models. Serving a Llama 7b model is significantly cheaper than footing the bill for GPT-4 endpoint calls, especially as your service grows. 
## LinkSQL Generation with Llama-2 fine-tuned models[](https://anyscale.com/blog/<#sql-generation-with-llama-2-fine-tuned-models>)
The next task we examine is SQL generation. The goal is to convert natural language queries to a functional SQL query that can then be executed on your database. For this task we examine the _b-mc2/sql-create-context_ dataset from Hugging Face, which is a combination of the _WikiSQL_ and _Spider_ datasets. 
Each of the 78,577 data points consists of a natural language query, corresponding SQL CREATE TABLE statements, and then the SQL query corresponding to the natural language question. The goal of the LLM is to take in the natural language query and SQL CREATE TABLE statements as context, and produce a SQL output that can query the given SQL tables and produce an output that answers the natural language query. 
### LinkExample Data Point[](https://anyscale.com/blog/<#example-data-point>)
One issue specific to this dataset was incorrect ground truth SQL outputs that had to be filtered out. In many data points, attributes that were integers were labeled as VARCHARs in the CREATE TABLE statements:
!Example Datapoint ChartExample Datapoint Chart
Note that the attribute “week” is defined as a string in the CREATE TABLE statement, however, is treated like an integer in the SQL query. To avoid resulting issues when testing, we filtered out all SQL queries that assumed an attribute was an integer, cutting the dataset from 70k data points to 45k data points. While this is a strong constraint on the dataset, the python SQL engine we were using did not have an easy way to type check between the CREATE TABLE and SQL query statements – unless we wanted to write an algorithm to parse through the AST and type check ourselves. Nonetheless, the resulting dataset was still challenging with plenty of tricky data points like the following: 
!Another Example Datapoint ChartAnother Example Datapoint Chart
### LinkWhy Might Fine-Tuning Be Promising?[](https://anyscale.com/blog/<#why-might-fine-tuning-be-promising?>)
This task shares some similarities to ViGGO – the LLM is trying to output a structured representation of natural language, which in this case is SQL. Unlike ViGGO, this task is slightly more ambiguous as there can be several SQL queries that could output the correct answer when executed on a data table. Nonetheless, this task is a great fit for fine-tuning as success hinges on an LLM’s ability to learn the “structure” of SQL and convert natural language to this structure. 
### LinkEvaluation[](https://anyscale.com/blog/<#evaluation>)
A major challenge with a SQL task like this is evaluation. Once the model has outputted a SQL query, how do we check if it is correct? One naive way would be to check character by character equivalence between the generated SQL code and the ground truth query provided by the dataset. This approach is sensitive to a lot of factors that can raise the number of false negatives. Another way is to check the equivalence of the abstract syntax tree (AST) of the two queries. However, this is also susceptible to things like order of variable names, etc. The last approach that would be the most reliable is to run the code on a fake dataset and check the equivalence of the outputs.
What we decided to do for this task is to use OpenAI's GPT-3.5 endpoint to generate unit tests for a few hundreds of these examples. GPT-3.5 is prompted to look at the question, the table schema, and the answer and generate a fake table with ten data points. This small data table can be used to compare and test the validity of an SQL query:
```
1from sqlglot.executor import execute
2
3gpt_data_table = {
4"table_name_64": [
5    {
6"position": "mayor",
7"first_election": "1988 as vice mayor 2009"8    },
9    ...
10    {
11"position": "mayor",
12"first_election": "2007 as councilor 2014"13    }
14  ]
15}
16
17 model_sql = get_llama_response(sql_prompt.format(create_table=..., query=...))
18 model_sql = model_sql[model_sql.find("<SQL>")+len("<SQL>"):model_sql.find("</SQL>")]
19 model_sql = model_sql.lower()
20
21try:
22        queryresult = execute(sql_query, tables=table)
23        modelresult = execute(model_sql, tables=table)
24ifstr(queryresult) == str(modelresult):
25# output is correct 26except Exception as e: 
27print(e)
28
```

To ensure the quality of the GPT-3.5 generated data tables, we first executed the ground truth SQL query against it. If the resulting table was either empty, or the same length as the initial table, the example was discarded. This resulted in filtering out roughly 50% of the GPT produced data tables. 
### LinkResults[](https://anyscale.com/blog/<#results>)
Both the Llama-7b and 13b fine-tuned models outperform the 70b-chat and GPT-4 models. One common source of error for the Llama chat models was that it would not consistently put its output SQL within <SQL> tags as instructed by the prompt – this was more common in the 7b and 13b chat models than the 70b one. 
!Various ModelsVarious Models
 _Dark colors present chat model performance. Fine-tuned models achieve ~90% success rate._
Note that some of the natural language queries in that SQL dataset were not perfect English. This noise from the dataset is likely to have slightly affected the GPT-4 results. It nonetheless highlights an important point about fine-tuning – that these models will quickly adapt to the quirks of a dataset, whatever those quirks may be. 
### LinkTakeaways[](https://anyscale.com/blog/<#takeaways>)
In this example, both the 7b and 13b fine-tuned models outperformed GPT-4 as well as the 70b chat model. Also keep in mind that for every call to GPT and the Llama base chat models, a lengthy prompt needed to be fed in. Additionally, while this wasn’t an issue for GPT, the Llama chat models would often output hundreds of miscellaneous tokens that were unnecessary for the task, further slowing down their inference time (e.g. “Sure! Happy to help…”).
## LinkGrade School Math reasoning (GSM8k)[](https://anyscale.com/blog/<#grade-school-math-reasoning-\(gsm8k\)>)
The final task we consider is GSM8k. This task is a standard academic benchmark for evaluating LLMs on math reasoning and understanding. The challenge of fine-tuning on this dataset differs from the previous two. As opposed to just learning structure, we wanted to see how much an LLM could improve its ability to reason on math problems.
### LinkExample data point[](https://anyscale.com/blog/<#example-data-point>)
**Question**| **Answer**  
---|---  
Natalia sold clips to 48 of her friends in April, and then she sold half as many clips in May. How many clips did Natalia sell altogether in April and May?| Natalia sold 48/2 = 24 clips in May. \n Natalia sold 48+24 = 72 clips altogether in April and May. \n#### 72  
While it would be impressive for an LLM to immediately produce the answer of 72, current LLMs are incapable of internalizing their "thought" process leading to the final answer. Instead, they must generate their "thought" process as part of the output, ensuring that the generation of each subsequent word is based on a solid reasoning process. The target answers in this dataset are formatted to outline the thought process, concluding with the final answer in the #### {answer} format for easy parsing.
This task necessitates that the language models not only understand simple calculations, but also know how to progress from the given assumptions to intermediate conclusions, and ultimately to a final answer. Thus, LLMs need a solid grasp of language (including the understanding of concepts and their interrelationships), as well as the ability to lay out a logical chain of thought. The interesting question here is how well do the chat-tuned models do on this task and how much can we gain with fine-tuning? 
### LinkEvaluation[](https://anyscale.com/blog/<#evaluation>)
To effectively evaluate an LLM on this task, you need a reliable method to extract the final answer generated by the language model and compare it to the ground truth. While this isn’t an issue with fine-tuned models, a common challenge with general language models is their inability to consistently adhere to a desired output format, making it tricky to evaluate. There are various proposed solutions for constrained generation, such as _guidance_, hinting at the constraints in the prompt, or providing few-shot examples. However, for the sake of simplicity and to ensure a specific output format for automating the evaluation process, we utilized _OpenAI's function calling API_.
The idea is to employ a gpt-4 or gpt-3.5-turbo model to process the generated response for LLMs that lack a predetermined output structure. Given the question, these models can extract the final answer without correcting it (if there are any errors). The following code demonstrates the extraction procedure:
```
1def extract_number_from_text(question: str, text: str) -> int:
2    ## Use GPT-3.5-turbo's functional API to extract the number from the text
3
4    functions = [
5        {
6"name": "report_answer",
7"description": "Reports the final answer from the text.",
8"parameters": {
9"type": "object",
10"properties": {
11"number": {
12"type": "integer",
13"description": ...
14                    },
15                },
16"required": ["number"],
17            },
18        }
19    ]
20
21    resp = openai.ChatCompletion.create(
22        model="gpt-3.5-turbo-0613",
23        messages=[...],
24        functions=functions,
25        function_call={"name": "report_answer"},
26    )
27
28    resp_msg = resp["choices"][0]["message"]
29    function_args = json.loads(resp_msg["function_call"]["arguments"])
30
31    return function_args["number"]
32
```

We instruct the gpt-3.5 model to read the question and utilize a function named report_answer, which accepts an integer number as its input. This approach ensures that the model will consistently output the final integer number found within the content generated by another model. For example if the model answers that “The answer is four” we can still parse the answer as answer = 4. We've tested this on the provided answers in the dataset to confirm its efficacy and ensure that it doesn't present any edge cases. The downside of this approach is that we need to pay for OpenAI tokens for evaluation. 
It's worth noting that the fine-tuned models quickly learn to adhere to the pattern exhibited in the target answers and rarely deviate from it – even if the answer itself is incorrect, the output structure is very predictable. Therefore, when evaluating fine-tuned models, we simply apply the regex pattern of #### {answer} to the output generated by these models, eliminating the need for post processing with OpenAI endpoints saving money during evaluation. 
### LinkWhy Might Fine-Tuning Be Promising?[](https://anyscale.com/blog/<#why-might-fine-tuning-be-promising?>)
For this task, we believe that the model has been exposed to sufficient mathematical concepts during its pre-training phase. As such, it should be able to generalize from there, and fine-tuning should help in activating the appropriate mode of its internal knowledge. Additionally, if we examine the published benchmarks on Llama-2, it performs notably well on the GSM8k dataset with 8 few-shot examples, outperforming other models. This underscores the importance of extensive pre-training data. The question then becomes: Can we further improve these numbers through fine-tuning?
!Benchmark Llama 2 TableBenchmark Llama 2 Table
### LinkBaselines[](https://anyscale.com/blog/<#baselines>)
Establishing the correct baselines is crucial for methodically measuring progress and the effectiveness of different approaches. For this test, we considered the following baselines:
  1. The reported 8-shot prompting approach using the base pre-trained models (note that we did not re-run these experiments ourselves; we are simply quoting the published results).
  2. Several prompt-engineered templates for the chat-tuned Llama variants. These “chat-tuned” models were trained by Meta using RLHF to function as general-purpose assistant models. If the RLHF training is conducted as rigorously as OpenAI's approach, we should expect high-quality results from these models as well. The following table presents a view of the prompt templates we used and illustrates how they differ from each other.

!Comparison with BaselineComparison with Baseline
### LinkResults[](https://anyscale.com/blog/<#results>)
!GSM8k Results Across LlamaGSM8k Results Across Llama
 _The fine-tuned 7b and 13b models have an improved accuracy by 10% when compared to their base counterparts. The margin is less when compared to the chat-tuned baselines, as these were likely trained with math examples in the chat-tuning process._
There a couple takeaways from these results:
  1. **Fine-tuning the base model consistently enhances its performance on this specific task.** However, it may not necessarily yield results significantly better than those of the chat-tuned models. Keep in mind that the chat models were fine-tuned to be versatile, so determining whether they are sufficient for your task requires experimenting with different prompts. 
  2. **Prompting the fine-tuned model does not always lead to better performance than the base model.** For instance, Llama-2-70B-chat could actually underperform relative to the base model with an 8-shot example prompt, while the fine-tuned model consistently does better than the 8-shot prompted base model. 
  3. **Fine-tuned models for this task demonstrate superior performance across all model sizes** , while potentially costing significantly less than the other baselines during serving. For this task, you will be charged for all the tokens in the prompt for each request, but for fine-tuned models, you would effectively only pay for the number of tokens in the question. Depending on the serving traffic you are targeting, your overall cost could be lower while using a more performant, customized model.
  4. **Chat-tuned models performed better than the non-fine-tuned base model.** It is important to make the distinction between the chat-tuned model and the base pre-trained model. The chat-tuned models were likely trained with math examples in the chat-tuning process, resulting in better accuracy than the base model. 

### LinkFurther Improving Fine-Tuning Results[](https://anyscale.com/blog/<#further-improving-fine-tuning-results>)
While we do see improvements from fine-tuning across the board, we wanted to focus on Llama-13b and see if results could be further improved with standard fine-tuning techniques. The GSM8k training dataset is relatively small, with only 8k data points. Since learning to solve math problems is less straightforward than just learning to output answers in a specific format, we figured it was unlikely that just 8k data points would be sufficient in unlocking the full-potential of a Llama-13b model on this dataset. 
With this in mind, we took the base Llama-13b model and first fine-tuned it on the MathQA dataset, before subsequently fine-tuning the model on the original GSM8k dataset. This extra round of fine-tuning resulted in a further 10% increase from the initial fine-tuned model results, adding up to a 20% increase from the base model. 
!Llama-13b GSM8k Accuracy.Llama-13b GSM8k Accuracy.
_Fine-tuning with just the GSM8k data yields a 10% improvement. Fine-tuning in two stages with both the MathQA and GSM8k datasets result in a cumulative 10% improvement._
While one might expect this to align with the classic “more data, better model” paradigm within machine learning, we found these results to be surprising given the nature of the MathQA dataset. MathQA is a collection of 30,000 question/answer pairs that are much noisier and of different structure than the GSM8K dataset. The answers are of poorer quality, and unlike GSM8k, the final answers in MathQA are multiple choice. As an example:
**Question**| **Answer Options**| **Answer**  
---|---|---  
the banker ' s gain of a certain sum due 3 years hence at 10 % per annum is rs . 36 . what is the present worth ?| a ) rs . 400 , b ) rs . 300 , c ) rs . 500 , d ) rs . 350 , e ) none of these| explanation : t = 3 years r = 10 % td = ( bg × 100 ) / tr = ( 36 × 100 ) / ( 3 × 10 ) = 12 × 10 = rs . 120 td = ( pw × tr ) / 100 ⇒ 120 = ( pw × 3 × 10 ) / 100 ⇒ 1200 = pw × 3 pw = 1200 / 3 = rs . 400 answer : option a  
Notice the odd spacing and compare the quality of this datapoint to the GSM8k question/answer pair from earlier:
**Question**| **Answer**  
---|---  
Natalia sold clips to 48 of her friends in April, and then she sold half as many clips in May. How many clips did Natalia sell altogether in April and May?| Natalia sold 48/2 = 24 clips in May. \n Natalia sold 48+24 = 72 clips altogether in April and May. \n#### 72  
Stratifying the fine-tuning into two rounds was an effective way to leverage this MathQA dataset and yield a much better final result for the GSM8k dataset.
### LinkConclusion[](https://anyscale.com/blog/<#conclusion>)
Hopefully going through these three examples should have convinced you that while closed-source models like GPT-4, Claude-2, etc. are strong enablers for prototyping and proving the initial value, they are not sufficient for running performant LLM apps in production. Fine-tuning LLMs for niche tasks is one of the promising solutions to elicit value out of LLMs for your business, not just because of privacy, but also latency, cost, and sometimes quality (e.g. in ViGGO and SQL examples). For fine-tuning your focus should be on collecting data and setting up evaluation pipelines that help you understand trade-offs between different solutions tied to your business, and not think about the infrastructure and intricacies of fine-tuning. At Anyscale we have built the best fine-tuning and serving solutions on top of Ray, so you can start repeating the same process outlined here on your own data and on your own cloud. Checkout _Anyscale Endpoints_ to learn more.**Learn More** We’ll be demonstrating this capability and diving into a wide range of AI use cases with many of the world’s top AI pioneers from OpenAI, Netflix, Pinterest, Verizon, Instacart and others at Ray Summit 2023 this Sept 18-19 in San Francisco.
#### Table of contents
  * Fine-Tuning Basics
  * General Training Flow
  * Special Tokens
  * Compute Details
  * Functional Representation of Unstructured Text (ViGGO)> "Functional Representation of Unstructured Text \(ViGGO\)")
  * Example Data Point
  * Why Might Fine-Tuning Be Promising?
  * Evaluation
  * Results
  * Takeaways
  * SQL Generation with Llama-2 fine-tuned models
  * Example Data Point
  * Why Might Fine-Tuning Be Promising?
  * Evaluation
  * Results
  * Takeaways
  * Grade School Math reasoning (GSM8k)> "Grade School Math reasoning \(GSM8k\)")
  * Example data point
  * Evaluation
  * Why Might Fine-Tuning Be Promising?
  * Baselines
  * Results
  * Further Improving Fine-Tuning Results
  * Conclusion

#### Sharing
[](https://anyscale.com/blog/<https:/www.facebook.com/sharer.php?u=https%3A%2F%2Fwww.anyscale.com%2Fblog%2Ffine-tuning-llama-2-a-comprehensive-case-study-for-tailoring-models-to-unique-applications>)[](https://anyscale.com/blog/<https:/twitter.com/intent/tweet?url=https%3A%2F%2Fwww.anyscale.com%2Fblog%2Ffine-tuning-llama-2-a-comprehensive-case-study-for-tailoring-models-to-unique-applications&text=Fine-Tuning%20Llama-2%3A%20A%20Comprehensive%20Case%20Study%20for%20Tailoring%20Models%20to%20Unique%20Applications>)[](https://anyscale.com/blog/<https:/www.linkedin.com/sharing/share-offsite/?url=https%3A%2F%2Fwww.anyscale.com%2Fblog%2Ffine-tuning-llama-2-a-comprehensive-case-study-for-tailoring-models-to-unique-applications>)
#### Sign up for product updates
#### Recommended content
#### 30% Faster Multimodal AI Training with Ray and Disaggregated Hybrid ParallelismRead more
# Ready to try Anyscale?
Access Anyscale today to see how companies using Anyscale and Ray benefit from rapid time-to-market and faster iterations across the entire AI lifecycle.
Try free
!Cookiebot session tracker icon loaded
