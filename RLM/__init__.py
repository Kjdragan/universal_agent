"""Standalone RLM experimental module for large-corpus distillation."""

from .runner import compare_lanes, run_distillation

__all__ = ["run_distillation", "compare_lanes"]
