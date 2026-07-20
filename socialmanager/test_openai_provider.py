from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from socialmanager.services.ai_provider import create_openai_client, get_openai_config_status


class OpenAIProviderTests(SimpleTestCase):
    @override_settings(
        AI_PROVIDER="openai",
        OPENAI_AGENT_ENABLED=False,
        OPENAI_API_KEY="test-secret",
        OPENAI_AGENT_MODEL="test-model",
    )
    @patch("socialmanager.services.ai_provider.OpenAI")
    def test_disabled_agent_does_not_create_client(self, openai_client):
        self.assertIsNone(create_openai_client())
        openai_client.assert_not_called()

    @override_settings(
        AI_PROVIDER="openai",
        OPENAI_AGENT_ENABLED=True,
        OPENAI_API_KEY="test-secret",
        OPENAI_AGENT_MODEL="test-model",
    )
    @patch("socialmanager.services.ai_provider.OpenAI")
    def test_complete_enabled_config_creates_client(self, openai_client):
        client = create_openai_client()
        self.assertIs(client, openai_client.return_value)
        openai_client.assert_called_once_with(api_key="test-secret")

    @override_settings(
        AI_PROVIDER="openai",
        OPENAI_AGENT_ENABLED=True,
        OPENAI_API_KEY="",
        OPENAI_AGENT_MODEL="test-model",
    )
    @patch("socialmanager.services.ai_provider.OpenAI")
    def test_missing_api_key_does_not_create_client(self, openai_client):
        status = get_openai_config_status()
        self.assertFalse(status.api_key_loaded)
        self.assertIsNone(create_openai_client())
        openai_client.assert_not_called()
