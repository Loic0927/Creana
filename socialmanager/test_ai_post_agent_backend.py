import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.auth.models import User
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from openai import APITimeoutError, RateLimitError

from socialmanager.forms import SocialMediaPostForm
from socialmanager.models import (
    SaaSSubscription,
    SocialMediaCampaign,
    SocialMediaPost,
    SubscriptionMembership,
    UserSettings,
)
from socialmanager.services.ai_post_agent import (
    ALLOWED_CONTENT_GOALS,
    AgentStructuredResponse,
    PostAgentError,
    build_post_agent_input,
    build_post_agent_payload,
    build_post_agent_instructions,
    generate_post_content,
    normalise_hashtags,
    validate_generated_content,
)
from socialmanager.services.ai_post_agent_images import PreparedAgentImage


class AIPostAgentServiceTests(SimpleTestCase):
    def make_input(self, **overrides):
        values = {
            "user": SimpleNamespace(pk=10),
            "workspace": SimpleNamespace(pk=20),
            "language": "english",
            "content_goal": "encourage_engagement",
            "context": "Launch a useful creator tool.",
            "skipped_context": False,
            "detected_media": ["image"],
            "media_metadata": [{"type": "image", "extension": ".png", "content_type": "image/png"}],
            "article_text": "",
            "existing_title": "",
            "existing_caption": "",
            "existing_hashtags": "",
            "requested_fields": ["title", "caption", "hashtags"],
            "preferred_hashtag_count": 3,
        }
        values.update(overrides)
        return build_post_agent_input(**values)

    @override_settings(AI_PROVIDER="openai", OPENAI_AGENT_ENABLED=False, OPENAI_API_KEY="secret", OPENAI_AGENT_MODEL="model")
    @patch("socialmanager.services.ai_post_agent.create_openai_client")
    def test_disabled_agent_never_creates_client(self, create_client):
        with self.assertRaisesRegex(PostAgentError, "disabled") as caught:
            generate_post_content(self.make_input())
        self.assertEqual(caught.exception.error_code, "agent_disabled")
        create_client.assert_not_called()

    @override_settings(AI_PROVIDER="openai", OPENAI_AGENT_ENABLED=True, OPENAI_API_KEY="secret", OPENAI_AGENT_MODEL="")
    def test_missing_model_is_explicit(self):
        with self.assertRaises(PostAgentError) as caught:
            generate_post_content(self.make_input())
        self.assertEqual(caught.exception.error_code, "missing_configuration")

    @override_settings(AI_PROVIDER="openai", OPENAI_AGENT_ENABLED=True, OPENAI_API_KEY="", OPENAI_AGENT_MODEL="model")
    def test_missing_key_is_explicit_and_not_leaked(self):
        with self.assertRaises(PostAgentError) as caught:
            generate_post_content(self.make_input())
        self.assertEqual(caught.exception.error_code, "missing_configuration")
        self.assertNotIn("secret", str(caught.exception))

    @override_settings(AI_PROVIDER="gemini", OPENAI_AGENT_ENABLED=True, OPENAI_API_KEY="secret", OPENAI_AGENT_MODEL="model")
    def test_non_openai_provider_is_rejected(self):
        with self.assertRaises(PostAgentError) as caught:
            generate_post_content(self.make_input())
        self.assertEqual(caught.exception.error_code, "provider_not_supported")

    def test_valid_structured_response_and_unrequested_fields(self):
        result = validate_generated_content(
            AgentStructuredResponse(title="Launch day", caption="Ignored", hashtags=[]),
            ["title"],
            3,
        )
        self.assertEqual(result.title, "Launch day")
        self.assertIsNone(result.caption)
        self.assertEqual(result.hashtags, [])

    def test_missing_requested_field_is_invalid(self):
        with self.assertRaises(PostAgentError) as caught:
            validate_generated_content(AgentStructuredResponse(), ["caption"], 3)
        self.assertEqual(caught.exception.error_code, "invalid_model_response")

    def test_hashtags_are_normalised_deduplicated_and_count_limited(self):
        self.assertEqual(
            normalise_hashtags(["##Creana", "#creana", " social media ", "#third", "#fourth"], 3),
            ["#Creana", "#socialmedia", "#third"],
        )

    def test_long_title_and_caption_are_safely_truncated(self):
        result = validate_generated_content(
            AgentStructuredResponse(title="T" * 80, caption="C" * 300),
            ["title", "caption"],
            3,
        )
        self.assertEqual(len(result.title), 50)
        self.assertEqual(len(result.caption), 250)

    def test_invalid_parsed_schema_is_handled(self):
        with self.assertRaises(PostAgentError) as caught:
            validate_generated_content(None, ["title"], 3)
        self.assertEqual(caught.exception.error_code, "invalid_model_response")

    @override_settings(AI_PROVIDER="openai", OPENAI_AGENT_ENABLED=True, OPENAI_API_KEY="secret", OPENAI_AGENT_MODEL="model")
    @patch("socialmanager.services.ai_post_agent.create_openai_client")
    def test_success_uses_responses_parse_with_configured_model(self, create_client):
        parsed = AgentStructuredResponse(title="Title", caption="Caption", hashtags=["#one", "#two", "#three"])
        configured = Mock()
        configured.responses.parse.return_value = SimpleNamespace(output_parsed=parsed)
        create_client.return_value.with_options.return_value = configured
        result = generate_post_content(self.make_input())
        self.assertEqual(result.title, "Title")
        self.assertEqual(configured.responses.parse.call_args.kwargs["model"], "model")

    def test_payload_contains_image_as_a_separate_multimodal_part(self):
        agent_input = self.make_input(images=[PreparedAgentImage(b"jpeg-bytes")])
        payload = build_post_agent_payload(agent_input)
        content = payload[0]["content"]
        self.assertEqual(content[0]["type"], "input_text")
        self.assertEqual(content[1]["type"], "input_image")
        self.assertTrue(content[1]["image_url"].startswith("data:image/jpeg;base64,"))
        self.assertNotIn("jpeg-bytes", content[0]["text"])

    def test_all_content_goals_are_allowlisted_and_mapped_into_payload(self):
        self.assertEqual(len(ALLOWED_CONTENT_GOALS), 7)
        for goal in ALLOWED_CONTENT_GOALS:
            custom_goal = "Help local artists find collaborators" if goal == "other" else ""
            agent_input = self.make_input(content_goal=goal, custom_content_goal=custom_goal)
            payload_text = build_post_agent_payload(agent_input)[0]["content"][0]["text"]
            self.assertIn('"content_goal"', payload_text)
            self.assertNotIn(f'"label": "{goal}"', payload_text)
            self.assertIn("structured content-goal guidance", build_post_agent_instructions(agent_input))

    def test_missing_translated_and_unknown_content_goals_are_rejected(self):
        for goal in ("", "Increase reach", "增加觸及", "Other", "arbitrary_goal"):
            with self.subTest(goal=goal), self.assertRaises(PostAgentError) as caught:
                self.make_input(content_goal=goal)
            self.assertEqual(caught.exception.error_code, "invalid_request")

    def test_other_requires_trimmed_bounded_custom_goal_and_adds_it_to_payload(self):
        for custom_goal in ("", "   ", "x" * 151):
            with self.subTest(custom_goal=custom_goal), self.assertRaises(PostAgentError) as caught:
                self.make_input(content_goal="other", custom_content_goal=custom_goal)
            self.assertEqual(caught.exception.error_code, "invalid_request")
        agent_input = self.make_input(content_goal="other", custom_content_goal="  Support local artists  ")
        self.assertEqual(agent_input.custom_content_goal, "Support local artists")
        payload_text = build_post_agent_payload(agent_input)[0]["content"][0]["text"]
        self.assertIn("Support local artists", payload_text)

    @override_settings(AI_PROVIDER="openai", OPENAI_AGENT_ENABLED=True, OPENAI_API_KEY="secret", OPENAI_AGENT_MODEL="model")
    @patch("socialmanager.services.ai_post_agent.create_openai_client")
    def test_timeout_mapping(self, create_client):
        configured = Mock()
        configured.responses.parse.side_effect = APITimeoutError(request=httpx.Request("POST", "https://api.openai.com/v1/responses"))
        create_client.return_value.with_options.return_value = configured
        with self.assertRaises(PostAgentError) as caught:
            generate_post_content(self.make_input())
        self.assertEqual(caught.exception.error_code, "provider_timeout")

    @override_settings(AI_PROVIDER="openai", OPENAI_AGENT_ENABLED=True, OPENAI_API_KEY="secret", OPENAI_AGENT_MODEL="model")
    @patch("socialmanager.services.ai_post_agent.create_openai_client")
    def test_rate_limit_mapping_does_not_leak_key(self, create_client):
        response = httpx.Response(429, request=httpx.Request("POST", "https://api.openai.com/v1/responses"))
        configured = Mock()
        configured.responses.parse.side_effect = RateLimitError("rate limited secret", response=response, body=None)
        create_client.return_value.with_options.return_value = configured
        with self.assertRaises(PostAgentError) as caught:
            generate_post_content(self.make_input())
        self.assertEqual(caught.exception.error_code, "provider_rate_limited")
        self.assertNotIn("secret", caught.exception.safe_message)


class AIPostAgentEndpointTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="agent-user", password="password12345")
        self.workspace = SaaSSubscription.objects.create(name="Agent Workspace", owner=self.user)
        self.membership = SubscriptionMembership.objects.create(
            subscription=self.workspace,
            user=self.user,
            role=SubscriptionMembership.Role.ADMIN,
            is_active_member=True,
        )
        self.settings, _ = UserSettings.objects.get_or_create(user=self.user)
        self.settings.ai_hashtag_count = 2
        self.settings.ai_language = "english"
        self.settings.save(update_fields=["ai_hashtag_count", "ai_language"])
        self.url = reverse("socialmanager:post_agent_generate_content")
        self.payload = {
            "context": "A product launch for creators.",
            "content_goal": "promote_product_service",
            "skipped_context": False,
            "requested_fields": ["title", "caption", "hashtags"],
            "detected_media": ["image"],
            "media_metadata": [{"type": "image", "extension": ".png", "content_type": "image/png"}],
            "article_text": "",
            "existing_title": "",
            "existing_caption": "",
            "existing_hashtags": "",
        }

    def post_json(self, payload=None):
        values = dict(self.payload if payload is None else payload)
        values["requested_fields"] = json.dumps(values.get("requested_fields", []))
        values["detected_media_types"] = json.dumps(values.pop("detected_media", []))
        values.pop("media_metadata", None)
        return self.client.post(self.url, data=values)

    def test_get_is_rejected(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_unauthenticated_request_is_rejected(self):
        response = self.post_json()
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error_code"], "permission_denied")

    def test_csrf_is_required(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        values = dict(self.payload)
        values["requested_fields"] = json.dumps(values["requested_fields"])
        values["detected_media_types"] = json.dumps(values.pop("detected_media"))
        values.pop("media_metadata", None)
        response = csrf_client.post(self.url, data=values)
        self.assertEqual(response.status_code, 403)

    def test_json_request_is_rejected(self):
        self.client.force_login(self.user)
        response = self.client.post(self.url, data=json.dumps(self.payload), content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error_code"], "invalid_request")

    @patch("socialmanager.agent_views.generate_post_content")
    def test_inactive_member_cannot_call_or_reach_openai_service(self, generate):
        self.membership.is_active_member = False
        self.membership.save(update_fields=["is_active_member"])
        self.client.force_login(self.user)
        response = self.post_json()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error_code"], "membership_required")
        generate.assert_not_called()

    @patch("socialmanager.agent_views.generate_post_content")
    def test_superuser_exemption_reaches_service_without_active_membership(self, generate):
        superuser = User.objects.create_superuser(username="agent-root", password="password12345")
        SaaSSubscription.objects.create(name="Root Agent Workspace", owner=superuser)
        generate.return_value = SimpleNamespace(as_dict=lambda: {
            "title": "Root", "caption": "Allowed.", "hashtags": ["#one", "#two"], "warnings": [],
        })
        self.client.force_login(superuser)
        response = self.post_json()
        self.assertEqual(response.status_code, 200)
        generate.assert_called_once()

    def test_other_workspace_post_is_rejected(self):
        other = User.objects.create_user(username="other-agent")
        other_workspace = SaaSSubscription.objects.create(name="Other", owner=other)
        post = SocialMediaPost.objects.create(
            subscription=other_workspace, author=other, title="Other", platform="instagram",
            content_format="article", status="draft", visibility="private",
        )
        self.client.force_login(self.user)
        response = self.post_json({**self.payload, "post_id": post.pk})
        self.assertEqual(response.status_code, 403)

    def test_project_source_queryset_is_scoped_to_current_workspace(self):
        own_project = SocialMediaCampaign.objects.create(
            subscription=self.workspace, created_by=self.user, name="Own Project",
        )
        other = User.objects.create_user(username="project-other")
        other_workspace = SaaSSubscription.objects.create(name="Project Other", owner=other)
        SocialMediaCampaign.objects.create(
            subscription=other_workspace, created_by=other, name="Hidden Project",
        )
        project_ids = set(SocialMediaPostForm(subscription=self.workspace).fields["campaign"].queryset.values_list("pk", flat=True))
        self.assertEqual(project_ids, {own_project.pk})

    def test_context_over_limit_and_invalid_fields_are_rejected(self):
        self.client.force_login(self.user)
        self.assertEqual(self.post_json({**self.payload, "context": "x" * 1001}).status_code, 400)
        self.assertEqual(self.post_json({**self.payload, "requested_fields": []}).json()["error_code"], "no_fields_selected")
        self.assertEqual(self.post_json({**self.payload, "requested_fields": ["admin"]}).status_code, 400)

    @patch("socialmanager.agent_views.generate_post_content")
    def test_missing_or_unsupported_goal_is_rejected_before_provider(self, generate):
        self.client.force_login(self.user)
        for goal in ("", "Increase reach", "unsupported"):
            with self.subTest(goal=goal):
                response = self.post_json({**self.payload, "content_goal": goal})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["error_code"], "invalid_request")
        generate.assert_not_called()

    @patch("socialmanager.agent_views.generate_post_content")
    def test_all_valid_goals_reach_service_as_structured_values(self, generate):
        generate.return_value = SimpleNamespace(as_dict=lambda: {
            "title": "Goal", "caption": "Guided.", "hashtags": ["#one", "#two"], "warnings": [],
        })
        self.client.force_login(self.user)
        for goal in ALLOWED_CONTENT_GOALS:
            with self.subTest(goal=goal):
                custom_goal = "Support local artists" if goal == "other" else ""
                response = self.post_json({**self.payload, "content_goal": goal, "custom_content_goal": custom_goal})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(generate.call_args.args[0].content_goal, goal)

    @patch("socialmanager.agent_views.generate_post_content")
    def test_invalid_custom_goal_is_rejected_before_provider(self, generate):
        self.client.force_login(self.user)
        for custom_goal in ("", "   ", "x" * 151):
            with self.subTest(custom_goal=custom_goal):
                response = self.post_json({**self.payload, "content_goal": "other", "custom_content_goal": custom_goal})
                self.assertEqual(response.status_code, 400)
        generate.assert_not_called()

    @patch("socialmanager.agent_views.generate_post_content")
    def test_success_shape_and_no_post_or_project_mutation(self, generate):
        generate.return_value = SimpleNamespace(as_dict=lambda: {
            "title": "Launch", "caption": "For creators.", "hashtags": ["#one", "#two"], "warnings": [],
        })
        self.client.force_login(self.user)
        posts_before = SocialMediaPost.objects.count()
        projects_before = SocialMediaCampaign.objects.count()
        response = self.post_json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])
        self.assertEqual(response.json()["data"]["hashtags"], ["#one", "#two"])
        self.assertEqual(generate.call_args.args[0].preferred_hashtag_count, 2)
        self.assertEqual(SocialMediaPost.objects.count(), posts_before)
        self.assertEqual(SocialMediaCampaign.objects.count(), projects_before)
        self.assertNotIn("secret", response.content.decode())

    @patch("socialmanager.agent_views.generate_post_content")
    def test_uploaded_image_is_prepared_and_reaches_service(self, generate):
        from io import BytesIO
        from PIL import Image

        output = BytesIO()
        Image.new("RGB", (30, 20), "blue").save(output, format="PNG")
        upload = SimpleUploadedFile("fox.png", output.getvalue(), content_type="image/png")
        generate.return_value = SimpleNamespace(as_dict=lambda: {
            "title": "Fox", "caption": "A blue fox.", "hashtags": ["#one", "#two"], "warnings": [],
        })
        self.client.force_login(self.user)
        values = dict(self.payload)
        values["requested_fields"] = json.dumps(values["requested_fields"])
        values["detected_media_types"] = json.dumps(values.pop("detected_media"))
        values.pop("media_metadata", None)
        values["image_files"] = upload
        response = self.client.post(self.url, data=values)
        self.assertEqual(response.status_code, 200)
        agent_input = generate.call_args.args[0]
        self.assertEqual(len(agent_input.images), 1)
        self.assertEqual(agent_input.images[0].content_type, "image/jpeg")

    @patch("socialmanager.agent_views.generate_post_content")
    def test_over_10_mb_image_is_rejected_before_provider_with_ai_limit_message(self, generate):
        upload = SimpleUploadedFile("large-photo.jpg", b"x" * (10 * 1024 * 1024 + 1), content_type="image/jpeg")
        self.client.force_login(self.user)
        values = dict(self.payload)
        values["requested_fields"] = json.dumps(values["requested_fields"])
        values["detected_media_types"] = json.dumps(values.pop("detected_media"))
        values.pop("media_metadata", None)
        values["image_files"] = upload
        response = self.client.post(self.url, data=values)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error_code"], "image_too_large")
        self.assertIn("large-photo.jpg", response.json()["message"])
        self.assertIn("10 MB AI analysis limit", response.json()["message"])
        generate.assert_not_called()
