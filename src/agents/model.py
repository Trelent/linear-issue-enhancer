"""Shared model configuration for all agents."""

import os
from agents.extensions.models.litellm_model import LitellmModel

MODEL_ID = "anthropic/claude-opus-4-5-20251101"


def get_model() -> LitellmModel:
    """Create a LitellmModel with explicit API key from environment."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY environment variable is not set")
    
    return LitellmModel(
        model=MODEL_ID,
        api_key=api_key,
    )

