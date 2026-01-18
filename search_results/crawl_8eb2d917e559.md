---
title: "FSDP + QLoRA – Axolotl"
source: https://docs.axolotl.ai/docs/fsdp_qlora.html
date: unknown
description: "Use FSDP with QLoRA to fine-tune large LLMs on consumer GPUs."
word_count: 253
---

## Background[](https://docs.axolotl.ai/docs/<#background>)
Using FSDP with QLoRA is essential for **fine-tuning larger (70b+ parameter) LLMs on consumer GPUs.** For example, you can use FSDP + QLoRA to train a 70b model on two 24GB GPUs1.
Below, we describe how to use this feature in Axolotl.
## Usage[](https://docs.axolotl.ai/docs/<#usage>)
To enable `QLoRA` with `FSDP`, you need to perform the following steps:
> ![Tip] See the example config file in addition to reading these instructions.
  1. Set `adapter: qlora` in your axolotl config file.
  2. Enable FSDP in your axolotl config, as described here.
  3. Use one of the supported model types: `llama`, `mistral` or `mixtral`.

## Enabling Swap for FSDP2[](https://docs.axolotl.ai/docs/<#enabling-swap-for-fsdp2>)
If available memory is insufficient even after FSDP’s CPU offloading, you can enable swap memory usage by setting `cpu_offload_pin_memory: false` alongside `offload_params: true` in FSDP config.
This disables memory pinning, allowing FSDP to use disk swap space as fallback. Disabling memory pinning itself incurs performance overhead, and actually having to use swap adds more, but it may enable training larger models that would otherwise cause OOM errors on resource constrained systems.
## Example Config[](https://docs.axolotl.ai/docs/<#example-config>)
examples/llama-2/qlora-fsdp.yml contains an example of how to enable QLoRA + FSDP in axolotl.
## References[](https://docs.axolotl.ai/docs/<#references>)
  * PR #1378 enabling QLoRA in FSDP in Axolotl.
  * Blog Post from the Answer.AI team describing the work that enabled QLoRA in FSDP.
  * Related HuggingFace PRs Enabling FDSP + QLoRA: 
    * Accelerate PR#2544
    * Transformers PR#29587
    * TRL PR#1416
    * PEFT PR#1550

## Footnotes[](https://docs.axolotl.ai/docs/<#footnotes-1>)
  1. This was enabled by this work from the Answer.AI team.↩︎

