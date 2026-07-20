from django.core.management.base import BaseCommand

from socialmanager.services.ai_provider import (
    SUPPORTED_AI_PROVIDERS,
    create_openai_client,
    get_openai_config_status,
)


class Command(BaseCommand):
    help = "Safely check OpenAI agent configuration without printing the API key."

    def handle(self, *args, **options):
        status = get_openai_config_status()
        client = create_openai_client()

        self.stdout.write(f"AI provider: {status.provider or 'Not set'}")
        self.stdout.write(
            f"AI provider supported: {'Yes' if status.provider in SUPPORTED_AI_PROVIDERS else 'No'}"
        )
        self.stdout.write(f"OpenAI agent enabled: {'Yes' if status.enabled else 'No'}")
        self.stdout.write(f"OpenAI model loaded: {'Yes' if status.model_loaded else 'No'}")
        self.stdout.write(f"OpenAI API Key loaded: {'Yes' if status.api_key_loaded else 'No'}")
        self.stdout.write(f"OpenAI Client initialized: {'Yes' if client is not None else 'No'}")

        if not status.enabled:
            self.stdout.write("OpenAI calls allowed: No (agent disabled)")
        elif not status.api_key_loaded or not status.model_loaded:
            self.stdout.write("OpenAI calls allowed: No (configuration incomplete)")
        else:
            self.stdout.write(self.style.SUCCESS("OpenAI calls allowed: Yes"))
