"""
Provider factory.

Creates the appropriate provider based on configuration.
"""

from __future__ import annotations

from .anthropic_provider import AnthropicProvider
from .base import Provider
from .openai_provider import OpenAIProvider


def create_provider(
    provider_type: str,
    api_key: str,
    base_url: str,
    model: str,
) -> Provider:
    """Create a provider instance based on provider_type.

    Both providers use the same api_key/base_url/model fields
    from LLMConfig. provider_type only selects which SDK to use.

    Args:
        provider_type: "openai" or "anthropic".
        api_key: API key for the provider.
        base_url: Base URL for the provider API.
        model: Model name/ID to use.

    Returns:
        Provider instance.

    Raises:
        ValueError: If provider_type is not recognized.
    """
    match provider_type:
        case "openai":
            return OpenAIProvider(api_key=api_key, base_url=base_url, model=model)
        case "anthropic":
            return AnthropicProvider(api_key=api_key, base_url=base_url, model=model)
        case _:
            raise ValueError(
                f"Unknown provider_type: '{provider_type}'. "
                "Supported values: 'openai', 'anthropic'."
            )
