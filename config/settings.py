"""Configuration and environment management for GenAI projects."""

import os
from pathlib import Path
from typing import Any, Dict
import yaml
from dotenv import load_dotenv

# Base project paths
BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config" / "config.yaml"
ENV_PATH = BASE_DIR / ".env"

# Load environment variables from .env
load_dotenv(dotenv_path=ENV_PATH)

def load_yaml_config(file_path: Path = CONFIG_PATH) -> Dict[str, Any]:
    """Load configuration from a YAML file."""
    if not file_path.exists():
        return {}
    with open(file_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

CONFIG = load_yaml_config()

# Convenient constants
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
HF_TOKEN = os.getenv("HF_TOKEN", "")
DEFAULT_LLM_MODEL = os.getenv("DEFAULT_LLM_MODEL", CONFIG.get("models", {}).get("default_llm", "gpt2"))
DEFAULT_EMBEDDING_MODEL = os.getenv(
    "DEFAULT_EMBEDDING_MODEL",
    CONFIG.get("models", {}).get("default_embedding", "sentence-transformers/all-MiniLM-L6-v2")
)
