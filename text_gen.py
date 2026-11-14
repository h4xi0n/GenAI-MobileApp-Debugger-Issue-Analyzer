"""Foundational Text Generation Entry Point.

This root script maintains backwards compatibility and delegates to
the modular tutorial at `tutorials/01_foundations/text_generation.py`.
"""

import sys
from pathlib import Path
import importlib.util

def main():
    target_path = Path(__file__).resolve().parent / "tutorials" / "01_foundations" / "text_generation.py"
    spec = importlib.util.spec_from_file_location("text_generation", target_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.run_text_generation()

if __name__ == "__main__":
    main()
