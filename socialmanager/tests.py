import json
import tempfile
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from allauth.account.signals import user_signed_up
from allauth.account.models import EmailAddress
from allauth.core.context import request_context
from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.helpers import complete_social_login
from allauth.socialaccount.models import SocialAccount, SocialLogin
from allauth.socialaccount.providers.base import AuthProcess
from django.contrib.auth.models import AnonymousUser
from django.contrib.admin.sites import AdminSite
from django.core.management import call_command
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.auth.models import Permission
from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils import timezone
from django.utils.http import urlsafe_base64_encode
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import ValidationError
from PIL import Image

from .admin import SaaSSubscriptionAdmin
from .adapters import (
    ADMIN_GOOGLE_LINK_MESSAGE,
    AUTHENTICATED_GOOGLE_LOGIN_MESSAGE,
    GOOGLE_EMAIL_REQUIRED_MESSAGE,
    GOOGLE_LINKED_ELSEWHERE_MESSAGE,
    USER_GOOGLE_EXISTS_MESSAGE,
    SocialManagerSocialAccountAdapter,
)
from .forms import CreanaPasswordResetForm, SignUpForm, SocialMediaCampaignForm, SocialMediaPostForm
from .services import ai_assistant
from .services.video_metadata import VideoDurationError
from .models import AISuggestionHistory, Announcement, HiddenUser, Notification, POST_CAPTION_MAX_LENGTH, POST_TITLE_MAX_LENGTH, PostComment, PostImage, SaaSSubscription, SocialMediaCampaign, SocialMediaPost, SubscriptionMembership, UserProfile, UserSettings, VideoAnalysis, VideoEngagementEvent, VideoWatchSession
from .subscriptions import user_has_active_subscription
from .views import cache_ai_insight, dashboard_analysis_to_report, PostAnalyticsView, parse_ai_insight_sections, render_ai_insight_html


class AIInsightRenderingTests(TestCase):
    def test_structured_parser_handles_json_fences_escaped_json_and_python_dicts(self):
        expected = [{"heading": "Overall performance", "points": ["Reach is growing."]}]
        payload = {"sections": expected}
        values = [
            payload,
            json.dumps(payload),
            "```json\n" + json.dumps(payload) + "\n```",
            json.dumps(json.dumps(payload)),
            "{'Overall performance': ['Reach is growing.']}",
        ]

        for value in values:
            with self.subTest(value=value):
                self.assertEqual(parse_ai_insight_sections(value), expected)

    def test_renderer_never_exposes_unparsed_model_output(self):
        raw_response = '{"sections": broken model output}'

        html = render_ai_insight_html(raw_response)

        self.assertIn("Unable to format this insight", html)
        self.assertNotIn(raw_response, html)
        self.assertNotIn('{"sections"', html)

    def test_unparseable_ai_response_is_not_cached(self):
        user = User.objects.create_user(username="uncached-ai-response")
        subscription = SaaSSubscription.objects.create(
            name="Uncached AI Workspace",
            owner=user,
        )

        cached = cache_ai_insight(
            subscription,
            user,
            "ai-insight:test:invalid",
            '{"sections": broken model output}',
            "Retention insight",
        )

        self.assertIsNone(cached)
        self.assertFalse(AISuggestionHistory.objects.exists())

    def test_intentional_no_data_reports_are_structured(self):
        dashboard_report = dashboard_analysis_to_report(
            {"has_enough_data": False, "message": "No dashboard data yet."}
        )
        campaign_report = ai_assistant.generate_campaign_rule_based_analysis(
            {"released_count": 0}
        )

        self.assertEqual(
            parse_ai_insight_sections(dashboard_report),
            [
                {
                    "heading": "Dashboard insight",
                    "points": ["No dashboard data yet."],
                }
            ],
        )
        self.assertEqual(
            parse_ai_insight_sections(campaign_report)[0]["heading"],
            "Campaign insight",
        )

    def test_traditional_chinese_retention_timed_events_are_localized(self):
        report = ai_assistant.generate_video_retention_rule_based_analysis(
            {
                "completion_rate": 50.0,
                "points": [
                    {"seconds": 0, "retention": 100.0, "engagement_count": 0},
                    {
                        "seconds": 10,
                        "retention": 50.0,
                        "engagement_count": 2,
                        "timed_likes": 1,
                        "timed_comments": 1,
                        "timed_shares": 0,
                    },
                ],
            },
            language="Traditional Chinese",
        )

        self.assertIn("記錄到的定時互動包括", report)
        self.assertIn("1 次按讚", report)
        self.assertIn("1 則留言", report)
        self.assertNotIn("Recorded activity includes", report)

    def test_rule_based_retention_report_is_structured_and_uses_completion_and_timed_events(self):
        report = ai_assistant.generate_video_retention_rule_based_analysis(
            {
                "completion_rate": 50.0,
                "points": [
                    {
                        "seconds": 0,
                        "retention": 100.0,
                        "engagement_count": 0,
                    },
                    {
                        "seconds": 10,
                        "retention": 50.0,
                        "engagement_count": 2,
                        "timed_likes": 1,
                        "timed_comments": 1,
                        "timed_shares": 0,
                    },
                ],
            }
        )

        sections = parse_ai_insight_sections(report)
        rendered_text = " ".join(
            point for section in sections for point in section["points"]
        )
        self.assertTrue(sections)
        self.assertIn("completion rate is 50.0%", rendered_text)
        self.assertIn("1 timed like", rendered_text)
        self.assertIn("1 timed comment", rendered_text)


class VideoContentAnalysisTests(TestCase):
    def create_user_and_post(self, *, active_member=True, content_format=SocialMediaPost.Format.VIDEO):
        user = User.objects.create_user(username=f"video-analysis-{User.objects.count()}", password="password12345")
        subscription = SaaSSubscription.objects.create(name="Video Workspace", owner=user)
        SubscriptionMembership.objects.create(
            subscription=subscription,
            user=user,
            role=SubscriptionMembership.Role.ADMIN,
            is_active_member=active_member,
        )
        post = SocialMediaPost.objects.create(
            subscription=subscription,
            author=user,
            title="Coffee tutorial",
            platform=SocialMediaPost.Platform.INSTAGRAM,
            content_format=content_format,
            video_file="social_videos/coffee.mp4" if content_format == SocialMediaPost.Format.VIDEO else None,
        )
        return user, post

    def test_video_chart_uses_bucket_intensity_and_preserves_retention_formula(self):
        user, post = self.create_user_and_post()
        second_viewer = User.objects.create_user(username="video-chart-second-viewer")
        VideoWatchSession.objects.create(
            post=post,
            viewer=user,
            watched_seconds=5,
            video_duration=10,
            watched_percentage=50,
        )
        VideoWatchSession.objects.create(
            post=post,
            viewer=second_viewer,
            watched_seconds=10,
            video_duration=10,
            watched_percentage=100,
        )
        for kind, second in (
            (VideoEngagementEvent.Kind.LIKE, 0),
            (VideoEngagementEvent.Kind.COMMENT, 2),
            (VideoEngagementEvent.Kind.SHARE, 7),
            (VideoEngagementEvent.Kind.SHARE, 8),
        ):
            VideoEngagementEvent.objects.create(
                post=post,
                viewer=user,
                kind=kind,
                video_second=second,
            )

        analytics_view = PostAnalyticsView()
        analytics_view.object = post
        chart = analytics_view.get_video_insights_chart_data()

        self.assertEqual(
            [point["retention"] for point in chart["points"]],
            [100.0, 100.0, 50.0],
        )
        self.assertEqual(
            [point["engagement_bucket_count"] for point in chart["points"]],
            [0, 1, 2],
        )
        self.assertEqual(
            [point["engagement_intensity"] for point in chart["points"]],
            [0, 50.0, 100.0],
        )
        self.assertEqual(chart["points"][0]["timed_likes"], 1)
        self.assertEqual(chart["points"][-1]["timed_comments"], 1)
        self.assertEqual(chart["points"][-1]["timed_shares"], 2)

    def test_single_timed_event_affects_only_its_engagement_bucket(self):
        user, post = self.create_user_and_post()
        VideoWatchSession.objects.create(
            post=post,
            viewer=user,
            watched_seconds=10,
            video_duration=10,
            watched_percentage=100,
        )
        VideoEngagementEvent.objects.create(
            post=post,
            viewer=user,
            kind=VideoEngagementEvent.Kind.LIKE,
            video_second=2,
        )

        analytics_view = PostAnalyticsView()
        analytics_view.object = post
        chart = analytics_view.get_video_insights_chart_data()

        self.assertEqual(
            [point["engagement_intensity"] for point in chart["points"]],
            [0, 100.0, 0.0],
        )

    @patch("socialmanager.views.generate_post_analysis")
    def test_post_analysis_uses_video_annotations_but_not_retention_data(self, generate_analysis):
        user, post = self.create_user_and_post()
        VideoAnalysis.objects.create(
            post=post,
            source_object_name=post.video_file.name,
            status=VideoAnalysis.Status.SUCCEEDED,
            result={
                "labels": [{"description": "coffee", "confidence": 0.91}],
                "shots": [{"start_seconds": 0, "end_seconds": 3}],
                "shot_count": 1,
                "explicit_content": {"max_likelihood": "VERY_UNLIKELY"},
            },
        )
        generate_analysis.return_value = {
            "sections": [
                {
                    "heading": "Overall performance",
                    "points": ["The annotated coffee subject matches the post."],
                }
            ]
        }
        self.client.force_login(user)

        response = self.client.get(reverse("socialmanager:post_ai_insight", args=[post.pk]))

        self.assertEqual(response.status_code, 200)
        payload = generate_analysis.call_args.args[0]
        self.assertTrue(payload["video_analysis"]["available"])
        self.assertEqual(payload["video_analysis"]["labels"][0]["description"], "coffee")
        self.assertNotIn("video_retention", payload)
        html = response.json()["insight_html"]
        self.assertIn("Overall performance", html)
        self.assertIn("<ul", html)
        self.assertNotIn('{"sections"', html)

    def test_post_pages_do_not_render_standalone_video_analysis(self):
        user, post = self.create_user_and_post()
        VideoWatchSession.objects.create(
            post=post,
            viewer=user,
            watched_seconds=8,
            video_duration=10,
            watched_percentage=80,
        )
        self.client.force_login(user)

        detail = self.client.get(
            reverse("socialmanager:post_detail_legacy", args=[post.pk]),
            follow=True,
        )
        analytics = self.client.get(reverse("socialmanager:post_analytics", args=[post.pk]))

        self.assertNotContains(detail, "Video content analysis")
        self.assertNotContains(detail, "Creator summary")
        self.assertNotContains(analytics, "Video content analysis")
        self.assertContains(analytics, "Audience Engagement & Retention")
        self.assertContains(analytics, "post-retention-ai-insight-panel")
        self.assertContains(analytics, "Retention AI Insight")

    def test_retention_ai_block_is_hidden_without_video_retention_data(self):
        user, video_post = self.create_user_and_post()
        _, image_post = self.create_user_and_post(
            content_format=SocialMediaPost.Format.IMAGE,
        )
        image_post.subscription = video_post.subscription
        image_post.author = user
        image_post.save(update_fields=["subscription", "author"])
        self.client.force_login(user)

        video_analytics = self.client.get(
            reverse("socialmanager:post_analytics", args=[video_post.pk])
        )
        image_analytics = self.client.get(
            reverse("socialmanager:post_analytics", args=[image_post.pk])
        )

        self.assertNotContains(video_analytics, "post-retention-ai-insight-panel")
        self.assertNotContains(image_analytics, "Audience Engagement & Retention")

    @patch("socialmanager.views.generate_video_retention_analysis")
    def test_retention_ai_endpoint_uses_timed_event_types_and_structured_renderer(self, generate):
        user, post = self.create_user_and_post()
        VideoWatchSession.objects.create(
            post=post,
            viewer=user,
            watched_seconds=10,
            video_duration=10,
            watched_percentage=100,
        )
        for kind, second in (
            (VideoEngagementEvent.Kind.LIKE, 2),
            (VideoEngagementEvent.Kind.COMMENT, 5),
            (VideoEngagementEvent.Kind.SHARE, 8),
        ):
            VideoEngagementEvent.objects.create(
                post=post,
                viewer=user,
                kind=kind,
                video_second=second,
            )
        generate.return_value = (
            "```json\n"
            '{"sections":[{"heading":"Retention diagnosis","points":["The main drop-off is after 5 seconds."]}]}'
            "\n```"
        )
        self.client.force_login(user)

        response = self.client.get(
            reverse("socialmanager:post_retention_ai_insight", args=[post.pk])
        )

        self.assertEqual(response.status_code, 200)
        retention_payload = generate.call_args.args[0]
        self.assertEqual(retention_payload["completion_rate"], 100.0)
        final_point = retention_payload["points"][-1]
        self.assertEqual(final_point["timed_likes"], 1)
        self.assertEqual(final_point["timed_comments"], 1)
        self.assertEqual(final_point["timed_shares"], 1)
        html = response.json()["insight_html"]
        self.assertIn("Retention diagnosis", html)
        self.assertIn("<ul", html)
        self.assertNotIn('```', html)
        self.assertNotIn('{"sections"', html)

    @patch("socialmanager.views._validate_video_duration_file", return_value=12)
    @patch("socialmanager.views.generate_video_content_guidance")
    @patch("socialmanager.views.analyze_gcs_video")
    def test_member_analysis_is_persisted_and_reused(self, analyze, generate_guidance, _duration):
        user, post = self.create_user_and_post()
        analyze.return_value = {
            "labels": [{"description": "coffee", "confidence": 0.91, "categories": []}],
            "shots": [{"start_seconds": 0, "end_seconds": 3, "duration_seconds": 3}],
            "shot_count": 1,
            "explicit_content": {"max_likelihood": "VERY_UNLIKELY", "flagged_frame_count": 0, "frames": []},
        }
        generate_guidance.return_value = {
            "summary": "A concise coffee tutorial.",
            "caption_ideas": ["Make better coffee."],
            "hashtags": ["#coffee"],
            "improvements": ["Show the result in the opening frame."],
        }
        self.client.force_login(user)
        url = reverse("socialmanager:post_video_analysis", args=[post.pk])

        first = self.client.post(url)
        second = self.client.post(url)

        self.assertEqual(first.status_code, 200)
        self.assertTrue(first.json()["success"])
        self.assertFalse(first.json()["cached"])
        self.assertTrue(second.json()["cached"])
        analyze.assert_called_once_with(post)
        record = VideoAnalysis.objects.get(post=post)
        self.assertEqual(record.status, VideoAnalysis.Status.SUCCEEDED)
        self.assertEqual(record.creator_guidance["hashtags"], ["#coffee"])

    @patch("socialmanager.views._validate_video_duration_file", return_value=12)
    @patch("socialmanager.views.analyze_gcs_video")
    def test_cloud_failure_is_non_blocking_and_recorded(self, analyze, _duration):
        user, post = self.create_user_and_post()
        analyze.side_effect = RuntimeError("cloud unavailable")
        self.client.force_login(user)

        response = self.client.post(reverse("socialmanager:post_video_analysis", args=[post.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["success"])
        self.assertIn("not affected", response.json()["error"])
        self.assertEqual(VideoAnalysis.objects.get(post=post).status, VideoAnalysis.Status.FAILED)

    @patch("socialmanager.views._validate_video_duration_file", side_effect=VideoDurationError("Please provide a video that is 60 seconds or shorter."))
    @patch("socialmanager.views.analyze_gcs_video")
    def test_overlong_video_analysis_rejects_before_provider(self, analyze, _duration):
        user, post = self.create_user_and_post()
        self.client.force_login(user)

        response = self.client.post(reverse("socialmanager:post_video_analysis", args=[post.pk]))

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["success"])
        self.assertEqual(response.json()["error"], "Please provide a video that is 60 seconds or shorter.")
        analyze.assert_not_called()

    @patch("socialmanager.views.analyze_gcs_video")
    def test_non_member_cannot_start_video_analysis(self, analyze):
        user, post = self.create_user_and_post(active_member=False)
        self.client.force_login(user)

        response = self.client.post(reverse("socialmanager:post_video_analysis", args=[post.pk]))

        self.assertEqual(response.status_code, 403)
        analyze.assert_not_called()


TEST_STATIC_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


class AnnouncementTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="reader", password="password12345")
        self.superuser = User.objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="password12345",
        )
        subscription = SaaSSubscription.objects.create(name="Reader Workspace", owner=self.user)
        SubscriptionMembership.objects.create(
            subscription=subscription,
            user=self.user,
            role=SubscriptionMembership.Role.ADMIN,
        )

    def test_regular_user_cannot_access_announcement_mutation_views(self):
        announcement = Announcement.objects.create(
            title="Reader visible",
            content="Only admins can edit this.",
            author=self.superuser,
        )
        self.client.force_login(self.user)

        urls = [
            reverse("socialmanager:announcement_create"),
            reverse("socialmanager:announcement_update", args=[announcement.pk]),
            reverse("socialmanager:announcement_delete", args=[announcement.pk]),
        ]

        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 403)

        response = self.client.post(
            reverse("socialmanager:announcement_create"),
            {"title": "Blocked", "content": "Nope", "is_active": "on"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Announcement.objects.filter(title="Blocked").exists())

    def test_superuser_can_create_edit_and_delete_announcement(self):
        self.client.force_login(self.superuser)

        response = self.client.post(
            reverse("socialmanager:announcement_create"),
            {"title": "Launch notice", "content": "Welcome to Creana."},
        )

        self.assertEqual(response.status_code, 302)
        announcement = Announcement.objects.get(title="Launch notice")
        self.assertEqual(announcement.author, self.superuser)
        self.assertTrue(announcement.is_active)

        response = self.client.post(
            reverse("socialmanager:announcement_update", args=[announcement.pk]),
            {"title": "Updated notice", "content": "Updated body."},
        )

        self.assertEqual(response.status_code, 302)
        announcement.refresh_from_db()
        self.assertEqual(announcement.title, "Updated notice")
        self.assertTrue(announcement.is_active)

        response = self.client.post(reverse("socialmanager:announcement_delete", args=[announcement.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Announcement.objects.filter(pk=announcement.pk).exists())

    def test_announcement_forms_hide_active_and_edit_preserves_current_value(self):
        announcement = Announcement.objects.create(
            title="Inactive notice",
            content="Hidden until reactivated in admin.",
            author=self.superuser,
            is_active=False,
        )
        self.client.force_login(self.superuser)

        create_response = self.client.get(reverse("socialmanager:announcement_create"))
        edit_response = self.client.get(reverse("socialmanager:announcement_update", args=[announcement.pk]))

        self.assertNotContains(create_response, 'name="is_active"')
        self.assertNotContains(edit_response, 'name="is_active"')

        response = self.client.post(
            reverse("socialmanager:announcement_update", args=[announcement.pk]),
            {"title": "Still inactive", "content": "Updated without changing status."},
        )

        self.assertEqual(response.status_code, 302)
        announcement.refresh_from_db()
        self.assertFalse(announcement.is_active)

    def test_feed_displays_active_announcements_under_filter_card(self):
        announcement = Announcement.objects.create(
            title="Active announcement",
            content="<strong>Safe body</strong>",
            author=self.superuser,
        )
        Announcement.objects.create(
            title="Inactive announcement",
            content="Hidden body",
            author=self.superuser,
            is_active=False,
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse("socialmanager:post_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Filter the feed")
        self.assertContains(response, "Announcements")
        self.assertContains(response, "Active announcement")
        self.assertContains(response, "announcement-item__link")
        self.assertContains(response, "announcement-item__pin")
        self.assertContains(response, "data-announcement-title")
        self.assertContains(response, "Safe body")
        self.assertNotContains(response, "announcement-modal__action")
        self.assertNotContains(response, "data-announcement-edit-url")
        self.assertNotContains(response, "data-announcement-delete-url")
        self.assertNotContains(response, reverse("socialmanager:announcement_create"))
        self.assertNotContains(response, reverse("socialmanager:announcement_update", args=[announcement.pk]))
        self.assertNotContains(response, reverse("socialmanager:announcement_delete", args=[announcement.pk]))
        self.assertNotContains(response, "Inactive announcement")

    def test_superuser_feed_displays_announcement_actions(self):
        announcement = Announcement.objects.create(
            title="Admin announcement",
            content="Admin body",
            author=self.superuser,
        )
        self.client.force_login(self.superuser)

        response = self.client.get(reverse("socialmanager:post_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("socialmanager:announcement_create"))
        self.assertContains(response, reverse("socialmanager:announcement_update", args=[announcement.pk]))
        self.assertContains(response, reverse("socialmanager:announcement_delete", args=[announcement.pk]))
        self.assertContains(response, "announcement-modal__action")
        self.assertContains(response, "data-announcement-edit-url")
        self.assertContains(response, "data-announcement-delete-url")
        self.assertNotContains(response, "announcement-item__action")


@override_settings(
    SECURE_SSL_REDIRECT=False,
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
)
class HiddenUserTests(TestCase):
    def setUp(self):
        self.viewer = User.objects.create_user(username="viewer", password="password12345")
        self.hidden_author = User.objects.create_user(username="hiddenauthor", password="password12345")
        self.visible_author = User.objects.create_user(username="visibleauthor", password="password12345")
        self.subscription = SaaSSubscription.objects.create(name="Viewer Workspace", owner=self.viewer)
        for user in (self.viewer, self.hidden_author, self.visible_author):
            SubscriptionMembership.objects.create(
                subscription=self.subscription,
                user=user,
                role=SubscriptionMembership.Role.STANDARD,
            )
        self.hidden_post = SocialMediaPost.objects.create(
            subscription=self.subscription,
            author=self.hidden_author,
            title="Hidden author post",
            platform=SocialMediaPost.Platform.INSTAGRAM,
            status=SocialMediaPost.Status.PUBLISHED,
            visibility=SocialMediaPost.Visibility.PUBLIC,
            caption="This should leave the feed only.",
        )
        self.visible_post = SocialMediaPost.objects.create(
            subscription=self.subscription,
            author=self.visible_author,
            title="Visible author post",
            platform=SocialMediaPost.Platform.INSTAGRAM,
            status=SocialMediaPost.Status.PUBLISHED,
            visibility=SocialMediaPost.Visibility.PUBLIC,
            caption="This should stay in the feed.",
        )

    def test_hidden_user_cannot_target_owner(self):
        hidden = HiddenUser(owner=self.viewer, hidden_user=self.viewer)

        with self.assertRaises(ValidationError):
            hidden.full_clean()

    def test_toggle_hidden_user_requires_post_and_uses_current_user(self):
        self.client.force_login(self.viewer)
        url = reverse("socialmanager:profile_hidden_user_toggle", args=[self.hidden_author.pk])

        get_response = self.client.get(url)
        self.assertEqual(get_response.status_code, 405)
        self.assertFalse(HiddenUser.objects.exists())

        post_response = self.client.post(url)
        self.assertRedirects(post_response, reverse("socialmanager:public_profile", args=[self.hidden_author.pk]))
        self.assertTrue(HiddenUser.objects.filter(owner=self.viewer, hidden_user=self.hidden_author).exists())

        second_response = self.client.post(url)
        self.assertRedirects(second_response, reverse("socialmanager:public_profile", args=[self.hidden_author.pk]))
        self.assertFalse(HiddenUser.objects.filter(owner=self.viewer, hidden_user=self.hidden_author).exists())

        username_response = self.client.post(
            reverse("socialmanager:profile_hidden_user_toggle_username", args=[self.hidden_author.username])
        )
        self.assertRedirects(username_response, reverse("socialmanager:public_profile", args=[self.hidden_author.pk]))
        self.assertTrue(HiddenUser.objects.filter(owner=self.viewer, hidden_user=self.hidden_author).exists())

    def test_hiding_user_only_excludes_their_posts_from_feed(self):
        HiddenUser.objects.create(owner=self.viewer, hidden_user=self.hidden_author)
        self.client.force_login(self.viewer)

        feed_response = self.client.get(reverse("socialmanager:post_list"))
        feed_posts = list(feed_response.context["object_list"])
        self.assertNotIn(self.hidden_post, feed_posts)
        self.assertIn(self.visible_post, feed_posts)

        profile_response = self.client.get(reverse("socialmanager:public_profile", args=[self.hidden_author.pk]))
        self.assertIn(self.hidden_post, list(profile_response.context["public_posts"]))
        self.assertTrue(profile_response.context["is_hidden_by_current_user"])
        self.assertContains(profile_response, "Unhide this user")

        detail_response = self.client.get(reverse("socialmanager:post_detail", args=[self.hidden_post.pk, self.hidden_post.slug]))
        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(detail_response, self.hidden_post.title)

    def test_profile_hide_action_not_shown_on_own_profile(self):
        self.client.force_login(self.viewer)

        response = self.client.get(reverse("socialmanager:public_profile", args=[self.viewer.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["is_own_profile"])
        self.assertNotContains(response, "Hide this user from my feed")
        self.assertNotContains(response, "profile_hidden_user_toggle")

    def test_username_search_finds_hidden_user_by_exact_username_case_insensitive(self):
        HiddenUser.objects.create(owner=self.viewer, hidden_user=self.hidden_author)
        self.client.force_login(self.viewer)

        response = self.client.post(
            reverse("socialmanager:profile_username_search"),
            {"username": "HIDDENAUTHOR", "next": reverse("socialmanager:post_list")},
        )

        self.assertRedirects(response, reverse("socialmanager:public_profile", args=[self.hidden_author.pk]))

    def test_username_search_does_not_search_email_and_redirects_back_on_failure(self):
        self.hidden_author.email = "hidden@example.com"
        self.hidden_author.save(update_fields=["email"])
        self.client.force_login(self.viewer)

        response = self.client.post(
            reverse("socialmanager:profile_username_search"),
            {"username": "hidden@example.com", "next": reverse("socialmanager:post_list")},
            follow=True,
        )

        self.assertRedirects(response, reverse("socialmanager:post_list"))
        self.assertContains(response, "No user found with that username.")


class AIImagePipelineTests(TestCase):
    class RemoteStorageImage(BytesIO):
        name = "social_posts/stored-image.jpg"
        content_type = "image/jpeg"

        @property
        def path(self):
            raise NotImplementedError("Remote storage has no local path")

        def open(self, mode="rb"):
            self.seek(0)
            return self

    def remote_image(self):
        return self.RemoteStorageImage(b"stored-gcs-image-bytes")

    def image_facts_response(self):
        return json.dumps(
            {
                "main_subjects": ["two fox characters"],
                "actions": [],
                "objects": ["glowing light bulb", "open box"],
                "setting": "simple indoor illustration scene",
                "visual_style": "playful digital illustration",
                "visible_text": [],
                "mood": ["curious", "playful"],
                "uncertain_details": [],
            }
        )

    def test_shared_image_loader_reads_storage_backed_file_without_local_path(self):
        image_input = ai_assistant._first_supported_image_input(self.remote_image())

        self.assertIsNotNone(image_input)
        self.assertEqual(image_input.data, b"stored-gcs-image-bytes")
        self.assertEqual(image_input.mime_type, "image/jpeg")

    @override_settings(GEMINI_API_KEY="", GEMINI_ENABLED=True)
    @patch("socialmanager.services.ai_assistant.logger")
    def test_field_feedback_does_not_log_api_key_diagnostics(self, logger):
        with self.assertRaisesRegex(ValueError, "GEMINI_API_KEY is missing"):
            ai_assistant.generate_post_field_feedback(
                {
                    "feedback_type": "title",
                    "title": "Missing key",
                    "ai_language_display": "Traditional Chinese",
                    "ai_tone": "Friendly",
                    "ai_hashtag_count": 3,
                }
            )

        logged_values = " ".join(str(call) for call in logger.mock_calls)
        self.assertNotIn("api_key", logged_values.lower())
        self.assertNotIn("Traditional Chinese", logged_values)
        self.assertNotIn("Friendly", logged_values)

    @override_settings(GEMINI_API_KEY="test-key", GEMINI_ENABLED=True)
    @patch("socialmanager.services.ai_assistant.genai.Client")
    def test_gemini_client_initializes_and_generate_content_is_reached(self, client_class):
        client = client_class.return_value
        client.models.generate_content.return_value = SimpleNamespace(
            text='{"ok": true}',
            candidates=[],
        )

        response_text = ai_assistant._gemini_generate_text(
            "System prompt",
            "User prompt",
            diagnostic_context={
                "language": "Traditional Chinese",
                "tone": "Friendly",
                "hashtag_count": 3,
            },
        )

        client_class.assert_called_once_with(api_key="test-key")
        client.models.generate_content.assert_called_once()
        self.assertEqual(response_text, '{"ok": true}')

    @override_settings(GEMINI_API_KEY="test-key")
    @patch("socialmanager.services.ai_assistant._gemini_generate_text")
    def test_caption_generation_passes_preferences_and_storage_image_to_gemini(self, generate):
        generate.side_effect = [
            self.image_facts_response(),
            json.dumps({
                "caption": "A focused caption with a clear question.",
                "hashtags": ["#one", "#two", "#three"],
            }),
        ]

        ai_assistant.generate_caption_and_hashtags(
            "Stored image topic",
            "Instagram",
            "Friendly",
            language="traditional_chinese",
            hashtag_count=3,
            image_file=self.remote_image(),
        )

        user_prompt = generate.call_args_list[1].args[1]
        image_input = generate.call_args_list[0].kwargs["image_input"]
        self.assertIn('"language": "Traditional Chinese"', user_prompt)
        self.assertIn('"tone": "Friendly"', user_prompt)
        self.assertIn('"hashtag_count": 3', user_prompt)
        self.assertEqual(image_input.data, b"stored-gcs-image-bytes")
        self.assertIsNone(generate.call_args_list[1].kwargs["image_input"])

    @override_settings(GEMINI_API_KEY="test-key")
    @patch("socialmanager.services.ai_assistant._gemini_generate_text")
    def test_field_feedback_uses_the_same_storage_image_loader(self, generate):
        generate.side_effect = [
            self.image_facts_response(),
            json.dumps(
                {
                    "title": {"text": "Stored Image Story", "feedback": "This title is easy to scan."},
                    "caption": {"text": "Caption", "feedback": "Feedback"},
                    "hashtags": {"text": "#one #two", "feedback": "Feedback"},
                }
            ),
        ]

        ai_assistant.generate_post_field_feedback(
            {
                "feedback_type": "title",
                "title": "Stored image",
                "platform": "Instagram",
                "ai_language": "english",
                "ai_language_display": "English",
                "ai_tone": "Casual",
                "ai_hashtag_count": 2,
                "image_file": self.remote_image(),
            }
        )

        self.assertEqual(generate.call_count, 2)
        image_input = generate.call_args_list[0].kwargs["image_input"]
        user_prompt = generate.call_args.args[1]
        self.assertEqual(image_input.data, b"stored-gcs-image-bytes")
        self.assertIn('"ai_language": "English"', user_prompt)
        self.assertIn('"ai_tone": "Casual"', user_prompt)
        self.assertIn('"ai_hashtag_count": 2', user_prompt)
        self.assertIn('"image_facts"', user_prompt)
        self.assertIn('"bio_tone_only"', user_prompt)

    @override_settings(GEMINI_API_KEY="test-key")
    @patch("socialmanager.services.ai_assistant._gemini_generate_text")
    def test_image_grounding_blocks_bio_only_magic_tutorial_claims(self, generate):
        generate.side_effect = [
            self.image_facts_response(),
            json.dumps(
                {
                    "title": {"text": "Magic Idea Sparks", "feedback": "This title promotes upcoming magic content."},
                    "caption": {"text": "An exciting new concept is taking shape for our upcoming magic tutorial!", "feedback": "This caption highlights an upcoming tutorial."},
                    "hashtags": {"text": "#MagicTutorial #LearnMagic", "feedback": "These tags match magic lessons."},
                }
            ),
        ]

        result = ai_assistant.generate_post_field_feedback(
            {
                "feedback_type": "caption",
                "post_type": "photo_post",
                "platform": "Instagram",
                "ai_language": "english",
                "ai_language_display": "English",
                "ai_tone": "Casual",
                "ai_hashtag_count": 3,
                "image_file": self.remote_image(),
                "image_source": "uploaded",
                "creator_context": {"bio": "Magician creating tutorials and teaching illusions."},
            }
        )

        self.assertTrue(result.used_image_input)
        self.assertEqual(result.grounding_mode, "image_primary")
        self.assertNotIn("magic", result.suggestion.lower())
        self.assertNotIn("tutorial", result.suggestion.lower())
        self.assertIn("fox", result.suggestion.lower())

    @override_settings(GEMINI_API_KEY="test-key")
    @patch("socialmanager.services.ai_assistant._gemini_generate_text")
    def test_current_input_can_explicitly_override_image_grounding(self, generate):
        generate.side_effect = [
            self.image_facts_response(),
            json.dumps(
                {
                    "title": {"text": "Magic Idea Sparks", "feedback": "This title uses the explicit tutorial context."},
                    "caption": {"text": "A preview of the next magic tutorial.", "feedback": "This caption uses the current input."},
                    "hashtags": {"text": "#MagicTutorial #FoxArt", "feedback": "These tags combine the current input and image."},
                }
            ),
        ]

        result = ai_assistant.generate_post_field_feedback(
            {
                "feedback_type": "caption",
                "post_type": "photo_post",
                "current_value": "This is a preview of my next magic tutorial.",
                "platform": "Instagram",
                "ai_language": "english",
                "ai_language_display": "English",
                "ai_tone": "Casual",
                "ai_hashtag_count": 2,
                "image_file": self.remote_image(),
                "image_source": "uploaded",
                "creator_context": {"bio": "Magician creating tutorials and teaching illusions."},
            }
        )

        self.assertIn("magic tutorial", result.suggestion.lower())

    @override_settings(GEMINI_API_KEY="test-key")
    @patch("socialmanager.services.ai_assistant._gemini_generate_text")
    def test_image_analysis_failure_stops_final_synthesis(self, generate):
        generate.return_value = json.dumps({"main_subjects": [], "objects": [], "mood": []})

        with self.assertRaises(ai_assistant.ImageInputError) as context:
            ai_assistant.generate_post_field_feedback(
                {
                    "feedback_type": "title",
                    "post_type": "photo_post",
                    "platform": "Instagram",
                    "ai_language": "english",
                    "ai_language_display": "English",
                    "ai_tone": "Casual",
                    "ai_hashtag_count": 2,
                    "image_file": self.remote_image(),
                    "image_source": "uploaded",
                }
            )

        self.assertEqual(context.exception.code, "image_analysis_failed")
        self.assertEqual(generate.call_count, 1)

    def assert_valid_hashtag_fallback(self, image_facts, hashtag_count=3):
        result = ai_assistant._image_grounded_fallback(
            "hashtags",
            {
                "feedback_type": "hashtags",
                "ai_language": "english",
                "ai_language_display": "English",
            },
            image_facts,
            hashtag_count,
        )
        tags = ai_assistant.split_hashtags(result.suggestion)
        self.assertEqual(len(tags), hashtag_count)
        self.assertEqual(len({tag.lower() for tag in tags}), len(tags))
        self.assertTrue(all(tag and tag != "#" for tag in tags))
        self.assertTrue(all(tag.startswith("#") for tag in tags))
        return tags

    def test_image_grounded_hashtag_fallback_handles_mood_with_value(self):
        tags = self.assert_valid_hashtag_fallback(
            {
                "main_subjects": ["fox character"],
                "objects": ["light bulb"],
                "setting": "",
                "visual_style": "digital illustration",
                "mood": ["playful"],
            },
            hashtag_count=4,
        )

        self.assertIn("#Playful", tags)

    def test_image_grounded_hashtag_fallback_handles_empty_mood(self):
        tags = self.assert_valid_hashtag_fallback(
            {
                "main_subjects": ["fox character"],
                "objects": ["light bulb"],
                "setting": "",
                "visual_style": "digital illustration",
                "mood": [],
            },
            hashtag_count=4,
        )

        self.assertNotIn("#", tags)

    def test_image_grounded_hashtag_fallback_handles_objects_without_subjects(self):
        tags = self.assert_valid_hashtag_fallback(
            {
                "main_subjects": [],
                "objects": ["open box", "glowing bulb"],
                "setting": "",
                "visual_style": "",
                "mood": [],
            },
            hashtag_count=3,
        )

        self.assertIn("#OpenBox", tags)

    def test_image_grounded_hashtag_fallback_handles_only_setting(self):
        self.assert_valid_hashtag_fallback(
            {
                "main_subjects": [],
                "objects": [],
                "setting": "studio desk",
                "visual_style": "",
                "mood": [],
            },
            hashtag_count=3,
        )

    def test_image_grounded_hashtag_fallback_handles_only_visual_style(self):
        tags = self.assert_valid_hashtag_fallback(
            {
                "main_subjects": [],
                "objects": [],
                "setting": "",
                "visual_style": "watercolor sketch",
                "mood": [],
            },
            hashtag_count=3,
        )

        self.assertIn("#WatercolorSketch", tags)

    def garden_facts(self):
        return {
            "main_subjects": ["garden", "flowers"],
            "actions": ["walking along a path"],
            "objects": ["flowers", "landscaped path"],
            "setting": "garden",
            "visual_style": "outdoor photography",
            "visible_text": [],
            "mood": ["calm"],
            "uncertain_details": [],
        }

    def contaminated_image_payload(self, language="english", current_value=""):
        return {
            "feedback_type": "caption",
            "post_type": "photo_post",
            "platform": "Instagram",
            "current_value": current_value,
            "ai_language": language,
            "ai_language_display": "Traditional Chinese" if language == "traditional_chinese" else "English",
            "creator_context": {
                "bio": "Creana Official Test magic tutorial product launch",
                "display_name": "Creana Official Test",
                "username": "creana_test",
                "recent_post_titles": ["Professional audience product launch"],
            },
            "previous_post_titles": ["Magic tutorial launch"],
            "previous_post_captions": ["Creana Official Test for a professional audience"],
            "previous_post_hashtags": ["#MagicTutorial #ProductLaunch"],
        }

    def test_image_aware_sanitizer_uses_garden_fallback_not_profile_context(self):
        payload = self.contaminated_image_payload()
        for feedback_type, suggestion in (
            ("title", "Creana Official Test"),
            ("caption", "Creana Official Test with a clearer hook."),
            ("hashtags", "#CreanaOfficialTest"),
        ):
            payload["feedback_type"] = feedback_type
            result = ai_assistant._sanitize_field_feedback(
                feedback_type,
                suggestion,
                "",
                payload,
                3,
                image_facts=self.garden_facts(),
            )
            combined = f"{result.suggestion} {result.explanation}".lower()
            self.assertTrue(any(term in combined for term in ("garden", "flower", "path")))
            for forbidden in ("creana official test", "magic tutorial", "launch", "professional audience"):
                self.assertNotIn(forbidden, combined)
            for internal in ("unsupported profile context", "grounding mode", "internal weighting"):
                self.assertNotIn(internal, combined)

    def test_text_only_sanitizer_can_still_use_text_fallback(self):
        payload = self.contaminated_image_payload()
        result = ai_assistant._sanitize_field_feedback("caption", "", "", payload, 3)
        self.assertIn("Creana", result.suggestion)

    @override_settings(GEMINI_API_KEY="test-key")
    @patch("socialmanager.services.ai_assistant._gemini_generate_text")
    def test_stage_two_uses_normalized_facts_without_raw_image(self, generate):
        generate.side_effect = [
            json.dumps(self.garden_facts()),
            json.dumps({
                "title": {"text": "Garden in Bloom", "feedback": "This title highlights the visible garden."},
                "caption": {"text": "Flowers line the garden path.\nWhich detail stands out most?", "feedback": "This caption highlights the flowers and path."},
                "hashtags": {"text": "#Garden #Flowers #GardenPath", "feedback": "These tags reflect the visible scene."},
            }),
        ]
        payload = self.contaminated_image_payload()
        payload.update({"image_file": self.remote_image(), "image_source": "uploaded"})

        result = ai_assistant.generate_post_field_feedback(payload)

        self.assertTrue(result.used_image_input)
        self.assertIsNotNone(generate.call_args_list[0].kwargs["image_input"])
        self.assertIsNone(generate.call_args_list[1].kwargs["image_input"])
        self.assertIn('"image_facts"', generate.call_args_list[1].args[1])

    def test_traditional_chinese_image_fallback_uses_visible_facts(self):
        payload = self.contaminated_image_payload(language="traditional_chinese")
        result = ai_assistant._sanitize_field_feedback(
            "caption", "Creana Official Test", "", payload, 3, image_facts=self.garden_facts()
        )
        self.assertTrue(any(term in result.suggestion.lower() for term in ("garden", "flower", "path")))
        self.assertNotIn("Creana Official Test", result.suggestion)

    def test_explicit_current_input_can_support_launch_topic(self):
        payload = self.contaminated_image_payload(current_value="Garden product launch")
        result = ai_assistant._sanitize_field_feedback(
            "title", "Garden Product Launch", "This title reflects the garden launch.", payload, 3,
            image_facts=self.garden_facts(),
        )
        self.assertEqual(result.suggestion, "Garden Product Launch")

    @override_settings(
        GEMINI_API_KEY="test-key",
        GEMINI_VIDEO_MAX_BYTES=50 * 1024 * 1024,
        GEMINI_VIDEO_MAX_SECONDS=60,
    )
    @patch("socialmanager.services.ai_assistant._gemini_generate_text")
    @patch("socialmanager.services.ai_assistant._gemini_generate_from_video")
    @patch("socialmanager.services.ai_assistant._probe_video_duration", return_value=12.0)
    def test_video_feedback_sends_real_video_to_gemini(self, _probe, generate_video, generate_text):
        generate_video.return_value = json.dumps({
            "title": {"text": "Coffee in Motion", "feedback": "This title reflects the visible sequence."},
            "caption": {"text": "A short coffee process.", "feedback": "This caption is grounded in the clip."},
            "hashtags": {"text": "#coffee #process", "feedback": "These tags match the clip."},
        })
        video = SimpleUploadedFile("clip.mp4", b"video-bytes", content_type="video/mp4")

        result = ai_assistant.generate_post_field_feedback({
            "feedback_type": "title",
            "post_type": "video",
            "platform": "Instagram",
            "ai_language": "english",
            "ai_language_display": "English",
            "ai_hashtag_count": 2,
            "video_file": video,
        })

        self.assertTrue(result.used_video_input)
        self.assertEqual(result.fallback_reason, "")
        self.assertEqual(generate_video.call_args.args[2].data, b"video-bytes")
        generate_text.assert_not_called()

    @override_settings(GEMINI_API_KEY="test-key")
    @patch("socialmanager.services.ai_assistant._gemini_generate_text")
    def test_video_feedback_without_file_uses_explicit_text_fallback(self, generate_text):
        generate_text.return_value = json.dumps({
            "title": {"text": "Launch Notes", "feedback": "This title uses the supplied text context."},
            "caption": {"text": "Launch caption", "feedback": "This caption uses supplied context."},
            "hashtags": {"text": "#launch #update", "feedback": "These tags use supplied context."},
        })

        result = ai_assistant.generate_post_field_feedback({
            "feedback_type": "caption",
            "post_type": "video",
            "title": "Launch notes",
            "caption": "Draft launch caption",
            "ai_language": "english",
            "ai_language_display": "English",
            "ai_hashtag_count": 2,
        })

        self.assertFalse(result.used_video_input)
        self.assertEqual(result.fallback_reason, "video_file_missing")
        self.assertIn("Video frames were not read", generate_text.call_args.args[1])

    @override_settings(GEMINI_VIDEO_MAX_BYTES=100, GEMINI_VIDEO_MAX_SECONDS=60)
    @patch("socialmanager.services.ai_assistant._probe_video_duration", return_value=61.0)
    def test_video_loader_rejects_long_video_before_gemini(self, _probe):
        video = SimpleUploadedFile("long.mp4", b"video", content_type="video/mp4")

        video_input, reason = ai_assistant._load_video_for_ai(video)

        self.assertIsNone(video_input)
        self.assertEqual(reason, "video_too_long")

    @override_settings(GEMINI_VIDEO_MAX_BYTES=52_428_800, GEMINI_VIDEO_MAX_SECONDS=60)
    def test_video_loader_rejects_video_over_50_mb_before_gemini(self):
        video = SimpleNamespace(
            name="too-large.mp4",
            content_type="video/mp4",
            size=52_428_801,
        )

        video_input, reason = ai_assistant._load_video_for_ai(video)

        self.assertIsNone(video_input)
        self.assertEqual(reason, "video_too_large")

    @override_settings(GEMINI_API_KEY="test-key")
    @patch("socialmanager.services.ai_assistant._gemini_generate_from_video")
    @patch("socialmanager.services.ai_assistant._probe_video_duration", return_value=10.0)
    def test_video_title_and_caption_limits_remain_enforced(self, _probe, generate_video):
        generate_video.return_value = json.dumps({
            "title": {"text": "T" * 80, "feedback": "A concise title."},
            "caption": {"text": "C" * 400, "feedback": "A concise caption."},
            "hashtags": {"text": "#one #two", "feedback": "Relevant tags."},
        })
        video = SimpleUploadedFile("clip.mp4", b"video", content_type="video/mp4")

        title_result = ai_assistant.generate_post_field_feedback({
            "feedback_type": "title", "post_type": "video", "video_file": video,
            "ai_language": "english", "ai_language_display": "English", "ai_hashtag_count": 2,
        })
        video = SimpleUploadedFile("clip.mp4", b"video", content_type="video/mp4")
        caption_result = ai_assistant.generate_post_field_feedback({
            "feedback_type": "caption", "post_type": "video", "video_file": video,
            "ai_language": "english", "ai_language_display": "English", "ai_hashtag_count": 2,
        })

        self.assertLessEqual(len(title_result.suggestion), POST_TITLE_MAX_LENGTH)
        self.assertLessEqual(len(caption_result.suggestion), POST_CAPTION_MAX_LENGTH)


class SubscriptionPostCreationTests(TestCase):
    def post_data(self, title="Regression post"):
        return {
            "title": title,
            "platform": SocialMediaPost.Platform.INSTAGRAM,
            "content_format": SocialMediaPost.Format.ARTICLE,
            "status": SocialMediaPost.Status.DRAFT,
            "visibility": SocialMediaPost.Visibility.PUBLIC,
            "caption": "A post body",
            "article_caption": "",
            "hashtags": "",
            "scheduled_for": "",
        }

    def create_workspace_user(self, username="video-uploader", *, active_member=False):
        user = User.objects.create_user(username=username, password="password12345")
        subscription = SaaSSubscription.objects.create(name=f"{username} Workspace", owner=user)
        SubscriptionMembership.objects.create(
            subscription=subscription,
            user=user,
            is_active_member=active_member,
        )
        return user, subscription

    def uploaded_jpeg(self, name="foxes.jpg"):
        buffer = BytesIO()
        Image.new("RGB", (1, 1), color=(80, 140, 220)).save(buffer, format="JPEG")
        return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/jpeg")

    def test_primary_image_url_uses_matching_single_image_thumbnail(self):
        user, subscription = self.create_workspace_user("single-image")
        post = SocialMediaPost.objects.create(
            subscription=subscription,
            author=user,
            title="Single image",
            platform=SocialMediaPost.Platform.INSTAGRAM,
            content_format=SocialMediaPost.Format.IMAGE,
            status=SocialMediaPost.Status.PUBLISHED,
            image="social_posts/original.jpg",
        )
        PostImage.objects.create(
            post=post,
            image="social_posts/original.jpg",
            thumbnail="social_posts/thumbnails/original-thumb.webp",
            order=0,
        )

        self.assertIn("social_posts/thumbnails/original-thumb.webp", post.primary_image_url)
        self.assertIn("social_posts/original.jpg", post.primary_original_image_url)

    def test_primary_image_url_uses_thumbnail_for_post_image_saved_under_avatar_path(self):
        user, subscription = self.create_workspace_user("avatar-path-image")
        post = SocialMediaPost.objects.create(
            subscription=subscription,
            author=user,
            title="Avatar path image",
            platform=SocialMediaPost.Platform.INSTAGRAM,
            content_format=SocialMediaPost.Format.IMAGE,
            status=SocialMediaPost.Status.PUBLISHED,
            image="profile_avatars/IMG_9869.jpeg",
        )
        PostImage.objects.create(
            post=post,
            image="profile_avatars/IMG_9869.jpeg",
            thumbnail="social_posts/thumbnails/IMG_9869-thumb.webp",
            order=0,
        )

        self.assertIn("social_posts/thumbnails/IMG_9869-thumb.webp", post.primary_image_url)
        self.assertIn("profile_avatars/IMG_9869.jpeg", post.primary_original_image_url)

    def test_primary_image_url_uses_first_thumbnail_when_exact_image_match_fails(self):
        user, subscription = self.create_workspace_user("single-image-fallback")
        post = SocialMediaPost.objects.create(
            subscription=subscription,
            author=user,
            title="Single image fallback",
            platform=SocialMediaPost.Platform.INSTAGRAM,
            content_format=SocialMediaPost.Format.IMAGE,
            status=SocialMediaPost.Status.PUBLISHED,
            image="social_posts/image-79.png",
        )
        PostImage.objects.create(
            post=post,
            image="social_posts/different-related-image.png",
            thumbnail="social_posts/thumbnails/image-79-28.webp",
            order=0,
        )

        self.assertIn("social_posts/thumbnails/image-79-28.webp", post.primary_image_url)
        self.assertIn("social_posts/image-79.png", post.primary_original_image_url)

    def test_primary_image_url_uses_first_carousel_thumbnail(self):
        user, subscription = self.create_workspace_user("carousel-image")
        post = SocialMediaPost.objects.create(
            subscription=subscription,
            author=user,
            title="Carousel",
            platform=SocialMediaPost.Platform.INSTAGRAM,
            content_format=SocialMediaPost.Format.CAROUSEL,
            status=SocialMediaPost.Status.PUBLISHED,
        )
        PostImage.objects.create(
            post=post,
            image="social_posts/carousel-first.jpg",
            thumbnail="social_posts/thumbnails/carousel-first-thumb.webp",
            order=0,
        )
        PostImage.objects.create(
            post=post,
            image="social_posts/carousel-second.jpg",
            thumbnail="social_posts/thumbnails/carousel-second-thumb.webp",
            order=1,
        )

        self.assertIn("social_posts/thumbnails/carousel-first-thumb.webp", post.primary_image_url)
        self.assertIn("social_posts/carousel-first.jpg", post.primary_original_image_url)

    def test_primary_image_url_uses_first_available_carousel_thumbnail(self):
        user, subscription = self.create_workspace_user("carousel-later-thumbnail")
        post = SocialMediaPost.objects.create(
            subscription=subscription,
            author=user,
            title="Carousel later thumbnail",
            platform=SocialMediaPost.Platform.INSTAGRAM,
            content_format=SocialMediaPost.Format.CAROUSEL,
            status=SocialMediaPost.Status.PUBLISHED,
        )
        PostImage.objects.bulk_create(
            [
                PostImage(
                    post=post,
                    image="social_posts/carousel-first.jpg",
                    order=0,
                ),
                PostImage(
                    post=post,
                    image="social_posts/carousel-second.jpg",
                    thumbnail="social_posts/thumbnails/carousel-second-thumb.webp",
                    order=1,
                ),
            ]
        )

        self.assertIn("social_posts/thumbnails/carousel-second-thumb.webp", post.primary_image_url)
        self.assertIn("social_posts/carousel-first.jpg", post.primary_original_image_url)

    def test_primary_image_url_falls_back_to_original_when_thumbnail_missing(self):
        user, subscription = self.create_workspace_user("fallback-image")
        post = SocialMediaPost.objects.create(
            subscription=subscription,
            author=user,
            title="Fallback image",
            platform=SocialMediaPost.Platform.INSTAGRAM,
            content_format=SocialMediaPost.Format.IMAGE,
            status=SocialMediaPost.Status.PUBLISHED,
            image="social_posts/fallback-original.jpg",
        )
        PostImage.objects.bulk_create(
            [
                PostImage(
                    post=post,
                    image="social_posts/fallback-original.jpg",
                    order=0,
                )
            ]
        )

        self.assertIn("social_posts/fallback-original.jpg", post.primary_image_url)

    def test_video_thumbnail_url_never_falls_back_to_original_video(self):
        user, subscription = self.create_workspace_user("video-thumbnail-url")
        post = SocialMediaPost.objects.create(
            subscription=subscription,
            author=user,
            title="Video thumbnail URL",
            platform=SocialMediaPost.Platform.INSTAGRAM,
            content_format=SocialMediaPost.Format.VIDEO,
            status=SocialMediaPost.Status.PUBLISHED,
            video_file="social_videos/original-video.mp4",
        )

        self.assertEqual(post.video_thumbnail_url, "")
        self.assertEqual(post.cached_video_thumbnail_url, "")

        post.video_thumbnail = "post_video_thumbnails/video-thumbnail.jpg"
        post.cached_video_thumbnail_url = post.video_thumbnail_url

        self.assertIn("post_video_thumbnails/video-thumbnail.jpg", post.cached_video_thumbnail_url)
        self.assertNotIn("social_videos/original-video.mp4", post.cached_video_thumbnail_url)

    def test_video_thumbnail_generation_logs_missing_ffmpeg_without_raising(self):
        from .services.video_thumbnail import generate_video_thumbnail

        user, subscription = self.create_workspace_user("video-thumbnail-logging")
        post = SocialMediaPost.objects.create(
            subscription=subscription,
            author=user,
            title="Video thumbnail logging",
            platform=SocialMediaPost.Platform.INSTAGRAM,
            content_format=SocialMediaPost.Format.VIDEO,
            status=SocialMediaPost.Status.DRAFT,
            video_file="social_videos/logged-video.mp4",
        )

        with patch("socialmanager.services.video_thumbnail.get_ffmpeg_path", return_value=None):
            with self.assertLogs("socialmanager.services.video_thumbnail", level="WARNING") as logs:
                result = generate_video_thumbnail(post)

        self.assertFalse(result)
        log_output = "\n".join(logs.output)
        self.assertIn(f"post_id={post.pk}", log_output)
        self.assertIn("social_videos/logged-video.mp4", log_output)
        self.assertIn("ffmpeg executable is unavailable", log_output)

    def test_video_thumbnail_generation_uses_one_second_frame(self):
        from .services.video_thumbnail import generate_video_thumbnail

        class ThumbnailField:
            def __init__(self):
                self.saved_name = ""

            def __bool__(self):
                return False

            def save(self, name, content, save=False):
                self.saved_name = name

        thumbnail = ThumbnailField()
        post = SimpleNamespace(
            pk=41,
            Format=SocialMediaPost.Format,
            content_format=SocialMediaPost.Format.VIDEO,
            video_file=SimpleNamespace(name="social_videos/one-second.mp4"),
            video_thumbnail=thumbnail,
            save=lambda **kwargs: None,
        )
        timestamps = []

        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "source.mp4"
            source_path.write_bytes(b"video")

            def extract_frame(ffmpeg_path, video_path, output_path, timestamp):
                timestamps.append(timestamp)
                output_path.write_bytes(b"thumbnail")
                return True

            with patch("socialmanager.services.video_thumbnail.get_ffmpeg_path", return_value="ffmpeg"):
                with patch("socialmanager.services.video_thumbnail._video_source_path", return_value=source_path):
                    with patch("socialmanager.services.video_thumbnail._run_ffmpeg", side_effect=extract_frame):
                        result = generate_video_thumbnail(post)

        self.assertTrue(result)
        self.assertEqual(timestamps, ["00:00:01"])
        self.assertEqual(thumbnail.saved_name, "post-41-thumbnail.jpg")

    def test_video_thumbnail_generation_falls_back_to_zero_for_short_video(self):
        from .services.video_thumbnail import generate_video_thumbnail

        class ThumbnailField:
            def __bool__(self):
                return False

            def save(self, name, content, save=False):
                return None

        post = SimpleNamespace(
            pk=42,
            Format=SocialMediaPost.Format,
            content_format=SocialMediaPost.Format.VIDEO,
            video_file=SimpleNamespace(name="social_videos/short.mp4"),
            video_thumbnail=ThumbnailField(),
            save=lambda **kwargs: None,
        )
        timestamps = []

        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "source.mp4"
            source_path.write_bytes(b"video")

            def extract_frame(ffmpeg_path, video_path, output_path, timestamp):
                timestamps.append(timestamp)
                if timestamp == "00:00:01":
                    return False
                output_path.write_bytes(b"thumbnail")
                return True

            with patch("socialmanager.services.video_thumbnail.get_ffmpeg_path", return_value="ffmpeg"):
                with patch("socialmanager.services.video_thumbnail._video_source_path", return_value=source_path):
                    with patch("socialmanager.services.video_thumbnail._run_ffmpeg", side_effect=extract_frame):
                        result = generate_video_thumbnail(post)

        self.assertTrue(result)
        self.assertEqual(timestamps, ["00:00:01", "00:00:00"])

    def test_video_thumbnail_is_not_regenerated_without_force(self):
        from .services.video_thumbnail import generate_video_thumbnail

        post = SimpleNamespace(
            pk=43,
            Format=SocialMediaPost.Format,
            content_format=SocialMediaPost.Format.VIDEO,
            video_file=SimpleNamespace(name="social_videos/existing.mp4"),
            video_thumbnail=SimpleNamespace(name="post_video_thumbnails/existing.jpg"),
        )

        with patch("socialmanager.services.video_thumbnail.get_ffmpeg_path") as ffmpeg_path:
            result = generate_video_thumbnail(post)

        self.assertTrue(result)
        ffmpeg_path.assert_not_called()

    def test_video_thumbnail_backfill_regenerates_existing_and_missing_thumbnails(self):
        user, subscription = self.create_workspace_user("video-thumbnail-backfill")
        existing = SocialMediaPost.objects.create(
            subscription=subscription,
            author=user,
            title="Existing thumbnail",
            platform=SocialMediaPost.Platform.INSTAGRAM,
            content_format=SocialMediaPost.Format.VIDEO,
            status=SocialMediaPost.Status.PUBLISHED,
            video_file="social_videos/existing-thumbnail.mp4",
            video_thumbnail="post_video_thumbnails/existing-thumbnail.jpg",
        )
        missing = SocialMediaPost.objects.create(
            subscription=subscription,
            author=user,
            title="Missing thumbnail",
            platform=SocialMediaPost.Platform.INSTAGRAM,
            content_format=SocialMediaPost.Format.VIDEO,
            status=SocialMediaPost.Status.PUBLISHED,
            video_file="social_videos/missing-thumbnail.mp4",
        )

        with patch(
            "socialmanager.management.commands.generate_video_thumbnails.get_ffmpeg_path",
            return_value="ffmpeg",
        ):
            with patch(
                "socialmanager.management.commands.generate_video_thumbnails.generate_video_thumbnail",
                return_value=True,
            ) as generate:
                call_command("generate_video_thumbnails")

        generated_posts = {call.args[0].pk for call in generate.call_args_list}
        self.assertEqual(generated_posts, {existing.pk, missing.pk})
        self.assertTrue(all(call.kwargs == {"force": True} for call in generate.call_args_list))

    def test_all_video_card_branches_use_cached_thumbnail_images_only(self):
        template_root = Path(__file__).resolve().parent / "templates" / "socialmanager"
        templates = {
            template_root / "partials" / "feed_posts.html": ("post", "{% if post.content_format == 'video' %}"),
            template_root / "partials" / "post_card.html": ("post", "{% if post.content_format == 'video' %}"),
            template_root / "partials" / "shared_post_card.html": ("post", "{% if post.content_format == 'video' %}"),
            template_root / "posts" / "post_detail.html": ("post", "{% if post.content_format == 'video' %}"),
            template_root / "posts" / "post_form.html": ("draft", "{% if draft.content_format == 'video' %}"),
        }

        for template_path, (variable, branch_start) in templates.items():
            with self.subTest(template=template_path.name):
                source = template_path.read_text(encoding="utf-8")
                branch = source.split(branch_start, 1)[1].split(
                    f"{{% elif {variable}.cached_primary_image_url %}}",
                    1,
                )[0]
                self.assertIn(f"{variable}.cached_video_thumbnail_url", branch)
                self.assertIn("default_video_placeholder.webp", branch)
                self.assertIn("<img", branch)
                self.assertIn("video-play-overlay", branch)
                self.assertNotIn("<video", branch)
                self.assertNotIn(f"{variable}.cached_video_url", branch)
                self.assertNotIn(f"{variable}.video_file.url", branch)
                self.assertNotIn(f"{variable}.video_thumbnail.url", branch)

    @override_settings(STORAGES=TEST_STATIC_STORAGES)
    def test_feed_video_preview_uses_thumbnail_image_without_original_video(self):
        user, subscription = self.create_workspace_user("feed-video-thumbnail", active_member=True)
        post = SocialMediaPost.objects.create(
            subscription=subscription,
            author=user,
            title="Feed video thumbnail",
            platform=SocialMediaPost.Platform.INSTAGRAM,
            content_format=SocialMediaPost.Format.VIDEO,
            status=SocialMediaPost.Status.PUBLISHED,
            visibility=SocialMediaPost.Visibility.PUBLIC,
            caption="Video preview",
            video_file="social_videos/original-feed-video.mp4",
            video_thumbnail="post_video_thumbnails/feed-video-thumbnail.jpg",
            published_at=timezone.now(),
        )
        self.client.force_login(user)

        response = self.client.get(reverse("socialmanager:post_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, post.cached_video_thumbnail_url)
        self.assertNotContains(response, "social_videos/original-feed-video.mp4")
        self.assertNotContains(response, "<video")
        self.assertContains(response, "video-play-overlay")

    @override_settings(STORAGES=TEST_STATIC_STORAGES)
    def test_feed_video_preview_uses_video_placeholder_when_thumbnail_is_missing(self):
        user, subscription = self.create_workspace_user("feed-video-placeholder", active_member=True)
        SocialMediaPost.objects.create(
            subscription=subscription,
            author=user,
            title="Feed video placeholder",
            platform=SocialMediaPost.Platform.INSTAGRAM,
            content_format=SocialMediaPost.Format.VIDEO,
            status=SocialMediaPost.Status.PUBLISHED,
            visibility=SocialMediaPost.Visibility.PUBLIC,
            caption="Video preview without thumbnail",
            video_file="social_videos/original-placeholder-video.mp4",
            published_at=timezone.now(),
        )
        self.client.force_login(user)

        response = self.client.get(reverse("socialmanager:post_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "socialmanager/images/default_video_placeholder.webp")
        self.assertNotContains(response, "social_videos/original-placeholder-video.mp4")
        self.assertNotContains(response, "<video")
        self.assertContains(response, "video-play-overlay")

    @override_settings(USE_GCS=True)
    def test_video_upload_start_rejects_unauthenticated_user(self):
        response = self.client.post(
            reverse("socialmanager:video_upload_start"),
            data=json.dumps({"filename": "clip.mp4", "content_type": "video/mp4", "size": 100, "duration_seconds": 12}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("socialmanager:login"), response.url)

    @override_settings(USE_GCS=True)
    def test_video_upload_start_rejects_unsupported_file_type(self):
        user, _ = self.create_workspace_user("video-type")
        self.client.force_login(user)

        response = self.client.post(
            reverse("socialmanager:video_upload_start"),
            data=json.dumps({"filename": "clip.avi", "content_type": "video/x-msvideo", "size": 100, "duration_seconds": 12}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("supported video", response.json()["error"])

    @override_settings(USE_GCS=True, VIDEO_UPLOAD_MAX_BYTES=100)
    def test_video_upload_start_rejects_oversized_file(self):
        user, _ = self.create_workspace_user("video-size")
        self.client.force_login(user)

        response = self.client.post(
            reverse("socialmanager:video_upload_start"),
            data=json.dumps({"filename": "clip.mp4", "content_type": "video/mp4", "size": 101, "duration_seconds": 12}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("too large", response.json()["error"])

    @override_settings(USE_GCS=True, VIDEO_UPLOAD_MAX_BYTES=1000)
    def test_video_upload_start_rejects_overlong_duration(self):
        user, _ = self.create_workspace_user("video-duration")
        self.client.force_login(user)

        response = self.client.post(
            reverse("socialmanager:video_upload_start"),
            data=json.dumps({"filename": "clip.mp4", "content_type": "video/mp4", "size": 100, "duration_seconds": 61}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "Please provide a video that is 60 seconds or shorter.")

    @override_settings(USE_GCS=True, VIDEO_UPLOAD_MAX_BYTES=1000)
    @patch("socialmanager.views.default_storage")
    def test_video_upload_start_returns_user_scoped_object_name(self, storage):
        user, _ = self.create_workspace_user("video-session")
        self.client.force_login(user)
        storage.bucket.blob.return_value.create_resumable_upload_session.return_value = (
            "https://storage.googleapis.test/upload-session"
        )

        response = self.client.post(
            reverse("socialmanager:video_upload_start"),
            data=json.dumps({"filename": "clip.mp4", "content_type": "video/mp4", "size": 100, "duration_seconds": 12}),
            content_type="application/json",
            HTTP_ORIGIN="https://creana-914298722301.australia-southeast1.run.app",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["object_name"].startswith(f"social_videos/user_{user.pk}/"))
        self.assertTrue(response.json()["object_name"].endswith(".mp4"))
        storage.bucket.blob.return_value.create_resumable_upload_session.assert_called_once_with(
            content_type="video/mp4",
            size=100,
            origin="https://creana-914298722301.australia-southeast1.run.app",
            if_generation_match=0,
        )

    @patch("socialmanager.views.generate_video_thumbnail", return_value=False)
    def test_post_create_saves_direct_uploaded_video_object_name(self, _thumbnail):
        user, _ = self.create_workspace_user("video-post")
        self.client.force_login(user)
        object_name = f"social_videos/user_{user.pk}/123e4567-e89b-42d3-a456-426614174000.mp4"
        data = self.post_data(title="Direct video")
        data["content_format"] = SocialMediaPost.Format.VIDEO
        data["uploaded_video_object_name"] = object_name
        data["uploaded_video_duration_seconds"] = "12"

        response = self.client.post(reverse("socialmanager:post_create"), data)

        self.assertRedirects(response, reverse("socialmanager:post_list"))
        self.assertEqual(SocialMediaPost.objects.get(title="Direct video").video_file.name, object_name)

    def test_post_create_rejects_direct_uploaded_video_over_duration_limit(self):
        user, _ = self.create_workspace_user("video-post-duration")
        self.client.force_login(user)
        object_name = f"social_videos/user_{user.pk}/123e4567-e89b-42d3-a456-426614174000.mp4"
        data = self.post_data(title="Direct video too long")
        data["content_format"] = SocialMediaPost.Format.VIDEO
        data["uploaded_video_object_name"] = object_name
        data["uploaded_video_duration_seconds"] = "61"

        response = self.client.post(reverse("socialmanager:post_create"), data)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Please provide a video that is 60 seconds or shorter.")
        self.assertFalse(SocialMediaPost.objects.filter(title="Direct video too long").exists())

    def test_post_create_requires_video_asset_for_video_post(self):
        user, _ = self.create_workspace_user("missing-video")
        self.client.force_login(user)
        data = self.post_data(title="Missing video")
        data["content_format"] = SocialMediaPost.Format.VIDEO

        response = self.client.post(reverse("socialmanager:post_create"), data)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Please upload a video before saving this post.")
        self.assertFalse(SocialMediaPost.objects.filter(title="Missing video").exists())

    @patch("socialmanager.views.generate_video_thumbnail", return_value=False)
    def test_post_update_keeps_existing_video_file_without_reupload(self, _thumbnail):
        user, subscription = self.create_workspace_user("existing-video")
        self.client.force_login(user)
        post = SocialMediaPost.objects.create(
            subscription=subscription,
            author=user,
            title="Existing video",
            platform=SocialMediaPost.Platform.INSTAGRAM,
            content_format=SocialMediaPost.Format.VIDEO,
            status=SocialMediaPost.Status.DRAFT,
            visibility=SocialMediaPost.Visibility.PUBLIC,
            caption="Old caption",
            video_file="social_videos/user_1/existing.mp4",
        )
        data = self.post_data(title="Existing video updated")
        data["content_format"] = SocialMediaPost.Format.VIDEO
        data["caption"] = "Updated caption"

        response = self.client.post(reverse("socialmanager:post_update", args=[post.pk]), data)

        self.assertRedirects(response, reverse("socialmanager:post_list"))
        post.refresh_from_db()
        self.assertEqual(post.video_file.name, "social_videos/user_1/existing.mp4")
        self.assertEqual(post.caption, "Updated caption")

    @override_settings(
        USE_GCS=True,
        VIDEO_UPLOAD_MAX_BYTES=500 * 1024 * 1024,
        VIDEO_FORM_UPLOAD_MAX_BYTES=20 * 1024 * 1024,
    )
    def test_post_form_renders_distinct_direct_and_fallback_video_limits(self):
        user, _ = self.create_workspace_user("video-limits")
        self.client.force_login(user)

        response = self.client.get(reverse("socialmanager:post_create"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-direct-video-upload-max-bytes="524288000"')
        self.assertContains(response, 'data-direct-upload-max-bytes="524288000"')
        self.assertContains(response, 'data-fallback-max-bytes="20971520"')
        self.assertContains(response, 'data-max-bytes="20971520"')
        self.assertContains(response, 'data-video-max-duration-seconds="60"')

    def test_post_create_assigns_current_users_subscription(self):
        user = User.objects.create_user(username="desktop", password="password12345")
        subscription = SaaSSubscription.objects.create(name="Desktop Workspace", owner=user)
        SubscriptionMembership.objects.create(
            subscription=subscription,
            user=user,
            role=SubscriptionMembership.Role.ADMIN,
        )
        self.client.force_login(user)

        response = self.client.post(reverse("socialmanager:post_create"), self.post_data())

        self.assertEqual(response.status_code, 302)
        post = SocialMediaPost.objects.get(title="Regression post")
        self.assertEqual(post.subscription, subscription)
        self.assertEqual(post.author, user)

    def test_draft_save_redirects_to_feed(self):
        user = User.objects.create_user(username="draftredirect", password="password12345")
        subscription = SaaSSubscription.objects.create(name="Draft Workspace", owner=user)
        SubscriptionMembership.objects.create(subscription=subscription, user=user)
        self.client.force_login(user)

        response = self.client.post(reverse("socialmanager:post_create"), self.post_data())

        self.assertRedirects(response, reverse("socialmanager:post_list"))

    def test_schedule_save_redirects_to_feed(self):
        user = User.objects.create_user(username="scheduleredirect", password="password12345")
        subscription = SaaSSubscription.objects.create(name="Schedule Workspace", owner=user)
        SubscriptionMembership.objects.create(subscription=subscription, user=user)
        self.client.force_login(user)
        data = self.post_data(title="Scheduled redirect")
        data["status"] = SocialMediaPost.Status.SCHEDULED
        data["scheduled_for"] = (timezone.now() + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")

        response = self.client.post(reverse("socialmanager:post_create"), data)

        self.assertRedirects(response, reverse("socialmanager:post_list"))

    def test_feed_get_publishes_due_scheduled_posts(self):
        user, subscription = self.create_workspace_user("request-scheduler")
        due_post = SocialMediaPost.objects.create(
            subscription=subscription,
            author=user,
            title="Due scheduled post",
            platform=SocialMediaPost.Platform.INSTAGRAM,
            content_format=SocialMediaPost.Format.ARTICLE,
            status=SocialMediaPost.Status.SCHEDULED,
            visibility=SocialMediaPost.Visibility.PUBLIC,
            caption="This should publish when the feed is loaded.",
            scheduled_for=timezone.now() - timedelta(minutes=5),
        )
        self.client.force_login(user)

        response = self.client.get(reverse("socialmanager:post_list"))

        self.assertEqual(response.status_code, 200)
        due_post.refresh_from_db()
        self.assertEqual(due_post.status, SocialMediaPost.Status.PUBLISHED)
        self.assertIsNotNone(due_post.published_at)

    def test_publish_scheduled_posts_command_publishes_due_posts(self):
        user, subscription = self.create_workspace_user("scheduled-command")
        due_post = SocialMediaPost.objects.create(
            subscription=subscription,
            author=user,
            title="Command scheduled post",
            platform=SocialMediaPost.Platform.INSTAGRAM,
            content_format=SocialMediaPost.Format.ARTICLE,
            status=SocialMediaPost.Status.SCHEDULED,
            visibility=SocialMediaPost.Visibility.PUBLIC,
            caption="This should be published by the management command.",
            scheduled_for=timezone.now() - timedelta(minutes=5),
        )

        call_command("publish_scheduled_posts")

        due_post.refresh_from_db()
        self.assertEqual(due_post.status, SocialMediaPost.Status.PUBLISHED)
        self.assertIsNotNone(due_post.published_at)

    @override_settings(VIDEO_UPLOAD_MAX_BYTES=10, VIDEO_FORM_UPLOAD_MAX_BYTES=10)
    def test_oversized_video_gets_form_error(self):
        video = SimpleUploadedFile("clip.mp4", b"x" * 11, content_type="video/mp4")
        form = SocialMediaPostForm(
            data={**self.post_data(), "content_format": SocialMediaPost.Format.VIDEO},
            files={"video_file": video},
        )

        self.assertFalse(form.is_valid())
        self.assertIn("smaller than", form.errors["video_file"][0])

    @override_settings(DEBUG=True)
    @patch("socialmanager.views.generate_post_field_feedback", side_effect=RuntimeError("provider down"))
    def test_create_post_ai_feedback_surfaces_provider_failure_without_fallback(self, _generate):
        user = User.objects.create_user(username="aifallback", password="password12345")
        subscription = SaaSSubscription.objects.create(name="AI Workspace", owner=user)
        SubscriptionMembership.objects.create(
            subscription=subscription,
            user=user,
            is_active_member=True,
        )
        self.client.force_login(user)

        response = self.client.post(
            reverse("socialmanager:post_ai_feedback"),
            data=json.dumps(
                {
                    "feedback_type": "caption",
                    "post_type": "video",
                    "title": "Launch notes",
                    "caption": "Draft launch caption",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error"], "provider down")
        feedback_payload = _generate.call_args.args[0]
        self.assertEqual(feedback_payload["post_type"], "video")
        self.assertIsNone(feedback_payload["image_file"])

    @patch("socialmanager.views.generate_post_field_feedback")
    def test_edit_post_ai_feedback_uses_stored_image_and_user_preferences(self, generate):
        user, subscription = self.create_workspace_user("stored-ai-image", active_member=True)
        user_settings, _ = UserSettings.objects.update_or_create(
            user=user,
            defaults={
                "ai_language": UserSettings.AILanguage.TRADITIONAL_CHINESE,
                "ai_tone": UserSettings.AITone.FRIENDLY,
                "ai_hashtag_count": 3,
            },
        )
        post = SocialMediaPost.objects.create(
            subscription=subscription,
            author=user,
            title="Stored image post",
            platform=SocialMediaPost.Platform.INSTAGRAM,
            content_format=SocialMediaPost.Format.IMAGE,
            image="social_posts/stored-image.jpg",
        )
        generate.return_value = ai_assistant.FieldFeedbackResult(
            suggestion="Grounded stored image caption.",
            explanation="This suggestion is grounded in the stored image.",
            used_image_input=True,
            image_source="stored",
            analyzed_image_count=1,
            grounding_mode="image_primary",
            context_priority=("image", "current_input", "bio", "previous_posts"),
            context_sources={"image": True, "bio": True, "previous_posts": False},
        )
        self.client.force_login(user)

        response = self.client.post(
            reverse("socialmanager:post_ai_feedback"),
            data=json.dumps(
                {
                    "feedback_type": "caption",
                    "post_id": post.pk,
                    "title": post.title,
                    "post_type": "photo_post",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        feedback_payload = generate.call_args.args[0]
        self.assertEqual(feedback_payload["image_file"].name, "social_posts/stored-image.jpg")
        self.assertEqual(feedback_payload["image_source"], "stored")
        self.assertEqual(feedback_payload["ai_language"], user_settings.ai_language)
        self.assertEqual(feedback_payload["ai_tone"], user_settings.get_ai_tone_display())
        self.assertEqual(feedback_payload["ai_hashtag_count"], 3)
        self.assertTrue(response.json()["used_image_input"])
        self.assertEqual(response.json()["image_source"], "stored")
        self.assertEqual(response.json()["grounding_mode"], "image_primary")

    @patch("socialmanager.views.generate_post_field_feedback")
    def test_new_image_ai_feedback_requires_uploaded_image(self, generate):
        user, _subscription = self.create_workspace_user("missing-image-ai", active_member=True)
        self.client.force_login(user)

        response = self.client.post(
            reverse("socialmanager:post_ai_feedback"),
            data=json.dumps({
                "feedback_type": "caption",
                "post_type": "photo_post",
                "title": "Fox image",
            }),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "image_input_unavailable")
        self.assertFalse(response.json()["used_image_input"])
        generate.assert_not_called()

    @patch("socialmanager.views.generate_post_field_feedback")
    def test_new_image_ai_feedback_accepts_uploaded_jpeg(self, generate):
        user, _subscription = self.create_workspace_user("uploaded-image-ai", active_member=True)
        self.client.force_login(user)
        generate.return_value = ai_assistant.FieldFeedbackResult(
            suggestion="A Spark of Ideas",
            explanation="This title stays grounded in the visible fox illustration.",
            used_image_input=True,
            image_source="uploaded",
            analyzed_image_count=1,
            grounding_mode="image_primary",
            context_priority=("image", "current_input", "bio", "previous_posts"),
            context_sources={"image": True, "bio": False, "previous_posts": False},
        )

        response = self.client.post(
            reverse("socialmanager:post_ai_feedback"),
            data={
                "payload": json.dumps({
                    "feedback_type": "title",
                    "post_type": "photo_post",
                }),
                "image": self.uploaded_jpeg(),
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = generate.call_args.args[0]
        self.assertEqual(payload["image_source"], "uploaded")
        self.assertEqual(payload["image_file"].content_type, "image/jpeg")
        self.assertTrue(response.json()["used_image_input"])
        self.assertEqual(response.json()["image_source"], "uploaded")
        self.assertEqual(response.json()["analyzed_image_count"], 1)

    @patch("socialmanager.views.generate_post_field_feedback")
    def test_image_ai_feedback_rejects_invalid_image_bytes(self, generate):
        user, _subscription = self.create_workspace_user("invalid-image-ai", active_member=True)
        self.client.force_login(user)

        response = self.client.post(
            reverse("socialmanager:post_ai_feedback"),
            data={
                "payload": json.dumps({
                    "feedback_type": "caption",
                    "post_type": "photo_post",
                }),
                "image": SimpleUploadedFile("foxes.jpg", b"not-an-image", content_type="image/jpeg"),
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "invalid_image")
        generate.assert_not_called()

    @patch("socialmanager.views.generate_post_field_feedback")
    def test_edit_video_ai_feedback_uses_stored_video(self, generate):
        user, subscription = self.create_workspace_user("stored-ai-video", active_member=True)
        post = SocialMediaPost.objects.create(
            subscription=subscription,
            author=user,
            title="Stored video post",
            platform=SocialMediaPost.Platform.INSTAGRAM,
            content_format=SocialMediaPost.Format.VIDEO,
            video_file="social_videos/stored-video.mp4",
        )
        generate.return_value = ai_assistant.FieldFeedbackResult(
            suggestion="A grounded video caption.",
            explanation="This caption uses the uploaded clip.",
            used_video_input=True,
        )
        self.client.force_login(user)

        response = self.client.post(
            reverse("socialmanager:post_ai_feedback"),
            data=json.dumps({
                "feedback_type": "caption",
                "post_id": post.pk,
                "post_type": "video",
                "title": post.title,
                "video_duration_seconds": 15,
            }),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        feedback_payload = generate.call_args.args[0]
        self.assertEqual(feedback_payload["video_file"].name, "social_videos/stored-video.mp4")
        self.assertTrue(response.json()["used_video_input"])
        self.assertIn("Gemini analyzed", response.json()["media_notice"])

    @patch("socialmanager.views.default_storage.open")
    @patch("socialmanager.views.default_storage.exists", return_value=True)
    @patch("socialmanager.views.generate_post_field_feedback")
    def test_video_only_ai_feedback_accepts_uploaded_object_name(self, generate, _exists, storage_open):
        user, _subscription = self.create_workspace_user("uploaded-object-ai", active_member=True)
        object_name = f"social_videos/user_{user.pk}/12345678-1234-4123-8123-123456789abc.mp4"
        storage_open.return_value = SimpleUploadedFile("clip.mp4", b"video", content_type="video/mp4")
        generate.return_value = ai_assistant.FieldFeedbackResult(
            suggestion="Video-only caption",
            explanation="Generated from the uploaded clip.",
            used_video_input=True,
        )
        self.client.force_login(user)

        response = self.client.post(
            reverse("socialmanager:post_ai_feedback"),
            data=json.dumps({
                "feedback_type": "caption",
                "post_type": "video",
                "uploaded_video_object_name": object_name,
                "video_duration_seconds": 12,
            }),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(generate.call_args.args[0]["video_file"])
        storage_open.assert_called_once_with(object_name, "rb")

    @patch("socialmanager.views.generate_post_field_feedback")
    def test_video_only_ai_feedback_accepts_selected_video(self, generate):
        user, _subscription = self.create_workspace_user("selected-video-ai", active_member=True)
        generate.return_value = ai_assistant.FieldFeedbackResult(
            suggestion="Selected video caption",
            explanation="Generated from the selected clip.",
            used_video_input=True,
        )
        self.client.force_login(user)
        video = SimpleUploadedFile("clip.mp4", b"video", content_type="video/mp4")

        response = self.client.post(
            reverse("socialmanager:post_ai_feedback"),
            data={
                "payload": json.dumps({
                    "feedback_type": "caption",
                    "post_type": "video",
                    "video_duration_seconds": 12,
                }),
                "video": video,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(generate.call_args.args[0]["video_file"].name, "clip.mp4")

    @patch("socialmanager.views.generate_post_field_feedback")
    def test_video_ai_feedback_rejects_overlong_duration_before_provider(self, generate):
        user, _subscription = self.create_workspace_user("overlong-video-ai", active_member=True)
        self.client.force_login(user)

        response = self.client.post(
            reverse("socialmanager:post_ai_feedback"),
            data=json.dumps({
                "feedback_type": "caption",
                "post_type": "video",
                "title": "Launch notes",
                "video_duration_seconds": 61,
            }),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "Please provide a video that is 60 seconds or shorter.")
        generate.assert_not_called()

    @override_settings(GEMINI_VIDEO_MAX_BYTES=4)
    @patch("socialmanager.views.generate_post_field_feedback")
    def test_video_ai_feedback_requires_text_when_size_exceeds_ai_limit(self, generate):
        user, _subscription = self.create_workspace_user("video-ai-size", active_member=True)
        self.client.force_login(user)
        video = SimpleUploadedFile("clip.mp4", b"video", content_type="video/mp4")

        response = self.client.post(
            reverse("socialmanager:post_ai_feedback"),
            data={
                "payload": json.dumps({
                    "feedback_type": "caption",
                    "post_type": "video",
                    "video_duration_seconds": 12,
                }),
                "video": video,
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Video analysis is unavailable", response.json()["error"])
        generate.assert_not_called()

    @override_settings(GEMINI_VIDEO_MAX_BYTES=4)
    @patch("socialmanager.views.generate_post_field_feedback")
    def test_video_ai_feedback_uses_text_fallback_when_size_exceeds_ai_limit(self, generate):
        generate.return_value = ai_assistant.FieldFeedbackResult(
            suggestion="Launch day, made clearer.",
            explanation="This caption gives the audience a clearer reason to respond.",
            used_video_input=False,
            fallback_reason="video_too_large",
        )
        user, _subscription = self.create_workspace_user("video-ai-size-fallback", active_member=True)
        self.client.force_login(user)
        video = SimpleUploadedFile("clip.mp4", b"video", content_type="video/mp4")

        response = self.client.post(
            reverse("socialmanager:post_ai_feedback"),
            data={
                "payload": json.dumps({
                    "feedback_type": "caption",
                    "post_type": "video",
                    "title": "Launch notes",
                    "video_duration_seconds": 12,
                }),
                "video": video,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["fallback_reason"], "video_too_large")
        self.assertIn("generated from your text", response.json()["media_notice"])
        payload = generate.call_args.args[0]
        self.assertIsNone(payload["video_file"])
        self.assertTrue(payload["use_text_only_fallback"])

    def test_article_ai_feedback_still_requires_text_or_media(self):
        user, _subscription = self.create_workspace_user("empty-nonvideo-ai", active_member=True)
        self.client.force_login(user)

        response = self.client.post(
            reverse("socialmanager:post_ai_feedback"),
            data=json.dumps({"feedback_type": "caption", "post_type": "article"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Please add a short description", response.json()["error"])

    @override_settings(GEMINI_VIDEO_MAX_BYTES=52_428_800, GEMINI_VIDEO_MAX_SECONDS=60)
    def test_video_ai_limits_are_exposed_to_frontend(self):
        user, _subscription = self.create_workspace_user("video-ai-limits", active_member=True)
        self.client.force_login(user)

        response = self.client.get(reverse("socialmanager:post_create"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-gemini-video-max-bytes="52428800"')
        self.assertContains(response, 'data-gemini-video-max-seconds="60"')

    @override_settings(DEBUG=True)
    def test_post_create_creates_local_dev_subscription_for_user_without_membership(self):
        user = User.objects.create_user(username="mobile", password="password12345")
        self.client.force_login(user)

        response = self.client.post(
            reverse("socialmanager:post_create"),
            self.post_data(title="Mobile account post"),
        )

        self.assertEqual(response.status_code, 302)
        membership = SubscriptionMembership.objects.get(user=user)
        post = SocialMediaPost.objects.get(title="Mobile account post")
        self.assertEqual(membership.subscription.owner, user)
        self.assertEqual(post.subscription, membership.subscription)

    @override_settings(DEBUG=False)
    def test_post_create_redirects_when_user_has_no_subscription_outside_debug(self):
        user = User.objects.create_user(username="unassigned", password="password12345")
        self.client.force_login(user)

        response = self.client.post(
            reverse("socialmanager:post_create"),
            self.post_data(title="Blocked post"),
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(SocialMediaPost.objects.filter(title="Blocked post").exists())

    def test_active_subscription_helper_requires_non_archived_membership(self):
        user = User.objects.create_user(username="archivedmember", password="password12345")
        subscription = SaaSSubscription.objects.create(name="Archived Workspace", owner=user, is_archived=True)
        SubscriptionMembership.objects.create(
            subscription=subscription,
            user=user,
            role=SubscriptionMembership.Role.ADMIN,
        )

        self.assertFalse(user_has_active_subscription(user))

    def test_active_subscription_helper_requires_active_member_flag(self):
        user = User.objects.create_user(username="inactiveaimember", password="password12345")
        subscription = SaaSSubscription.objects.create(name="Inactive AI Workspace", owner=user)
        SubscriptionMembership.objects.create(
            subscription=subscription,
            user=user,
            role=SubscriptionMembership.Role.ADMIN,
            is_active_member=False,
        )

        self.assertFalse(user_has_active_subscription(user))

    def test_superuser_is_always_active_member(self):
        user = User.objects.create_superuser(
            username="aisuperuser",
            email="aisuperuser@example.com",
            password="password12345",
        )

        self.assertTrue(user_has_active_subscription(user))

    @override_settings(DEBUG=False)
    def test_ai_feedback_requires_active_subscription(self):
        user = User.objects.create_user(username="aiunassigned", password="password12345")
        self.client.force_login(user)

        response = self.client.post(
            reverse("socialmanager:post_ai_feedback"),
            data=json.dumps(
                {
                    "feedback_type": "caption",
                    "title": "Launch notes",
                    "caption": "Draft launch caption",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"], "AI features are available for members only.")

    def test_signup_creates_subscription_membership_for_new_user(self):
        response = self.client.post(
            reverse("socialmanager:signup"),
            {
                "username": "  newaccount  ",
                "email": "newaccount@example.com",
                "subscription_name": "New Account Workspace",
                "password1": "complex-password-123",
                "password2": "complex-password-123",
            },
        )

        self.assertEqual(response.status_code, 302)
        user = User.objects.get(username="newaccount")
        self.assertEqual(user.email, "newaccount@example.com")
        membership = SubscriptionMembership.objects.get(user=user)
        self.assertEqual(membership.subscription.name, "New Account Workspace")
        self.assertEqual(membership.role, SubscriptionMembership.Role.ADMIN)
        self.assertFalse(membership.is_active_member)

    @override_settings(STORAGES=TEST_STATIC_STORAGES)
    def test_signup_without_username_uses_email_prefix(self):
        response = self.client.post(
            reverse("socialmanager:signup"),
            {
                "email": "missingusername@example.com",
                "subscription_name": "Missing Username Workspace",
                "password1": "complex-password-123",
                "password2": "complex-password-123",
            },
        )

        self.assertEqual(response.status_code, 302)
        user = User.objects.get(email="missingusername@example.com")
        self.assertEqual(user.username, "missingusername")

    def test_allauth_account_signup_redirects_to_custom_password_signup(self):
        response = self.client.get("/accounts/signup/")

        self.assertRedirects(
            response,
            reverse("socialmanager:signup"),
            fetch_redirect_response=False,
        )

    @override_settings(STORAGES=TEST_STATIC_STORAGES)
    def test_signup_username_collision_uses_numeric_suffix(self):
        User.objects.create_user(
            username="takenname",
            email="takenname@example.com",
            password="password12345",
        )

        response = self.client.post(
            reverse("socialmanager:signup"),
            {
                "email": "takenname@another.example.com",
                "subscription_name": "Duplicate Username Workspace",
                "password1": "complex-password-123",
                "password2": "complex-password-123",
            },
        )

        self.assertEqual(response.status_code, 302)
        user = User.objects.get(email="takenname@another.example.com")
        self.assertEqual(user.username, "takenname1")

    @override_settings(STORAGES=TEST_STATIC_STORAGES)
    def test_signup_duplicate_email_returns_form_error_without_creating_user(self):
        User.objects.create_user(
            username="existingemailview",
            email="takenview@example.com",
            password="password12345",
        )

        response = self.client.post(
            reverse("socialmanager:signup"),
            {
                "username": "secondemailview",
                "email": "TAKENVIEW@example.com",
                "subscription_name": "Duplicate Email Workspace",
                "password1": "complex-password-123",
                "password2": "complex-password-123",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response.context["form"],
            "email",
            "This email is already registered. Please sign in or use password reset.",
        )
        self.assertFalse(User.objects.filter(username="secondemailview").exists())

    @override_settings(
        STRIPE_SECRET_KEY="stripe-secret-for-tests",
        STRIPE_MEMBERSHIP_PRICE_ID="price_membership",
        SITE_URL="https://creana.test",
    )
    @patch("socialmanager.views.stripe.checkout.Session.create")
    def test_membership_apply_starts_stripe_subscription_checkout(self, create_session):
        user = User.objects.create_user(
            username="checkoutuser",
            email="checkoutuser@example.com",
            password="password12345",
        )
        create_session.return_value = SimpleNamespace(
            id="cs_test_membership",
            url="https://checkout.stripe.test/session",
        )
        self.client.force_login(user)

        response = self.client.post(reverse("socialmanager:membership_apply"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "https://checkout.stripe.test/session")
        membership = SubscriptionMembership.objects.get(user=user)
        self.assertFalse(membership.is_active_member)
        self.assertEqual(membership.stripe_checkout_session_id, "cs_test_membership")
        create_session.assert_called_once()
        checkout_kwargs = create_session.call_args.kwargs
        self.assertEqual(checkout_kwargs["mode"], "subscription")
        self.assertEqual(checkout_kwargs["line_items"][0]["price"], "price_membership")
        self.assertEqual(checkout_kwargs["metadata"]["membership_id"], str(membership.pk))

    def test_stripe_checkout_webhook_activates_membership(self):
        user = User.objects.create_user(username="paidmember", password="password12345")
        subscription = SaaSSubscription.objects.create(name="Paid Workspace", owner=user)
        membership = SubscriptionMembership.objects.create(
            subscription=subscription,
            user=user,
            role=SubscriptionMembership.Role.ADMIN,
            is_active_member=False,
        )
        payload = {
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_paid",
                    "customer": "cus_paid",
                    "subscription": "sub_paid",
                    "client_reference_id": str(user.pk),
                    "metadata": {
                        "user_id": str(user.pk),
                        "membership_id": str(membership.pk),
                    },
                }
            },
        }

        response = self.client.post(
            reverse("socialmanager:stripe_webhook"),
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        membership.refresh_from_db()
        self.assertTrue(membership.is_active_member)
        self.assertEqual(membership.stripe_customer_id, "cus_paid")
        self.assertEqual(membership.stripe_subscription_id, "sub_paid")
        self.assertEqual(membership.stripe_checkout_session_id, "cs_paid")

    def test_membership_success_without_session_id_shows_friendly_message(self):
        user = User.objects.create_user(username="nosessionmember", password="password12345")
        self.client.force_login(user)

        response = self.client.get(reverse("socialmanager:membership_success"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Checkout session details were missing.")

    @override_settings(DEBUG=True, STRIPE_SECRET_KEY="stripe-secret-for-tests")
    @patch("socialmanager.views.stripe.checkout.Session.retrieve")
    def test_membership_success_uses_stripe_object_attributes(self, retrieve_session):
        user = User.objects.create_user(username="successmember", password="password12345")
        retrieve_session.return_value = SimpleNamespace(
            id="cs_success",
            customer="cus_success",
            subscription="sub_success",
            client_reference_id=str(user.pk),
            payment_status="paid",
            status="complete",
        )
        self.client.force_login(user)

        response = self.client.get(
            reverse("socialmanager:membership_success"),
            {"session_id": "cs_success"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Checkout completed.")
        membership = SubscriptionMembership.objects.get(user=user)
        self.assertTrue(membership.is_active_member)
        self.assertEqual(membership.stripe_subscription_id, "sub_success")

    def test_stripe_subscription_cancel_webhook_deactivates_membership(self):
        user = User.objects.create_user(username="cancelmember", password="password12345")
        subscription = SaaSSubscription.objects.create(name="Cancel Workspace", owner=user)
        membership = SubscriptionMembership.objects.create(
            subscription=subscription,
            user=user,
            role=SubscriptionMembership.Role.ADMIN,
            is_active_member=True,
            stripe_subscription_id="sub_cancelled",
        )
        payload = {
            "type": "customer.subscription.deleted",
            "data": {"object": {"id": "sub_cancelled", "status": "canceled"}},
        }

        response = self.client.post(
            reverse("socialmanager:stripe_webhook"),
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        membership.refresh_from_db()
        self.assertFalse(membership.is_active_member)

    def test_stripe_cancel_webhook_does_not_remove_superuser_membership(self):
        user = User.objects.create_superuser(username="rootsubscriber", password="password12345")
        subscription = SaaSSubscription.objects.create(name="Root Workspace", owner=user)
        membership = SubscriptionMembership.objects.create(
            subscription=subscription,
            user=user,
            role=SubscriptionMembership.Role.ADMIN,
            is_active_member=True,
            stripe_subscription_id="sub_root",
        )
        payload = {
            "type": "customer.subscription.deleted",
            "data": {"object": {"id": "sub_root", "status": "canceled"}},
        }

        response = self.client.post(
            reverse("socialmanager:stripe_webhook"),
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        membership.refresh_from_db()
        self.assertTrue(membership.is_active_member)
        self.assertTrue(user_has_active_subscription(user))

    def test_signup_truncates_generated_username_to_twenty_characters(self):
        form = SignUpForm(
            data={
                "email": "averylongemailprefixname@example.com",
                "subscription_name": "Long Name Workspace",
                "password1": "complex-password-123",
                "password2": "complex-password-123",
            }
        )

        self.assertTrue(form.is_valid())
        user = form.save(commit=False)
        self.assertEqual(user.username, "averylongemailprefix")
        self.assertEqual(len(user.username), 20)

    def test_signup_rejects_duplicate_email(self):
        User.objects.create_user(
            username="existingemail",
            email="taken@example.com",
            password="password12345",
        )

        form = SignUpForm(
            data={
                "username": "secondemail",
                "email": "TAKEN@example.com",
                "subscription_name": "Duplicate Email Workspace",
                "password1": "complex-password-123",
                "password2": "complex-password-123",
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn("email", form.errors)
        self.assertIn("This email is already registered.", form.errors["email"][0])

    def test_password_reset_rejects_google_only_account(self):
        user = User.objects.create_user(username="googleonly", email="googleonly@example.com")
        user.set_unusable_password()
        user.save(update_fields=["password"])
        SocialAccount.objects.create(user=user, provider="google", uid="google-only")

        form = CreanaPasswordResetForm(data={"email": "googleonly@example.com"})

        self.assertFalse(form.is_valid())
        self.assertIn("This account uses Google Sign-In.", form.errors["email"][0])

    def test_profile_rejects_bio_over_limit(self):
        user = User.objects.create_user(username="profilebio", password="password12345")
        profile = UserProfile(user=user, bio="x" * 251)

        with self.assertRaises(ValidationError):
            profile.full_clean()

    def test_profile_rejects_more_than_five_links(self):
        user = User.objects.create_user(username="profilelinks", password="password12345")
        profile = UserProfile(
            user=user,
            links="|".join(f"https://example{i}.com" for i in range(6)),
        )

        with self.assertRaises(ValidationError):
            profile.full_clean()

    def test_post_form_rejects_caption_over_limit_for_image_post(self):
        form_data = self.post_data(title="Long caption")
        form_data["content_format"] = SocialMediaPost.Format.IMAGE
        form_data["caption"] = "x" * 251

        form = SocialMediaPostForm(data=form_data, subscription=None)

        self.assertFalse(form.is_valid())
        self.assertIn("caption", form.errors)

    def test_post_form_rejects_title_over_limit(self):
        form_data = self.post_data(title="x" * 51)

        form = SocialMediaPostForm(data=form_data, subscription=None)

        self.assertFalse(form.is_valid())
        self.assertIn("title", form.errors)

    def test_post_form_rejects_more_than_five_hashtags(self):
        form_data = self.post_data(title="Too many hashtags")
        form_data["hashtags"] = "#one #two #three #four #five #six"

        form = SocialMediaPostForm(data=form_data, subscription=None)

        self.assertFalse(form.is_valid())
        self.assertIn("hashtags", form.errors)

    def test_post_model_rejects_more_than_five_hashtags(self):
        user = User.objects.create_user(username="postmodel", password="password12345")
        subscription = SaaSSubscription.objects.create(name="Model Workspace", owner=user)
        post = SocialMediaPost(
            subscription=subscription,
            author=user,
            title="Model validation",
            platform=SocialMediaPost.Platform.INSTAGRAM,
            content_format=SocialMediaPost.Format.IMAGE,
            caption="Short caption",
            hashtags="#one #two #three #four #five #six",
        )

        with self.assertRaises(ValidationError):
            post.full_clean()

    def test_campaign_form_rejects_name_over_limit(self):
        form = SocialMediaCampaignForm(
            data={
                "name": "x" * 51,
                "objective": "Launch campaign",
                "platform_focus": "[]",
                "campaign_posts": "[]",
                "start_date": "",
                "end_date": "",
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn("name", form.errors)

    def test_campaign_model_rejects_name_over_limit(self):
        user = User.objects.create_user(username="campaignmodel", password="password12345")
        subscription = SaaSSubscription.objects.create(name="Campaign Workspace", owner=user)
        campaign = SocialMediaCampaign(
            subscription=subscription,
            created_by=user,
            name="x" * 51,
        )

        with self.assertRaises(ValidationError):
            campaign.full_clean()

    def test_allauth_signup_creates_profile_workspace_and_membership(self):
        user = User.objects.create_user(
            username="googleuser",
            email="googleuser@example.com",
        )

        user_signed_up.send(sender=User, request=None, user=user)

        profile = UserProfile.objects.get(user=user)
        membership = SubscriptionMembership.objects.get(user=user)
        self.assertEqual(profile.user, user)
        self.assertEqual(membership.subscription.owner, user)
        self.assertEqual(membership.subscription.name, "googleuser's Workspace")
        self.assertEqual(membership.role, SubscriptionMembership.Role.ADMIN)

    def test_existing_email_password_login_still_works(self):
        User.objects.create_user(
            username="emaillogin",
            email="emaillogin@example.com",
            password="password12345",
        )

        response = self.client.post(
            reverse("socialmanager:login"),
            {
                "username": "emaillogin@example.com",
                "password": "password12345",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("_auth_user_id", self.client.session)
        self.assertEqual(
            self.client.session.get("_auth_user_backend"),
            "django.contrib.auth.backends.ModelBackend",
        )


class GoogleAccountLinkingTests(TestCase):
    def setUp(self):
        self.adapter = SocialManagerSocialAccountAdapter()
        self.request_factory = RequestFactory()

    def test_new_user_signal_creates_settings_with_ai_push_disabled(self):
        user = User.objects.create_user(
            username="settings-defaults",
            email="settings-defaults@example.com",
        )

        settings_obj = UserSettings.objects.get(user=user)
        self.assertFalse(settings_obj.push_ai_finished)

    def social_login(self, email, uid="google-uid", user=None):
        user = user or User(username=email.split("@", 1)[0], email=email)
        account = SocialAccount(provider="google", uid=uid, user=user)
        sociallogin = SocialLogin(
            user=user,
            account=account,
            email_addresses=[
                EmailAddress(
                    user=user,
                    email=email,
                    verified=True,
                    primary=True,
                )
            ],
        )
        sociallogin.provider = SimpleNamespace(app=None, get_settings=lambda: {})
        return sociallogin

    def request_for_user(self, user=None):
        request = self.request_factory.get("/accounts/google/login/callback/")
        request.user = user or AnonymousUser()
        request.session = SessionStore()
        request._messages = FallbackStorage(request)
        return request

    def assert_blocked_with_message(self, request, sociallogin, message, redirect_name):
        with self.assertRaises(ImmediateHttpResponse) as raised:
            self.adapter.pre_social_login(request, sociallogin)

        self.assertEqual(raised.exception.response.status_code, 302)
        self.assertEqual(raised.exception.response.url, reverse(redirect_name))
        self.assertIn(message, [str(item) for item in request._messages])

    def complete_login(self, request, sociallogin):
        with request_context(request):
            return complete_social_login(request, sociallogin)

    def test_google_email_matching_superuser_links_for_normal_site_login(self):
        superuser = User.objects.create_superuser(
            username="root",
            email="root@example.com",
            password="password12345",
        )

        request = self.request_for_user()
        sociallogin = self.social_login(email="root@example.com")

        response = self.complete_login(request, sociallogin)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("socialmanager:post_list"))
        self.assertEqual(sociallogin.user, superuser)
        self.assertTrue(SocialAccount.objects.filter(user=superuser, provider="google").exists())
        self.assertTrue(superuser.check_password("password12345"))

    def test_google_email_matching_staff_user_links_for_normal_site_login(self):
        staff_user = User.objects.create_user(
            username="staff",
            email="staff@example.com",
            password="password12345",
            is_staff=True,
        )

        request = self.request_for_user()
        sociallogin = self.social_login(email="staff@example.com")

        response = self.complete_login(request, sociallogin)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("socialmanager:post_list"))
        self.assertEqual(sociallogin.user, staff_user)
        self.assertTrue(SocialAccount.objects.filter(user=staff_user, provider="google").exists())
        self.assertTrue(staff_user.check_password("password12345"))

    def test_normal_google_login_links_existing_email_without_social_signup(self):
        user = User.objects.create_user(
            username="existing",
            email="existing@example.com",
            password="password12345",
        )

        request = self.request_for_user()
        sociallogin = self.social_login(email="existing@example.com", uid="existing-google")

        response = self.complete_login(request, sociallogin)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("socialmanager:post_list"))
        self.assertNotEqual(response.url, "/accounts/social/signup/")
        self.assertEqual(sociallogin.user, user)
        self.assertTrue(user.check_password("password12345"))
        linked_account = SocialAccount.objects.get(user=user, provider="google")
        self.assertEqual(linked_account.uid, "existing-google")
        email_address = EmailAddress.objects.get(user=user, email="existing@example.com")
        self.assertTrue(email_address.verified)
        self.assertTrue(email_address.primary)
        self.assertEqual(request.session.get("_auth_user_id"), str(user.pk))

    def test_existing_email_with_different_google_account_still_blocks(self):
        user = User.objects.create_user(
            username="existing-conflict",
            email="existing-conflict@example.com",
            password="password12345",
        )
        SocialAccount.objects.create(user=user, provider="google", uid="first-google")
        request = self.request_for_user()
        sociallogin = self.social_login(
            email="existing-conflict@example.com",
            uid="second-google",
        )

        response = self.complete_login(request, sociallogin)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("socialmanager:login"))
        self.assertIn(
            USER_GOOGLE_EXISTS_MESSAGE,
            [str(item) for item in request._messages],
        )
        self.assertFalse(SocialAccount.objects.filter(uid="second-google").exists())

    def test_new_google_email_auto_signs_up_and_logs_in_without_social_signup(self):
        request = self.request_for_user()
        sociallogin = self.social_login(email="newgoogle@example.com", uid="new-google")

        response = self.complete_login(request, sociallogin)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("socialmanager:post_list"))
        self.assertNotEqual(response.url, "/accounts/social/signup/")
        self.assertIsNotNone(sociallogin.user.pk)
        self.assertEqual(request.session.get("_auth_user_id"), str(sociallogin.user.pk))
        self.assertTrue(SocialAccount.objects.filter(user=sociallogin.user, uid="new-google").exists())
        email_address = EmailAddress.objects.get(user=sociallogin.user, email="newgoogle@example.com")
        self.assertTrue(email_address.verified)
        self.assertTrue(email_address.primary)
        self.assertTrue(UserProfile.objects.filter(user=sociallogin.user).exists())
        self.assertTrue(UserSettings.objects.filter(user=sociallogin.user).exists())
        self.assertTrue(SubscriptionMembership.objects.filter(user=sociallogin.user).exists())

    def test_google_login_without_email_is_blocked_instead_of_showing_signup(self):
        request = self.request_for_user()
        user = User(username="")
        sociallogin = SocialLogin(
            user=user,
            account=SocialAccount(provider="google", uid="no-email", user=user),
            email_addresses=[],
        )

        self.assert_blocked_with_message(
            request,
            sociallogin,
            GOOGLE_EMAIL_REQUIRED_MESSAGE,
            "socialmanager:login",
        )

    def test_new_google_user_uses_email_local_part_username_and_unusable_password(self):
        request = self.request_for_user()
        sociallogin = self.social_login(email="verylongusernameabcde@gmail.com", uid="long-google")

        self.adapter.pre_social_login(request, sociallogin)
        user = self.adapter.save_user(request, sociallogin)

        self.assertEqual(user.username, "verylongusernameabcd")
        self.assertEqual(len(user.username), 20)
        self.assertEqual(user.email, "verylongusernameabcde@gmail.com")
        self.assertFalse(user.has_usable_password())

    def test_new_google_username_collision_gets_short_suffix_within_limit(self):
        User.objects.create_user(username="verylongusernameabcd", email="first@example.com")
        request = self.request_for_user()
        sociallogin = self.social_login(email="verylongusernameabcde@gmail.com", uid="collision-google")

        self.adapter.pre_social_login(request, sociallogin)
        user = self.adapter.save_user(request, sociallogin)

        self.assertEqual(user.username, "verylongusernameabc1")
        self.assertEqual(len(user.username), 20)

    def test_google_username_slugifies_email_prefix(self):
        cases = {
            "First.Last+tag@example.com": "firstlasttag",
            "first_last@example.com": "first_last",
            "使用者@example.com": "user",
        }

        for email, expected in cases.items():
            with self.subTest(email=email):
                user = User(email=email)
                sociallogin = self.social_login(
                    email=email,
                    uid=f"slug-{len(expected)}-{email}",
                    user=user,
                )
                populated = self.adapter.populate_user(
                    self.request_for_user(),
                    sociallogin,
                    {"email": email},
                )
                self.assertEqual(populated.username, expected)

    def test_logged_in_user_cannot_link_second_google_account(self):
        user = User.objects.create_user(
            username="linked",
            email="linked@example.com",
            password="password12345",
        )
        SocialAccount.objects.create(user=user, provider="google", uid="first-google")

        request = self.request_for_user(user)
        sociallogin = self.social_login(email="second@example.com", uid="second-google")
        sociallogin.state["process"] = AuthProcess.CONNECT

        self.assert_blocked_with_message(
            request,
            sociallogin,
            USER_GOOGLE_EXISTS_MESSAGE,
            "socialmanager:settings",
        )

    def test_logged_in_user_cannot_link_google_account_belonging_to_another_user(self):
        user = User.objects.create_user(
            username="current",
            email="current@example.com",
            password="password12345",
        )
        other_user = User.objects.create_user(
            username="othergoogle",
            email="othergoogle@example.com",
            password="password12345",
        )
        SocialAccount.objects.create(user=other_user, provider="google", uid="shared-google")

        request = self.request_for_user(user)
        sociallogin = self.social_login(email="shared@example.com", uid="shared-google")
        sociallogin.state["process"] = AuthProcess.CONNECT

        self.assert_blocked_with_message(
            request,
            sociallogin,
            GOOGLE_LINKED_ELSEWHERE_MESSAGE,
            "socialmanager:settings",
        )

    def test_logged_in_staff_user_cannot_connect_google_without_dedicated_allowance(self):
        staff_user = User.objects.create_user(
            username="staff-connect",
            email="staff-connect@example.com",
            password="password12345",
            is_staff=True,
        )

        request = self.request_for_user(staff_user)
        sociallogin = self.social_login(email="staff-connect@example.com", uid="staff-google")
        sociallogin.state["process"] = AuthProcess.CONNECT

        self.assert_blocked_with_message(
            request,
            sociallogin,
            ADMIN_GOOGLE_LINK_MESSAGE,
            "socialmanager:settings",
        )
        self.assertFalse(SocialAccount.objects.filter(user=staff_user, provider="google", uid="staff-google").exists())
        self.assertTrue(staff_user.check_password("password12345"))

    def test_authenticated_superuser_google_login_from_login_page_is_blocked(self):
        superuser = User.objects.create_superuser(
            username="currentroot",
            email="currentroot@example.com",
            password="password12345",
        )

        request = self.request_for_user(superuser)
        sociallogin = self.social_login(email="deleted@example.com", uid="deleted-google")

        self.assert_blocked_with_message(
            request,
            sociallogin,
            AUTHENTICATED_GOOGLE_LOGIN_MESSAGE,
            "socialmanager:login",
        )
        self.assertFalse(SocialAccount.objects.filter(user=superuser, uid="deleted-google").exists())

    def test_authenticated_user_google_login_cannot_switch_identity(self):
        current_user = User.objects.create_user(
            username="currentlogin",
            email="currentlogin@example.com",
            password="password12345",
        )
        other_user = User.objects.create_user(
            username="otherlogin",
            email="otherlogin@example.com",
            password="password12345",
        )

        request = self.request_for_user(current_user)
        sociallogin = self.social_login(email="otherlogin@example.com", uid="other-google")

        self.assert_blocked_with_message(
            request,
            sociallogin,
            AUTHENTICATED_GOOGLE_LOGIN_MESSAGE,
            "socialmanager:login",
        )
        self.assertFalse(SocialAccount.objects.filter(user=current_user, uid="other-google").exists())
        self.assertFalse(SocialAccount.objects.filter(user=other_user, uid="other-google").exists())

    def test_authenticated_user_google_login_with_same_linked_account_redirects_to_feed(self):
        current_user = User.objects.create_user(
            username="returninggoogle",
            email="returninggoogle@example.com",
            password="password12345",
        )
        account = SocialAccount.objects.create(
            user=current_user,
            provider="google",
            uid="returning-google",
        )
        request = self.request_for_user(current_user)
        sociallogin = SocialLogin(user=current_user, account=account)

        with self.assertRaises(ImmediateHttpResponse) as raised:
            self.adapter.pre_social_login(request, sociallogin)

        self.assertEqual(raised.exception.response.status_code, 302)
        self.assertEqual(raised.exception.response.url, reverse("socialmanager:post_list"))
        self.assertEqual([str(item) for item in request._messages], [])

    def test_deleting_user_removes_social_account_and_email_address(self):
        user = User.objects.create_user(
            username="deletegoogle",
            email="deletegoogle@example.com",
            password="password12345",
        )
        SocialAccount.objects.create(user=user, provider="google", uid="delete-google")
        EmailAddress.objects.create(user=user, email="deletegoogle@example.com", verified=True, primary=True)
        user_id = user.pk

        user.delete()

        self.assertFalse(SocialAccount.objects.filter(uid="delete-google").exists())
        self.assertFalse(EmailAddress.objects.filter(email="deletegoogle@example.com").exists())
        self.assertFalse(UserProfile.objects.filter(user_id=user_id).exists())
        self.assertFalse(UserSettings.objects.filter(user_id=user_id).exists())

    def test_google_login_after_deleted_user_creates_new_normal_user_not_superuser(self):
        superuser = User.objects.create_superuser(
            username="root-for-delete",
            email="root-for-delete@example.com",
            password="password12345",
        )
        deleted_user = User.objects.create_user(
            username="deletedgmail",
            email="deletedgmail@example.com",
            password="password12345",
        )
        SocialAccount.objects.create(user=deleted_user, provider="google", uid="deleted-google")
        EmailAddress.objects.create(user=deleted_user, email="deletedgmail@example.com", verified=True, primary=True)
        deleted_user.delete()

        request = self.request_for_user()
        sociallogin = self.social_login(email="deletedgmail@example.com", uid="deleted-google")

        self.adapter.pre_social_login(request, sociallogin)
        new_user = self.adapter.save_user(request, sociallogin)
        user_signed_up.send(sender=User, request=request, user=new_user)

        self.assertNotEqual(new_user.pk, superuser.pk)
        self.assertEqual(new_user.email, "deletedgmail@example.com")
        self.assertFalse(new_user.is_staff)
        self.assertFalse(new_user.is_superuser)
        self.assertTrue(SocialAccount.objects.filter(user=new_user, provider="google", uid="deleted-google").exists())
        self.assertTrue(UserProfile.objects.filter(user=new_user).exists())
        self.assertTrue(UserSettings.objects.filter(user=new_user).exists())
        self.assertTrue(SubscriptionMembership.objects.filter(user=new_user).exists())

    def test_existing_linked_social_account_can_login_normally(self):
        user = User.objects.create_user(
            username="socialuser",
            email="socialuser@example.com",
            password="password12345",
        )
        SocialAccount.objects.create(user=user, provider="google", uid="known-google")
        sociallogin = self.social_login(email="socialuser@example.com", uid="known-google")
        request = self.request_for_user()

        response = self.complete_login(request, sociallogin)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("socialmanager:post_list"))
        self.assertEqual(request.session.get("_auth_user_id"), str(user.pk))

    def test_password_login_still_works_after_google_linking(self):
        user = User.objects.create_user(
            username="passworduser",
            email="passworduser@example.com",
            password="password12345",
        )
        request = self.request_for_user()
        sociallogin = self.social_login(email="passworduser@example.com")

        response = self.complete_login(request, sociallogin)

        self.assertEqual(response.url, reverse("socialmanager:post_list"))
        self.assertTrue(user.check_password("password12345"))
        response = self.client.post(
            reverse("socialmanager:login"),
            {
                "username": "passworduser@example.com",
                "password": "password12345",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("_auth_user_id", self.client.session)
        self.assertEqual(
            self.client.session.get("_auth_user_backend"),
            "django.contrib.auth.backends.ModelBackend",
        )

    def test_superuser_password_login_still_works(self):
        User.objects.create_superuser(
            username="superpassword",
            email="superpassword@example.com",
            password="password12345",
        )

        response = self.client.post(
            reverse("socialmanager:login"),
            {
                "username": "superpassword@example.com",
                "password": "password12345",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("_auth_user_id", self.client.session)

    def test_admin_site_login_works_with_superuser_password(self):
        User.objects.create_superuser(
            username="adminsite",
            email="adminsite@example.com",
            password="password12345",
        )

        response = self.client.post(
            reverse("admin:login"),
            {
                "username": "adminsite",
                "password": "password12345",
                "next": reverse("admin:index"),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("admin:index"))

    def test_admin_site_rejects_staff_session_created_by_google_backend(self):
        staff_user = User.objects.create_user(
            username="socialadmin",
            email="socialadmin@example.com",
            password="password12345",
            is_staff=True,
        )
        self.client.force_login(
            staff_user,
            backend="allauth.account.auth_backends.AuthenticationBackend",
        )

        response = self.client.get(reverse("admin:index"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, f"{reverse('admin:login')}?next={reverse('admin:index')}")

    def test_logout_clears_session(self):
        user = User.objects.create_user(
            username="logoutuser",
            email="logoutuser@example.com",
            password="password12345",
        )
        self.client.force_login(user)
        session = self.client.session
        session["temporary_identity_state"] = "present"
        session.save()

        response = self.client.post(reverse("socialmanager:logout"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("socialmanager:login"))
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertNotIn("temporary_identity_state", self.client.session)


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="support@creana.test",
)
class PasswordResetFlowTests(TestCase):
    def test_forgot_password_link_points_to_reset_request_page(self):
        response = self.client.get(reverse("socialmanager:login"))

        self.assertContains(response, reverse("socialmanager:password_reset"))
        self.assertContains(response, "Forgot password?")

    def test_password_reset_request_uses_generic_success_for_existing_email(self):
        User.objects.create_user(
            username="resetuser",
            email="resetuser@example.com",
            password="old-password-123",
        )

        response = self.client.post(
            reverse("socialmanager:password_reset"),
            {"email": "resetuser@example.com"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("socialmanager:password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].from_email, "support@creana.test")
        self.assertEqual(mail.outbox[0].subject, "Reset your Creana password")
        self.assertIn("/password-reset/", mail.outbox[0].body)
        self.assertIn("http://testserver/password-reset/", mail.outbox[0].body)

    def test_password_reset_request_uses_same_success_for_unknown_email(self):
        response = self.client.post(
            reverse("socialmanager:password_reset"),
            {"email": "missing@example.com"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("socialmanager:password_reset_done"))
        self.assertEqual(len(mail.outbox), 0)

    def test_password_reset_sends_for_active_user_with_unusable_password(self):
        user = User.objects.create_user(
            username="googleonly",
            email="googleonly@example.com",
        )
        user.set_unusable_password()
        user.save(update_fields=["password"])

        response = self.client.post(
            reverse("socialmanager:password_reset"),
            {"email": "googleonly@example.com"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("socialmanager:password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("/password-reset/", mail.outbox[0].body)

    def test_password_reset_updates_password_and_keeps_google_link(self):
        user = User.objects.create_user(
            username="linkedreset",
            email="linkedreset@example.com",
            password="old-password-123",
        )
        social_account = SocialAccount.objects.create(
            user=user,
            provider="google",
            uid="linked-reset-google",
        )

        uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)
        confirm_url = reverse("socialmanager:password_reset_confirm", args=[uidb64, token])
        response = self.client.get(confirm_url)
        self.assertEqual(response.status_code, 302)

        response = self.client.post(
            response.url,
            {
                "new_password1": "new-password-12345",
                "new_password2": "new-password-12345",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("socialmanager:password_reset_complete"))
        user.refresh_from_db()
        self.assertTrue(user.check_password("new-password-12345"))
        self.assertTrue(SocialAccount.objects.filter(pk=social_account.pk, user=user).exists())

        response = self.client.post(
            reverse("socialmanager:login"),
            {
                "username": "linkedreset@example.com",
                "password": "new-password-12345",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("_auth_user_id", self.client.session)


class SaaSSubscriptionAdminTests(TestCase):
    def setUp(self):
        self.admin = SaaSSubscriptionAdmin(SaaSSubscription, AdminSite())
        self.request_factory = RequestFactory()

    def request_for_user(self, user):
        request = self.request_factory.get("/admin/socialmanager/saassubscription/")
        request.user = user
        return request

    def test_superuser_can_delete_subscription_in_admin(self):
        user = User.objects.create_superuser(
            username="superadmin",
            email="superadmin@example.com",
            password="password12345",
        )

        self.assertTrue(self.admin.has_delete_permission(self.request_for_user(user)))

    def test_staff_with_delete_permission_can_delete_subscription_in_admin(self):
        user = User.objects.create_user(
            username="staffadmin",
            email="staffadmin@example.com",
            password="password12345",
            is_staff=True,
        )
        user.user_permissions.add(Permission.objects.get(codename="delete_saassubscription"))

        self.assertTrue(self.admin.has_delete_permission(self.request_for_user(user)))

    def test_admin_bulk_delete_removes_subscription_instead_of_archiving(self):
        user = User.objects.create_superuser(
            username="deleteadmin",
            email="deleteadmin@example.com",
            password="password12345",
        )
        subscription = SaaSSubscription.objects.create(
            name="Archived Workspace",
            owner=user,
            is_archived=False,
        )

        self.admin.delete_queryset(
            self.request_for_user(user),
            SaaSSubscription.objects.filter(pk=subscription.pk),
        )

        self.assertFalse(SaaSSubscription.objects.filter(pk=subscription.pk).exists())


class NotificationTests(TestCase):
    def setUp(self):
        self.actor = User.objects.create_user(username="actor", password="password12345")
        self.recipient = User.objects.create_user(username="recipient", password="password12345")
        self.subscription = SaaSSubscription.objects.create(name="Recipient Workspace", owner=self.recipient)
        SubscriptionMembership.objects.create(
            subscription=self.subscription,
            user=self.recipient,
            role=SubscriptionMembership.Role.ADMIN,
        )
        SubscriptionMembership.objects.create(
            subscription=self.subscription,
            user=self.actor,
            role=SubscriptionMembership.Role.STANDARD,
        )
        self.post = SocialMediaPost.objects.create(
            subscription=self.subscription,
            author=self.recipient,
            title="Published post",
            platform=SocialMediaPost.Platform.INSTAGRAM,
            content_format=SocialMediaPost.Format.ARTICLE,
            status=SocialMediaPost.Status.PUBLISHED,
            visibility=SocialMediaPost.Visibility.PUBLIC,
            caption="Post body",
        )

    def test_new_post_generates_slug_once(self):
        self.assertEqual(self.post.slug, "published-post")

        self.post.title = "Updated title"
        self.post.save(update_fields=["title"])
        self.post.refresh_from_db()

        self.assertEqual(self.post.slug, "published-post")

    def test_post_slug_uses_id_fallback_when_slugify_is_empty(self):
        post = SocialMediaPost.objects.create(
            subscription=self.subscription,
            author=self.recipient,
            title="😀😀",
            platform=SocialMediaPost.Platform.INSTAGRAM,
        )

        self.assertEqual(post.slug, f"post-{post.pk}")
        self.assertEqual(SocialMediaPost.objects.get(pk=post.pk).slug, f"post-{post.pk}")

    def test_post_detail_uses_canonical_slug_and_keeps_legacy_url(self):
        self.client.force_login(self.actor)
        canonical_url = reverse(
            "socialmanager:post_detail",
            args=[self.post.pk, self.post.slug],
        )

        self.assertEqual(self.client.get(canonical_url).status_code, 200)

        wrong_slug_response = self.client.get(
            reverse("socialmanager:post_detail", args=[self.post.pk, "wrong-slug"])
        )
        self.assertRedirects(
            wrong_slug_response,
            canonical_url,
            status_code=301,
            fetch_redirect_response=False,
        )

        legacy_response = self.client.get(
            reverse("socialmanager:post_detail_legacy", args=[self.post.pk])
        )
        self.assertRedirects(
            legacy_response,
            canonical_url,
            status_code=301,
            fetch_redirect_response=False,
        )

    def test_like_creates_notification_for_post_author(self):
        self.client.force_login(self.actor)

        response = self.client.post(
            reverse("socialmanager:post_engagement_toggle", args=[self.post.pk, "like"])
        )

        self.assertEqual(response.status_code, 200)
        notification = Notification.objects.get()
        self.assertEqual(notification.recipient, self.recipient)
        self.assertEqual(notification.actor, self.actor)
        self.assertEqual(notification.kind, Notification.Kind.LIKE)
        self.assertEqual(notification.post, self.post)
        self.assertFalse(notification.is_read)

    def test_disabled_like_notifications_are_not_created(self):
        UserSettings.objects.update_or_create(
            user=self.recipient,
            defaults={"notify_post_like": False},
        )
        self.client.force_login(self.actor)

        response = self.client.post(
            reverse("socialmanager:post_engagement_toggle", args=[self.post.pk, "like"])
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Notification.objects.exists())

    def test_disabled_comment_like_notifications_are_not_created(self):
        from .views import create_notification

        comment = PostComment.objects.create(
            post=self.post,
            author=self.recipient,
            body="Original comment",
        )
        UserSettings.objects.update_or_create(
            user=self.recipient,
            defaults={"notify_comment_like": False},
        )

        create_notification(
            recipient=self.recipient,
            actor=self.actor,
            kind=Notification.Kind.COMMENT_LIKE,
            post=self.post,
            comment=comment,
        )

        self.assertFalse(Notification.objects.exists())

    def test_disabled_comment_reply_notifications_are_not_created(self):
        from .views import create_notification

        comment = PostComment.objects.create(
            post=self.post,
            author=self.recipient,
            body="Original comment",
        )
        reply = PostComment.objects.create(
            post=self.post,
            author=self.actor,
            parent=comment,
            body="Reply",
        )
        UserSettings.objects.update_or_create(
            user=self.recipient,
            defaults={"notify_comment_reply": False},
        )

        create_notification(
            recipient=self.recipient,
            actor=self.actor,
            kind=Notification.Kind.COMMENT_REPLY,
            post=self.post,
            comment=reply,
        )

        self.assertFalse(Notification.objects.exists())

    def test_share_creates_notification_for_post_author(self):
        self.client.force_login(self.actor)

        response = self.client.post(
            reverse("socialmanager:post_engagement_toggle", args=[self.post.pk, "share"])
        )

        self.assertEqual(response.status_code, 200)
        notification = Notification.objects.get()
        self.assertEqual(notification.recipient, self.recipient)
        self.assertEqual(notification.actor, self.actor)
        self.assertEqual(notification.kind, Notification.Kind.SHARE)
        self.assertEqual(notification.post, self.post)

    def test_comment_creates_notification_for_post_author(self):
        self.client.force_login(self.actor)

        response = self.client.post(
            reverse("socialmanager:post_detail", args=[self.post.pk, self.post.slug]),
            {"body": "Nice work"},
        )

        self.assertEqual(response.status_code, 302)
        notification = Notification.objects.get()
        self.assertEqual(notification.kind, Notification.Kind.COMMENT)
        self.assertEqual(notification.recipient, self.recipient)
        self.assertEqual(notification.actor, self.actor)
        self.assertEqual(notification.post, self.post)
        self.assertIsNotNone(notification.comment)

        self.client.force_login(self.recipient)
        notification_response = self.client.get(reverse("socialmanager:notifications"))
        self.assertContains(notification_response, f"{self.actor.username}")
        self.assertContains(notification_response, "commented on your post")

    def test_notifications_group_same_post_interaction(self):
        actors = [
            self.actor,
            User.objects.create_user(username="actor_two", password="password12345"),
            User.objects.create_user(username="actor_three", password="password12345"),
            User.objects.create_user(username="actor_four", password="password12345"),
            User.objects.create_user(username="actor_five", password="password12345"),
        ]
        for actor in actors:
            Notification.objects.create(
                recipient=self.recipient,
                actor=actor,
                kind=Notification.Kind.LIKE,
                post=self.post,
            )

        self.client.force_login(self.recipient)
        response = self.client.get(reverse("socialmanager:notifications"))

        grouped_notifications = response.context["grouped_notifications"]
        self.assertNotIn("notifications", response.context)
        self.assertEqual(len(grouped_notifications), 1)
        self.assertEqual(grouped_notifications[0].actor_count, 5)
        self.assertEqual(grouped_notifications[0].other_count, 3)
        self.assertContains(response, "actor_five, actor_four, and 3 others liked your post")
        self.assertContains(response, '<a class="notification-target" href="/notifications/5/open/">Published post</a>', html=True)
        self.assertContains(response, '<span class="notification-message">actor_five, actor_four, and 3 others liked your post</span>', html=True)
        self.assertNotContains(response, "notification-actor")
        self.assertNotContains(response, "data-target-url")

    def test_notifications_group_does_not_repeat_same_actor(self):
        actor_two = User.objects.create_user(username="actor_two", password="password12345")
        Notification.objects.create(
            recipient=self.recipient,
            actor=self.actor,
            kind=Notification.Kind.LIKE,
            post=self.post,
        )
        Notification.objects.create(
            recipient=self.recipient,
            actor=self.actor,
            kind=Notification.Kind.LIKE,
            post=self.post,
        )
        Notification.objects.create(
            recipient=self.recipient,
            actor=actor_two,
            kind=Notification.Kind.LIKE,
            post=self.post,
        )

        self.client.force_login(self.recipient)
        response = self.client.get(reverse("socialmanager:notifications"))

        grouped_notification = response.context["grouped_notifications"][0]
        self.assertEqual(grouped_notification.actor_count, 2)
        self.assertContains(response, "actor_two and actor liked your post")
        self.assertContains(response, "Published post")
        self.assertNotContains(response, "actor and actor")

    def test_notifications_group_post_kind_aliases_by_same_post(self):
        actor_two = User.objects.create_user(username="actor_two", password="password12345")
        Notification.objects.create(
            recipient=self.recipient,
            actor=self.actor,
            kind="post_like",
            post=self.post,
        )
        Notification.objects.create(
            recipient=self.recipient,
            actor=actor_two,
            kind=Notification.Kind.LIKE,
            post=self.post,
        )

        self.client.force_login(self.recipient)
        response = self.client.get(reverse("socialmanager:notifications"))

        grouped_notifications = response.context["grouped_notifications"]
        self.assertEqual(len(grouped_notifications), 1)
        self.assertEqual(grouped_notifications[0].kind, Notification.Kind.LIKE)
        self.assertEqual(grouped_notifications[0].target_content_type, "post")
        self.assertEqual(grouped_notifications[0].target_object_id, self.post.pk)
        self.assertContains(response, "actor_two and actor liked your post")
        self.assertContains(response, "Published post")

    def test_notifications_do_not_group_different_types_for_same_post(self):
        Notification.objects.create(
            recipient=self.recipient,
            actor=self.actor,
            kind=Notification.Kind.LIKE,
            post=self.post,
        )
        Notification.objects.create(
            recipient=self.recipient,
            actor=self.actor,
            kind=Notification.Kind.SHARE,
            post=self.post,
        )

        self.client.force_login(self.recipient)
        response = self.client.get(reverse("socialmanager:notifications"))

        grouped_notifications = response.context["grouped_notifications"]
        self.assertEqual(len(grouped_notifications), 2)
        self.assertEqual({group.kind for group in grouped_notifications}, {Notification.Kind.LIKE, Notification.Kind.SHARE})

    def test_notifications_group_replies_to_same_comment(self):
        actor_two = User.objects.create_user(username="actor_two", password="password12345")
        parent = PostComment.objects.create(
            post=self.post,
            author=self.recipient,
            body="Original comment",
        )
        first_reply = PostComment.objects.create(
            post=self.post,
            author=self.actor,
            parent=parent,
            body="First reply",
        )
        second_reply = PostComment.objects.create(
            post=self.post,
            author=actor_two,
            parent=parent,
            body="Second reply",
        )
        Notification.objects.create(
            recipient=self.recipient,
            actor=self.actor,
            kind=Notification.Kind.COMMENT_REPLY,
            post=self.post,
            comment=first_reply,
            is_reply=True,
        )
        latest_notification = Notification.objects.create(
            recipient=self.recipient,
            actor=actor_two,
            kind=Notification.Kind.COMMENT_REPLY,
            post=self.post,
            comment=second_reply,
            is_reply=True,
        )

        self.client.force_login(self.recipient)
        response = self.client.get(reverse("socialmanager:notifications"))

        grouped_notifications = response.context["grouped_notifications"]
        self.assertEqual(len(grouped_notifications), 1)
        self.assertEqual(grouped_notifications[0].target_content_type, "comment")
        self.assertEqual(grouped_notifications[0].target_object_id, parent.pk)
        self.assertContains(response, '<span class="notification-message">actor_two and actor replied to</span>', html=True)
        self.assertContains(
            response,
            f'<a class="notification-target" href="{reverse("socialmanager:notification_open", args=[latest_notification.pk])}">your comment</a>',
            html=True,
        )

    def test_open_grouped_notification_marks_related_notifications_read(self):
        first_notification = Notification.objects.create(
            recipient=self.recipient,
            actor=self.actor,
            kind=Notification.Kind.LIKE,
            post=self.post,
        )
        actor_two = User.objects.create_user(username="actor_two", password="password12345")
        latest_notification = Notification.objects.create(
            recipient=self.recipient,
            actor=actor_two,
            kind=Notification.Kind.LIKE,
            post=self.post,
        )

        self.client.force_login(self.recipient)
        response = self.client.get(
            reverse("socialmanager:notification_open", args=[latest_notification.pk])
        )

        self.assertRedirects(response, reverse("socialmanager:post_detail", args=[self.post.pk, self.post.slug]))
        first_notification.refresh_from_db()
        latest_notification.refresh_from_db()
        self.assertTrue(first_notification.is_read)
        self.assertTrue(latest_notification.is_read)

    def test_reply_creates_child_comment_for_parent(self):
        parent = PostComment.objects.create(
            post=self.post,
            author=self.recipient,
            body="Original comment",
        )
        self.client.force_login(self.actor)

        response = self.client.post(
            reverse("socialmanager:post_detail", args=[self.post.pk, self.post.slug]),
            {"body": "Reply body", "parent_id": str(parent.pk)},
        )

        self.assertEqual(response.status_code, 302)
        reply = PostComment.objects.get(body="Reply body")
        self.assertEqual(reply.parent, parent)
        self.assertEqual(reply.post, self.post)
        notification = Notification.objects.get(comment=reply)
        self.assertEqual(notification.recipient, self.recipient)

    def test_reply_notification_goes_to_parent_comment_author_only(self):
        comment_author = self.actor
        reply_author = User.objects.create_user(username="reply-author", password="password12345")
        SubscriptionMembership.objects.create(
            subscription=self.subscription,
            user=reply_author,
            role=SubscriptionMembership.Role.STANDARD,
        )
        parent = PostComment.objects.create(
            post=self.post,
            author=comment_author,
            body="Original comment",
        )
        self.client.force_login(reply_author)

        response = self.client.post(
            reverse("socialmanager:post_detail", args=[self.post.pk, self.post.slug]),
            {"body": "Reply body", "parent_id": str(parent.pk)},
        )

        self.assertEqual(response.status_code, 302)
        reply = PostComment.objects.get(body="Reply body")
        notification = Notification.objects.get(comment=reply)
        self.assertEqual(notification.recipient, comment_author)
        self.assertEqual(notification.actor, reply_author)
        self.assertEqual(notification.kind, Notification.Kind.COMMENT_REPLY)
        self.assertFalse(Notification.objects.filter(comment=reply, recipient=self.recipient).exists())

        self.client.force_login(comment_author)
        notification_response = self.client.get(reverse("socialmanager:notifications"))
        self.assertContains(notification_response, f"{reply_author.username}")
        self.assertContains(notification_response, f'<span class="notification-message">{reply_author.username} replied to</span>', html=True)
        self.assertContains(
            notification_response,
            f'<a class="notification-target" href="{reverse("socialmanager:notification_open", args=[notification.pk])}">your comment</a>',
            html=True,
        )
        self.assertNotContains(notification_response, "commented on your post")

    def test_nested_reply_targets_actual_parent_comment(self):
        middle_author = self.actor
        nested_author = User.objects.create_user(username="nested-reply-author", password="password12345")
        SubscriptionMembership.objects.create(
            subscription=self.subscription,
            user=nested_author,
            role=SubscriptionMembership.Role.STANDARD,
        )
        top_level = PostComment.objects.create(
            post=self.post,
            author=self.recipient,
            body="Top-level comment",
        )
        middle_reply = PostComment.objects.create(
            post=self.post,
            author=middle_author,
            parent=top_level,
            body="Reply to top-level",
        )
        self.client.force_login(nested_author)

        response = self.client.post(
            reverse("socialmanager:post_detail", args=[self.post.pk, self.post.slug]),
            {"body": "Reply to middle reply", "parent_id": str(middle_reply.pk)},
        )

        self.assertEqual(response.status_code, 302)
        nested_reply = PostComment.objects.get(body="Reply to middle reply")
        self.assertEqual(nested_reply.parent, middle_reply)
        notification = Notification.objects.get(comment=nested_reply)
        self.assertEqual(notification.recipient, middle_author)
        self.assertNotEqual(notification.recipient, self.recipient)
        self.assertEqual(notification.kind, Notification.Kind.COMMENT_REPLY)

        detail_response = self.client.get(reverse("socialmanager:post_detail", args=[self.post.pk, self.post.slug]))
        self.assertContains(detail_response, "Reply to middle reply")
        self.assertContains(detail_response, f"reply to {middle_author.username}")
        self.assertContains(detail_response, f'data-comment-id="{middle_reply.pk}"')
        self.assertContains(detail_response, f'<input type="hidden" name="parent_id" value="{middle_reply.pk}">', html=True)

    def test_reply_to_own_comment_does_not_create_notification(self):
        parent = PostComment.objects.create(
            post=self.post,
            author=self.actor,
            body="Own parent comment",
        )
        self.client.force_login(self.actor)

        response = self.client.post(
            reverse("socialmanager:post_detail", args=[self.post.pk, self.post.slug]),
            {"body": "Self reply", "parent_id": str(parent.pk)},
        )

        self.assertEqual(response.status_code, 302)
        reply = PostComment.objects.get(body="Self reply")
        self.assertEqual(reply.parent, parent)
        self.assertFalse(Notification.objects.filter(comment=reply).exists())

    def test_each_reply_creates_separate_notification_with_comment_anchor(self):
        comment_author = self.actor
        reply_author = User.objects.create_user(username="multi-reply-author", password="password12345")
        SubscriptionMembership.objects.create(
            subscription=self.subscription,
            user=reply_author,
            role=SubscriptionMembership.Role.STANDARD,
        )
        first_parent = PostComment.objects.create(
            post=self.post,
            author=comment_author,
            body="First parent",
        )
        second_parent = PostComment.objects.create(
            post=self.post,
            author=comment_author,
            body="Second parent",
        )
        self.client.force_login(reply_author)

        self.client.post(
            reverse("socialmanager:post_detail", args=[self.post.pk, self.post.slug]),
            {"body": "First reply", "parent_id": str(first_parent.pk)},
        )
        self.client.post(
            reverse("socialmanager:post_detail", args=[self.post.pk, self.post.slug]),
            {"body": "Second reply", "parent_id": str(second_parent.pk)},
        )

        replies = list(PostComment.objects.filter(body__in=["First reply", "Second reply"]).order_by("body"))
        notifications = Notification.objects.filter(
            recipient=comment_author,
            actor=reply_author,
            kind=Notification.Kind.COMMENT_REPLY,
            is_reply=True,
        )
        self.assertEqual(notifications.count(), 2)
        self.assertEqual(set(notifications.values_list("comment_id", flat=True)), {reply.pk for reply in replies})

        self.client.force_login(comment_author)
        notification_response = self.client.get(reverse("socialmanager:notifications"))
        for notification in notifications:
            self.assertContains(
                notification_response,
                f'<a class="notification-target" href="{reverse("socialmanager:notification_open", args=[notification.pk])}">your comment</a>',
                html=True,
            )

    def test_deleted_reply_notification_is_preserved_with_deleted_message(self):
        parent = PostComment.objects.create(
            post=self.post,
            author=self.actor,
            body="Parent comment",
        )
        reply = PostComment.objects.create(
            post=self.post,
            author=self.recipient,
            parent=parent,
            body="Reply to delete",
        )
        notification = Notification.objects.create(
            recipient=self.actor,
            actor=self.recipient,
            kind=Notification.Kind.COMMENT_REPLY,
            post=self.post,
            comment=reply,
            is_reply=True,
        )

        reply.delete()
        notification.refresh_from_db()

        self.assertIsNone(notification.comment)
        self.assertEqual(notification.post, self.post)
        self.assertTrue(notification.is_reply)

        self.client.force_login(self.actor)
        notification_response = self.client.get(reverse("socialmanager:notifications"))
        self.assertContains(notification_response, "Reply has been deleted")
        self.assertNotContains(notification_response, "data-target-url")
        self.assertNotContains(notification_response, "#comment-")

    def test_deleted_comment_notification_is_preserved_with_deleted_message(self):
        comment = PostComment.objects.create(
            post=self.post,
            author=self.actor,
            body="Comment to delete",
        )
        notification = Notification.objects.create(
            recipient=self.recipient,
            actor=self.actor,
            kind=Notification.Kind.COMMENT,
            post=self.post,
            comment=comment,
            is_reply=False,
        )

        comment.delete()
        notification.refresh_from_db()

        self.assertIsNone(notification.comment)
        self.assertFalse(notification.is_reply)

        self.client.force_login(self.recipient)
        notification_response = self.client.get(reverse("socialmanager:notifications"))
        self.assertContains(notification_response, "Comment has been deleted")

    def test_deleted_comment_like_notification_is_preserved_with_deleted_message(self):
        comment = PostComment.objects.create(
            post=self.post,
            author=self.actor,
            body="Liked comment to delete",
        )
        notification = Notification.objects.create(
            recipient=self.actor,
            actor=self.recipient,
            kind=Notification.Kind.COMMENT_LIKE,
            post=self.post,
            comment=comment,
        )

        comment.delete()
        notification.refresh_from_db()

        self.assertIsNone(notification.comment)
        self.client.force_login(self.actor)
        notification_response = self.client.get(reverse("socialmanager:notifications"))
        self.assertContains(notification_response, "Comment has been deleted")

    def test_post_detail_renders_youtube_style_comment_thread(self):
        parent = PostComment.objects.create(
            post=self.post,
            author=self.recipient,
            body="Original comment",
        )
        PostComment.objects.create(
            post=self.post,
            author=self.actor,
            parent=parent,
            body="Reply body",
        )
        self.client.force_login(self.actor)

        response = self.client.get(reverse("socialmanager:post_detail", args=[self.post.pk, self.post.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="post-comments post-detail-content-shell"')
        self.assertContains(response, 'class="comment-replies"')
        self.assertContains(response, 'data-comment-like-button')
        self.assertContains(response, 'data-comment-reply-toggle')
        self.assertContains(response, 'class="reply-to-label"')
        self.assertContains(response, f"reply to {parent.author.username}")

    def test_post_detail_more_posts_uses_current_post_author_public_posts(self):
        author_other_post = SocialMediaPost.objects.create(
            subscription=self.subscription,
            author=self.recipient,
            title="Author other public post",
            platform=SocialMediaPost.Platform.INSTAGRAM,
            content_format=SocialMediaPost.Format.ARTICLE,
            status=SocialMediaPost.Status.PUBLISHED,
            visibility=SocialMediaPost.Visibility.PUBLIC,
            caption="Another post by the creator",
        )
        SocialMediaPost.objects.create(
            subscription=self.subscription,
            author=self.actor,
            title="Viewer public post",
            platform=SocialMediaPost.Platform.INSTAGRAM,
            content_format=SocialMediaPost.Format.ARTICLE,
            status=SocialMediaPost.Status.PUBLISHED,
            visibility=SocialMediaPost.Visibility.PUBLIC,
            caption="This belongs to the viewer",
        )
        SocialMediaPost.objects.create(
            subscription=self.subscription,
            author=self.recipient,
            title="Author draft post",
            platform=SocialMediaPost.Platform.INSTAGRAM,
            content_format=SocialMediaPost.Format.ARTICLE,
            status=SocialMediaPost.Status.DRAFT,
            visibility=SocialMediaPost.Visibility.PUBLIC,
            caption="Draft post",
        )
        self.client.force_login(self.actor)

        response = self.client.get(reverse("socialmanager:post_detail", args=[self.post.pk, self.post.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual([post.pk for post in response.context["related_posts"]], [author_other_post.pk])

    def test_post_detail_more_posts_empty_state_for_viewer_hides_create_button(self):
        self.client.force_login(self.actor)

        response = self.client.get(reverse("socialmanager:post_detail", args=[self.post.pk, self.post.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No more posts yet")
        self.assertContains(response, "This creator has not shared more posts yet.")
        self.assertNotContains(response, "You have no other posts yet. Share another post when you are ready.")
        self.assertNotContains(response, 'class="creana-empty-actions"')

    def test_post_detail_more_posts_empty_state_for_author_shows_create_button(self):
        self.client.force_login(self.recipient)

        response = self.client.get(reverse("socialmanager:post_detail", args=[self.post.pk, self.post.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No more posts yet")
        self.assertContains(response, "You have no other posts yet. Share another post when you are ready.")
        self.assertContains(response, 'class="creana-empty-actions"')
        self.assertContains(response, reverse("socialmanager:post_create"))

    def test_post_detail_only_replies_show_reply_to_meta(self):
        PostComment.objects.create(
            post=self.post,
            author=self.recipient,
            body="Original comment",
        )
        self.client.force_login(self.actor)

        response = self.client.get(reverse("socialmanager:post_detail", args=[self.post.pk, self.post.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'class="reply-to-label"')

    def test_comment_update_marks_comment_as_edited(self):
        comment = PostComment.objects.create(
            post=self.post,
            author=self.actor,
            body="Original comment",
        )
        self.client.force_login(self.actor)

        response = self.client.post(
            reverse("socialmanager:comment_update", args=[comment.pk]),
            {"body": "Updated comment"},
        )

        self.assertEqual(response.status_code, 302)
        comment.refresh_from_db()
        self.assertTrue(comment.is_edited)

        detail_response = self.client.get(reverse("socialmanager:post_detail", args=[self.post.pk, self.post.slug]))
        self.assertContains(detail_response, 'class="comment-edited-label"')
        self.assertContains(detail_response, "(edited)")

    def test_post_owner_cannot_manage_another_users_comment(self):
        comment = PostComment.objects.create(
            post=self.post,
            author=self.actor,
            body="Actor comment",
        )
        self.client.force_login(self.recipient)

        detail_response = self.client.get(reverse("socialmanager:post_detail", args=[self.post.pk, self.post.slug]))
        edit_response = self.client.post(
            reverse("socialmanager:comment_update", args=[comment.pk]),
            {"body": "Owner edit attempt"},
        )
        delete_response = self.client.post(reverse("socialmanager:comment_delete", args=[comment.pk]))

        self.assertEqual(detail_response.status_code, 200)
        self.assertNotContains(detail_response, f'action="{reverse("socialmanager:comment_update", args=[comment.pk])}"')
        self.assertNotContains(detail_response, f'action="{reverse("socialmanager:comment_delete", args=[comment.pk])}"')
        self.assertEqual(edit_response.status_code, 403)
        self.assertEqual(delete_response.status_code, 403)
        comment.refresh_from_db()
        self.assertEqual(comment.body, "Actor comment")

    def test_post_owner_cannot_manage_another_users_reply(self):
        parent = PostComment.objects.create(
            post=self.post,
            author=self.recipient,
            body="Parent comment",
        )
        reply = PostComment.objects.create(
            post=self.post,
            author=self.actor,
            parent=parent,
            body="Actor reply",
        )
        self.client.force_login(self.recipient)

        detail_response = self.client.get(reverse("socialmanager:post_detail", args=[self.post.pk, self.post.slug]))
        edit_response = self.client.post(
            reverse("socialmanager:comment_update", args=[reply.pk]),
            {"body": "Owner reply edit attempt"},
        )
        delete_response = self.client.post(reverse("socialmanager:comment_delete", args=[reply.pk]))

        self.assertEqual(detail_response.status_code, 200)
        self.assertNotContains(detail_response, f'action="{reverse("socialmanager:comment_update", args=[reply.pk])}"')
        self.assertNotContains(detail_response, f'action="{reverse("socialmanager:comment_delete", args=[reply.pk])}"')
        self.assertEqual(edit_response.status_code, 403)
        self.assertEqual(delete_response.status_code, 403)
        reply.refresh_from_db()
        self.assertEqual(reply.body, "Actor reply")

    def test_comment_author_can_edit_and_delete_own_comment(self):
        comment = PostComment.objects.create(
            post=self.post,
            author=self.actor,
            body="Author comment",
        )
        self.client.force_login(self.actor)

        detail_response = self.client.get(reverse("socialmanager:post_detail", args=[self.post.pk, self.post.slug]))
        edit_response = self.client.post(
            reverse("socialmanager:comment_update", args=[comment.pk]),
            {"body": "Author updated comment"},
        )
        comment.refresh_from_db()
        delete_response = self.client.post(reverse("socialmanager:comment_delete", args=[comment.pk]))

        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(detail_response, f'action="{reverse("socialmanager:comment_update", args=[comment.pk])}"')
        self.assertContains(detail_response, f'action="{reverse("socialmanager:comment_delete", args=[comment.pk])}"')
        self.assertEqual(edit_response.status_code, 302)
        self.assertEqual(comment.body, "Author updated comment")
        self.assertEqual(delete_response.status_code, 302)
        self.assertFalse(PostComment.objects.filter(pk=comment.pk).exists())

    def test_superuser_cannot_manage_another_users_comment_in_frontend(self):
        superuser = User.objects.create_superuser(
            username="comment-superuser",
            email="comment-superuser@example.com",
            password="password12345",
        )
        comment = PostComment.objects.create(
            post=self.post,
            author=self.actor,
            body="Comment for superuser",
        )
        self.client.force_login(superuser)

        detail_response = self.client.get(reverse("socialmanager:post_detail", args=[self.post.pk, self.post.slug]))
        edit_response = self.client.post(
            reverse("socialmanager:comment_update", args=[comment.pk]),
            {"body": "Superuser updated comment"},
        )
        delete_response = self.client.post(reverse("socialmanager:comment_delete", args=[comment.pk]))

        self.assertEqual(detail_response.status_code, 200)
        self.assertNotContains(detail_response, f'action="{reverse("socialmanager:comment_update", args=[comment.pk])}"')
        self.assertNotContains(detail_response, f'action="{reverse("socialmanager:comment_delete", args=[comment.pk])}"')
        self.assertEqual(edit_response.status_code, 403)
        self.assertEqual(delete_response.status_code, 403)
        comment.refresh_from_db()
        self.assertEqual(comment.body, "Comment for superuser")

    def test_superuser_can_manage_comments_in_django_admin(self):
        superuser = User.objects.create_superuser(
            username="comment-admin-superuser",
            email="comment-admin-superuser@example.com",
            password="password12345",
        )
        comment = PostComment.objects.create(
            post=self.post,
            author=self.actor,
            body="Comment for admin",
        )
        self.client.force_login(superuser)

        change_response = self.client.get(f"/admin/socialmanager/postcomment/{comment.pk}/change/")
        delete_response = self.client.get(f"/admin/socialmanager/postcomment/{comment.pk}/delete/")

        self.assertEqual(change_response.status_code, 200)
        self.assertEqual(delete_response.status_code, 200)

    def test_staff_user_cannot_manage_another_users_comment_without_superuser(self):
        staff_user = User.objects.create_user(
            username="comment-staff",
            email="comment-staff@example.com",
            password="password12345",
            is_staff=True,
        )
        SubscriptionMembership.objects.create(
            subscription=self.subscription,
            user=staff_user,
            role=SubscriptionMembership.Role.ADMIN,
        )
        comment = PostComment.objects.create(
            post=self.post,
            author=self.actor,
            body="Comment for staff",
        )
        self.client.force_login(staff_user)

        detail_response = self.client.get(reverse("socialmanager:post_detail", args=[self.post.pk, self.post.slug]))
        edit_response = self.client.post(
            reverse("socialmanager:comment_update", args=[comment.pk]),
            {"body": "Staff edit attempt"},
        )
        delete_response = self.client.post(reverse("socialmanager:comment_delete", args=[comment.pk]))

        self.assertEqual(detail_response.status_code, 200)
        self.assertNotContains(detail_response, f'action="{reverse("socialmanager:comment_update", args=[comment.pk])}"')
        self.assertNotContains(detail_response, f'action="{reverse("socialmanager:comment_delete", args=[comment.pk])}"')
        self.assertEqual(edit_response.status_code, 403)
        self.assertEqual(delete_response.status_code, 403)
        comment.refresh_from_db()
        self.assertEqual(comment.body, "Comment for staff")

    def test_comment_like_toggle_likes_and_unlikes_once_per_user(self):
        comment = PostComment.objects.create(
            post=self.post,
            author=self.recipient,
            body="Original comment",
        )
        self.client.force_login(self.actor)
        url = reverse("socialmanager:comment_like_toggle", args=[comment.pk])

        like_response = self.client.post(url, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        unlike_response = self.client.post(url, HTTP_X_REQUESTED_WITH="XMLHttpRequest")

        self.assertEqual(like_response.status_code, 200)
        self.assertEqual(like_response.json()["active"], True)
        self.assertEqual(like_response.json()["likes_count"], 1)
        self.assertEqual(unlike_response.status_code, 200)
        self.assertEqual(unlike_response.json()["active"], False)
        self.assertEqual(unlike_response.json()["likes_count"], 0)
        self.assertFalse(comment.liked_by.filter(pk=self.actor.pk).exists())
        notification = Notification.objects.get(kind=Notification.Kind.COMMENT_LIKE)
        self.assertEqual(notification.recipient, self.recipient)
        self.assertEqual(notification.actor, self.actor)
        self.assertEqual(notification.post, self.post)
        self.assertEqual(notification.comment, comment)

    def test_comment_like_notification_links_to_comment(self):
        comment = PostComment.objects.create(
            post=self.post,
            author=self.recipient,
            body="Original comment",
        )
        self.client.force_login(self.actor)

        self.client.post(reverse("socialmanager:comment_like_toggle", args=[comment.pk]))
        self.client.force_login(self.recipient)
        notification_response = self.client.get(reverse("socialmanager:notifications"))

        notification = Notification.objects.get(kind=Notification.Kind.COMMENT_LIKE)
        self.assertContains(notification_response, '<span class="notification-message">actor liked</span>', html=True)
        self.assertContains(
            notification_response,
            f'<a class="notification-target" href="{reverse("socialmanager:notification_open", args=[notification.pk])}">your comment</a>',
            html=True,
        )

        open_response = self.client.get(reverse("socialmanager:notification_open", args=[notification.pk]))
        self.assertRedirects(
            open_response,
            f'{reverse("socialmanager:post_detail", args=[self.post.pk, self.post.slug])}#comment-{comment.pk}',
        )
        notification.refresh_from_db()
        self.assertTrue(notification.is_read)

    def test_self_comment_like_does_not_create_notification(self):
        comment = PostComment.objects.create(
            post=self.post,
            author=self.actor,
            body="Own comment",
        )
        self.client.force_login(self.actor)

        response = self.client.post(reverse("socialmanager:comment_like_toggle", args=[comment.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(comment.liked_by.filter(pk=self.actor.pk).exists())
        self.assertFalse(Notification.objects.filter(kind=Notification.Kind.COMMENT_LIKE).exists())

    def test_comment_unlike_does_not_create_notification(self):
        comment = PostComment.objects.create(
            post=self.post,
            author=self.recipient,
            body="Original comment",
        )
        comment.liked_by.add(self.actor)
        self.client.force_login(self.actor)

        response = self.client.post(reverse("socialmanager:comment_like_toggle", args=[comment.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Notification.objects.filter(kind=Notification.Kind.COMMENT_LIKE).exists())

    def test_anonymous_user_cannot_like_comment(self):
        comment = PostComment.objects.create(
            post=self.post,
            author=self.recipient,
            body="Original comment",
        )

        response = self.client.post(reverse("socialmanager:comment_like_toggle", args=[comment.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertFalse(comment.liked_by.exists())

    def test_follow_creates_notification_for_target_user(self):
        self.client.force_login(self.actor)

        response = self.client.post(
            reverse("socialmanager:profile_follow_toggle", args=[self.recipient.pk])
        )

        self.assertEqual(response.status_code, 302)
        notification = Notification.objects.get()
        self.assertEqual(notification.kind, Notification.Kind.FOLLOW)
        self.assertEqual(notification.recipient, self.recipient)
        self.assertEqual(notification.actor, self.actor)

    def test_self_like_does_not_create_notification(self):
        self.client.force_login(self.recipient)

        response = self.client.post(
            reverse("socialmanager:post_engagement_toggle", args=[self.post.pk, "like"])
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Notification.objects.exists())

    def test_mark_all_as_read_updates_current_user_notifications(self):
        other_user = User.objects.create_user(username="other", password="password12345")
        Notification.objects.create(
            recipient=self.recipient,
            actor=self.actor,
            kind=Notification.Kind.FOLLOW,
        )
        other_notification = Notification.objects.create(
            recipient=other_user,
            actor=self.actor,
            kind=Notification.Kind.FOLLOW,
        )
        self.client.force_login(self.recipient)

        response = self.client.post(reverse("socialmanager:notifications_mark_all_read"))

        self.assertEqual(response.status_code, 302)
        self.assertTrue(Notification.objects.get(recipient=self.recipient).is_read)
        self.assertFalse(Notification.objects.get(pk=other_notification.pk).is_read)


class VideoAnalyticsChartTests(TestCase):
    def setUp(self):
        self.author = User.objects.create_user(username="videoauthor", password="password12345")
        self.viewer_one = User.objects.create_user(username="viewerone", password="password12345")
        self.viewer_two = User.objects.create_user(username="viewertwo", password="password12345")
        self.subscription = SaaSSubscription.objects.create(name="Video Workspace", owner=self.author)
        SubscriptionMembership.objects.create(
            subscription=self.subscription,
            user=self.author,
            role=SubscriptionMembership.Role.ADMIN,
        )
        self.post = SocialMediaPost.objects.create(
            subscription=self.subscription,
            author=self.author,
            title="Video analytics post",
            platform=SocialMediaPost.Platform.INSTAGRAM,
            content_format=SocialMediaPost.Format.VIDEO,
            status=SocialMediaPost.Status.PUBLISHED,
            visibility=SocialMediaPost.Visibility.PUBLIC,
            caption="Video body",
        )

    def test_video_engagement_line_uses_per_bucket_intensity(self):
        VideoWatchSession.objects.create(
            post=self.post,
            viewer=self.viewer_one,
            watched_seconds=20,
            video_duration=20,
            watched_percentage=100,
        )
        VideoWatchSession.objects.create(
            post=self.post,
            viewer=self.viewer_two,
            watched_seconds=10,
            video_duration=20,
            watched_percentage=50,
        )
        VideoEngagementEvent.objects.create(
            post=self.post,
            viewer=self.viewer_one,
            kind=VideoEngagementEvent.Kind.LIKE,
            video_second=5,
        )
        VideoEngagementEvent.objects.create(
            post=self.post,
            viewer=self.viewer_two,
            kind=VideoEngagementEvent.Kind.SHARE,
            video_second=15,
        )

        analytics_view = PostAnalyticsView()
        analytics_view.object = self.post
        chart = analytics_view.get_video_insights_chart_data()

        self.assertEqual([point["seconds"] for point in chart["points"]], [0, 5, 10, 15, 20])
        self.assertEqual(
            [point["engagement_intensity"] for point in chart["points"]],
            [0, 100.0, 0.0, 100.0, 0.0],
        )
        self.assertEqual(chart["points"][0]["retention"], 100.0)
        self.assertEqual(chart["points"][3]["retention"], 50.0)
        self.assertEqual([tick["value"] for tick in chart["y_ticks"]], [100, 75, 50, 25, 0])


class UserSettingsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="settings-user", password="password12345")

    def test_user_settings_are_created_with_defaults(self):
        settings_obj = UserSettings.objects.get(user=self.user)

        self.assertEqual(settings_obj.language, UserSettings.Language.ENGLISH)
        self.assertEqual(settings_obj.theme, UserSettings.Theme.LIGHT)
        self.assertTrue(settings_obj.notify_post_like)
        self.assertTrue(settings_obj.notify_post_comment)
        self.assertTrue(settings_obj.notify_post_share)
        self.assertTrue(settings_obj.notify_comment_like)
        self.assertTrue(settings_obj.notify_comment_reply)
        self.assertTrue(settings_obj.notify_follow)
        self.assertEqual(settings_obj.ai_tone, UserSettings.AITone.PROFESSIONAL)
        self.assertEqual(settings_obj.ai_hashtag_count, 5)

    @override_settings(SITE_URL="https://creana.app/")
    def test_settings_page_keeps_language_field_without_duplicate_heading(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("socialmanager:settings"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, '<h2 class="card-title">Language</h2>', html=True)
        self.assertContains(response, '<h2 class="settings-section-title">Language</h2>', html=True)
        self.assertContains(response, '<label class="sr-only" for="id_language">Language</label>', html=True)
        self.assertContains(response, 'name="language"')


@override_settings(SECURE_SSL_REDIRECT=False)
class LandingAuthenticationUITests(TestCase):
    def test_anonymous_landing_keeps_login_and_signup_actions(self):
        response = self.client.get(reverse("socialmanager:landing"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("socialmanager:login"))
        self.assertContains(response, reverse("socialmanager:signup"))
        self.assertContains(response, "Create Account")
        self.assertNotContains(response, "Continue to Creana")
        self.assertNotContains(response, "Go to Feed")

    def test_authenticated_landing_redirects_to_feed(self):
        user = User.objects.create_user(
            username="landing-authenticated",
            email="landing-authenticated@example.com",
            password="password12345",
        )
        self.client.force_login(user)

        response = self.client.get(reverse("socialmanager:landing"))

        self.assertRedirects(
            response,
            reverse("socialmanager:post_list"),
            fetch_redirect_response=False,
        )

    def test_authenticated_user_can_access_normal_authenticated_page(self):
        user = User.objects.create_user(
            username="landing-page-access",
            email="landing-page-access@example.com",
            password="password12345",
        )
        self.client.force_login(user)

        response = self.client.get(reverse("socialmanager:post_list"))

        self.assertEqual(response.status_code, 200)

    def test_logout_flow_still_clears_session_and_returns_to_login(self):
        user = User.objects.create_user(
            username="landing-logout",
            email="landing-logout@example.com",
            password="password12345",
        )
        self.client.force_login(user)

        response = self.client.post(reverse("socialmanager:logout"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("socialmanager:login"))
        self.assertNotIn("_auth_user_id", self.client.session)
        landing_response = self.client.get(reverse("socialmanager:landing"))
        self.assertEqual(landing_response.status_code, 200)


class ProductionTemplateTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="settings-user-extra", password="password12345")

    @override_settings(SITE_URL="https://creana.app/")
    def test_login_metadata_and_static_links_use_production_site_url(self):
        response = self.client.get(reverse("socialmanager:login"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<meta name="robots" content="noindex, nofollow">', html=True)
        self.assertNotContains(response, '<link rel="canonical"')
        self.assertContains(response, '<meta property="og:url" content="https://creana.app/login/">', html=True)
        self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")
        self.assertContains(response, 'rel="icon" type="image/webp"')
        self.assertContains(response, "/static/socialmanager/images/icon.")
        self.assertContains(response, ".webp")
        self.assertContains(response, 'rel="manifest" href="/static/site.')
        self.assertContains(response, ".webmanifest")
        self.assertNotContains(response, "https://creana.app//")

    @override_settings(SITE_URL="", DEBUG=True, ALLOWED_HOSTS=["fallback.example"])
    def test_local_metadata_falls_back_to_request_origin(self):
        response = self.client.get(reverse("socialmanager:login"), HTTP_HOST="fallback.example")

        self.assertContains(response, 'content="http://fallback.example/login/"')

    @override_settings(SITE_URL="", DEBUG=False, ALLOWED_HOSTS=["run-host.example"])
    def test_production_metadata_falls_back_to_canonical_creana_origin(self):
        response = self.client.get(reverse("socialmanager:landing"), HTTP_HOST="run-host.example")

        self.assertContains(response, '<link rel="canonical" href="https://creana.app/">', html=True)
        self.assertNotContains(response, "run-host.example")


@override_settings(SECURE_SSL_REDIRECT=False, SITE_URL="https://creana.app")
class SEOInfrastructureTests(TestCase):
    public_content_routes = (
        "introduction", "community", "privacy", "terms", "ai_policy",
        "features", "ai_features", "pricing", "supported_platforms", "faq",
        "about", "contact", "how_it_works",
    )

    def setUp(self):
        self.author = User.objects.create_user(
            username="seo-author",
            email="seo-author@example.com",
            password="password12345",
        )
        self.subscription = SaaSSubscription.objects.create(
            name="SEO Workspace",
            owner=self.author,
        )
        self.published_post = SocialMediaPost.objects.create(
            subscription=self.subscription,
            author=self.author,
            title="Safe public SEO post",
            platform=SocialMediaPost.Platform.INSTAGRAM,
            content_format=SocialMediaPost.Format.ARTICLE,
            status=SocialMediaPost.Status.PUBLISHED,
            visibility=SocialMediaPost.Visibility.PUBLIC,
            caption="<p>A safe &amp; useful article body for search engines.</p>",
            article_caption="A concise public summary for creators.",
            published_at=timezone.now(),
        )
        self.private_post = SocialMediaPost.objects.create(
            subscription=self.subscription,
            author=self.author,
            title="Private SEO post",
            platform=SocialMediaPost.Platform.INSTAGRAM,
            status=SocialMediaPost.Status.PUBLISHED,
            visibility=SocialMediaPost.Visibility.PRIVATE,
        )
        self.draft_post = SocialMediaPost.objects.create(
            subscription=self.subscription,
            author=self.author,
            title="Draft SEO post",
            platform=SocialMediaPost.Platform.INSTAGRAM,
            status=SocialMediaPost.Status.DRAFT,
            visibility=SocialMediaPost.Visibility.PUBLIC,
        )
        self.scheduled_post = SocialMediaPost.objects.create(
            subscription=self.subscription,
            author=self.author,
            title="Scheduled SEO post",
            platform=SocialMediaPost.Platform.INSTAGRAM,
            status=SocialMediaPost.Status.SCHEDULED,
            visibility=SocialMediaPost.Visibility.PUBLIC,
            scheduled_for=timezone.now() + timedelta(days=2),
        )
        archived_subscription = SaaSSubscription.objects.create(
            name="Archived SEO Workspace",
            owner=self.author,
            is_archived=True,
        )
        self.archived_post = SocialMediaPost.objects.create(
            subscription=archived_subscription,
            author=self.author,
            title="Archived workspace post",
            platform=SocialMediaPost.Platform.INSTAGRAM,
            status=SocialMediaPost.Status.PUBLISHED,
            visibility=SocialMediaPost.Visibility.PUBLIC,
        )

    def test_robots_txt_lists_sitemap_and_private_routes(self):
        response = self.client.get(reverse("robots_txt"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/plain; charset=utf-8")
        self.assertContains(response, "Allow: /")
        self.assertContains(response, "Disallow: /admin/")
        self.assertContains(response, "Disallow: /posts/new/")
        self.assertContains(response, "Sitemap: https://creana.app/sitemap.xml")

    def test_sitemap_contains_public_content_pages_and_only_public_posts(self):
        response = self.client.get(reverse("sitemap_xml"))
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Robots-Tag"], "noindex, follow")
        self.assertIn("https://creana.app/", content)
        for route_name in self.public_content_routes:
            self.assertIn(
                f"https://creana.app{reverse(f'socialmanager:{route_name}')}",
                content,
            )
        self.assertIn(
            f"https://creana.app{reverse('socialmanager:post_detail', args=[self.published_post.pk, self.published_post.slug])}",
            content,
        )
        for post in (self.private_post, self.draft_post, self.scheduled_post, self.archived_post):
            self.assertNotIn(
                reverse("socialmanager:post_detail", args=[post.pk, post.slug]),
                content,
            )
        self.assertNotIn("/dashboard/", content)
        self.assertNotIn("/users/", content)

    def test_public_content_pages_are_anonymous_indexable_and_canonical(self):
        for route_name in self.public_content_routes:
            with self.subTest(route_name=route_name):
                url = reverse(f"socialmanager:{route_name}")
                response = self.client.get(url)

                self.assertEqual(response.status_code, 200)
                self.assertContains(
                    response,
                    '<meta name="robots" content="index, follow">',
                    html=True,
                )
                self.assertContains(response, '<meta name="description"')
                self.assertContains(
                    response,
                    f'<link rel="canonical" href="https://creana.app{url}">',
                    html=True,
                )
                self.assertContains(
                    response,
                    f'<meta property="og:url" content="https://creana.app{url}">',
                    html=True,
                )
                self.assertContains(response, "<h1")

    def test_landing_links_to_pages_and_preserves_hero(self):
        response = self.client.get(reverse("socialmanager:landing"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "socialmanager/partials/public_header.html")
        self.assertTemplateUsed(response, "socialmanager/partials/public_footer.html")
        self.assertContains(response, "Create Smarter.")
        self.assertContains(response, "Analyse Better.")
        self.assertContains(response, "landing-hero__fox")
        for route_name in self.public_content_routes:
            self.assertContains(response, reverse(f"socialmanager:{route_name}"))

    def test_public_pages_render_simplified_navigation_and_grouped_footer(self):
        response = self.client.get(reverse("socialmanager:introduction"))

        self.assertTemplateUsed(response, "socialmanager/partials/public_header.html")
        self.assertTemplateUsed(response, "socialmanager/partials/public_footer.html")
        self.assertContains(response, "data-mega-panel", count=1)
        self.assertContains(response, 'aria-label="Breadcrumb"')
        self.assertContains(response, 'class="public-mobile-nav"')
        self.assertContains(response, "public-mobile-menu-icon--close")
        self.assertContains(response, 'aria-label="Mobile public navigation"')
        self.assertContains(response, 'class="btn btn-primary public-mobile-login"')
        self.assertContains(response, "Home")
        self.assertNotContains(response, "Continue learning about Creana")
        self.assertNotContains(response, "public-related-card")
        self.assertNotContains(response, "public-content-eyebrow")
        for heading in ("Product", "Resources", "Company", "Legal"):
            self.assertContains(response, heading)
        content = response.content.decode()
        self.assertGreater(
            content.rfind("&copy; 2026 Creana. All rights reserved."),
            content.rfind("AI Policy"),
        )
        header = content.split("<main", 1)[0]
        for route_name in self.public_content_routes:
            self.assertIn(reverse(f"socialmanager:{route_name}"), header)
        for group_name in ("Product", "Legal", "Pricing", "FAQ"):
            self.assertIn(group_name, header)
        self.assertEqual(header.count("data-mega-trigger="), 2)
        self.assertEqual(header.count("data-mobile-group-trigger="), 2)
        self.assertIn("public-mega-trigger is-active", header)
        footer = content.split('<footer class="footer public-content-footer">', 1)[1]
        for route_name in self.public_content_routes:
            self.assertIn(reverse(f"socialmanager:{route_name}"), footer)
        self.assertContains(response, "public-content-container")

    def test_faq_answers_are_server_rendered_with_safe_schema(self):
        response = self.client.get(reverse("socialmanager:faq"))

        self.assertContains(response, 'class="public-faq-item"', count=12)
        self.assertContains(response, "Does Creana analyze my Instagram or TikTok account?")
        self.assertContains(
            response,
            "Creana does not currently claim external social-account analytics import.",
        )
        self.assertContains(response, reverse("socialmanager:supported_platforms"))
        self.assertContains(response, '"@type": "FAQPage"')
        self.assertContains(response, '"@type": "BreadcrumbList"')

    def test_specialized_public_pages_render_visible_tables_and_timeline(self):
        platforms = self.client.get(reverse("socialmanager:supported_platforms"))
        pricing = self.client.get(reverse("socialmanager:pricing"))
        how_it_works = self.client.get(reverse("socialmanager:how_it_works"))

        self.assertContains(platforms, "Platform support at a glance")
        self.assertContains(platforms, "Not currently claimed", count=8)
        self.assertContains(pricing, "Community and member access")
        self.assertContains(pricing, "USD $5/month")
        self.assertContains(how_it_works, "public-timeline-step", count=6)
        self.assertNotContains(how_it_works, "Key parts of the Creana experience")

        ai_features = self.client.get(reverse("socialmanager:ai_features"))
        self.assertContains(ai_features, "Which AI tool should I use?")
        self.assertContains(ai_features, "Requires Creana analytics data?")

    @override_settings(USE_GCS=True, GS_QUERYSTRING_AUTH=True)
    def test_public_post_is_anonymously_readable_with_safe_metadata(self):
        url = reverse(
            "socialmanager:post_detail",
            args=[self.published_post.pk, self.published_post.slug],
        )
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<meta name="robots" content="index, follow">', html=True)
        self.assertContains(response, "A concise public summary for creators.")
        self.assertContains(response, f'<link rel="canonical" href="https://creana.app{url}">', html=True)
        self.assertContains(response, '<meta property="og:type" content="article">', html=True)
        self.assertContains(response, "https://creana.app/static/socialmanager/images/icon.")
        self.assertContains(response, ".webp")
        self.assertNotContains(response, "social_posts/")

    def test_non_public_posts_are_not_anonymously_exposed(self):
        for post in (self.private_post, self.draft_post, self.scheduled_post):
            response = self.client.get(
                reverse("socialmanager:post_detail", args=[post.pk, post.slug])
            )
            self.assertEqual(response.status_code, 404)

    def test_private_application_page_is_noindex_without_canonical(self):
        response = self.client.get(reverse("socialmanager:login"))

        self.assertContains(response, '<meta name="robots" content="noindex, nofollow">', html=True)
        self.assertNotContains(response, '<link rel="canonical"')
        self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")


class ProductionSettingsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="settings-user-extra", password="password12345")

    def test_settings_update_api_saves_one_preference(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("socialmanager:settings_update"),
            {"field": "ai_hashtag_count", "value": "3"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])
        settings_obj = UserSettings.objects.get(user=self.user)
        self.assertEqual(settings_obj.ai_hashtag_count, 3)

    def test_settings_update_api_rejects_unknown_setting(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("socialmanager:settings_update"),
            {"field": "theme", "value": "dark"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["success"])

    def test_settings_update_api_limits_hashtag_count_to_five(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("socialmanager:settings_update"),
            {"field": "ai_hashtag_count", "value": "6"},
        )

        self.assertEqual(response.status_code, 400)
        settings_obj = UserSettings.objects.get(user=self.user)
        self.assertEqual(settings_obj.ai_hashtag_count, 5)

    def test_settings_form_displays_legacy_hashtag_count_as_five(self):
        settings_obj = UserSettings.objects.get(user=self.user)
        settings_obj.ai_hashtag_count = 20
        settings_obj.save(update_fields=["ai_hashtag_count"])
        self.client.force_login(self.user)

        response = self.client.get(reverse("socialmanager:settings"))

        self.assertContains(response, '<option value="5" selected>5</option>', html=True)
