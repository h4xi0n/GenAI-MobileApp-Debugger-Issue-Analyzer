"""Tutorial 02: Prompt Engineering Techniques (Zero-shot, Few-shot, System Directives)."""

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.prompts.templates import PromptTemplate

def run_prompt_demo():
    # 1. Zero-shot template
    zero_shot = PromptTemplate("Translate the following English sentence to French:\nEnglish: {sentence}\nFrench:")
    print("--- Zero-Shot Prompt ---")
    print(zero_shot.format(sentence="The generative AI architecture is modular."))

    # 2. Few-shot template
    few_shot_template = """Classify sentiment as Positive, Neutral, or Negative.

Review: The model latency is ultra-fast and outputs are crisp!
Sentiment: Positive

Review: It crashes every second request.
Sentiment: Negative

Review: {user_review}
Sentiment:"""
    few_shot = PromptTemplate(few_shot_template)
    print("\n--- Few-Shot Prompt ---")
    print(few_shot.format(user_review="The setup process was straightforward."))

if __name__ == "__main__":
    run_prompt_demo()
