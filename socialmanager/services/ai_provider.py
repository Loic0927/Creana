"""Provider configuration and lazy OpenAI client initialization.

This module deliberately does not route existing Gemini features. It provides
the provider boundary needed by a future agent without changing today's AI
caption, feedback, image, or video pipelines.
"""

from dataclasses import dataclass

from django.conf import settings
from openai import OpenAI


SUPPORTED_AI_PROVIDERS = frozenset({"gemini", "openai"})


@dataclass(frozen=True)
class OpenAIConfigStatus:
    provider: str
    enabled: bool
    api_key_loaded: bool
    model: str

    @property
    def model_loaded(self):
        return bool(self.model)

    @property
    def can_initialize_client(self):
        return self.enabled and self.api_key_loaded and self.model_loaded


def get_ai_provider():
    """Return the normalized configured provider name."""
    return str(getattr(settings, "AI_PROVIDER", "gemini") or "gemini").strip().lower()


def get_openai_config_status():
    """Return non-secret OpenAI configuration state."""
    api_key = str(getattr(settings, "OPENAI_API_KEY", "") or "").strip()
    model = str(getattr(settings, "OPENAI_AGENT_MODEL", "") or "").strip()
    return OpenAIConfigStatus(
        provider=get_ai_provider(),
        enabled=bool(getattr(settings, "OPENAI_AGENT_ENABLED", False)),
        api_key_loaded=bool(api_key),
        model=model,
    )


def create_openai_client():
    """Create an OpenAI client only when the agent configuration permits it.

    Returning ``None`` for incomplete or disabled configuration keeps callers
    from accidentally making OpenAI requests. Client construction itself does
    not make a network request.
    """
    status = get_openai_config_status()
    if not status.can_initialize_client:
        return None
    return OpenAI(api_key=settings.OPENAI_API_KEY)


def get_configured_provider_client():
    """Provider boundary for future functionality; Gemini remains unchanged."""
    if get_ai_provider() == "openai":
        return create_openai_client()
    return None
