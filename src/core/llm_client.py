"""Unified LLM client interface for local and API-based models."""

from typing import List, Optional
from transformers import pipeline, Pipeline
from config.settings import DEFAULT_LLM_MODEL
from src.core.logger import setup_logger

logger = setup_logger(__name__)

class HuggingFaceLocalLLM:
    """Wrapper around HuggingFace Transformers text-generation pipeline."""

    def __init__(self, model_name: str = DEFAULT_LLM_MODEL, task: str = "text-generation"):
        self.model_name = model_name
        self.task = task
        logger.info(f"Initializing local pipeline for task '{task}' with model '{model_name}'...")
        self.generator: Pipeline = pipeline(task, model=model_name)
        logger.info("Model pipeline loaded successfully.")

    def generate(
        self,
        prompt: str,
        max_length: int = 50,
        num_return_sequences: int = 1,
        temperature: Optional[float] = None,
        **kwargs
    ) -> List[str]:
        """Generate text completions for a given prompt."""
        gen_kwargs = {
            "max_length": max_length,
            "num_return_sequences": num_return_sequences,
            **kwargs
        }
        if temperature is not None:
            gen_kwargs["temperature"] = temperature
            gen_kwargs["do_sample"] = True

        outputs = self.generator(prompt, **gen_kwargs)
        return [out.get("generated_text", "") for out in outputs]
