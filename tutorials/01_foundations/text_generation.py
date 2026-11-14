"""Tutorial 01: Foundations - Text Generation with HuggingFace Transformers.

Demonstrates pipeline loading, temperature sampling, and text generation.
"""

import sys
from pathlib import Path

# Add project root to sys.path so src and config are discoverable
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.core.llm_client import HuggingFaceLocalLLM
from src.core.logger import setup_logger

logger = setup_logger("tutorial_01")

def run_text_generation():
    prompt = "Artificial Intelligence will"
    logger.info(f"Running generation with prompt: '{prompt}'")

    # Initialize model using shared core client
    llm = HuggingFaceLocalLLM(model_name="gpt2")

    # Generate completions
    results = llm.generate(
        prompt=prompt,
        max_length=50,
        num_return_sequences=1,
        temperature=0.7
    )

    print("\n" + "=" * 50)
    print(f"PROMPT: {prompt}")
    print("=" * 50)
    for i, text in enumerate(results, start=1):
        print(f"\nGenerated Result {i}:\n{text}")
    print("=" * 50 + "\n")

if __name__ == "__main__":
    run_text_generation()
