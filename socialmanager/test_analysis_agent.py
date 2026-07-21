from pathlib import Path

from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, SimpleTestCase, TestCase
from django.urls import reverse

from .models import PostMetric, PostTrackingSnapshot, SaaSSubscription, SocialMediaPost, SubscriptionMembership, VideoWatchSession


APP_DIR = Path(__file__).resolve().parent


class SharedAnalysisAgentTests(SimpleTestCase):
    def test_dashboard_project_and_post_remove_legacy_insight_cards(self):
        templates = [
            APP_DIR / "templates/socialmanager/dashboard.html",
            APP_DIR / "templates/socialmanager/campaigns/campaign_detail.html",
            APP_DIR / "templates/socialmanager/posts/post_analytics.html",
        ]
        rendered_sources = "\n".join(path.read_text(encoding="utf-8") for path in templates)

        self.assertNotIn("ai-insight-card", rendered_sources)
        self.assertNotIn("ai-insight-toggle", rendered_sources)
        self.assertNotIn("Retention AI Insight", rendered_sources)

    def test_all_analysis_contexts_use_one_partial_and_script(self):
        partial = (APP_DIR / "templates/socialmanager/partials/analysis_agent.html").read_text(encoding="utf-8")
        javascript = (APP_DIR / "static/socialmanager/js/analysis_agent.js").read_text(encoding="utf-8")
        base = (APP_DIR / "templates/socialmanager/base.html").read_text(encoding="utf-8")

        self.assertIn("data-context-type", partial)
        self.assertIn("data-primary-url", partial)
        self.assertIn("data-secondary-url", partial)
        self.assertIn("analysis_agent.js", base)
        self.assertIn('requestJson(root.dataset.secondaryUrl)', javascript)

    def test_launcher_is_transparent_and_agent_is_compact_non_modal_card(self):
        partial = (APP_DIR / "templates/socialmanager/partials/analysis_agent.html").read_text(encoding="utf-8")
        stylesheet = (APP_DIR / "static/socialmanager/css/components/analysis_agent.css").read_text(encoding="utf-8")
        launcher = partial.split('class="analysis-agent-launcher"', 1)[1].split("</button>", 1)[0]

        self.assertIn("agent.PNG", launcher)
        self.assertNotIn("AI Agent</", launcher)
        self.assertIn("background: transparent", stylesheet)
        self.assertNotIn("transform: translateX(100%)", stylesheet)
        self.assertNotIn("border-left:", stylesheet)
        self.assertIn("max-height:", stylesheet)
        self.assertIn("border-radius: 22px", stylesheet)
        self.assertIn('role="dialog"', partial)
        self.assertIn('aria-modal="false"', partial)
        self.assertIn("transform: scale(1.08)", stylesheet)

    def test_header_has_title_close_button_and_no_context_eyebrow(self):
        partial = (APP_DIR / "templates/socialmanager/partials/analysis_agent.html").read_text(encoding="utf-8")
        header = partial.split('<header class="analysis-agent-header">', 1)[1].split("</header>", 1)[0]
        self.assertIn("AI Agent", header)
        self.assertNotIn("agent_context_type", header)
        self.assertNotIn("analysis-agent-context", partial)
        self.assertIn("<button", header)
        self.assertIn("Close AI Agent", header)
        self.assertIn("data-analysis-agent-refresh", partial)

    def test_script_guards_initialisation_reopen_and_stale_responses(self):
        javascript = (APP_DIR / "static/socialmanager/js/analysis_agent.js").read_text(encoding="utf-8")
        self.assertIn("analysisAgentReady", javascript)
        self.assertIn("requestVersion", javascript)
        self.assertIn("window.clearTimeout(closeTimer)", javascript)

    def test_loading_state_has_one_spinner_and_one_visible_live_status(self):
        partial = (APP_DIR / "templates/socialmanager/partials/analysis_agent.html").read_text(encoding="utf-8")
        loading = partial.split('class="analysis-agent-loading"', 1)[1].split("</div>", 1)[0]

        self.assertEqual(loading.count("analysis-agent-spinner"), 1)
        self.assertEqual(loading.count('data-analysis-agent-status'), 1)
        self.assertEqual(loading.count('{% trans "AI Thinking..." %}'), 1)
        self.assertIn('role="status"', loading)
        self.assertIn('aria-live="polite"', loading)
        self.assertIn('aria-atomic="true"', loading)
        self.assertNotIn("Opening...", loading)

    def test_loading_renderer_reuses_nodes_for_all_request_paths(self):
        javascript = (APP_DIR / "static/socialmanager/js/analysis_agent.js").read_text(encoding="utf-8")
        show_loading = javascript.split("function showLoading(label) {", 1)[1].split("}", 1)[0]

        self.assertIn("status.textContent = label", show_loading)
        self.assertNotIn("append", show_loading)
        self.assertNotIn("insertAdjacentHTML", show_loading)
        self.assertNotIn("innerHTML", show_loading)
        self.assertEqual(javascript.count("function showLoading(label)"), 1)
        self.assertIn("showLoading(root.dataset.thinking)", javascript)
        self.assertIn("showLoading(root.dataset.thinking);", javascript)
        self.assertIn('root.querySelector("[data-analysis-agent-retry]")?.addEventListener', javascript)
        self.assertIn('refreshButton?.addEventListener("click", () => loadAnalysis(true))', javascript)
        self.assertIn('url.searchParams.set("force_refresh", "1")', javascript)
        self.assertIn('force ? {cache: "no-store"} : {}', javascript)
        self.assertIn("trackPerformanceButton?.addEventListener", javascript)

    def test_footer_label_is_removed_and_track_action_is_dashboard_only(self):
        partial = (APP_DIR / "templates/socialmanager/partials/analysis_agent.html").read_text(encoding="utf-8")
        self.assertNotIn("Follow-up suggestions", partial)
        self.assertIn('agent_context_type == "dashboard"', partial)
        self.assertIn("Track a post", partial)
        self.assertIn("Refresh analysis", partial)

    def test_picker_uses_accessible_lightweight_cards_and_internal_states(self):
        javascript = (APP_DIR / "static/socialmanager/js/analysis_agent.js").read_text(encoding="utf-8")
        stylesheet = (APP_DIR / "static/socialmanager/css/components/analysis_agent.css").read_text(encoding="utf-8")
        partial = (APP_DIR / "templates/socialmanager/partials/analysis_agent.html").read_text(encoding="utf-8")
        self.assertIn('currentView = "post-picker"', javascript)
        self.assertIn('currentView = "tracking-result"', javascript)
        self.assertIn('image.loading = "lazy"', javascript)
        self.assertIn('card.setAttribute("aria-pressed", "false")', javascript)
        self.assertIn('search.value.trim()', javascript)
        self.assertNotIn("document.createElement(\"video\")", javascript)
        self.assertIn('const label = element("label", "sr-only", gettext("Search posts"))', javascript)
        self.assertIn("label.htmlFor = searchId", javascript)
        self.assertIn('search.placeholder = gettext("Search posts")', javascript)
        self.assertIn('search.type = "text"', javascript)
        self.assertIn('searchButton.type = "button"', javascript)
        self.assertIn('searchButton.setAttribute("aria-label", gettext("Search posts"))', javascript)
        self.assertIn("<svg", javascript)
        self.assertNotIn('search.type = "search"', javascript)
        self.assertNotIn('"analysis-agent-search-label"', javascript)
        self.assertIn("results.append(heading, label, searchGroup", javascript)
        self.assertLess(javascript.index("card.append(image)"), javascript.index('card.append(element("span", "analysis-agent-post-title"'))
        self.assertIn("flex-direction: column", stylesheet)
        self.assertIn(".analysis-agent-post-card {", stylesheet)
        post_list_rule = stylesheet.split(".analysis-agent-post-list {", 1)[1].split("}", 1)[0]
        self.assertNotIn("grid-template-columns", post_list_rule)
        self.assertIn("flex-direction: column", post_list_rule)
        self.assertIn("object-fit: cover", stylesheet)
        self.assertIn("flex-basis: 66px", stylesheet)
        self.assertIn('setFooter(["picker-back", "track-performance"])', javascript)
        self.assertIn('setFooter(["another-post", "dashboard-insight"])', javascript)
        self.assertNotIn("analysis-agent-picker-actions", javascript)
        self.assertNotIn("analysis-agent-result-actions", javascript)
        self.assertIn('data-agent-action="picker-back"', partial)
        self.assertIn('data-agent-action="track-performance" hidden disabled', partial)
        self.assertIn("trackPerformanceButton.disabled = !selectedPost", javascript)
        self.assertNotIn("analysis-agent-picker-actions", stylesheet)
        self.assertNotIn("track-again", javascript)
        self.assertNotIn("Track again", partial)
        self.assertIn('data-agent-action="another-post"', partial)
        self.assertIn('data-agent-action="dashboard-insight"', partial)
        self.assertIn("Performance metrics", partial)
        self.assertIn("ResponsiveContainer", javascript)
        self.assertIn("destroyTrackingChart", javascript)
        self.assertIn('summary.textContent = chartData.map', javascript)
        self.assertLess(javascript.index("trackedSection"), javascript.index("statusSection"))
        self.assertLess(javascript.index("statusSection"), javascript.index("metricsSection"))
        self.assertIn('searchButton.addEventListener("click", submitSearch)', javascript)
        self.assertIn('event.key === "Enter"', javascript)
        self.assertIn("event.preventDefault()", javascript)
        self.assertIn("pickerPage = 1; selectedPost = null", javascript)
        self.assertIn("trackPerformanceButton.disabled = true", javascript)
        self.assertIn("searchController?.abort()", javascript)
        self.assertIn("version !== requestVersion", javascript)
        self.assertNotIn("search.value = \"\"", javascript)
        self.assertIn(".analysis-agent-search-button", stylesheet)


class AnalysisAgentTrackingAPITests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="tracking-owner", password="test-password")
        self.subscription = SaaSSubscription.objects.create(name="Tracking workspace", owner=self.owner)
        SubscriptionMembership.objects.create(
            subscription=self.subscription,
            user=self.owner,
            role=SubscriptionMembership.Role.ADMIN,
            is_active_member=True,
        )
        self.post = SocialMediaPost.objects.create(
            subscription=self.subscription,
            author=self.owner,
            title="Campaign Launch",
            platform=SocialMediaPost.Platform.INSTAGRAM,
            status=SocialMediaPost.Status.PUBLISHED,
        )
        self.client.force_login(self.owner)

    def test_list_is_authenticated_filtered_case_insensitive_and_lightweight(self):
        draft = SocialMediaPost.objects.create(
            subscription=self.subscription, author=self.owner, title="Campaign draft",
            platform=SocialMediaPost.Platform.INSTAGRAM, status=SocialMediaPost.Status.DRAFT,
            caption="private caption", image="social_posts/original.jpg",
        )
        response = self.client.get(reverse("socialmanager:analysis_agent_posts"), {"q": "  campaign LAUNCH  "})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual([item["id"] for item in payload["posts"]], [self.post.pk])
        self.assertNotIn(draft.pk, [item["id"] for item in payload["posts"]])
        self.assertNotIn("caption", payload["posts"][0])
        self.assertNotIn("media_url", payload["posts"][0])

    def test_search_does_not_match_caption_or_hashtags_and_empty_query_is_unfiltered(self):
        hidden_term = SocialMediaPost.objects.create(
            subscription=self.subscription, author=self.owner, title="Ordinary title",
            platform=SocialMediaPost.Platform.INSTAGRAM, status=SocialMediaPost.Status.PUBLISHED,
            caption="needle appears only in caption", hashtags="#needle",
        )
        searched = self.client.get(reverse("socialmanager:analysis_agent_posts"), {"q": " needle "}).json()
        self.assertEqual(searched["posts"], [])
        unfiltered = self.client.get(reverse("socialmanager:analysis_agent_posts"), {"q": "   "}).json()
        self.assertIn(hidden_term.pk, [item["id"] for item in unfiltered["posts"]])

    def test_search_excludes_posts_from_another_workspace(self):
        other = User.objects.create_user(username="search-other-owner")
        other_subscription = SaaSSubscription.objects.create(name="Search Other", owner=other)
        SocialMediaPost.objects.create(
            subscription=other_subscription, author=other, title="Campaign Launch elsewhere",
            platform=SocialMediaPost.Platform.INSTAGRAM, status=SocialMediaPost.Status.PUBLISHED,
        )
        payload = self.client.get(reverse("socialmanager:analysis_agent_posts"), {"q": "campaign launch"}).json()
        self.assertEqual([item["id"] for item in payload["posts"]], [self.post.pk])

    def test_list_paginates_ten_posts(self):
        for index in range(11):
            SocialMediaPost.objects.create(
                subscription=self.subscription, author=self.owner, title=f"Post {index}",
                platform=SocialMediaPost.Platform.INSTAGRAM, status=SocialMediaPost.Status.PUBLISHED,
            )
        first = self.client.get(reverse("socialmanager:analysis_agent_posts")).json()
        second = self.client.get(reverse("socialmanager:analysis_agent_posts"), {"page": 2}).json()
        self.assertEqual(len(first["posts"]), 10)
        self.assertTrue(first["has_next"])
        self.assertGreaterEqual(len(second["posts"]), 1)

    def test_anonymous_and_cross_workspace_access_is_rejected(self):
        self.client.logout()
        response = self.client.get(reverse("socialmanager:analysis_agent_posts"))
        self.assertEqual(response.status_code, 403)
        other = User.objects.create_user(username="other-owner")
        other_subscription = SaaSSubscription.objects.create(name="Other", owner=other)
        other_post = SocialMediaPost.objects.create(
            subscription=other_subscription, author=other, title="Other post",
            platform=SocialMediaPost.Platform.INSTAGRAM, status=SocialMediaPost.Status.PUBLISHED,
        )
        self.client.force_login(self.owner)
        with patch("socialmanager.views._create_tracking_baseline_recommendation") as provider:
            response = self.client.post(reverse("socialmanager:analysis_agent_post_track", args=[other_post.pk]))
        self.assertEqual(response.status_code, 404)
        provider.assert_not_called()

    def test_tracking_requires_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner)
        response = client.post(reverse("socialmanager:analysis_agent_post_track", args=[self.post.pk]))
        self.assertEqual(response.status_code, 403)

    @patch("socialmanager.views._create_tracking_baseline_recommendation", return_value="Ask a direct question.")
    def test_first_tracking_creates_baseline_without_claiming_change(self, _recommendation):
        PostMetric.objects.create(post=self.post, impressions=10, likes=2, comments=1, shares=0)
        response = self.client.post(reverse("socialmanager:analysis_agent_post_track", args=[self.post.pk]))
        self.assertEqual(response.status_code, 200)
        report = response.json()["report"]
        self.assertEqual(report["progress_status"], "baseline_created")
        self.assertIsNone(report["deltas"]["views"])
        self.assertEqual(PostTrackingSnapshot.objects.filter(post=self.post).count(), 1)
        self.assertIn("No previous recommendation", report["previous_recommendation"])
        self.assertEqual(report["what_changed"], "No previous tracking data is available yet.")
        self.assertNotEqual(report["performance_update"], report["what_changed"])
        self.assertIsNone(report["previous_metrics"])
        self.assertEqual(report["snapshot"]["elapsed_display"], "Just created")
        self.assertEqual(report["metrics"]["views"], 10)
        self.assertTrue(report["metric_availability"]["views"])

    @patch("socialmanager.views._create_tracking_baseline_recommendation", return_value="Ask a direct question.")
    def test_follow_up_uses_latest_snapshot_and_backend_deltas(self, _recommendation):
        PostMetric.objects.create(post=self.post, impressions=10, likes=2, comments=1, shares=0)
        self.client.post(reverse("socialmanager:analysis_agent_post_track", args=[self.post.pk]))
        snapshot = PostTrackingSnapshot.objects.get(post=self.post)
        snapshot.captured_at = snapshot.captured_at - __import__("datetime").timedelta(hours=2)
        snapshot.save(update_fields=["captured_at"])
        PostMetric.objects.create(post=self.post, impressions=25, likes=4, comments=5, shares=1)
        response = self.client.post(reverse("socialmanager:analysis_agent_post_track", args=[self.post.pk]))
        report = response.json()["report"]
        self.assertEqual(report["progress_status"], "improved")
        self.assertEqual(report["deltas"]["views"], 15)
        self.assertEqual(report["deltas"]["comments"], 4)
        self.assertEqual(report["previous_metrics"]["views"], 10)
        self.assertNotEqual(report["snapshot"]["baseline_at"], report["snapshot"]["latest_at"])
        self.assertIn(report["progress_status"], {"baseline_created", "not_enough_data", "improved", "stable", "declined"})

    @patch("socialmanager.views._create_tracking_baseline_recommendation", return_value="Use the measured results.")
    def test_zero_metrics_are_distinct_from_unavailable_metrics(self, _recommendation):
        PostMetric.objects.create(post=self.post, impressions=0, likes=0, comments=0, shares=0)
        measured = self.client.post(reverse("socialmanager:analysis_agent_post_track", args=[self.post.pk])).json()["report"]
        self.assertEqual(measured["metrics"]["views"], 0)
        self.assertTrue(measured["metric_availability"]["views"])

        empty_post = SocialMediaPost.objects.create(
            subscription=self.subscription, author=self.owner, title="No analytics",
            platform=SocialMediaPost.Platform.INSTAGRAM, status=SocialMediaPost.Status.PUBLISHED,
        )
        unavailable = self.client.post(reverse("socialmanager:analysis_agent_post_track", args=[empty_post.pk])).json()["report"]
        self.assertEqual(unavailable["metrics"]["views"], 0)
        self.assertFalse(unavailable["metric_availability"]["views"])

    @patch("socialmanager.views._create_tracking_baseline_recommendation", return_value="Use the measured results.")
    def test_negative_and_zero_deltas_and_percentage_points_are_backend_values(self, _recommendation):
        PostTrackingSnapshot.objects.create(
            post=self.post, subscription=self.subscription, created_by=self.owner,
            views=20, likes=3, comments=0, shares=0, engagement_rate=15,
            captured_at=__import__("django.utils.timezone", fromlist=["now"]).now() - __import__("datetime").timedelta(hours=2),
            recommendation="Use the measured results.",
        )
        PostMetric.objects.create(post=self.post, impressions=10, likes=3, comments=0, shares=0)
        report = self.client.post(reverse("socialmanager:analysis_agent_post_track", args=[self.post.pk])).json()["report"]
        self.assertEqual(report["deltas"]["views"], -10)
        self.assertEqual(report["deltas"]["likes"], 0)
        self.assertEqual(report["deltas"]["engagement_rate"], 15)

    @patch("socialmanager.views._create_tracking_baseline_recommendation", return_value="Use the measured results.")
    def test_video_metrics_are_measured_and_non_video_metrics_are_absent(self, _recommendation):
        video = SocialMediaPost.objects.create(
            subscription=self.subscription, author=self.owner, title="Measured video",
            platform=SocialMediaPost.Platform.INSTAGRAM, status=SocialMediaPost.Status.PUBLISHED,
            content_format=SocialMediaPost.Format.VIDEO,
        )
        VideoWatchSession.objects.create(post=video, viewer=self.owner, watched_seconds=8, video_duration=10, watched_percentage=80)
        viewer = User.objects.create_user(username="video-completer")
        VideoWatchSession.objects.create(post=video, viewer=viewer, watched_seconds=10, video_duration=10, watched_percentage=100)
        video_report = self.client.post(reverse("socialmanager:analysis_agent_post_track", args=[video.pk])).json()["report"]
        self.assertEqual(video_report["video_metrics"]["average_watch_seconds"], 9)
        self.assertEqual(video_report["video_metrics"]["completion_rate"], 50)
        self.assertEqual(video_report["video_metrics"]["retention_rate"], 90)
        non_video_report = self.client.post(reverse("socialmanager:analysis_agent_post_track", args=[self.post.pk])).json()["report"]
        self.assertIsNone(non_video_report["video_metrics"])
