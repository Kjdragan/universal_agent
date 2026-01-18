---
title: "Llama 3.3 | Model Cards and Prompt formats"
source: https://www.llama.com/docs/model-cards-and-prompt-formats/llama3_3
date: unknown
description: "."
word_count: 880
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
Model Cards & Prompt formats
# Llama 3.3
##  Introduction 
Llama 3.3 is a text-only 70B instruction-tuned model that provides enhanced performance relative to Llama 3.1 70B–and relative to Llama 3.2 90B when used for text-only applications. Moreover, for some applications, Llama 3.3 70B approaches the performance of Llama 3.1 405B.
Llama 3.3 70B is provided only as an instruction-tuned model; a pretrained version is not available.
##  Model Card 
For comprehensive technical information about Llama 3.3, including details on its enhanced performance, please see the official model card, located on GitHub.
##  Download the Model 
Download Llama 3.3.
##  Prompt Template 
Llama 3.3 uses the same prompt format as Llama 3.1. Prompts written for Llama 3.1 work unchanged with Llama 3.3.
###  Zero-shot function calling 
Llama 3.3 supports the same function-calling format as Llama 3.2. This format is designed to be more flexible and powerful than the format in 3.1. For example, all available functions can be provided in the user message. The following sections show examples of zero-shot function calling with Llama 3.3. 
####  Input Prompt Format 
The following code block demonstrates zero-shot function calling in the system message.
```
<|begin_of_text|><|start_header_id|>system<|end_header_id|>
You are an expert in composing functions. You are given a question and a set of possible functions.
Based on the question, you will need to make one or more function/tool calls to achieve the purpose.
If none of the functions can be used, point it out. If the given question lacks the parameters required by the function, also point it out. You should only return the function call in tools call sections.
If you decide to invoke any of the function(s), you MUST put it in the format of [func_name1(params_name1=params_value1, params_name2=params_value2...), func_name2(params)]
You SHOULD NOT include any other text in the response.
Here is a list of functions in JSON format that you can invoke.
[
  {
    "name": "get_weather",
    "description": "Get weather info for places",
    "parameters": {
      "type": "dict",
      "required": [
        "city"
      ],
      "properties": {
        "city": {
          "type": "string",
          "description": "The name of the city to get the weather for"
        },
        "metric": {
          "type": "string",
          "description": "The metric for weather. Options are: celsius, fahrenheit",
          "default": "celsius"
        }
      }
    }
  }
]<|eot_id|><|start_header_id|>user<|end_header_id|>
What is the weather in SF and Seattle?<|eot_id|><|start_header_id|>assistant<|end_header_id|>

```

####  Model Response Format 
```
[get_weather(city='San Francisco', metric='celsius'), get_weather(city='Seattle', metric='celsius')]<|eot_id|>

```

###  Notes 
  * The output supports multiple function calls, as well as function calls running in parallel--but note that the model doesn't actually call the functions; it only specifies which functions should be called. 
  * The JSON format for defining the functions in the system prompt is similar to Llama 3.1. 

###  Zero-shot function calling in user message 
While it is common to specify all function calls in a system message, in Llama3.3, you can also provide this information in a user message.
####  Input Prompt Format 
```
<|begin_of_text|><|start_header_id|>user<|end_header_id|>
Questions: Can you retrieve the details for the user with the ID 7890, who has black as their special request?
Here is a list of functions in JSON format that you can invoke:
[
  {
    "name": "get_user_info",
    "description": "Retrieve details for a specific user by their unique identifier. Note that the provided function is in Python 3 syntax.",
    "parameters": {
      "type": "dict",
      "required": [
        "user_id"
      ],
      "properties": {
        "user_id": {
        "type": "integer",
        "description": "The unique identifier of the user. It is used to fetch the specific user details from the database."
      },
      "special": {
        "type": "string",
        "description": "Any special information or parameters that need to be considered while fetching user details.",
        "default": "none"
        }
      }
    }
  }
]
Should you decide to return the function calls, put them in the format [func1(params_name=params_value, params_name2=params_value2...), func2(params)]
NO other text MUST be included.<|eot_id|><|start_header_id|>assistant<|end_header_id|>

```

####  Model Response Format 
```
[get_user_info(user_id=7890, special='black')]<|eot_id|>
```

###  Notes 
  * The function-calling format for the model is the same whether you specify function calls in the system message or user message. 
  * While builtin-tool calls end with `<|eom_id|>`, notice the `<|eot_id|>` for zero-shot function calls. 

###  Builtin Tool Calling 
As mentioned earlier, the Llama 3.3 prompt format is fully compatible with Llama 3.1; for more information on tool-calling with the builtin tools using Llama 3.3, see the documentation page for Llama 3.1.
Llama 3.3 and Llama 3.1 support the following builtin tools:
  * **Brave Search:** Tool call to perform web searches.
  * **Wolfram Alpha:** Tool call to perform complex mathematical calculations.
  * **Code Interpreter:** Enables the model to output python code.

Was this page helpful?
Yes
No
On this page
Llama 3.3
 Introduction 
 Model Card 
 Download the Model 
 Prompt Template 
 Zero-shot function calling 
 Notes 
 Zero-shot function calling in user message 
 Notes 
 Builtin Tool Calling 
