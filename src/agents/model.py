"""Shared model configuration for all agents."""

import os
import re
from dataclasses import dataclass

from agents import ModelSettings
from agents.extensions.models.litellm_model import LitellmModel
from openai.types.shared import Reasoning

# Model shorthand mapping (value is model ID, or None for native OpenAI models)
MODEL_MAP = {
    "sonnet": "anthropic/claude-sonnet-4-5-20250929",
    "haiku": "anthropic/claude-haiku-4-5-20251001",
    "opus": "anthropic/claude-opus-4-5-20251101",
    "gpt": "openai/gpt-5.2",
}

# Models that use native OpenAI (not LiteLLM)
OPENAI_MODELS = {"gpt"}

DEFAULT_MODEL = "sonnet"


@dataclass
class ModelConfig:
    """Configuration for an agent's model."""
    model: LitellmModel | str
    model_settings: ModelSettings | None = None


def get_model_config(shorthand: str | None = None) -> ModelConfig:
    """Get model configuration for the specified shorthand."""
    model_key = shorthand or DEFAULT_MODEL
    
    if model_key not in MODEL_MAP:
        model_key = DEFAULT_MODEL
    
    model_id = MODEL_MAP[model_key]
    
    # GPT-5 uses native OpenAI with reasoning settings
    if model_key in OPENAI_MODELS:
        return ModelConfig(
            model=model_id,
            model_settings=ModelSettings(
                reasoning=Reasoning(effort="high"),
            ),
        )
    
    # Claude models use LiteLLM
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY environment variable is not set")
    
    return ModelConfig(
        model=LitellmModel(model=model_id, api_key=api_key),
        model_settings=ModelSettings(),
    )


def parse_model_tag(text: str) -> str | None:
    """Extract model shorthand from [model=X] tag in text."""
    match = re.search(r'\[model=(\w+)\]', text, re.IGNORECASE)
    if not match:
        return None
    
    shorthand = match.group(1).lower()
    if shorthand not in MODEL_MAP:
        return None
    
    return shorthand

