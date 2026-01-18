---
title: "Config Reference – Axolotl"
source: https://docs.axolotl.ai/docs/config-reference.html
date: unknown
description: "A complete list of all configuration options."
word_count: 9822
---

```
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1>)# Allow overwrite yml config using from cli
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-2>)strict: bool | None = False
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-3>)# Resume from a specific checkpoint dir
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-4>)resume_from_checkpoint: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-5>)# If resume_from_checkpoint isn't set and you simply want it to start where it left off.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-6>)# Be careful with this being turned on between different models.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-7>)auto_resume_from_checkpoints: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-8>)# Resize the model embeddings when new tokens are added to multiples of 32. This is
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-9>)# reported to improve training speed on some models
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-10>)resize_token_embeddings_to_32x: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-11>)mean_resizing_embeddings: bool | None = False
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-12>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-13>)# Whether to shrink the embeddings to len(tokenizer). By default, we won't shrink.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-14>)shrink_embeddings: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-15>)# Don't upcast the embeddings to float32 when using PEFT. Useful for low-VRAM GPUs
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-16>)embeddings_skip_upcast: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-17>)# Reinitialize model weights randomly instead of loading pretrained weights
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-18>)reinit_weights: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-19>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-20>)# module to custom trainer class to use for training
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-21>)trainer_cls: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-22>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-23>)# Use RL training: 'dpo', 'ipo', 'kto', 'simpo', 'orpo', 'grpo'
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-24>)rl: RLType | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-25>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-26>)trl: TRLConfig | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-27>) # For TRLConfig:
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-28>) # Beta parameter for the RL training. Same as `rl_beta`. Use
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-29>)beta: float | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-30>) # Maximum length of the completion for RL training.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-31>)max_completion_length: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-32>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-33>) # Whether to use VLLM for RL training.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-34>)use_vllm: bool = False
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-35>) # VLLM mode to use, one of 'server' or 'colocate'
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-36>)vllm_mode: Literal['server', 'colocate'] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-37>) # Host of the vLLM server to connect to.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-38>)vllm_server_host: str | None = 0.0.0.0
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-39>) # Port of the vLLM server to connect to.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-40>)vllm_server_port: int | None = 8000
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-41>) # Total timeout (in seconds) to wait for the vLLM server to respond.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-42>)vllm_server_timeout: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-43>) # Regex for vLLM guided decoding.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-44>)vllm_guided_decoding_regex: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-45>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-46>) # List of reward functions to load. Paths must be importable from current dir.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-47>)reward_funcs: list[str] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-48>) # List of reward weights for the reward functions.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-49>)reward_weights: list[float] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-50>) # Number of generations to sample.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-51>)num_generations: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-52>) # Whether to log completions.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-53>)log_completions: bool | None = False
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-54>) # Number of completions to print when log_completions is True.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-55>)num_completions_to_print: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-56>) # Controls whether importance sampling ratios are computed at the `'token'` or
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-57>) # `'sequence'` level. For GSPO, use `sequence`, default is None which corresponds to
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-58>) # the original GRPO paper.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-59>)importance_sampling_level: Literal['sequence', 'token'] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-60>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-61>) # Whether to sync the reference model.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-62>)sync_ref_model: bool | None = False
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-63>) # Mixup alpha for the reference model.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-64>)ref_model_mixup_alpha: float | None = 0.9
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-65>) # Sync steps for the reference model.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-66>)ref_model_sync_steps: int | None = 64
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-67>) # Whether to scale rewards by their standard deviation.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-68>)scale_rewards: bool = True
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-69>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-70>) # Sampling temperature for the GRPO policy.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-71>)temperature: float | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-72>) # Top-p sampling probability for the generation policy.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-73>)top_p: float | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-74>) # Top-k sampling for the generation policy.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-75>)top_k: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-76>) # Minimum probability for the generation policy.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-77>)min_p: float | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-78>) # Penalty for tokens that appear in prompt and generated text.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-79>)repetition_penalty: float | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-80>) # Number of iterations per batch (μ) for GRPO.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-81>)num_iterations: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-82>) # Epsilon value for clipping in the GRPO algorithm.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-83>)epsilon: float | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-84>) # Upper-bound epsilon value for clipping in the GRPO algorithm.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-85>)epsilon_high: float | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-86>) # Whether to use Liger loss for GRPO.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-87>)use_liger_loss: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-88>) # Loss formulation to use. Supported values: grpo, bnpo, dr_grpo.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-89>)loss_type: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-90>) # Whether to exclude truncated completions from loss calculation.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-91>)mask_truncated_completions: bool = False
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-92>) # Enable sleep mode for vLLM to offload VRAM when idle
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-93>)vllm_enable_sleep_mode: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-94>) # Path to custom rollout function. Must be importable from current dir.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-95>)rollout_func: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-96>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-97>)vllm: VllmConfig | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-98>) # For VllmConfig:
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-99>) # Device to use for VLLM
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-100>)device: str | None = auto
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-101>) # Tensor parallel size for VLLM
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-102>)tensor_parallel_size: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-103>) # Data parallel size for VLLM
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-104>)data_parallel_size: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-105>) # GPU memory utilization for VLLM
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-106>)gpu_memory_utilization: float | None = 0.9
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-107>) # Data type for VLLM
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-108>)dtype: str | None = auto
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-109>) # Maximum length of the model context for VLLM
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-110>)max_model_len: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-111>) # Enable prefix caching for VLLM
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-112>)enable_prefix_caching: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-113>) # Host for the vLLM server to start on
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-114>)host: str | None = 0.0.0.0
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-115>) # Port of the vLLM server to start on
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-116>)port: int | None = 8000
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-117>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-118>) # Enable reasoning for VLLM
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-119>)enable_reasoning: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-120>) # Reasoning parser for VLLM
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-121>)reasoning_parser: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-122>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-123>)qat: QATConfig | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-124>) # For QATConfig:
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-125>) # Fake quantization layout to use for activation quantization.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-126>)activation_dtype: TorchAOQuantDType | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-127>) # Fake quantization layout to use for weight quantization.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-128>)weight_dtype: TorchAOQuantDType = TorchAOQuantDType.int8
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-129>) # Quantize embedding
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-130>)quantize_embedding: bool | None = False
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-131>) # The number of elements in each group for per-group fake quantization
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-132>)group_size: int | None = 32
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-133>) # The number of steps to apply fake quantization after
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-134>)fake_quant_after_n_steps: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-135>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-136>)quantization: PTQConfig | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-137>) # For PTQConfig:
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-138>) # Fake quantization layout to use for weight quantization.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-139>)weight_dtype: TorchAOQuantDType = TorchAOQuantDType.int8
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-140>) # Fake quantization layout to use for activation quantization.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-141>)activation_dtype: TorchAOQuantDType | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-142>) # Whether to quantize the embedding layer.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-143>)quantize_embedding: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-144>) # The number of elements in each group for per-group fake quantization
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-145>)group_size: int | None = 32
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-146>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-147>)# Reward modelling: `True` or `False`
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-148>)reward_model: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-149>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-150>)# Configuration for dynamic checkpointing (trigger by file or signal). Set 'enabled:
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-151>)# true' to activate this feature.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-152>)dynamic_checkpoint: DynamicCheckpointConfig | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-153>) # For DynamicCheckpointConfig:
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-154>) # Enable dynamic checkpoint triggering during training. Create a file
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-155>) # 'axolotl_checkpoint.save' in the configured `output_dir` to trigger.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-156>)enabled: bool = False
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-157>) # Check for trigger file every N steps (reduces I/O overhead). Default: 100
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-158>)check_interval: int = 10
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-159>) # Custom trigger filename (optional). If not specified, defaults to
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-160>) # 'axolotl_checkpoint.save'. Specify a filename (not a full path) to override the
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-161>) # default.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-162>)trigger_file_path: str = 
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-163>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-164>)# Process reward modelling: `True` or `False`
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-165>)process_reward_model: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-166>)# Coefficient to incentivize the reward model to output mean-zero rewards (proposed by
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-167>)# https://huggingface.co/papers/2312.09244, Eq. 2). Recommended value: `0.01`.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-168>)center_rewards_coefficient: float | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-169>)num_labels: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-170>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-171>)# Whether to perform weighting in DPO trainer
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-172>)dpo_use_weighting: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-173>)dpo_use_logits_to_keep: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-174>)dpo_label_smoothing: float | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-175>)dpo_norm_loss: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-176>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-177>)# Whether to use Liger kernel for DPO loss.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-178>)dpo_use_liger_kernel: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-179>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-180>)dpo_padding_free: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-181>)dpo_generate_during_eval: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-182>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-183>)# A list of one or more datasets to finetune the model with
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-184>)datasets: Annotated[list[SFTDataset | DPODataset | KTODataset | StepwiseSupervisedDataset], MinLen(1)] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-185>) # For SFTDataset:
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-186>) # HuggingFace dataset repo | s3:// | gs:// | path to local file or directory
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-187>)path: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-188>) # name of dataset split to load from
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-189>)split: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-190>) # The type of prompt to use for training. [alpaca, gpteacher, oasst, reflection]
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-191>)type: str | UserDefinedPrompterType | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-192>)  # For UserDefinedPrompterType:
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-193>)  # Custom user instruction prompt
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-194>)system_prompt: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-195>)  # Use {system} as key to be replaced
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-196>)system_format: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-197>)field_system: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-198>)field_instruction: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-199>)field_input: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-200>)field_output: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-201>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-202>)  # Customizable to be single line or multi-line. Use {instruction}/{input} as key to
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-203>)  # be replaced. 'format' can include {input}
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-204>)format: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-205>)  # 'no_input_format' cannot include {input}
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-206>)no_input_format: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-207>)input_transform: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-208>) # split dataset into N pieces (use with shards_idx)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-209>)shards: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-210>) # the index of sharded dataset to use
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-211>)shards_idx: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-212>) # process dataset in N sequential chunks for memory efficiency (exclusive with
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-213>) # `shards`)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-214>)preprocess_shards: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-215>)conversation: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-216>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-217>) # The name of the chat template to use for training, following values are supported:
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-218>) # tokenizer_default: Uses the chat template that is available in the
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-219>) # tokenizer_config.json. If the chat template is not available in the tokenizer, it
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-220>) # will raise an error. This is the default.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-221>) # alpaca/inst/chatml/gemma/cohere/llama3/phi_3/deepseek_v2/jamba: These chat templates
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-222>) # are available in the axolotl codebase at src/axolotl/utils/chat_templates.py.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-223>) # tokenizer_default_fallback_*: where * is the name of the chat template to fallback
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-224>) # to if the tokenizer does not have a chat template else default to tokenizer. E.g.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-225>) # tokenizer_default_fallback_chatml. jinja: Uses a custom jinja template for the chat
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-226>) # template. The custom jinja template should be provided in the chat_template_jinja
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-227>) # field.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-228>)chat_template: ChatTemplate | str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-229>) # Custom jinja chat template or path to jinja file. Used only if `chat_template:
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-230>) # jinja` or empty.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-231>)chat_template_jinja: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-232>) # path to source data files
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-233>)data_files: str | list[str] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-234>)input_format: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-235>) # name of dataset configuration to load
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-236>)name: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-237>) # defines the datatype when path is a file
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-238>)ds_type: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-239>) # For `completion` datasets only, uses the provided field instead of `text` column
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-240>)field: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-241>)field_human: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-242>)field_model: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-243>) # Key containing the messages (default: "messages")
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-244>)field_messages: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-245>) # Key containing the tools (default: "tools"). Must be a list[dict] and follow JSON
[ # schema](https://json-schema.org/learn/getting-started-step-by-step).
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-247>)field_tools: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-248>) # Key containing the reasoning trace (default: "reasoning_content").
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-249>)field_thinking: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-250>) # The key the chat template expects that indicates the reasoning trace.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-251>)template_thinking_key: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-252>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-253>)message_field_role: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-254>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-255>)message_field_content: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-256>) # Mapping of properties from the input dataset to the chat template. (default:
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-257>) # message_property_mappings={'role':'role', 'content':'content'}) If a property exists
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-258>) # in the template but not in this mapping, the system will attempt to load it directly
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-259>) # from the message using the property name as the key. Example: In the mapping below,
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-260>) # 'from' is loaded from input dataset and used as 'role', while 'value' is loaded and
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-261>) # used as 'content' in the chat template.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-262>)message_property_mappings: dict[str, str] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-263>) # The key in the message turn that indicates via boolean whether tokens of a turn
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-264>) # should be considered for training. Useful to selectively train on certain turns
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-265>) # besides the `roles_to_train`.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-266>)message_field_training: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-267>) # The key in the message turn that contains the training details. Useful to
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-268>) # selectively train on certain tokens in a turn. The value of the key is a List[Dict]
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-269>) # containing `begin_offset` (start character index in content), `end_offset` (end
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-270>) # character index in content), and `train` (boolean whether to train).
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-271>)message_field_training_detail: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-272>) # (for Qwen3 template only) Whether to split the assistant content based on a
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-273>) # reasoning trace inside delimited tags
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-274>)split_thinking: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-275>)logprobs_field: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-276>)temperature: float | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-277>) # Roles to train on. The tokens from these roles will be considered for the loss.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-278>)roles_to_train: list[str] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-279>) # Which EOS tokens to train on in the conversation. Possible values are: all: train on
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-280>) # all EOS tokens, turn (default): train on the EOS token at the end of each trainable
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-281>) # turn, last: train on the last EOS token in the conversation
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-282>)train_on_eos: Literal['all', 'turn', 'last'] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-283>) # Roles mapping in the messages. The format is {target_role: [source_roles]}. All
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-284>) # source roles will be mapped to the target role. The default is: user: "human",
[ # "user"], assistant: ["gpt", "assistant"], system: ["system"], tool: ["tool"]
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-286>)roles: dict[str, list[str]] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-287>) # Whether to drop the system turn from the dataset. Only works with chat_template.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-288>) # This does not drop the default system message from chat_template if it exists. If
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-289>) # you wish to, we recommend using a custom jinja template with the default system
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-290>) # message removed or adding a system turn with empty content.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-291>)drop_system_message: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-292>) # Trust remote code for untrusted source
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-293>)trust_remote_code: bool | None = False
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-294>) # The specific revision of the dataset to use when loading from the Hugging Face Hub.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-295>) # This can be a commit hash, tag, or branch name. If not specified, the latest version
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-296>) # will be used. This parameter is ignored for local datasets.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-297>)revision: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-298>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-299>) # For DPODataset:
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-300>)path: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-301>)split: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-302>)type: UserDefinedDPOType | str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-303>)  # For UserDefinedDPOType:
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-304>)field_system: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-305>)field_prompt: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-306>)field_chosen: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-307>)field_rejected: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-308>)prompt_format: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-309>)chosen_format: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-310>)rejected_format: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-311>)data_files: list[str] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-312>)revision: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-313>)field_messages: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-314>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-315>) # For KTODataset:
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-316>)path: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-317>)split: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-318>)type: UserDefinedKTOType | str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-319>)  # For UserDefinedKTOType:
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-320>)field_system: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-321>)field_prompt: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-322>)field_completion: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-323>)field_label: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-324>)prompt_format: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-325>)completion_format: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-326>)data_files: list[str] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-327>)trust_remote_code: bool | None = False
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-328>)revision: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-329>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-330>) # For StepwiseSupervisedDataset:
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-331>)path: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-332>)split: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-333>)data_files: list[str] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-334>)revision: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-335>)step_separator: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-336>)max_completion_length: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-337>)train_on_last_step_only: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-338>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-339>)# A list of one or more datasets to eval the model with. You can use either
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-340>)# test_datasets, or val_set_size, but not both.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-341>)test_datasets: Annotated[list[SFTDataset | DPODataset | KTODataset | StepwiseSupervisedDataset], MinLen(1)] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-342>) # For SFTDataset:
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-343>) # HuggingFace dataset repo | s3:// | gs:// | path to local file or directory
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-344>)path: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-345>) # name of dataset split to load from
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-346>)split: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-347>) # The type of prompt to use for training. [alpaca, gpteacher, oasst, reflection]
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-348>)type: str | UserDefinedPrompterType | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-349>)  # For UserDefinedPrompterType:
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-350>)  # Custom user instruction prompt
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-351>)system_prompt: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-352>)  # Use {system} as key to be replaced
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-353>)system_format: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-354>)field_system: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-355>)field_instruction: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-356>)field_input: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-357>)field_output: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-358>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-359>)  # Customizable to be single line or multi-line. Use {instruction}/{input} as key to
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-360>)  # be replaced. 'format' can include {input}
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-361>)format: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-362>)  # 'no_input_format' cannot include {input}
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-363>)no_input_format: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-364>)input_transform: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-365>) # split dataset into N pieces (use with shards_idx)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-366>)shards: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-367>) # the index of sharded dataset to use
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-368>)shards_idx: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-369>) # process dataset in N sequential chunks for memory efficiency (exclusive with
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-370>) # `shards`)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-371>)preprocess_shards: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-372>)conversation: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-373>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-374>) # The name of the chat template to use for training, following values are supported:
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-375>) # tokenizer_default: Uses the chat template that is available in the
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-376>) # tokenizer_config.json. If the chat template is not available in the tokenizer, it
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-377>) # will raise an error. This is the default.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-378>) # alpaca/inst/chatml/gemma/cohere/llama3/phi_3/deepseek_v2/jamba: These chat templates
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-379>) # are available in the axolotl codebase at src/axolotl/utils/chat_templates.py.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-380>) # tokenizer_default_fallback_*: where * is the name of the chat template to fallback
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-381>) # to if the tokenizer does not have a chat template else default to tokenizer. E.g.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-382>) # tokenizer_default_fallback_chatml. jinja: Uses a custom jinja template for the chat
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-383>) # template. The custom jinja template should be provided in the chat_template_jinja
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-384>) # field.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-385>)chat_template: ChatTemplate | str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-386>) # Custom jinja chat template or path to jinja file. Used only if `chat_template:
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-387>) # jinja` or empty.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-388>)chat_template_jinja: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-389>) # path to source data files
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-390>)data_files: str | list[str] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-391>)input_format: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-392>) # name of dataset configuration to load
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-393>)name: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-394>) # defines the datatype when path is a file
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-395>)ds_type: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-396>) # For `completion` datasets only, uses the provided field instead of `text` column
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-397>)field: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-398>)field_human: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-399>)field_model: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-400>) # Key containing the messages (default: "messages")
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-401>)field_messages: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-402>) # Key containing the tools (default: "tools"). Must be a list[dict] and follow JSON
[ # schema](https://json-schema.org/learn/getting-started-step-by-step).
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-404>)field_tools: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-405>) # Key containing the reasoning trace (default: "reasoning_content").
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-406>)field_thinking: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-407>) # The key the chat template expects that indicates the reasoning trace.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-408>)template_thinking_key: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-409>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-410>)message_field_role: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-411>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-412>)message_field_content: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-413>) # Mapping of properties from the input dataset to the chat template. (default:
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-414>) # message_property_mappings={'role':'role', 'content':'content'}) If a property exists
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-415>) # in the template but not in this mapping, the system will attempt to load it directly
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-416>) # from the message using the property name as the key. Example: In the mapping below,
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-417>) # 'from' is loaded from input dataset and used as 'role', while 'value' is loaded and
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-418>) # used as 'content' in the chat template.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-419>)message_property_mappings: dict[str, str] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-420>) # The key in the message turn that indicates via boolean whether tokens of a turn
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-421>) # should be considered for training. Useful to selectively train on certain turns
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-422>) # besides the `roles_to_train`.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-423>)message_field_training: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-424>) # The key in the message turn that contains the training details. Useful to
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-425>) # selectively train on certain tokens in a turn. The value of the key is a List[Dict]
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-426>) # containing `begin_offset` (start character index in content), `end_offset` (end
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-427>) # character index in content), and `train` (boolean whether to train).
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-428>)message_field_training_detail: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-429>) # (for Qwen3 template only) Whether to split the assistant content based on a
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-430>) # reasoning trace inside delimited tags
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-431>)split_thinking: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-432>)logprobs_field: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-433>)temperature: float | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-434>) # Roles to train on. The tokens from these roles will be considered for the loss.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-435>)roles_to_train: list[str] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-436>) # Which EOS tokens to train on in the conversation. Possible values are: all: train on
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-437>) # all EOS tokens, turn (default): train on the EOS token at the end of each trainable
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-438>) # turn, last: train on the last EOS token in the conversation
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-439>)train_on_eos: Literal['all', 'turn', 'last'] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-440>) # Roles mapping in the messages. The format is {target_role: [source_roles]}. All
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-441>) # source roles will be mapped to the target role. The default is: user: "human",
[ # "user"], assistant: ["gpt", "assistant"], system: ["system"], tool: ["tool"]
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-443>)roles: dict[str, list[str]] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-444>) # Whether to drop the system turn from the dataset. Only works with chat_template.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-445>) # This does not drop the default system message from chat_template if it exists. If
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-446>) # you wish to, we recommend using a custom jinja template with the default system
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-447>) # message removed or adding a system turn with empty content.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-448>)drop_system_message: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-449>) # Trust remote code for untrusted source
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-450>)trust_remote_code: bool | None = False
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-451>) # The specific revision of the dataset to use when loading from the Hugging Face Hub.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-452>) # This can be a commit hash, tag, or branch name. If not specified, the latest version
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-453>) # will be used. This parameter is ignored for local datasets.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-454>)revision: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-455>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-456>) # For DPODataset:
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-457>)path: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-458>)split: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-459>)type: UserDefinedDPOType | str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-460>)  # For UserDefinedDPOType:
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-461>)field_system: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-462>)field_prompt: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-463>)field_chosen: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-464>)field_rejected: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-465>)prompt_format: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-466>)chosen_format: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-467>)rejected_format: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-468>)data_files: list[str] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-469>)revision: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-470>)field_messages: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-471>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-472>) # For KTODataset:
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-473>)path: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-474>)split: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-475>)type: UserDefinedKTOType | str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-476>)  # For UserDefinedKTOType:
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-477>)field_system: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-478>)field_prompt: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-479>)field_completion: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-480>)field_label: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-481>)prompt_format: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-482>)completion_format: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-483>)data_files: list[str] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-484>)trust_remote_code: bool | None = False
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-485>)revision: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-486>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-487>) # For StepwiseSupervisedDataset:
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-488>)path: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-489>)split: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-490>)data_files: list[str] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-491>)revision: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-492>)step_separator: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-493>)max_completion_length: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-494>)train_on_last_step_only: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-495>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-496>)# If false, the datasets will not be shuffled and will keep their original order in
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-497>)# `datasets`. The same applies to the `test_datasets` option and the
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-498>)# `pretraining_dataset` option. Default is true.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-499>)shuffle_merged_datasets: bool | None = True
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-500>)# If true, each dataset in `datasets` will be shuffled before merging. This allows
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-501>)# curriculum learning strategies to be applied at the dataset level. Default is false.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-502>)shuffle_before_merging_datasets: bool | None = False
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-503>)# Axolotl attempts to save the dataset as an arrow after packing the data together so
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-504>)# subsequent training attempts load faster, relative path
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-505>)dataset_prepared_path: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-506>)# Num shards for whole dataset
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-507>)dataset_shard_num: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-508>)# Index of shard to use for whole dataset
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-509>)dataset_shard_idx: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-510>)skip_prepare_dataset: bool | None = False
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-511>)# Number of shards to save the prepared dataset
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-512>)num_dataset_shards_to_save: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-513>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-514>)# Set to HF dataset for type: 'completion' for streaming instead of pre-tokenize
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-515>)pretraining_dataset: Annotated[list[PretrainingDataset | SFTDataset], MinLen(1)] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-516>) # For PretrainingDataset:
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-517>)name: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-518>)path: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-519>)split: str | None = train
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-520>)text_column: str | None = text
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-521>)type: str | None = pretrain
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-522>)trust_remote_code: bool | None = False
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-523>)data_files: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-524>)skip: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-525>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-526>) # For SFTDataset:
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-527>) # HuggingFace dataset repo | s3:// | gs:// | path to local file or directory
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-528>)path: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-529>) # name of dataset split to load from
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-530>)split: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-531>) # The type of prompt to use for training. [alpaca, gpteacher, oasst, reflection]
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-532>)type: str | UserDefinedPrompterType | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-533>)  # For UserDefinedPrompterType:
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-534>)  # Custom user instruction prompt
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-535>)system_prompt: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-536>)  # Use {system} as key to be replaced
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-537>)system_format: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-538>)field_system: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-539>)field_instruction: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-540>)field_input: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-541>)field_output: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-542>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-543>)  # Customizable to be single line or multi-line. Use {instruction}/{input} as key to
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-544>)  # be replaced. 'format' can include {input}
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-545>)format: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-546>)  # 'no_input_format' cannot include {input}
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-547>)no_input_format: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-548>)input_transform: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-549>) # split dataset into N pieces (use with shards_idx)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-550>)shards: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-551>) # the index of sharded dataset to use
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-552>)shards_idx: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-553>) # process dataset in N sequential chunks for memory efficiency (exclusive with
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-554>) # `shards`)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-555>)preprocess_shards: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-556>)conversation: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-557>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-558>) # The name of the chat template to use for training, following values are supported:
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-559>) # tokenizer_default: Uses the chat template that is available in the
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-560>) # tokenizer_config.json. If the chat template is not available in the tokenizer, it
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-561>) # will raise an error. This is the default.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-562>) # alpaca/inst/chatml/gemma/cohere/llama3/phi_3/deepseek_v2/jamba: These chat templates
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-563>) # are available in the axolotl codebase at src/axolotl/utils/chat_templates.py.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-564>) # tokenizer_default_fallback_*: where * is the name of the chat template to fallback
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-565>) # to if the tokenizer does not have a chat template else default to tokenizer. E.g.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-566>) # tokenizer_default_fallback_chatml. jinja: Uses a custom jinja template for the chat
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-567>) # template. The custom jinja template should be provided in the chat_template_jinja
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-568>) # field.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-569>)chat_template: ChatTemplate | str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-570>) # Custom jinja chat template or path to jinja file. Used only if `chat_template:
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-571>) # jinja` or empty.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-572>)chat_template_jinja: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-573>) # path to source data files
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-574>)data_files: str | list[str] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-575>)input_format: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-576>) # name of dataset configuration to load
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-577>)name: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-578>) # defines the datatype when path is a file
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-579>)ds_type: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-580>) # For `completion` datasets only, uses the provided field instead of `text` column
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-581>)field: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-582>)field_human: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-583>)field_model: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-584>) # Key containing the messages (default: "messages")
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-585>)field_messages: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-586>) # Key containing the tools (default: "tools"). Must be a list[dict] and follow JSON
[ # schema](https://json-schema.org/learn/getting-started-step-by-step).
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-588>)field_tools: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-589>) # Key containing the reasoning trace (default: "reasoning_content").
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-590>)field_thinking: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-591>) # The key the chat template expects that indicates the reasoning trace.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-592>)template_thinking_key: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-593>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-594>)message_field_role: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-595>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-596>)message_field_content: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-597>) # Mapping of properties from the input dataset to the chat template. (default:
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-598>) # message_property_mappings={'role':'role', 'content':'content'}) If a property exists
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-599>) # in the template but not in this mapping, the system will attempt to load it directly
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-600>) # from the message using the property name as the key. Example: In the mapping below,
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-601>) # 'from' is loaded from input dataset and used as 'role', while 'value' is loaded and
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-602>) # used as 'content' in the chat template.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-603>)message_property_mappings: dict[str, str] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-604>) # The key in the message turn that indicates via boolean whether tokens of a turn
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-605>) # should be considered for training. Useful to selectively train on certain turns
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-606>) # besides the `roles_to_train`.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-607>)message_field_training: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-608>) # The key in the message turn that contains the training details. Useful to
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-609>) # selectively train on certain tokens in a turn. The value of the key is a List[Dict]
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-610>) # containing `begin_offset` (start character index in content), `end_offset` (end
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-611>) # character index in content), and `train` (boolean whether to train).
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-612>)message_field_training_detail: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-613>) # (for Qwen3 template only) Whether to split the assistant content based on a
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-614>) # reasoning trace inside delimited tags
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-615>)split_thinking: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-616>)logprobs_field: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-617>)temperature: float | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-618>) # Roles to train on. The tokens from these roles will be considered for the loss.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-619>)roles_to_train: list[str] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-620>) # Which EOS tokens to train on in the conversation. Possible values are: all: train on
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-621>) # all EOS tokens, turn (default): train on the EOS token at the end of each trainable
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-622>) # turn, last: train on the last EOS token in the conversation
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-623>)train_on_eos: Literal['all', 'turn', 'last'] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-624>) # Roles mapping in the messages. The format is {target_role: [source_roles]}. All
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-625>) # source roles will be mapped to the target role. The default is: user: "human",
[ # "user"], assistant: ["gpt", "assistant"], system: ["system"], tool: ["tool"]
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-627>)roles: dict[str, list[str]] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-628>) # Whether to drop the system turn from the dataset. Only works with chat_template.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-629>) # This does not drop the default system message from chat_template if it exists. If
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-630>) # you wish to, we recommend using a custom jinja template with the default system
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-631>) # message removed or adding a system turn with empty content.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-632>)drop_system_message: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-633>) # Trust remote code for untrusted source
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-634>)trust_remote_code: bool | None = False
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-635>) # The specific revision of the dataset to use when loading from the Hugging Face Hub.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-636>) # This can be a commit hash, tag, or branch name. If not specified, the latest version
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-637>) # will be used. This parameter is ignored for local datasets.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-638>)revision: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-639>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-640>)# The maximum number of processes to use while preprocessing your input dataset. This
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-641>)# defaults to `os.cpu_count()` if not set. For Runpod VMs, it will default to number of
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-642>)# vCPUs via RUNPOD_CPU_COUNT.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-643>)dataset_processes: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-644>)# The maximum number of processes to use while preprocessing your input dataset. This
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-645>)# defaults to `os.cpu_count()` if not set. For Runpod VMs, it will default to number of
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-646>)# vCPUs via RUNPOD_CPU_COUNT.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-647>)dataset_num_proc: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-648>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-649>)# Deduplicates datasets and test_datasets with identical entries
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-650>)dataset_exact_deduplication: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-651>)# Keep dataset in memory while preprocessing. Only needed if cached dataset is taking
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-652>)# too much storage
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-653>)dataset_keep_in_memory: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-654>)dataloader_pin_memory: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-655>)dataloader_num_workers: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-656>)dataloader_prefetch_factor: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-657>)dataloader_drop_last: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-658>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-659>)accelerator_config: dict[str, Any] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-660>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-661>)remove_unused_columns: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-662>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-663>)# Push prepared dataset to hub - repo_org/repo_name
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-664>)push_dataset_to_hub: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-665>)# Whether to use hf `use_auth_token` for loading datasets. Useful for fetching private
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-666>)# datasets. Required to be true when used in combination with `push_dataset_to_hub`
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-667>)hf_use_auth_token: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-668>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-669>)device: Any | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-670>)# Passed through to transformers when loading the model when launched without
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-671>)# accelerate. Use `sequential` when training w/ model parallelism to limit memory
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-672>)device_map: Any | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-673>)world_size: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-674>)# Don't mess with this, it's here for accelerate and torchrun
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-675>)local_rank: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-676>)ddp: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-677>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-678>)# Seed for reproducibility
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-679>)seed: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-680>)# Advanced DDP Arguments - timeout
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-681>)ddp_timeout: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-682>)# Advanced DDP Arguments - bucket cap in MB
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-683>)ddp_bucket_cap_mb: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-684>)# Advanced DDP Arguments - broadcast buffers
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-685>)ddp_broadcast_buffers: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-686>)ddp_find_unused_parameters: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-687>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-688>)# Approximate number of predictions sent to wandb depending on batch size. Enabled above
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-689>)# 0. Default is 0
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-690>)eval_table_size: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-691>)# Total number of tokens generated for predictions sent to wandb. Default is 128
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-692>)eval_max_new_tokens: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-693>)# Whether to run causal language model evaluation for metrics in
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-694>)# `eval_causal_lm_metrics`
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-695>)do_causal_lm_eval: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-696>)# HF evaluate metrics used during evaluation. Default is 'sacrebleu', 'comet', 'ter',
[# 'chrf', 'perplexity']
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-698>)eval_causal_lm_metrics: list[str] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-699>)do_bench_eval: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-700>)bench_dataset: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-701>)bench_split: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-702>)metric_for_best_model: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-703>)greater_is_better: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-704>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-705>)# High loss value, indicating the learning has broken down (a good estimate is ~2 times
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-706>)# the loss at the start of training)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-707>)loss_watchdog_threshold: float | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-708>)# Number of high-loss steps in a row before the trainer aborts (default: 3)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-709>)loss_watchdog_patience: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-710>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-711>)# Run garbage collection every `gc_steps` steps. -1 will run on epoch end and before
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-712>)# evaluations. Default is 0 (disabled).
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-713>)gc_steps: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-714>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-715>)# Use CUDA bf16. bool or 'full' for `bf16_full_eval`, or 'auto' for automatic detection.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-716>)# require >=ampere
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-717>)bf16: Literal['auto'] | bool | None = auto
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-718>)# Use CUDA fp16
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-719>)fp16: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-720>)# Enable FP8 mixed precision training using TorchAO. Best used in combination with
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-721>)# torch.compile.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-722>)fp8: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-723>)# Enable FSDP float8 all-gather optimization for FP8 training. Can improve training
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-724>)# speed by 10-15% when FSDP is enabled.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-725>)fp8_enable_fsdp_float8_all_gather: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-726>)# No AMP (automatic mixed precision) - require >=ampere
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-727>)bfloat16: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-728>)# No AMP (automatic mixed precision)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-729>)float16: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-730>)# Use CUDA tf32 - require >=ampere
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-731>)tf32: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-732>)float32: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-733>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-734>)# Whether to use gradient checkpointing. Available options are: true, false, 'offload',
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-735>)# 'offload_disk'.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-736>)# https://huggingface.co/docs/transformers/v4.18.0/en/performance#gradient-checkpointing
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-737>)gradient_checkpointing: Literal['offload', 'offload_disk'] | bool | None = False
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-738>)# Additional kwargs to pass to the trainer for gradient checkpointing
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-739>)gradient_checkpointing_kwargs: dict[str, Any] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-740>)# Whether to offload activations. Available options are: true, false, 'legacy', 'disk'.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-741>)activation_offloading: Literal['legacy', 'disk'] | bool | None = False
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-742>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-743>)unfrozen_parameters: list[str] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-744>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-745>)# The maximum length of an input to train with, this should typically be less than 2048
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-746>)# as most models have a token/context limit of 2048
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-747>)sequence_len: int = 512
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-748>)# What to do when a tokenized row exceeds sequence_len. 'drop' removes the row;
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-749>)# 'truncate' slices tensors to sequence_len; 'raise' raises a ValueError. Defaults to
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-750>)# 'drop' for backward compatibility.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-751>)excess_length_strategy: Literal['drop', 'truncate', 'raise'] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-752>)# The maximum length of an input for evaluation. If not specified, defaults to
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-753>)# sequence_len
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-754>)eval_sequence_len: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-755>)min_sample_len: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-756>)# maximum prompt length for RL training
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-757>)max_prompt_len: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-758>)# Use efficient multi-packing with block diagonal attention and per sequence
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-759>)# position_ids. Recommend set to 'true'
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-760>)sample_packing: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-761>)# The number of samples packed at a time. Increasing the following values helps with
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-762>)# packing, but usually only slightly (<%1.)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-763>)sample_packing_group_size: int | None = 100000
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-764>)# The number of samples which can be packed into one sequence. Increase if using a large
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-765>)# sequence_len with many short samples.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-766>)sample_packing_bin_size: int | None = 200
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-767>)# Whether to pack samples sequentially
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-768>)sample_packing_sequentially: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-769>)# The multiprocessing start method to use for packing. Should be 'fork', 'spawn' or
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-770>)# 'forkserver'
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-771>)sample_packing_mp_start_method: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-772>)# Set to 'false' if getting errors during eval with sample_packing on
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-773>)eval_sample_packing: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-774>)# Pad inputs so each step uses constant sized buffers. This will reduce memory
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-775>)# fragmentation and may prevent OOMs, by re-using memory more efficiently. Defaults to
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-776>)# True if `sample_packing` enabled
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-777>)pad_to_sequence_len: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-778>)# Whether to use sequential sampling for curriculum learning
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-779>)curriculum_sampling: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-780>)multipack_real_batches: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-781>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-782>)# Use batch flattening for speedups when not using sample_packing
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-783>)batch_flattening: Literal['auto'] | bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-784>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-785>)use_pose: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-786>)pose_split_on_token_ids: list[int] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-787>)pose_max_context_len: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-788>)pose_num_chunks: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-789>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-790>)pretrain_multipack_buffer_size: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-791>)# whether to prevent cross attention for packed sequences during pretraining
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-792>)pretrain_multipack_attn: bool | None = True
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-793>)# whether to concatenate samples during pretraining
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-794>)pretraining_sample_concatenation: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-795>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-796>)# Use streaming mode for loading datasets
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-797>)streaming: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-798>)# Buffer size for multipack streaming datasets
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-799>)streaming_multipack_buffer_size: int | None = 10000
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-800>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-801>)# Whether to use xformers attention patch https://github.com/facebookresearch/xformers
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-802>)xformers_attention: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-803>)# Whether to use scaled-dot-product attention https://pytorch.org/docs/stable/generated/
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-804>)# torch.nn.functional.scaled_dot_product_attention.html
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-805>)sdp_attention: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-806>)# Shifted-sparse attention (only llama) - https://arxiv.org/pdf/2309.12307.pdf
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-807>)s2_attention: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-808>)flex_attention: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-809>)flex_attn_compile_kwargs: dict[str, Any] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-810>)# Whether to use flash attention patch https://github.com/Dao-AILab/flash-attention
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-811>)flash_attention: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-812>)# Whether to use flash-attention cross entropy implementation - advanced use only
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-813>)flash_attn_cross_entropy: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-814>)# Whether to use flash-attention rms norm implementation - advanced use only
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-815>)flash_attn_rms_norm: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-816>)# Whether to fuse part of the MLP into a single operation
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-817>)flash_attn_fuse_mlp: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-818>)# Whether to use bettertransformers
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-819>)flash_optimum: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-820>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-821>)eager_attention: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-822>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-823>)# Specify a custom attention implementation, used mostly for kernels.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-824>)attn_implementation: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-825>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-826>)# Whether to use Scaled Softmax (SSMax) attention. Ref: https://arxiv.org/abs/2501.19399
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-827>)scaling_softmax: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-828>)# Scaling factor for SSMax attention. Default is 0.43
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-829>)scaling_softmax_factor: float | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-830>)# Bias for SSMax attention. Default is 0.0. Note: The paper recommends bias=0 for better
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-831>)# length generalization.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-832>)scaling_softmax_bias: float | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-833>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-834>)unsloth_cross_entropy_loss: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-835>)unsloth_lora_mlp: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-836>)unsloth_lora_qkv: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-837>)unsloth_lora_o: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-838>)unsloth_rms_norm: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-839>)unsloth_rope: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-840>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-841>)# Apply custom LoRA autograd functions and activation function Triton kernels for speed
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-842>)# and memory savings. See: https://docs.axolotl.ai/docs/lora_optims.html
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-843>)lora_mlp_kernel: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-844>)# Apply custom LoRA autograd functions and activation function Triton kernels for speed
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-845>)# and memory savings. See: https://docs.axolotl.ai/docs/lora_optims.html
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-846>)lora_qkv_kernel: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-847>)# Apply custom LoRA autograd functions and activation function Triton kernels for speed
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-848>)# and memory savings. See: https://docs.axolotl.ai/docs/lora_optims.html
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-849>)lora_o_kernel: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-850>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-851>)# Whether to use chunked cross entropy loss for memory efficiency
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-852>)chunked_cross_entropy: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-853>)# Number of chunks to use for chunked cross entropy loss
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-854>)chunked_cross_entropy_num_chunks: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-855>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-856>)# Whether to use ALST tiled mlp for memory efficient long context
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-857>)tiled_mlp: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-858>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-859>)# Number of shards to use for ALST tiled mlp. If unset, it will be set based on
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-860>)# seqlen/hidden_size
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-861>)tiled_mlp_num_shards: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-862>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-863>)# Whether to use original mlp for ALST tiled mlp. Otherwise uses a generic MLP based on
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-864>)# llama.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-865>)tiled_mlp_use_original_mlp: bool | None = True
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-866>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-867>)llama4_linearized_experts: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-868>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-869>)# Deepspeed config path. e.g., deepspeed_configs/zero3.json
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-870>)deepspeed: str | dict[str, Any] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-871>)# Whether to use deepcompile for faster training with deepspeed
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-872>)deepcompile: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-873>)# FSDP configuration
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-874>)fsdp: list[str] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-875>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-876>)# FSDP configuration options
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-877>)fsdp_config: FSDPConfig | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-878>) # For FSDPConfig:
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-879>) # Enable activation checkpointing to reduce memory usage during forward passes
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-880>)activation_checkpointing: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-881>) # Offload parameters to CPU to reduce GPU memory usage
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-882>)offload_params: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-883>) # Synchronize module states across all processes
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-884>)sync_module_states: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-885>) # Enable CPU RAM efficient loading to reduce memory usage during model loading
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-886>)cpu_ram_efficient_loading: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-887>) # Disabling this enables swap memory usage for resource-constrained setups when
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-888>) # offload_params is enabled.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-889>)cpu_offload_pin_memory: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-890>) # Use original parameters instead of flattened parameters
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-891>)use_orig_params: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-892>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-893>) # Type of state dict to use for saving/loading checkpoints
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-894>)state_dict_type: Literal['FULL_STATE_DICT', 'LOCAL_STATE_DICT', 'SHARDED_STATE_DICT'] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-895>) # Final state dict type to use after training completion
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-896>)final_state_dict_type: Literal['FULL_STATE_DICT', 'LOCAL_STATE_DICT', 'SHARDED_STATE_DICT'] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-897>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-898>) # Policy for automatically wrapping modules with FSDP
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-899>)auto_wrap_policy: Literal['TRANSFORMER_BASED_WRAP', 'SIZE_BASED_WRAP'] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-900>) # Class name of transformer layers to wrap (e.g., 'LlamaDecoderLayer')
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-901>)transformer_layer_cls_to_wrap: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-902>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-903>) # Reshard parameters after forward pass to save memory
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-904>)reshard_after_forward: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-905>) # Mixed precision policy for FSDP (e.g., 'fp16', 'bf16')
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-906>)mixed_precision_policy: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-907>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-908>)# FSDP version
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-909>)fsdp_version: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-910>)fsdp_final_state_dict_type: Literal['FULL_STATE_DICT', 'LOCAL_STATE_DICT', 'SHARDED_STATE_DICT'] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-911>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-912>)# How much of the dataset to set aside as evaluation. 1 = 100%, 0.50 = 50%, etc. 0 for
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-913>)# no eval.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-914>)val_set_size: float | None = 0.0
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-915>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-916>)# Number of devices to shard across. If not set, will use all available devices.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-917>)dp_shard_size: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-918>)# Number of devices to replicate across.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-919>)dp_replicate_size: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-920>)# Deprecated: use `context_parallel_size` instead
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-921>)sequence_parallel_degree: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-922>)# Set to a divisor of the number of GPUs available to split sequences into chunks of
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-923>)# equal size. Use in long context training to prevent OOM when sequences cannot fit into
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-924>)# a single GPU's VRAM. E.g., if 4 GPUs are available, set this value to 2 to split each
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-925>)# sequence into two equal-sized subsequences, or set to 4 to split into four equal-sized
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-926>)# subsequences. See https://docs.axolotl.ai/docs/sequence_parallelism.html for more
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-927>)# details.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-928>)context_parallel_size: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-929>)# Optional; strides across the key dimension. Larger values use more memory but should
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-930>)# make training faster. Must evenly divide the number of KV heads in your model.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-931>)heads_k_stride: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-932>)# One of 'varlen_llama3', 'batch_ring', 'batch_zigzag', 'batch_stripe'. Defaults to
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-933>)# 'varlen_llama3' in the sample packing case, and 'batch_ring' in the non-sample packing
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-934>)# case.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-935>)ring_attn_func: RingAttnFunc | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-936>)# Number of tensor parallel processes in TP group. Only supported with DeepSpeed AutoTP.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-937>)tensor_parallel_size: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-938>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-939>)# Add or change special tokens. If you add tokens here, you don't need to add them to
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-940>)# the `tokens` list.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-941>)special_tokens: SpecialTokensConfig | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-942>) # For SpecialTokensConfig:
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-943>)bos_token: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-944>)eos_token: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-945>)pad_token: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-946>)unk_token: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-947>)additional_special_tokens: list[str] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-948>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-949>)# Add extra tokens to the tokenizer
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-950>)tokens: list[str] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-951>)# Mapping token_id to new_token_string to override reserved added_tokens in the
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-952>)# tokenizer. Only works for tokens that are not part of the base vocab (aka are
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-953>)# added_tokens). Can be checked if they exist in tokenizer.json added_tokens.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-954>)added_tokens_overrides: dict[int, str] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-955>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-956>)# Whether to use torch.compile and which backend to use. setting to `auto` will enable
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-957>)# torch compile when torch>=2.6.0
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-958>)torch_compile: Literal['auto'] | bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-959>)# Backend to use for torch.compile
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-960>)torch_compile_backend: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-961>)torch_compile_mode: Literal['default', 'reduce-overhead', 'max-autotune'] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-962>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-963>)# Maximum number of iterations to train for. It precedes num_epochs which means that if
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-964>)# both are set, num_epochs will not be guaranteed. e.g., when 1 epoch is 1000 steps =>
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-965>)# `num_epochs: 2` and `max_steps: 100` will train for 100 steps
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-966>)max_steps: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-967>)# Number of warmup steps. Cannot use with warmup_ratio
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-968>)warmup_steps: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-969>)# Warmup ratio. Cannot use with warmup_steps
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-970>)warmup_ratio: float | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-971>)# Leave empty to eval at each epoch, integer for every N steps. float for fraction of
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-972>)# total steps
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-973>)eval_steps: int | float | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-974>)# Number of times per epoch to run evals, mutually exclusive with eval_steps
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-975>)evals_per_epoch: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-976>)# Set to `no` to skip evaluation, `epoch` at end of each epoch, leave empty to infer
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-977>)# from `eval_steps`
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-978>)eval_strategy: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-979>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-980>)# Leave empty to save at each epoch, integer for every N steps. float for fraction of
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-981>)# total steps
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-982>)save_steps: int | float | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-983>)# Number of times per epoch to save a checkpoint, mutually exclusive with save_steps
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-984>)saves_per_epoch: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-985>)# Set to `no` to skip checkpoint saves, `epoch` at end of each epoch, `best` when better
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-986>)# result is achieved, leave empty to infer from `save_steps`
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-987>)save_strategy: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-988>)# Checkpoints saved at a time
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-989>)save_total_limit: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-990>)# Whether to checkpoint a model after the first step of training. Defaults to False.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-991>)save_first_step: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-992>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-993>)# Logging frequency
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-994>)logging_steps: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-995>)# Stop training after this many evaluation losses have increased in a row. https://huggi
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-996>)# ngface.co/transformers/v4.2.2/_modules/transformers/trainer_callback.html#EarlyStoppin
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-997>)# gCallback
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-998>)early_stopping_patience: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-999>)load_best_model_at_end: bool | None = False
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1000>)# Save only the model weights, skipping the optimizer. Using this means you can't resume
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1001>)# from checkpoints.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1002>)save_only_model: bool | None = False
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1003>)# Use tensorboard for logging
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1004>)use_tensorboard: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1005>)# Enable the pytorch profiler to capture the first N steps of training to the
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1006>)# output_dir. see https://pytorch.org/blog/understanding-gpu-memory-1/ for more
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1007>)# information. Snapshots can be visualized @ https://pytorch.org/memory_viz
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1008>)profiler_steps: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1009>)# Which step to start the profiler at. Useful for only capturing a few steps mid-run.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1010>)profiler_steps_start: int | None = 0
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1011>)# bool of whether to report tokens per second at the end of training. This is not
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1012>)# supported with pre-training datasets.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1013>)include_tokens_per_second: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1014>)# bool of whether to report tokens per second per-gpu during training by measuring
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1015>)# throughput of non-padding tokens.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1016>)include_tkps: bool | None = True
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1017>)# NEFT https://arxiv.org/abs/2310.05914, set this to a number (paper default is 5) to
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1018>)# add noise to embeddings. Currently only supported on Llama and Mistral
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1019>)neftune_noise_alpha: float | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1020>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1021>)# Parameter controlling the relative ratio loss weight in the ORPO loss. Passed to
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1022>)# `beta` in `ORPOConfig` due to trl mapping.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1023>)orpo_alpha: float | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1024>)# Weighting of NLL term in loss from RPO paper
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1025>)rpo_alpha: float | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1026>)# Target reward margin for the SimPO loss
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1027>)simpo_gamma: float | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1028>)# Weight of the BC regularizer
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1029>)cpo_alpha: float | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1030>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1031>)# Factor for desirable loss term in KTO loss
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1032>)kto_desirable_weight: float | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1033>)# Factor for undesirable loss term in KTO loss
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1034>)kto_undesirable_weight: float | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1035>)# The beta parameter for the RL training
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1036>)rl_beta: float | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1037>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1038>)# Defines the max memory usage per gpu on the system. Passed through to transformers
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1039>)# when loading the model.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1040>)max_memory: dict[int | Literal['cpu', 'disk'], int | str] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1041>)# Limit the memory for all available GPUs to this amount (if an integer, expressed in
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1042>)# gigabytes); default: unset
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1043>)gpu_memory_limit: int | str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1044>)# Whether to use low_cpu_mem_usage
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1045>)low_cpu_mem_usage: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1046>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1047>)# The name of the chat template to use for training, following values are supported:
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1048>)# tokenizer_default: Uses the chat template that is available in the
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1049>)# tokenizer_config.json. If the chat template is not available in the tokenizer, it will
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1050>)# raise an error. This is the default value.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1051>)# alpaca/inst/chatml/gemma/cohere/llama3/phi_3/deepseek_v2/jamba: These chat templates
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1052>)# are available in the axolotl codebase at src/axolotl/utils/chat_templates.py.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1053>)# tokenizer_default_fallback_*: where * is the name of the chat template to fallback to.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1054>)# E.g. tokenizer_default_fallback_chatml. This is useful when the chat template is not
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1055>)# available in the tokenizer. jinja: Uses a custom jinja template for the chat template.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1056>)# The custom jinja template should be provided in the chat_template_jinja field. The
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1057>)# selected chat template will be saved to the tokenizer_config.json for easier
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1058>)# inferencing
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1059>)chat_template: ChatTemplate | Annotated[str, StringConstraints(pattern='^tokenizer_default_fallback_')] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1060>)# Custom jinja template or path to jinja file for chat template. This will be only used
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1061>)# if chat_template is set to `jinja` or `null` (in which case chat_template is
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1062>)# automatically set to `jinja`). Default is null.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1063>)chat_template_jinja: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1064>)# Additional kwargs to pass to the chat template. This is useful for customizing the
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1065>)# chat template. For example, you can pass `thinking=False` to add a generation prompt
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1066>)# to the chat template.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1067>)chat_template_kwargs: dict[str, Any] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1068>)# Custom EOT (End-of-Turn) tokens to mask/unmask during training. These tokens mark the
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1069>)# boundaries between conversation turns. For example: '/INST', '</s>',
[# '[/SYSTEM_PROMPT]']. If not specified, defaults to just the model's eos_token. This is
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1071>)# useful for templates that use multiple delimiter tokens.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1072>)eot_tokens: list[str] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1073>)# Changes the default system message. Currently only supports chatml.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1074>)default_system_message: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1075>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1076>)# Token index or indices to adjust embedding weights to the mean of the other tokens.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1077>)# This is useful when the model has untrained embeddings.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1078>)fix_untrained_tokens: int | list[int] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1079>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1080>)is_preprocess: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1081>)preprocess_iterable: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1082>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1083>)# Total number of tokens - internal use
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1084>)total_num_tokens: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1085>)total_supervised_tokens: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1086>)# You can set these packing optimizations AFTER starting a training at least once. The
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1087>)# trainer will provide recommended values for these values.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1088>)sample_packing_eff_est: float | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1089>)axolotl_config_path: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1090>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1091>)# Internal use only - Used to identify which the model is based on
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1092>)is_falcon_derived_model: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1093>)# Internal use only - Used to identify which the model is based on
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1094>)is_llama_derived_model: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1095>)# Internal use only - Used to identify which the model is based on. Please note that if
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1096>)# you set this to true, `padding_side` will be set to 'left' by default
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1097>)is_mistral_derived_model: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1098>)# Internal use only - Used to identify which the model is based on
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1099>)is_qwen_derived_model: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1100>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1101>)# Add plugins to extend the pipeline. See `src/axolotl/integrations` for the available
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1102>)# plugins or doc below for more details.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1103>)# https://docs.axolotl.ai/docs/custom_integrations.html
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1104>)plugins: list[str] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1105>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1106>)# This is the huggingface model that contains *.pt, *.safetensors, or *.bin files. This
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1107>)# can also be a relative path to a model on disk
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1108>)base_model: str (required)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1109>)# If the base_model repo on hf hub doesn't include configuration .json files, You can
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1110>)# set that here, or leave this empty to default to base_model
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1111>)base_model_config: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1112>)# transformers config class (e.g., 'LlamaConfig', 'MistralConfig'). Defaults to
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1113>)# AutoConfig.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1114>)cls_model_config: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1115>)# Optional tokenizer configuration path in case you want to use a different tokenizer
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1116>)# than the one defined in the base model
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1117>)tokenizer_config: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1118>)# use_fast option for tokenizer loading from_pretrained, default to True
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1119>)tokenizer_use_fast: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1120>)# Whether to use the legacy tokenizer setting, defaults to True
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1121>)tokenizer_legacy: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1122>)# Whether to use mistral-common tokenizer. If set to True, it will use the mistral-
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1123>)# common tokenizer.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1124>)tokenizer_use_mistral_common: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1125>)# Corresponding tokenizer for the model AutoTokenizer is a good choice
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1126>)tokenizer_type: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1127>)# transformers processor class
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1128>)processor_type: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1129>)# Whether to save jinja files for tokenizer, transformers default is True
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1130>)tokenizer_save_jinja_files: bool | None = True
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1131>)# Trust remote code for untrusted source
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1132>)trust_remote_code: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1133>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1134>)# Don't move the model to the device before sharding. Set to `false` to revert to legacy
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1135>)# behavior.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1136>)experimental_skip_move_to_device: bool | None = True
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1137>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1138>)# Use custom kernels, e.g. MegaBlocks.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1139>)use_kernels: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1140>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1141>)# Model loading quantization config
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1142>)model_quantization_config: Literal['Mxfp4Config'] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1143>)# kwargs for model quantization config
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1144>)model_quantization_config_kwargs: dict[str, Any] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1145>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1146>)# Where to save the full-finetuned model to
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1147>)output_dir: str = ./model-out
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1148>)# push checkpoints to hub
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1149>)hub_model_id: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1150>)# how to push checkpoints to hub
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1151>)hub_strategy: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1152>)# Save model as safetensors (require safetensors package). Default True
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1153>)save_safetensors: bool | None = True
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1154>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1155>)# This will attempt to quantize the model down to 8 bits and use adam 8 bit optimizer
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1156>)load_in_8bit: bool | None = False
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1157>)# Use bitsandbytes 4 bit
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1158>)load_in_4bit: bool | None = False
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1159>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1160>)# If you want to use 'lora' or 'qlora' or leave blank to train all parameters in
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1161>)# original model
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1162>)adapter: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1163>)# If you already have a lora model trained that you want to load, put that here. This
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1164>)# means after training, if you want to test the model, you should set this to the value
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1165>)# of `output_dir`. Note that if you merge an adapter to the base model, a new
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1166>)# subdirectory `merged` will be created under the `output_dir`.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1167>)lora_model_dir: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1168>)lora_r: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1169>)lora_alpha: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1170>)lora_fan_in_fan_out: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1171>)lora_target_modules: str | list[str] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1172>)lora_target_parameters: str | list[str] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1173>)# If true, will target all linear modules
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1174>)lora_target_linear: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1175>)# If you added new tokens to the tokenizer, you may need to save some LoRA modules
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1176>)# because they need to know the new tokens. For LLaMA and Mistral, you need to save
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1177>)# `embed_tokens` and `lm_head`. It may vary for other models. `embed_tokens` converts
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1178>)# tokens to embeddings, and `lm_head` converts embeddings to token probabilities.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1179>)lora_modules_to_save: list[str] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1180>)lora_dropout: float | None = 0.0
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1181>)# The layer indices to transform, otherwise, apply to all layers
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1182>)peft_layers_to_transform: list[int] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1183>)peft_layers_pattern: list[str] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1184>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1185>)peft: PeftConfig | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1186>) # For PeftConfig:
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1187>) # Configuration options for loftq initialization for LoRA
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1188>)loftq_config: LoftQConfig | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1189>)  # For LoftQConfig:
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1190>)  # typically 4 bits
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1191>)loftq_bits: int = 4
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1192>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1193>)# Whether to use DoRA.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1194>)peft_use_dora: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1195>)# Whether to use RSLoRA.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1196>)peft_use_rslora: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1197>)# List of layer indices to replicate.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1198>)peft_layer_replication: list[tuple[int, int]] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1199>)# How to initialize LoRA weights. Default to True which is MS original implementation.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1200>)peft_init_lora_weights: bool | str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1201>)# A list of token indices to fine-tune on the `embed_tokens` layer. Otherwise, a dict
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1202>)# mapping an embedding layer name to its trainable token indices. See
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1203>)# https://huggingface.co/docs/peft/v0.17.0/en/developer_guides/lora#efficiently-train-
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1204>)# tokens-alongside-lora
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1205>)peft_trainable_token_indices: list[int] | dict[str, list[int]] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1206>)# Whether to tie adapter weights for tied model weights. See
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1207>)# https://github.com/huggingface/peft/issues/2864
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1208>)peft_ensure_weight_tying: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1209>)# Whether to upcast the LoRA adapter to fp32. This is enabled by default in PEFT.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1210>)peft_autocast_adapter_dtype: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1211>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1212>)# load qlora model in sharded format for FSDP using answer.ai technique.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1213>)qlora_sharded_model_loading: bool | None = False
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1214>)# Do the LoRA/PEFT loading on CPU -- this is required if the base model is so large it
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1215>)# takes up most or all of the available GPU VRAM, e.g. during a model and LoRA merge
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1216>)lora_on_cpu: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1217>)# Whether you are training a 4-bit GPTQ quantized model
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1218>)gptq: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1219>)# optional overrides to the bnb 4bit quantization configuration
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1220>)bnb_config_kwargs: dict[str, Any] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1221>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1222>)# loraplus learning rate ratio lr_B / lr_A. Recommended value is 2^4.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1223>)loraplus_lr_ratio: float | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1224>)# loraplus learning rate for lora embedding layers. Default value is 1e-6.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1225>)loraplus_lr_embedding: float | None = 1e-06
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1226>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1227>)merge_lora: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1228>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1229>)# Whether to use ReLoRA. Use with jagged_restart_*steps options.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1230>)relora: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1231>)# threshold for optimizer magnitude when pruning
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1232>)relora_prune_ratio: float | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1233>)# True to perform lora weight merges on cpu during restarts, for modest gpu memory
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1234>)# savings
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1235>)relora_cpu_offload: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1236>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1237>)# how often to reset for jagged restarts
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1238>)jagged_restart_steps: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1239>)# how many warmup steps to take after reset for jagged restarts
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1240>)jagged_restart_warmup_steps: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1241>)# how many anneal steps to take before reset for jagged restarts
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1242>)jagged_restart_anneal_steps: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1243>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1244>)# If greater than 1, backpropagation will be skipped and the gradients will be
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1245>)# accumulated for the given number of steps.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1246>)gradient_accumulation_steps: int | None = 1
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1247>)# The number of samples to include in each batch. This is the number of samples sent to
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1248>)# each GPU. Batch size per gpu = micro_batch_size * gradient_accumulation_steps
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1249>)micro_batch_size: int | None = 1
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1250>)# Total batch size, we do not recommended setting this manually
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1251>)batch_size: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1252>)# per gpu micro batch size for evals, defaults to value of micro_batch_size
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1253>)eval_batch_size: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1254>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1255>)# whether to find batch size that fits in memory. Passed to underlying transformers
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1256>)# Trainer
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1257>)auto_find_batch_size: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1258>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1259>)# Whether to mask out or include the human's prompt from the training labels
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1260>)train_on_inputs: bool | None = False
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1261>)# Group similarly sized data to minimize padding. May be slower to start, as it must
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1262>)# download and sort the entire dataset. Note that training loss may have an oscillating
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1263>)# pattern with this enabled.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1264>)group_by_length: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1265>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1266>)learning_rate: str | float (required)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1267>)embedding_lr: float | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1268>)embedding_lr_scale: float | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1269>)# Specify weight decay
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1270>)weight_decay: float | None = 0.0
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1271>)# Specify optimizer
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1272>)optimizer: OptimizerNames | CustomSupportedOptimizers | None = OptimizerNames.ADAMW_TORCH_FUSED
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1273>)# Dictionary of arguments to pass to the optimizer
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1274>)optim_args: str | dict[str, Any] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1275>)# The target modules to optimize, i.e. the module names that you would like to train,
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1276>)# right now this is used only for GaLore algorithm
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1277>)optim_target_modules: list[str] | Literal['all_linear'] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1278>)# Path to torch distx for optim 'adamw_anyprecision'
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1279>)torchdistx_path: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1280>)lr_scheduler: SchedulerType | Literal['one_cycle'] | Literal['rex'] | None = SchedulerType.COSINE
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1281>)# Specify a scheduler and kwargs to use with the optimizer
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1282>)lr_scheduler_kwargs: dict[str, Any] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1283>)lr_quadratic_warmup: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1284>)# decay lr to some percentage of the peak lr, e.g. cosine_min_lr_ratio=0.1 for 10% of
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1285>)# peak lr
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1286>)cosine_min_lr_ratio: float | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1287>)# freeze lr at some percentage of the step, e.g. cosine_constant_lr_ratio=0.8 means
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1288>)# start cosine_min_lr at 80% of training step
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1289>)cosine_constant_lr_ratio: float | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1290>)# Learning rate div factor
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1291>)lr_div_factor: float | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1292>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1293>)lr_groups: list[LrGroup] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1294>) # For LrGroup:
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1295>)name: str (required)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1296>)modules: list[str] (required)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1297>)lr: float (required)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1298>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1299>)# adamw hyperparams
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1300>)adam_epsilon: float | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1301>)# only used for CAME Optimizer
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1302>)adam_epsilon2: float | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1303>)# adamw hyperparams
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1304>)adam_beta1: float | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1305>)# adamw hyperparams
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1306>)adam_beta2: float | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1307>)# only used for CAME Optimizer
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1308>)adam_beta3: float | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1309>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1310>)# Dion Optimizer learning rate
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1311>)dion_lr: float | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1312>)# Dion Optimizer momentum
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1313>)dion_momentum: float | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1314>)# Dion Optimizer: r/d fraction for low-rank approximation. Used to compute the low-rank
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1315>)# dimension.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1316>)dion_rank_fraction: float | None = 1.0
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1317>)# Dion Optimizer: Round up the low-rank dimension to a multiple of this number. This may
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1318>)# be useful to ensure even sharding.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1319>)dion_rank_multiple_of: int | None = 1
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1320>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1321>)# Gradient clipping max norm
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1322>)max_grad_norm: float | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1323>)num_epochs: float = 1.0
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1324>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1325>)use_wandb: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1326>)# Set the name of your wandb run
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1327>)wandb_name: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1328>)# Set the ID of your wandb run
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1329>)wandb_run_id: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1330>)# "offline" to save run metadata locally and not sync to the server, "disabled" to turn
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1331>)# off wandb
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1332>)wandb_mode: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1333>)# Your wandb project name
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1334>)wandb_project: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1335>)# A wandb Team name if using a Team
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1336>)wandb_entity: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1337>)wandb_watch: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1338>)# "checkpoint" to log model to wandb Artifacts every `save_steps` or "end" to log only
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1339>)# at the end of training
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1340>)wandb_log_model: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1341>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1342>)use_mlflow: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1343>)# URI to mlflow
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1344>)mlflow_tracking_uri: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1345>)# Your experiment name
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1346>)mlflow_experiment_name: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1347>)# Your run name
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1348>)mlflow_run_name: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1349>)# set to true to copy each saved checkpoint on each save to mlflow artifact registry
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1350>)hf_mlflow_log_artifacts: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1351>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1352>)# Enable or disable Comet integration.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1353>)use_comet: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1354>)# API key for Comet. Recommended to set via `comet login`.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1355>)comet_api_key: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1356>)# Workspace name in Comet. Defaults to the user's default workspace.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1357>)comet_workspace: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1358>)# Project name in Comet. Defaults to Uncategorized.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1359>)comet_project_name: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1360>)# Identifier for the experiment. Used to append data to an existing experiment or
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1361>)# control the key of new experiments. Default to a random key.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1362>)comet_experiment_key: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1363>)# Create a new experiment ("create") or log to an existing one ("get"). Default
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1364>)# ("get_or_create") auto-selects based on configuration.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1365>)comet_mode: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1366>)# Set to True to log data to Comet server, or False for offline storage. Default is
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1367>)# True.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1368>)comet_online: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1369>)# Dictionary for additional configuration settings, see the doc for more details.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1370>)comet_experiment_config: dict[str, Any] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1371>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1372>)use_trackio: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1373>)# Your trackio project name
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1374>)trackio_project_name: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1375>)# Set the name of your trackio run
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1376>)trackio_run_name: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1377>)# Hugging Face Space ID to sync dashboard to (optional, runs locally if not provided)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1378>)trackio_space_id: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1379>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1380>)# Enable OpenTelemetry metrics collection and Prometheus export
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1381>)use_otel_metrics: bool | None = False
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1382>)# Host to bind the OpenTelemetry metrics server to
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1383>)otel_metrics_host: str | None = localhost
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1384>)# Port for the Prometheus metrics HTTP server
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1385>)otel_metrics_port: int | None = 8000
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1386>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1387>)# the number of activate layers in LISA
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1388>)lisa_n_layers: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1389>)# how often to switch layers in LISA
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1390>)lisa_step_interval: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1391>)# path under the model to access the layers
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1392>)lisa_layers_attribute: str | None = model.layers
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1393>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1394>)gradio_title: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1395>)gradio_share: bool | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1396>)gradio_server_name: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1397>)gradio_server_port: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1398>)gradio_max_new_tokens: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1399>)gradio_temperature: float | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1400>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1401>)use_ray: bool = False
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1402>)ray_run_name: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1403>)ray_num_workers: int = 1
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1404>)resources_per_worker: dict
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1405>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1406>)# The size of the image to resize to. It can be an integer (resized into padded-square
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1407>)# image) or a tuple (width, height).If not provided, we will attempt to load from
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1408>)# preprocessor.size, otherwise, images won't be resized.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1409>)image_size: int | tuple[int, int] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1410>)# The resampling algorithm to use for image resizing. Default is bilinear. Please refer
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1411>)# to PIL.Image.Resampling for more details.
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1412>)image_resize_algorithm: Literal['bilinear', 'bicubic', 'lanczos'] | Resampling | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1413>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1414>)# optional overrides to the base model configuration
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1415>)overrides_of_model_config: dict[str, Any] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1416>)# optional overrides the base model loading from_pretrained
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1417>)overrides_of_model_kwargs: dict[str, Any] | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1418>)# If you want to specify the type of model to load, AutoModelForCausalLM is a good
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1419>)# choice too
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1420>)type_of_model: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1421>)# You can specify to choose a specific model revision from huggingface hub
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1422>)revision_of_model: str | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1423>)
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1424>)max_packed_sequence_len: int | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1425>)rope_scaling: Any | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1426>)noisy_embedding_alpha: float | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1427>)dpo_beta: float | None
[](https://docs.axolotl.ai/docs/<https:/docs.axolotl.ai/docs/config-reference.html#cb1-1428>)evaluation_strategy: str | None
```

