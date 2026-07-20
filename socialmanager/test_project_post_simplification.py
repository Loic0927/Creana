from django.contrib.auth.models import User
from django.test import TestCase

from .forms import SocialMediaCampaignForm, SocialMediaPostForm
from .models import SaaSSubscription, SocialMediaCampaign, SocialMediaPost


class ProjectPostSimplificationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="simplified", password="test-password")
        self.workspace = SaaSSubscription.objects.create(name="Workspace", owner=self.user)

    def test_project_form_only_exposes_name_objective_and_posts(self):
        form = SocialMediaCampaignForm(subscription=self.workspace, user=self.user)

        self.assertEqual(set(form.fields), {"name", "objective", "campaign_posts"})
        self.assertEqual(form.fields["objective"].label, "Objective / Goal")
        self.assertEqual(
            form.fields["objective"].widget.attrs["placeholder"],
            "What you want to achieve in the Project",
        )

    def test_project_saves_without_dates_status_or_platforms(self):
        form = SocialMediaCampaignForm(
            data={"name": "Launch", "objective": "Reach customers", "campaign_posts": "[]"},
            subscription=self.workspace,
            user=self.user,
        )
        self.assertTrue(form.is_valid(), form.errors)
        project = form.save(commit=False)
        project.subscription = self.workspace
        project.created_by = self.user
        project.save()

        self.assertEqual(project.platform_focus, [])
        self.assertIsNone(project.start_date)
        self.assertIsNone(project.end_date)

    def test_edit_project_preserves_legacy_hidden_values(self):
        project = SocialMediaCampaign.objects.create(
            subscription=self.workspace,
            created_by=self.user,
            name="Legacy",
            platform_focus=["Instagram"],
            start_date="2026-01-01",
            end_date="2026-02-01",
        )
        form = SocialMediaCampaignForm(
            data={"name": "Updated", "objective": "New goal", "campaign_posts": "[]"},
            instance=project,
            subscription=self.workspace,
            user=self.user,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        project.refresh_from_db()

        self.assertEqual(project.platform_focus, ["Instagram"])
        self.assertEqual(project.start_date.isoformat(), "2026-01-01")
        self.assertEqual(project.end_date.isoformat(), "2026-02-01")

    def test_post_form_omits_platform_and_new_post_can_save_without_it(self):
        form = SocialMediaPostForm(
            data={
                "campaign": "",
                "title": "General post",
                "content_format": SocialMediaPost.Format.ARTICLE,
                "status": SocialMediaPost.Status.DRAFT,
                "visibility": SocialMediaPost.Visibility.PUBLIC,
                "caption": "Caption",
                "article_caption": "",
                "hashtags": "",
                "scheduled_for": "",
            },
            subscription=self.workspace,
        )
        self.assertNotIn("platform", form.fields)
        self.assertTrue(form.is_valid(), form.errors)
        post = form.save(commit=False)
        post.subscription = self.workspace
        post.author = self.user
        post.save()

        self.assertEqual(post.platform, "")

    def test_edit_post_preserves_legacy_platform(self):
        post = SocialMediaPost.objects.create(
            subscription=self.workspace,
            author=self.user,
            title="Legacy post",
            platform=SocialMediaPost.Platform.INSTAGRAM,
            content_format=SocialMediaPost.Format.ARTICLE,
            status=SocialMediaPost.Status.DRAFT,
            visibility=SocialMediaPost.Visibility.PUBLIC,
            caption="Caption",
        )
        form = SocialMediaPostForm(
            data={
                "campaign": "",
                "title": "Updated post",
                "content_format": SocialMediaPost.Format.ARTICLE,
                "status": SocialMediaPost.Status.DRAFT,
                "visibility": SocialMediaPost.Visibility.PUBLIC,
                "caption": "Updated caption",
                "article_caption": "",
                "hashtags": "",
                "scheduled_for": "",
            },
            instance=post,
            subscription=self.workspace,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        post.refresh_from_db()

        self.assertEqual(post.platform, SocialMediaPost.Platform.INSTAGRAM)
