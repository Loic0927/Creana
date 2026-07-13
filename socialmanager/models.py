import logging
import time
from io import BytesIO
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _
from PIL import Image, ImageOps, UnidentifiedImageError, features

USERNAME_MAX_LENGTH = 20
PROFILE_BIO_MAX_LENGTH = 250
PROFILE_LINKS_MAX_COUNT = 5
POST_TITLE_MAX_LENGTH = 50
POST_CAPTION_MAX_LENGTH = 250
POST_HASHTAGS_MAX_COUNT = 5
CAMPAIGN_NAME_MAX_LENGTH = 50
POST_IMAGE_THUMBNAIL_MAX_SIZE = (512, 512)
POST_IMAGE_THUMBNAIL_QUALITY = 70
AVATAR_THUMBNAIL_MAX_SIZE = (128, 128)
AVATAR_THUMBNAIL_QUALITY = 75
logger = logging.getLogger(__name__)


def _log_posts_url_timing(stage, elapsed_seconds, **extra):
    return


class Announcement(models.Model):
    title = models.CharField(_("Title"), max_length=120)
    content = models.TextField(_("Content"))
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="announcements",
        verbose_name=_("Author"),
    )
    is_active = models.BooleanField(_("Active"), default=True)
    created_at = models.DateTimeField(_("Created at"), auto_now_add=True)
    updated_at = models.DateTimeField(_("Updated at"), auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("Announcement")
        verbose_name_plural = _("Announcements")

    def __str__(self):
        return self.title


def split_profile_links(value):
    return [item.strip() for item in (value or "").split("|") if item.strip()]


def split_hashtags(value):
    return [item.strip() for item in (value or "").replace(",", " ").split() if item.strip()]


def normalize_line_endings(value):
    return (value or "").replace("\r\n", "\n").replace("\r", "\n")


class SaaSSubscription(models.Model):
    class Plan(models.TextChoices):
        STARTER = "starter", "Starter"
        PRO = "pro", "Pro"
        ENTERPRISE = "enterprise", "Enterprise"

    name = models.CharField(max_length=150)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="owned_subscriptions",
    )
    plan = models.CharField(max_length=20, choices=Plan.choices, default=Plan.STARTER)
    is_archived = models.BooleanField(default=False)
    archived_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["is_archived", "name"]

    def __str__(self):
        return self.name

    def archive(self):
        self.is_archived = True
        self.archived_at = timezone.now()
        self.save(update_fields=["is_archived", "archived_at", "updated_at"])


class SubscriptionMembership(models.Model):
    class Role(models.TextChoices):
        ADMIN = "admin", "Admin"
        STANDARD = "standard", "Standard user"

    subscription = models.ForeignKey(
        SaaSSubscription,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="subscription_access",
    )
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.STANDARD)
    is_active_member = models.BooleanField(
        _("Active member"),
        default=False,
        help_text=_("Allows this user to access member-only AI tools."),
    )
    stripe_customer_id = models.CharField(max_length=255, blank=True)
    stripe_subscription_id = models.CharField(max_length=255, blank=True, db_index=True)
    stripe_checkout_session_id = models.CharField(max_length=255, blank=True)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("subscription", "user")
        ordering = ["subscription__name", "user__username"]

    def __str__(self):
        return f"{self.user} in {self.subscription} ({self.get_role_display()})"


class UserFollow(models.Model):
    follower = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="following_relationships",
    )
    following = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="follower_relationships",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("follower", "following")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.follower} follows {self.following}"


class HiddenUser(models.Model):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="hidden_users",
    )
    hidden_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="hidden_by_users",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["owner", "hidden_user"], name="unique_hidden_user_owner"),
            models.CheckConstraint(condition=~models.Q(owner=models.F("hidden_user")), name="prevent_self_hidden_user"),
        ]

    def clean(self):
        super().clean()
        if self.owner_id and self.hidden_user_id and self.owner_id == self.hidden_user_id:
            raise ValidationError(_("You cannot hide yourself from your own feed."))

    def __str__(self):
        return f"{self.owner} hides {self.hidden_user}"


class UserProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
    )
    bio = models.TextField(blank=True)
    links = models.TextField(blank=True)
    links_public = models.BooleanField(default=True)
    avatar = models.ImageField(upload_to="profile_avatars/", blank=True, null=True)
    avatar_thumbnail = models.ImageField(upload_to="profile_avatars/thumbnails/", blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["user__username"]

    def __str__(self):
        return f"Profile for {self.user}"

    @property
    def links_list(self):
        return split_profile_links(self.links)

    @property
    def avatar_thumbnail_url(self):
        started_at = time.perf_counter()
        result = ""
        source = "none"
        try:
            if self.avatar_thumbnail and self.avatar_thumbnail.name:
                source = "thumbnail"
                file_field = self.avatar_thumbnail
            elif self.avatar and self.avatar.name:
                source = "avatar"
                file_field = self.avatar
            else:
                return ""

            generate_started_at = time.perf_counter()
            result = file_field.url
            _log_posts_url_timing(
                "UserProfile.avatar_thumbnail_url_generate",
                time.perf_counter() - generate_started_at,
                profile_id=self.pk,
                source=source,
                has_value=1 if result else 0,
            )
            return result
        finally:
            _log_posts_url_timing(
                "UserProfile.avatar_thumbnail_url",
                time.perf_counter() - started_at,
                profile_id=self.pk,
                source=source,
                has_value=1 if result else 0,
            )

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.avatar and not self.avatar_thumbnail:
            try:
                self.generate_avatar_thumbnail()
            except Exception:
                logger.exception("Could not generate avatar thumbnail for user_profile_id=%s", self.pk)

    def generate_avatar_thumbnail(self, force=False):
        if not self.avatar:
            return False
        if self.avatar_thumbnail and not force:
            return False

        try:
            self.avatar.open("rb")
            with Image.open(self.avatar) as source:
                image = ImageOps.exif_transpose(source)
                image.thumbnail(AVATAR_THUMBNAIL_MAX_SIZE, Image.Resampling.LANCZOS)
                if image.mode not in ("RGB", "RGBA"):
                    image = image.convert("RGB")

                output = BytesIO()
                image.save(
                    output,
                    format="WEBP",
                    quality=AVATAR_THUMBNAIL_QUALITY,
                    optimize=True,
                )
        except (OSError, UnidentifiedImageError):
            logger.exception("Could not read avatar image for user_profile_id=%s", self.pk)
            return False
        finally:
            try:
                self.avatar.close()
            except Exception:
                pass

        if self.avatar_thumbnail:
            self.avatar_thumbnail.delete(save=False)

        original_stem = Path(self.avatar.name).stem or "avatar"
        filename = f"{original_stem}-{self.pk}.webp"
        self.avatar_thumbnail.save(filename, ContentFile(output.getvalue()), save=False)
        super().save(update_fields=["avatar_thumbnail"])
        return True

    def clean(self):
        super().clean()
        errors = {}
        self.bio = normalize_line_endings(self.bio)
        if len(self.bio or "") > PROFILE_BIO_MAX_LENGTH:
            errors["bio"] = _(f"Bio must be {PROFILE_BIO_MAX_LENGTH} characters or fewer.")
        if len(self.links_list) > PROFILE_LINKS_MAX_COUNT:
            errors["links"] = _(f"Add no more than {PROFILE_LINKS_MAX_COUNT} profile links.")
        if errors:
            raise ValidationError(errors)


class UserSettings(models.Model):
    class Language(models.TextChoices):
        ENGLISH = "en", _("English")
        TRADITIONAL_CHINESE = "zh-hant", _("Traditional Chinese")

    class Theme(models.TextChoices):
        LIGHT = "light", _("Light")
        DARK = "dark", _("Dark")
        SYSTEM = "system", _("System")

    class AITone(models.TextChoices):
        PROFESSIONAL = "professional", _("Professional")
        FRIENDLY = "friendly", _("Friendly")
        MARKETING = "marketing", _("Marketing")
        CASUAL = "casual", _("Casual")

    class AILanguage(models.TextChoices):
        ENGLISH = "english", _("English")
        TRADITIONAL_CHINESE = "traditional_chinese", _("Traditional Chinese")
        AUTO = "auto", _("Auto Detect")

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="settings",
    )
    language = models.CharField(max_length=20, choices=Language.choices, default=Language.ENGLISH)
    theme = models.CharField(max_length=20, choices=Theme.choices, default=Theme.LIGHT)
    notify_like = models.BooleanField(default=True)
    notify_comment = models.BooleanField(default=True)
    notify_share = models.BooleanField(default=True)
    notify_post_like = models.BooleanField(default=True)
    notify_post_comment = models.BooleanField(default=True)
    notify_post_share = models.BooleanField(default=True)
    notify_comment_like = models.BooleanField(default=True)
    notify_comment_reply = models.BooleanField(default=True)
    notify_follow = models.BooleanField(default=True)
    enable_push_notifications = models.BooleanField(default=True)
    push_likes = models.BooleanField(default=True)
    push_comments = models.BooleanField(default=True)
    push_replies = models.BooleanField(default=True)
    push_shares = models.BooleanField(default=True)
    push_follows = models.BooleanField(default=True)
    push_announcements = models.BooleanField(default=False)
    push_scheduled_post_published = models.BooleanField(default=True)
    push_scheduled_post_failed = models.BooleanField(default=True)
    push_ai_finished = models.BooleanField(default=False)
    ai_tone = models.CharField(max_length=20, choices=AITone.choices, default=AITone.PROFESSIONAL)
    ai_language = models.CharField(max_length=30, choices=AILanguage.choices, default=AILanguage.AUTO)
    ai_hashtag_count = models.PositiveSmallIntegerField(default=5)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["user__username"]

    def __str__(self):
        return f"Settings for {self.user}"

    def allows_notification_kind(self, kind):
        preference_lookup = {
            "like": self.notify_post_like,
            "post_like": self.notify_post_like,
            "comment": self.notify_post_comment,
            "post_comment": self.notify_post_comment,
            "share": self.notify_post_share,
            "post_share": self.notify_post_share,
            "comment_like": self.notify_comment_like,
            "comment_reply": self.notify_comment_reply,
            "follow": self.notify_follow,
        }
        return preference_lookup.get(kind, True)

    def allows_push_kind(self, kind):
        if not self.enable_push_notifications:
            return False
        preference_lookup = {
            "like": self.push_likes,
            "post_like": self.push_likes,
            "comment": self.push_comments,
            "post_comment": self.push_comments,
            "comment_reply": self.push_replies,
            "share": self.push_shares,
            "post_share": self.push_shares,
            "follow": self.push_follows,
            "announcement": self.push_announcements,
            "scheduled_post_published": self.push_scheduled_post_published,
            "scheduled_post_failed": self.push_scheduled_post_failed,
        }
        return preference_lookup.get(kind, True)


class PushSubscription(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="push_subscriptions",
    )
    endpoint = models.URLField(max_length=2000, unique=True)
    p256dh_key = models.CharField(max_length=255)
    auth_key = models.CharField(max_length=255)
    user_agent = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return f"Push subscription for {self.user_id}"


class Notification(models.Model):
    class Kind(models.TextChoices):
        LIKE = "like", "Like"
        SHARE = "share", "Share"
        COMMENT = "comment", "Comment"
        COMMENT_REPLY = "comment_reply", "Comment reply"
        COMMENT_LIKE = "comment_like", "Comment like"
        FOLLOW = "follow", "Follow"

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications_sent",
    )
    kind = models.CharField(max_length=20, choices=Kind.choices)
    post = models.ForeignKey(
        "SocialMediaPost",
        on_delete=models.SET_NULL,
        related_name="notifications",
        blank=True,
        null=True,
    )
    comment = models.ForeignKey(
        "PostComment",
        on_delete=models.SET_NULL,
        related_name="notifications",
        blank=True,
        null=True,
    )
    is_reply = models.BooleanField(default=False)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["recipient", "is_read", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.actor} {self.kind} notification for {self.recipient}"


class SocialMediaCampaign(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        ACTIVE = "active", _("Active")
        COMPLETED = "completed", _("Completed")
        ARCHIVED = "archived", _("Archived")

    subscription = models.ForeignKey(
        SaaSSubscription,
        on_delete=models.CASCADE,
        related_name="campaigns",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="created_campaigns",
    )
    name = models.CharField(max_length=CAMPAIGN_NAME_MAX_LENGTH)
    objective = models.TextField(blank=True)
    platform_focus = models.JSONField(blank=True, default=list)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    campaign_posts = models.ManyToManyField(
        "SocialMediaPost",
        blank=True,
        related_name="campaign_groups",
    )
    start_date = models.DateField(blank=True, null=True)
    end_date = models.DateField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name

    @property
    def platform_focus_list(self):
        platform_options = (
            "TikTok",
            "Instagram",
            "YouTube",
            "Facebook",
            "X / Twitter",
            "Reddit",
        )
        option_lookup = {
            option.lower().replace(" ", "").replace("/", ""): option
            for option in platform_options
        }
        option_lookup["tiktok"] = "TikTok"
        option_lookup["twitter"] = "X / Twitter"
        option_lookup["x"] = "X / Twitter"
        option_lookup["reddit"] = "Reddit"

        if isinstance(self.platform_focus, list):
            raw_platforms = self.platform_focus

        elif isinstance(self.platform_focus, str):
            raw_platforms = self.platform_focus.replace("[", "").replace("]", "").split(",")

        else:
            raw_platforms = []

        platforms = []
        for platform in raw_platforms:
            cleaned = str(platform).replace("[", "").replace("]", "").strip().strip("'\"")
            lookup_key = cleaned.lower().replace(" ", "").replace("/", "")
            canonical = option_lookup.get(lookup_key, cleaned)
            if canonical and canonical.lower() not in {existing.lower() for existing in platforms}:
                platforms.append(canonical)

        return platforms

    @property
    def platform_focus_display(self):
        return ", ".join(self.platform_focus_list)

    @property
    def effective_status(self):
        return self.date_driven_status

    @property
    def date_driven_status(self):
        today = timezone.localdate()
        if self.end_date and today > self.end_date:
            return self.Status.COMPLETED
        if self.start_date and self.end_date and self.start_date <= today <= self.end_date:
            return self.Status.ACTIVE
        return self.Status.DRAFT

    @property
    def effective_status_display(self):
        return self.Status(self.effective_status).label


class SocialMediaPost(models.Model):
    class Platform(models.TextChoices):
        TIKTOK = "tiktok", "TikTok"
        INSTAGRAM = "instagram", "Instagram"
        YOUTUBE = "youtube", "YouTube"
        FACEBOOK = "facebook", "Facebook"
        X = "x", "X / Twitter"
        REDDIT = "reddit", "Reddit"

    class Format(models.TextChoices):
        IMAGE = "image", _("Image")
        VIDEO = "video", _("Video")
        ARTICLE = "article", _("Article")
        CAROUSEL = "carousel", _("Carousel")

    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        SCHEDULED = "scheduled", _("Scheduled")
        PUBLISHED = "published", _("Published")

    class Visibility(models.TextChoices):
        PUBLIC = "public", _("Public")
        PRIVATE = "private", _("Private")

    subscription = models.ForeignKey(
        SaaSSubscription,
        on_delete=models.CASCADE,
        related_name="posts",
    )
    campaign = models.ForeignKey(
        SocialMediaCampaign,
        on_delete=models.SET_NULL,
        related_name="posts",
        blank=True,
        null=True,
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="social_posts",
    )
    title = models.CharField(max_length=POST_TITLE_MAX_LENGTH)
    slug = models.SlugField(max_length=150, blank=True, db_index=True)
    platform = models.CharField(max_length=20, choices=Platform.choices)
    content_format = models.CharField(max_length=20, choices=Format.choices, default=Format.IMAGE)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    visibility = models.CharField(max_length=20, choices=Visibility.choices, default=Visibility.PUBLIC)
    caption = models.TextField(blank=True)
    article_caption = models.TextField(blank=True)
    hashtags = models.CharField(max_length=255, blank=True)
    image = models.ImageField(upload_to="social_posts/", blank=True, null=True)
    video_file = models.FileField(upload_to="social_videos/", blank=True, null=True)
    video_thumbnail = models.ImageField(upload_to="post_video_thumbnails/", blank=True, null=True)
    likes_count = models.PositiveIntegerField(default=0)
    shares_count = models.PositiveIntegerField(default=0)
    scheduled_for = models.DateTimeField(blank=True, null=True)
    published_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title)
            if not self.slug and self.pk:
                self.slug = f"post-{self.pk}"

            update_fields = kwargs.get("update_fields")
            if self.slug and update_fields is not None:
                kwargs["update_fields"] = set(update_fields) | {"slug"}

        super().save(*args, **kwargs)

        if not self.slug:
            self.slug = f"post-{self.pk}"
            type(self).objects.filter(pk=self.pk, slug="").update(slug=self.slug)

    def clean(self):
        super().clean()
        errors = {}
        self.caption = normalize_line_endings(self.caption)
        self.article_caption = normalize_line_endings(self.article_caption)
        if self.content_format == self.Format.ARTICLE:
            if len(self.article_caption or "") > POST_CAPTION_MAX_LENGTH:
                errors["article_caption"] = _(f"Caption cannot exceed {POST_CAPTION_MAX_LENGTH} characters.")
        elif len(self.caption or "") > POST_CAPTION_MAX_LENGTH:
            errors["caption"] = _(f"Caption cannot exceed {POST_CAPTION_MAX_LENGTH} characters.")
        if len(split_hashtags(self.hashtags)) > POST_HASHTAGS_MAX_COUNT:
            errors["hashtags"] = _(f"Add no more than {POST_HASHTAGS_MAX_COUNT} hashtags.")
        if errors:
            raise ValidationError(errors)

    @property
    def is_private(self):
        return self.visibility == self.Visibility.PRIVATE

    @property
    def profile_filter_status(self):
        if self.is_private:
            return "private"
        return self.status

    @property
    def status_badge_class(self):
        return "private" if self.is_private else self.status

    @property
    def status_badge_label(self):
        return "Private" if self.is_private else self.get_status_display()

    @property
    def comment_count(self):
        return getattr(self, "comments_count", self.comments.count())

    def _first_ordered_image(self):
        prefetched_images = getattr(self, "ordered_images", None)
        if prefetched_images is not None:
            return prefetched_images[0] if prefetched_images else None
        return self.images.order_by("order", "created_at", "pk").first()

    def _post_image_for_primary_image(self):
        if not self.image:
            return None
        prefetched_images = getattr(self, "ordered_images", None)
        if prefetched_images is not None:
            return next(
                (
                    post_image
                    for post_image in prefetched_images
                    if post_image.image and post_image.image.name == self.image.name
                ),
                None,
            )
        return self.images.filter(image=self.image.name).order_by("order", "created_at", "pk").first()

    def _first_ordered_image_with_thumbnail(self):
        prefetched_images = getattr(self, "ordered_images", None)
        if prefetched_images is not None:
            return next(
                (post_image for post_image in prefetched_images if post_image.thumbnail),
                None,
            )
        return (
            self.images.exclude(thumbnail__isnull=True)
            .exclude(thumbnail="")
            .order_by("order", "created_at", "pk")
            .first()
        )

    @property
    def primary_image_url(self):
        if hasattr(self, "_cached_primary_image_url"):
            return self._cached_primary_image_url
        started_at = time.perf_counter()
        result = ""
        source = "none"
        file_name = ""
        try:
            if self.content_format == self.Format.VIDEO:
                return ""

            if self.content_format == self.Format.CAROUSEL:
                first_thumbnail_image = self._first_ordered_image_with_thumbnail()
                if first_thumbnail_image and first_thumbnail_image.thumbnail:
                    source = "first_thumbnail"
                    file_name = first_thumbnail_image.thumbnail.name
                    result = first_thumbnail_image.thumbnail.url
                    return result
                first_image = self._first_ordered_image()
                if first_image and first_image.image:
                    source = "fallback_original"
                    file_name = first_image.image.name
                    result = first_image.image.url
                    return result
                return ""

            primary_post_image = self._post_image_for_primary_image()
            if primary_post_image and primary_post_image.thumbnail:
                source = "primary_thumbnail"
                file_name = primary_post_image.thumbnail.name
                result = primary_post_image.thumbnail.url
                return result
            first_thumbnail_image = self._first_ordered_image_with_thumbnail()
            if first_thumbnail_image and first_thumbnail_image.thumbnail:
                source = "first_thumbnail"
                file_name = first_thumbnail_image.thumbnail.name
                result = first_thumbnail_image.thumbnail.url
                return result
            first_image = self._first_ordered_image()
            if primary_post_image and primary_post_image.image:
                source = "fallback_original"
                file_name = primary_post_image.image.name
                result = primary_post_image.image.url
                return result
            if self.image:
                source = "fallback_original"
                file_name = self.image.name
                result = self.image.url
                return result
            if first_image and first_image.image:
                source = "fallback_original"
                file_name = first_image.image.name
                result = first_image.image.url
                return result
            return ""
        finally:
            _log_posts_url_timing(
                "SocialMediaPost.primary_image_url",
                time.perf_counter() - started_at,
                post_id=self.pk,
                source=source,
                file_name=file_name,
                has_value=1 if result else 0,
            )

    @property
    def primary_original_image_url(self):
        if hasattr(self, "_cached_primary_original_image_url"):
            return self._cached_primary_original_image_url
        started_at = time.perf_counter()
        result = ""
        source = "none"
        try:
            if self.content_format == self.Format.VIDEO:
                return ""
            if self.content_format == self.Format.CAROUSEL:
                first_image = self._first_ordered_image()
                if first_image and first_image.image:
                    source = "carousel_image"
                    result = first_image.image.url
                    return result
                return ""
            if self.image:
                source = "legacy_image"
                result = self.image.url
                return result
            first_image = self._first_ordered_image()
            if first_image and first_image.image:
                source = "first_image"
                result = first_image.image.url
                return result
            return ""
        finally:
            _log_posts_url_timing(
                "SocialMediaPost.primary_original_image_url",
                time.perf_counter() - started_at,
                post_id=self.pk,
                source=source,
                has_value=1 if result else 0,
            )

    @property
    def video_url(self):
        if hasattr(self, "_cached_video_url"):
            return self._cached_video_url
        if self.video_file:
            return self.video_file.url
        if self.content_format == self.Format.VIDEO and self.image:
            return self.image.url
        return ""

    @property
    def video_thumbnail_url(self):
        if self.video_thumbnail:
            return self.video_thumbnail.url
        return ""

    @property
    def cached_primary_image_url(self):
        if not hasattr(self, "_cached_primary_image_url"):
            self._cached_primary_image_url = self.primary_image_url
        return self._cached_primary_image_url

    @cached_primary_image_url.setter
    def cached_primary_image_url(self, value):
        self._cached_primary_image_url = value or ""

    @property
    def cached_primary_original_image_url(self):
        if not hasattr(self, "_cached_primary_original_image_url"):
            self._cached_primary_original_image_url = self.primary_original_image_url
        return self._cached_primary_original_image_url

    @cached_primary_original_image_url.setter
    def cached_primary_original_image_url(self, value):
        self._cached_primary_original_image_url = value or ""

    @property
    def cached_video_thumbnail_url(self):
        if not hasattr(self, "_cached_video_thumbnail_url"):
            self._cached_video_thumbnail_url = self.video_thumbnail_url
        return self._cached_video_thumbnail_url

    @cached_video_thumbnail_url.setter
    def cached_video_thumbnail_url(self, value):
        self._cached_video_thumbnail_url = value or ""

    @property
    def cached_video_url(self):
        if not hasattr(self, "_cached_video_url"):
            self._cached_video_url = self.video_url
        return self._cached_video_url

    @cached_video_url.setter
    def cached_video_url(self, value):
        self._cached_video_url = value or ""


class PostImage(models.Model):
    post = models.ForeignKey(
        SocialMediaPost,
        on_delete=models.CASCADE,
        related_name="images",
    )
    image = models.ImageField(upload_to="social_posts/", blank=False)
    thumbnail = models.ImageField(upload_to="social_posts/thumbnails/", blank=True, null=True)
    order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["order", "created_at", "pk"]

    def __str__(self):
        return f"Image for {self.post}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.image and not self.thumbnail:
            try:
                self.generate_thumbnail()
            except Exception:
                logger.exception("Could not generate thumbnail for post_image_id=%s", self.pk)

    def generate_thumbnail(self, force=False):
        if not self.image:
            return False
        if self.thumbnail and not force:
            return False

        try:
            self.image.open("rb")
            with Image.open(self.image) as source:
                image = ImageOps.exif_transpose(source)
                image.thumbnail(POST_IMAGE_THUMBNAIL_MAX_SIZE, Image.Resampling.LANCZOS)

                use_webp = features.check("webp")
                output_format = "WEBP" if use_webp else "JPEG"
                extension = "webp" if use_webp else "jpg"
                if output_format == "WEBP" and image.mode not in ("RGB", "RGBA"):
                    image = image.convert("RGB")
                elif output_format == "JPEG" and image.mode != "RGB":
                    image = image.convert("RGB")

                output = BytesIO()
                image.save(
                    output,
                    format=output_format,
                    quality=POST_IMAGE_THUMBNAIL_QUALITY,
                    optimize=True,
                )
        except (OSError, UnidentifiedImageError):
            logger.exception("Could not read source image for post_image_id=%s", self.pk)
            return False
        finally:
            try:
                self.image.close()
            except Exception:
                pass

        if self.thumbnail:
            self.thumbnail.delete(save=False)

        original_stem = Path(self.image.name).stem or "post-image"
        filename = f"{original_stem}-{self.pk}.{extension}"
        self.thumbnail.save(filename, ContentFile(output.getvalue()), save=False)
        super().save(update_fields=["thumbnail"])
        return True


class PostMetric(models.Model):
    post = models.ForeignKey(
        SocialMediaPost,
        on_delete=models.CASCADE,
        related_name="metrics",
    )
    captured_at = models.DateTimeField(default=timezone.now)
    impressions = models.PositiveIntegerField(default=0)
    clicks = models.PositiveIntegerField(default=0)
    likes = models.PositiveIntegerField(default=0)
    comments = models.PositiveIntegerField(default=0)
    shares = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-captured_at"]

    def __str__(self):
        return f"Metrics for {self.post} at {self.captured_at:%Y-%m-%d %H:%M}"


class PostView(models.Model):
    post = models.ForeignKey(
        SocialMediaPost,
        on_delete=models.CASCADE,
        related_name="views",
    )
    viewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="post_views",
    )
    viewed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["post", "viewer"], name="unique_post_viewer"),
        ]
        ordering = ["-viewed_at"]

    def __str__(self):
        return f"{self.viewer} viewed {self.post}"


class VideoWatchSession(models.Model):
    post = models.ForeignKey(
        SocialMediaPost,
        on_delete=models.CASCADE,
        related_name="video_watch_sessions",
    )
    viewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="video_watch_sessions",
    )
    watched_seconds = models.PositiveIntegerField(default=0)
    video_duration = models.PositiveIntegerField(default=0)
    watched_percentage = models.FloatField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["post", "viewer", "-updated_at"]),
        ]

    def __str__(self):
        return f"{self.viewer} watched {self.post} ({self.watched_percentage:.1f}%)"


class PostEngagement(models.Model):
    class Kind(models.TextChoices):
        LIKE = "like", "Like"
        SHARE = "share", "Share"

    post = models.ForeignKey(
        SocialMediaPost,
        on_delete=models.CASCADE,
        related_name="engagements",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="post_engagements",
    )
    kind = models.CharField(max_length=12, choices=Kind.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("post", "user", "kind")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user} {self.kind} on {self.post}"


class VideoEngagementEvent(models.Model):
    class Kind(models.TextChoices):
        LIKE = "like", "Like"
        COMMENT = "comment", "Comment"
        SHARE = "share", "Share"

    post = models.ForeignKey(
        SocialMediaPost,
        on_delete=models.CASCADE,
        related_name="video_engagement_events",
    )
    viewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="video_engagement_events",
    )
    kind = models.CharField(max_length=12, choices=Kind.choices)
    video_second = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["post", "video_second"]),
        ]

    def __str__(self):
        return f"{self.viewer} {self.kind} at {self.video_second}s on {self.post}"


class PostComment(models.Model):
    post = models.ForeignKey(
        SocialMediaPost,
        on_delete=models.CASCADE,
        related_name="comments",
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="social_post_comments",
    )
    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        related_name="replies",
        blank=True,
        null=True,
    )
    liked_by = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name="liked_comments",
    )
    body = models.TextField()
    is_edited = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"Comment by {self.author} on {self.post}"


class AISuggestionHistory(models.Model):
    subscription = models.ForeignKey(
        SaaSSubscription,
        on_delete=models.CASCADE,
        related_name="ai_suggestions",
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="ai_suggestions",
    )
    topic = models.CharField(max_length=200)
    platform = models.CharField(max_length=20, choices=SocialMediaPost.Platform.choices)
    tone = models.CharField(max_length=80)
    generated_caption = models.TextField()
    generated_hashtags = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"AI suggestion for {self.topic}"


class VideoAnalysis(models.Model):
    class Status(models.TextChoices):
        PROCESSING = "processing", _("Processing")
        SUCCEEDED = "succeeded", _("Succeeded")
        FAILED = "failed", _("Failed")

    post = models.OneToOneField(
        SocialMediaPost,
        on_delete=models.CASCADE,
        related_name="video_analysis",
    )
    source_object_name = models.CharField(max_length=1024)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PROCESSING)
    result = models.JSONField(blank=True, default=dict)
    creator_guidance = models.JSONField(blank=True, default=dict)
    guidance_language = models.CharField(max_length=30, blank=True)
    error_message = models.CharField(max_length=255, blank=True)
    analyzed_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return f"Video analysis for post {self.post_id} ({self.status})"
