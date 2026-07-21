import json

from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.contrib.auth import authenticate
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.forms import PasswordResetForm
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.forms import _unicode_ci_compare
from django.contrib.auth import get_user_model
from django.contrib.auth.models import User
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from allauth.account.models import EmailAddress

from .models import (
    Announcement,
    POST_CAPTION_MAX_LENGTH,
    POST_HASHTAGS_MAX_COUNT,
    POST_TITLE_MAX_LENGTH,
    CAMPAIGN_NAME_MAX_LENGTH,
    USERNAME_MAX_LENGTH,
    PostComment,
    SaaSSubscription,
    SocialMediaCampaign,
    SocialMediaPost,
    UserSettings,
    split_hashtags,
)
from .account_identity import generate_unique_username_from_email, get_active_users_for_email, normalize_email, user_email_exists
from .services.video_metadata import VideoDurationError, validate_video_duration_file
from .utils.html_sanitizer import sanitize_article_html


SUPPORTED_VIDEO_CONTENT_TYPES = {"video/mp4", "video/webm", "video/quicktime"}
SUPPORTED_VIDEO_EXTENSIONS = {".mp4", ".webm", ".mov"}


def apply_character_counter(field, max_length):
    field.max_length = max_length
    field.widget.attrs.setdefault("maxlength", max_length)
    field.widget.attrs.setdefault("data-character-counter", "true")
    field.widget.attrs.setdefault("data-character-counter-max", str(max_length))


def normalize_line_endings(value):
    return (value or "").replace("\r\n", "\n").replace("\r", "\n")


def validate_video_upload(file):
    if not file:
        return
    if not hasattr(file, "content_type"):
        return

    file_name = getattr(file, "name", "").lower()
    content_type = (getattr(file, "content_type", "") or "").lower()
    has_supported_extension = any(file_name.endswith(extension) for extension in SUPPORTED_VIDEO_EXTENSIONS)
    has_supported_type = content_type in SUPPORTED_VIDEO_CONTENT_TYPES or content_type.startswith("video/")

    if not has_supported_extension or not has_supported_type:
        raise ValidationError(_("Upload a supported video file: MP4, WebM, or MOV."))

    max_bytes = getattr(settings, "VIDEO_FORM_UPLOAD_MAX_BYTES", 20 * 1024 * 1024)
    if max_bytes > 0 and getattr(file, "size", 0) > max_bytes:
        max_mb = max_bytes // (1024 * 1024)
        raise ValidationError(
            _("This video is too large. Choose a video smaller than %(max_mb)s MB.")
            % {"max_mb": max_mb}
        )

    try:
        validate_video_duration_file(
            file,
            max_seconds=getattr(settings, "VIDEO_MAX_DURATION_SECONDS", 60),
            tolerance_seconds=getattr(settings, "VIDEO_DURATION_TOLERANCE_SECONDS", 0.05),
        )
    except VideoDurationError as exc:
        raise ValidationError(str(exc)) from exc


class DesignSystemFormMixin:
    def _apply_design_system(self):
        for name, field in self.fields.items():
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput):
                widget.attrs["class"] = "field-checkbox"
            elif isinstance(widget, (forms.Select, forms.SelectMultiple)):
                widget.attrs["class"] = "field-input field-select"
            elif isinstance(widget, forms.FileInput):
                widget.attrs["class"] = "field-input field-file-input"
            elif isinstance(widget, forms.Textarea):
                widget.attrs["class"] = "field-input field-textarea"
                widget.attrs.setdefault("rows", 4)
            else:
                widget.attrs["class"] = "field-input"


class SignUpForm(DesignSystemFormMixin, UserCreationForm):
    duplicate_email_message = _("This email is already registered. Please sign in or use password reset.")

    email = forms.EmailField(label=_("Email"))
    subscription_name = forms.CharField(
        max_length=150,
        help_text=_("This becomes your workspace name."),
    )

    class Meta:
        model = User
        fields = (
            "email",
            "subscription_name",
            "password1",
            "password2",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._apply_design_system()
        self.fields["subscription_name"].initial = self.fields["subscription_name"].initial or "My Workspace"
        self.fields["password1"].label = _("Password")
        self.fields["password2"].label = _("Confirm password")
        self.fields["email"].widget.attrs.update(
            {
                "placeholder": "name@company.com",
                "autocomplete": "email",
            }
        )
        self.fields["subscription_name"].widget.attrs.update(
            {
                "placeholder": _("Workspace name"),
                "autocomplete": "organization",
                "maxlength": self.fields["subscription_name"].max_length,
            }
        )
        self.fields["password1"].widget.attrs.update(
            {
                "placeholder": _("Create a password"),
                "autocomplete": "new-password",
            }
        )
        self.fields["password2"].widget.attrs.update(
            {
                "placeholder": _("Confirm your password"),
                "autocomplete": "new-password",
            }
        )

    def clean_email(self):
        email = normalize_email(self.cleaned_data.get("email"))
        if user_email_exists(email) or EmailAddress.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(self.duplicate_email_message)
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = normalize_email(self.cleaned_data["email"])
        user.username = generate_unique_username_from_email(user.email, exclude_user=user)
        if commit:
            user.save()
        return user


class LoginForm(AuthenticationForm):
    def __init__(self, request=None, *args, **kwargs):
        super().__init__(request, *args, **kwargs)
        self.fields["username"].label = _("Username or email")
        self.fields["username"].widget.attrs.update(
            {
                "class": "form-control",
                "placeholder": _("username or email account"),
                "autocomplete": "username",
            }
        )
        self.fields["password"].widget.attrs.update(
            {
                "class": "form-control",
                "placeholder": _("Enter your password"),
                "autocomplete": "current-password",
            }
        )

    def clean(self):
        username_or_email = self.cleaned_data.get("username")
        password = self.cleaned_data.get("password")

        if username_or_email and password:
            lookup_value = username_or_email.strip()
            if "@" in lookup_value:
                matched_users = list(
                    User.objects.filter(email__iexact=lookup_value)
                    .order_by("id")
                    [:2]
                )
                if len(matched_users) > 1:
                    raise self.get_invalid_login_error()
                matched_user = matched_users[0] if matched_users else None
                if matched_user:
                    username_or_email = matched_user.get_username()
                    self.cleaned_data["username"] = username_or_email

            self.user_cache = authenticate(
                self.request,
                username=username_or_email,
                password=password,
            )
            if self.user_cache is None:
                raise self.get_invalid_login_error()
            self.confirm_login_allowed(self.user_cache)

        return self.cleaned_data


class CreanaPasswordResetForm(PasswordResetForm):
    google_only_message = _("This account uses Google Sign-In. Please continue with Google to access your account.")
    duplicate_email_message = _("Multiple accounts use this email. Please contact support before resetting your password.")

    def clean_email(self):
        email = normalize_email(self.cleaned_data["email"])
        users = get_active_users_for_email(email)
        if len(users) > 1:
            raise forms.ValidationError(self.duplicate_email_message)
        if len(users) == 1 and not users[0].has_usable_password():
            raise forms.ValidationError(self.google_only_message)
        return email

    def get_users(self, email):
        return (
            user
            for user in get_active_users_for_email(email)
            if user.has_usable_password()
            and _unicode_ci_compare(email, getattr(user, get_user_model().get_email_field_name()))
        )

class SaaSSubscriptionForm(DesignSystemFormMixin, forms.ModelForm):
    class Meta:
        model = SaaSSubscription
        fields = ["name", "plan"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._apply_design_system()
        apply_character_counter(self.fields["name"], SaaSSubscription._meta.get_field("name").max_length)


class AnnouncementForm(DesignSystemFormMixin, forms.ModelForm):
    class Meta:
        model = Announcement
        fields = ["title", "content"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._apply_design_system()
        self.fields["title"].label = _("Announcement title")
        apply_character_counter(self.fields["title"], Announcement._meta.get_field("title").max_length)
        self.fields["title"].widget.attrs.setdefault("placeholder", _("Announcement title"))
        self.fields["title"].widget.attrs["class"] = "announcement-form-input"
        self.fields["content"].label = _("Announcement content")
        self.fields["content"].widget.attrs.setdefault("placeholder", _("Write the announcement content."))
        self.fields["content"].widget.attrs["class"] = "announcement-form-input announcement-form-textarea"
        self.fields["content"].widget.attrs.setdefault("rows", 8)


class SocialMediaCampaignForm(DesignSystemFormMixin, forms.ModelForm):
    campaign_posts = forms.CharField(required=False, widget=forms.HiddenInput)

    class Meta:
        model = SocialMediaCampaign
        fields = [
            "name",
            "objective",
            "campaign_posts",
        ]

    def __init__(self, *args, subscription=None, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.subscription = subscription
        self.user = user
        self.post_queryset = SocialMediaPost.objects.none()
        if user and subscription:
            post_filters = Q(author=user)
            if self.instance.pk:
                post_filters |= Q(campaign_groups=self.instance)
            self.post_queryset = (
                SocialMediaPost.objects.filter(subscription=subscription)
                .filter(post_filters)
                .distinct()
                .order_by("-updated_at", "-created_at")
            )
        elif user:
            self.post_queryset = SocialMediaPost.objects.filter(author=user).order_by("-updated_at", "-created_at")
        self.post_options = list(self.post_queryset.only("id", "title"))
        self._apply_design_system()
        self.fields["name"].label = _("Project name")
        apply_character_counter(self.fields["name"], CAMPAIGN_NAME_MAX_LENGTH)
        self.fields["name"].widget.attrs.setdefault("placeholder", _("Spring product launch"))
        self.fields["objective"].label = _("Objective / Goal")
        self.fields["objective"].widget.attrs.setdefault(
            "placeholder",
            _("What you want to achieve in the Project"),
        )
        self.fields["campaign_posts"].label = _("Project posts")
        if not self.is_bound and self.instance.pk:
            selected_post_ids = list(
                self.instance.campaign_posts.all().values_list("pk", flat=True)
            )
            selected_post_ids_json = json.dumps(selected_post_ids)
            self.initial["campaign_posts"] = selected_post_ids_json
            self.fields["campaign_posts"].initial = selected_post_ids_json
        self.fields["campaign_posts"].widget.attrs.setdefault("placeholder", _("Select posts"))

    def clean_campaign_posts(self):
        value = self.cleaned_data.get("campaign_posts") or ""

        try:
            decoded = json.loads(value) if value else []
            candidate_ids = decoded if isinstance(decoded, list) else []
        except (TypeError, json.JSONDecodeError):
            candidate_ids = [item for item in str(value).split(",") if item.strip()]

        post_ids = []
        for candidate_id in candidate_ids:
            try:
                post_id = int(candidate_id)
            except (TypeError, ValueError):
                continue
            if post_id not in post_ids:
                post_ids.append(post_id)

        posts = list(self.post_queryset.filter(pk__in=post_ids))
        if len(posts) != len(post_ids):
            raise ValidationError(_("Select only posts you own."))

        posts_by_id = {post.pk: post for post in posts}
        return [posts_by_id[post_id] for post_id in post_ids if post_id in posts_by_id]

    def save(self, commit=True):
        campaign = super().save(commit=commit)

        if commit and self.subscription and self.user:
            selected_posts = self.cleaned_data.get("campaign_posts") or []
            campaign.campaign_posts.set(selected_posts)

        return campaign


class SocialMediaPostForm(DesignSystemFormMixin, forms.ModelForm):
    ai_topic = forms.CharField(
        max_length=200,
        required=False,
        help_text=_("Used only for caption and hashtag suggestions."),
    )
    ai_tone = forms.CharField(
        max_length=80,
        required=False,
        initial=_("Professional"),
    )

    class Meta:
        model = SocialMediaPost
        fields = [
            "campaign",
            "title",
            "content_format",
            "status",
            "visibility",
            "caption",
            "article_caption",
            "hashtags",
            "image",
            "video_file",
            "video_thumbnail",
            "scheduled_for",
        ]
        widgets = {
            "scheduled_for": forms.DateTimeInput(
                attrs={"type": "datetime-local"},
                format="%Y-%m-%dT%H:%M",
            ),
            "caption": forms.Textarea(attrs={"rows": 6, "maxlength": POST_CAPTION_MAX_LENGTH}),
            "article_caption": forms.Textarea(attrs={"rows": 4, "maxlength": POST_CAPTION_MAX_LENGTH}),
        }

    def __init__(self, *args, subscription=None, user_settings=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._apply_design_system()
        self.fields["campaign"].queryset = (
            subscription.campaigns.all() if subscription else self.fields["campaign"].queryset.none()
        )
        if not self.is_bound and self.instance.pk:
            linked_campaign = self.instance.campaign_groups.first()
            if linked_campaign:
                self.initial["campaign"] = linked_campaign.pk
        self.fields["title"].label = _("Title")
        apply_character_counter(self.fields["title"], POST_TITLE_MAX_LENGTH)
        self.fields["caption"].label = _("Caption")
        self.fields["hashtags"].label = _("Hashtag")
        self.fields["visibility"].label = _("Visibility")
        self.fields["title"].widget.attrs.setdefault("placeholder", _("Optional article title"))
        self.fields["caption"].widget.attrs.setdefault(
            "placeholder",
            _("Write a clear, concise caption for your audience. (up to 250 characters)"),
        )
        apply_character_counter(self.fields["caption"], POST_CAPTION_MAX_LENGTH)
        self.fields["caption"].help_text = _(f"Use {POST_CAPTION_MAX_LENGTH} characters or fewer.")
        self.fields["article_caption"].widget.attrs.setdefault(
            "placeholder",
            _("Write a clear, concise caption for your audience. (up to 250 characters)"),
        )
        apply_character_counter(self.fields["article_caption"], POST_CAPTION_MAX_LENGTH)
        self.fields["article_caption"].help_text = _(f"Use {POST_CAPTION_MAX_LENGTH} characters or fewer.")
        self.fields["article_caption"].label = _("Caption")
        self.fields["hashtags"].widget.attrs.setdefault(
            "placeholder",
            _("#product #update #socialmedia (up to 5 hashtags)"),
        )
        self.fields["hashtags"].widget.attrs.setdefault("data-max-tags", str(POST_HASHTAGS_MAX_COUNT))
        self.fields["hashtags"].help_text = _(f"Add up to {POST_HASHTAGS_MAX_COUNT} hashtags.")
        self.fields["visibility"].widget.attrs.setdefault("aria-label", _("Visibility"))
        self.fields["campaign"].empty_label = _("Unassigned Project")
        self.fields["campaign"].label = _("Project")
        self.fields["status"].widget.attrs.setdefault("aria-label", _("Post status"))
        self.fields["content_format"].widget.attrs.setdefault("aria-label", _("Content format"))
        self.fields["image"].widget.attrs.setdefault("accept", "image/*")
        self.fields["video_file"].widget.attrs.setdefault(
            "accept",
            "video/mp4,video/webm,video/quicktime,video/*",
        )
        self.fields["video_file"].widget.attrs["data-max-bytes"] = str(
            getattr(settings, "VIDEO_FORM_UPLOAD_MAX_BYTES", 20 * 1024 * 1024)
        )
        self.fields["video_file"].widget.attrs["data-direct-upload-max-bytes"] = str(
            getattr(settings, "VIDEO_UPLOAD_MAX_BYTES", 500 * 1024 * 1024)
        )
        self.fields["video_file"].widget.attrs["data-fallback-max-bytes"] = str(
            getattr(settings, "VIDEO_FORM_UPLOAD_MAX_BYTES", 20 * 1024 * 1024)
        )
        self.fields["video_thumbnail"].widget.attrs.setdefault("accept", "image/webp,image/jpeg,image/png,image/*")
        if not self.is_bound and user_settings:
            self.initial.setdefault("ai_tone", user_settings.get_ai_tone_display())
        scheduled_value = self.initial.get("scheduled_for") or getattr(self.instance, "scheduled_for", None)
        if scheduled_value:
            if timezone.is_aware(scheduled_value):
                scheduled_value = timezone.localtime(scheduled_value)
            self.initial["scheduled_for"] = scheduled_value.strftime("%Y-%m-%dT%H:%M")

    def clean_caption(self):
        caption = normalize_line_endings(self.cleaned_data.get("caption"))
        content_format = self.cleaned_data.get("content_format") or self.data.get("content_format")
        if content_format != SocialMediaPost.Format.ARTICLE and len(caption) > POST_CAPTION_MAX_LENGTH:
            raise forms.ValidationError(_(f"Caption cannot exceed {POST_CAPTION_MAX_LENGTH} characters."))
        return caption

    def clean_article_caption(self):
        article_caption = normalize_line_endings(self.cleaned_data.get("article_caption"))
        if len(article_caption) > POST_CAPTION_MAX_LENGTH:
            raise forms.ValidationError(_(f"Caption cannot exceed {POST_CAPTION_MAX_LENGTH} characters."))
        return article_caption

    def clean_hashtags(self):
        hashtags = self.cleaned_data.get("hashtags") or ""
        if len(split_hashtags(hashtags)) > POST_HASHTAGS_MAX_COUNT:
            raise forms.ValidationError(_(f"Add no more than {POST_HASHTAGS_MAX_COUNT} hashtags."))
        return hashtags

    def clean_video_file(self):
        video_file = self.cleaned_data.get("video_file")
        validate_video_upload(video_file)
        return video_file

    def clean_video_thumbnail(self):
        thumbnail = self.cleaned_data.get("video_thumbnail")
        if not thumbnail:
            return thumbnail
        content_type = (getattr(thumbnail, "content_type", "") or "").lower()
        if not content_type.startswith("image/"):
            raise forms.ValidationError(_("Upload a supported image thumbnail."))
        if getattr(thumbnail, "size", 0) > 2 * 1024 * 1024:
            raise forms.ValidationError(_("The video thumbnail is too large. Choose an image smaller than 2 MB."))
        return thumbnail

    def clean(self):
        cleaned_data = super().clean()
        content_format = cleaned_data.get("content_format")

        if content_format != SocialMediaPost.Format.VIDEO:
            cleaned_data["video_file"] = None
            cleaned_data["video_thumbnail"] = None

        if content_format == SocialMediaPost.Format.ARTICLE:
            cleaned_data["caption"] = sanitize_article_html(cleaned_data.get("caption"))

        return cleaned_data


class PostCommentForm(DesignSystemFormMixin, forms.ModelForm):
    class Meta:
        model = PostComment
        fields = ["body"]
        widgets = {
            "body": forms.Textarea(
                attrs={
                    "id": "comment",
                    "placeholder": _("Write a thoughtful reply to this post."),
                    "rows": 4,
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._apply_design_system()
        self.fields["body"].label = _("Add a comment")


class UserSettingsForm(DesignSystemFormMixin, forms.ModelForm):
    class Meta:
        model = UserSettings
        fields = [
            "language",
            "notify_post_like",
            "notify_post_comment",
            "notify_post_share",
            "notify_comment_like",
            "notify_comment_reply",
            "notify_follow",
            "enable_push_notifications",
            "push_likes",
            "push_comments",
            "push_replies",
            "push_shares",
            "push_follows",
            "push_announcements",
            "push_scheduled_post_published",
            "push_scheduled_post_failed",
            "ai_tone",
            "ai_language",
            "ai_hashtag_count",
        ]
        widgets = {
            "ai_hashtag_count": forms.Select(choices=[(1, "1"), (2, "2"), (3, "3"), (4, "4"), (5, "5")]),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        notification_fields = (
            "notify_post_like",
            "notify_post_comment",
            "notify_post_share",
            "notify_comment_like",
            "notify_comment_reply",
            "notify_follow",
            "enable_push_notifications",
            "push_likes",
            "push_comments",
            "push_replies",
            "push_shares",
            "push_follows",
            "push_announcements",
            "push_scheduled_post_published",
            "push_scheduled_post_failed",
        )
        for field_name in notification_fields:
            self.fields[field_name].widget.attrs["data-settings-field"] = field_name
        for field_name in ("language", "ai_tone", "ai_language", "ai_hashtag_count"):
            self.fields[field_name].widget.attrs["data-settings-field"] = field_name
        if self.instance and self.instance.pk and self.instance.ai_hashtag_count > 5:
            self.initial["ai_hashtag_count"] = 5
        self._apply_design_system()

    def clean_ai_hashtag_count(self):
        count = self.cleaned_data["ai_hashtag_count"]
        if count not in {1, 2, 3, 4, 5}:
            raise forms.ValidationError(_("Choose between 1 and 5 hashtags."))
        return count
