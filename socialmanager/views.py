import json
import logging
import re
import stripe
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlparse

from allauth.account.models import EmailAddress
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login, logout
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.auth.models import User
from django.contrib.auth.views import LoginView as DjangoLoginView
from django.contrib.auth.views import PasswordResetView as DjangoPasswordResetView
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from django.core.exceptions import DisallowedHost, ValidationError
from django.core.files.storage import default_storage
from django.db import IntegrityError, transaction
from django.db.models import Case, Count, DateTimeField, Exists, Max, OuterRef, Prefetch, Q, Sum, When
from django.db.models.functions import Coalesce, TruncDate
from django.http import HttpResponseBadRequest, HttpResponseForbidden, HttpResponseRedirect, JsonResponse
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.urls import reverse, reverse_lazy
from django.utils.html import escape
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils import timezone, translation
from django.utils.timesince import timesince
from django.utils.translation import gettext as gettext
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, DeleteView, DetailView, ListView, TemplateView, UpdateView

from .forms import AnnouncementForm, LoginForm, PostCommentForm, SaaSSubscriptionForm, SignUpForm, SocialMediaCampaignForm, SocialMediaPostForm, UserSettingsForm, validate_video_upload
from .models import (
    Announcement,
    POST_CAPTION_MAX_LENGTH,
    POST_HASHTAGS_MAX_COUNT,
    POST_TITLE_MAX_LENGTH,
    PROFILE_BIO_MAX_LENGTH,
    PROFILE_LINKS_MAX_COUNT,
    USERNAME_MAX_LENGTH,
    AISuggestionHistory,
    HiddenUser,
    Notification,
    PostComment,
    PostEngagement,
    PostImage,
    PostView,
    SaaSSubscription,
    SocialMediaCampaign,
    SocialMediaPost,
    SubscriptionMembership,
    UserFollow,
    UserProfile,
    UserSettings,
    VideoEngagementEvent,
    VideoAnalysis,
    VideoWatchSession,
    split_hashtags,
    split_profile_links,
)
from .services.ai_assistant import (
    AI_USAGE_LIMIT_REACHED_MESSAGE,
    AI_USAGE_LIMIT_REACHED_MESSAGE_ZH_HANT,
    AI_TEMPORARILY_UNAVAILABLE_MESSAGE,
    GeminiQuotaError,
    NOT_ENOUGH_DASHBOARD_DATA_MESSAGE,
    _localize_analysis_terms,
    generate_caption_and_hashtags,
    generate_campaign_analysis,
    generate_campaign_rule_based_analysis,
    generate_dashboard_analysis,
    generate_post_field_feedback,
    generate_post_analysis,
    generate_post_rule_based_analysis,
    generate_dashboard_rule_based_analysis,
    generate_video_retention_analysis,
    generate_video_retention_rule_based_analysis,
    generate_video_content_guidance,
    store_suggestion_history,
)
from .seo import clean_meta_description, public_post_metadata
from .services.analytics import get_dashboard_summary, get_recent_analytics_date_range, get_recent_post_metrics
from .services.account_setup import ensure_user_account_setup
from .services.scheduler import validate_schedule
from socialmanager.services.scheduler import publish_due_scheduled_posts
from .services.video_thumbnail import generate_video_thumbnail
from .services.video_intelligence import analyze_gcs_video
from .subscriptions import activate_membership, deactivate_membership_for_subscription, user_has_active_subscription
from .account_identity import normalize_email, user_email_exists
from .utils.html_sanitizer import sanitize_article_html
from .utils.timing import reset_timing_path, set_timing_path


logger = logging.getLogger(__name__)

SUPPORTED_VIDEO_UPLOAD_TYPES = {"video/mp4": ".mp4", "video/webm": ".webm", "video/quicktime": ".mov"}
SUPPORTED_VIDEO_UPLOAD_EXTENSIONS = {".mp4", ".webm", ".mov"}


def _is_posts_timing_request(request):
    return (getattr(request, "path", "") or "").startswith("/posts/")


def _log_posts_timing(request, stage, elapsed_seconds, **extra):
    return


def _cache_post_media_urls(post, *, include_video=True):
    post.cached_primary_image_url = post.primary_image_url
    post.cached_primary_original_image_url = post.primary_original_image_url
    post.cached_video_thumbnail_url = post.video_thumbnail_url
    if include_video:
        post.cached_video_url = post.video_url


def _safe_storage_url(file_field):
    """Return a log-safe storage URL without signed query parameters."""
    try:
        return (file_field.url or "").split("?", 1)[0]
    except Exception:
        return ""


def _log_saved_upload(label, uploaded_file, saved_field):
    logger.info("Saved %s upload", label)


def ai_quota_limit_message(language, retry_delay_seconds=None):
    language_key = (language or "").strip().lower().replace("-", "_").replace(" ", "_")
    use_traditional_chinese = language_key in {"traditional_chinese", "zh_hant", "zh_hant_"}
    if use_traditional_chinese:
        message = AI_USAGE_LIMIT_REACHED_MESSAGE_ZH_HANT
        if retry_delay_seconds is not None:
            message = f"{message} 約 {retry_delay_seconds} 秒後再試。"
        return message
    message = AI_USAGE_LIMIT_REACHED_MESSAGE
    if retry_delay_seconds is not None:
        message = f"{message} Please try again in about {retry_delay_seconds} seconds."
    return message

LEGACY_DEFAULT_PROFILE_BIO = (
    "Social content strategist helping product teams turn launches, tutorials, and campaign moments "
    "into clear short-form stories that audiences actually remember."
)
BIO_PLACEHOLDER_TEXT = "Please insert your bio here (up to 250 characters)"
LEGACY_BIO_PLACEHOLDER_TEXT = "Please insert your bio here"
LINK_PLACEHOLDER_TEXT = "Please add your link here"


def normalize_line_endings(value):
    return (value or "").replace("\r\n", "\n").replace("\r", "\n")


def clean_profile_bio(value):
    value = normalize_line_endings(value).replace(BIO_PLACEHOLDER_TEXT, "").replace(LEGACY_BIO_PLACEHOLDER_TEXT, "").strip()
    if value in {LEGACY_DEFAULT_PROFILE_BIO, BIO_PLACEHOLDER_TEXT, LEGACY_BIO_PLACEHOLDER_TEXT}:
        return ""
    return value


def _is_spam_comment(text):
    value = (text or "").lower()
    spam_markers = (
        "follow me", "check my profile", "dm me", "promo", "discount", "crypto",
        "investment", "earn money", "free followers", "link in bio", "whatsapp",
        "telegram", "onlyfans", "casino", "betting", "loan", "giveaway", "click here",
        "http://", "https://", "www.", ".com", "bit.ly",
    )
    return any(marker in value for marker in spam_markers)


def _is_gibberish_comment(text):
    value = re.sub(r"\s+", "", (text or "").lower())
    if not value:
        return False
    keyboard_mash_markers = ("asdf", "qwert", "zxcv", "wefwef", "lkjlkj", "fafwef")
    if value in {"asdfasdf", "qwerty", "zxcvzxcv", "wefwefwef", "lkjlkjlkj", "123123123"}:
        return True
    if len(value) >= 8 and any(marker in value for marker in keyboard_mash_markers):
        return True
    if re.fullmatch(r"(.)\1{5,}", value):
        return True
    if re.fullmatch(r"(.{1,4})\1{2,}", value):
        return True
    if len(value) >= 12 and value.isascii() and value.isalnum():
        trigrams = [value[index:index + 3] for index in range(len(value) - 2)]
        if trigrams and max(trigrams.count(item) for item in set(trigrams)) >= 3:
            return True
    return False


def _is_meaningful_comment(text):
    value = re.sub(r"\s+", " ", (text or "").strip())
    if not value or _is_spam_comment(value) or _is_gibberish_comment(value):
        return False
    without_tags = re.sub(r"(?:^|\s)[#@][\w.-]+", "", value).strip(" !?.。！？~")
    if not without_tags or not any(char.isalnum() for char in without_tags):
        return False
    normalized = without_tags.lower().strip(" !?.。！？~")
    low_signal = {"ok", "hi", "lol", "haha", "test", "nice", "...", "???", "!!!"}
    return normalized not in low_signal and len(normalized) > 2


def _clean_comments_for_ai(comments, post_author_id, limit=20):
    meaningful = []
    ignored_reasons = {"spam": 0, "gibberish": 0, "low_signal": 0}
    for comment in comments:
        body = re.sub(r"\s+", " ", (comment.body or "").strip())
        if _is_spam_comment(body):
            ignored_reasons["spam"] += 1
            continue
        if _is_gibberish_comment(body):
            ignored_reasons["gibberish"] += 1
            continue
        if not _is_meaningful_comment(body):
            ignored_reasons["low_signal"] += 1
            continue
        meaningful.append(
            {
                "body": body[:280],
                "is_reply": bool(comment.parent_id),
                "author_role": "creator" if comment.author_id == post_author_id else "viewer",
                "like_count": int(getattr(comment, "like_count", 0) or 0),
                "created_at": comment.created_at.isoformat() if comment.created_at else "",
            }
        )
        if len(meaningful) >= limit:
            break
    ignored_count = sum(ignored_reasons.values())
    return {
        "meaningful_comments": meaningful,
        "meaningful_comment_count": len(meaningful),
        "ignored_comment_count": ignored_count,
        "ignored_comment_reasons": ignored_reasons,
        # Backward-compatible keys for existing service helpers.
        "items": meaningful,
        "meaningful_count": len(meaningful),
        "skipped_spam_count": ignored_reasons["spam"],
        "skipped_low_signal_count": ignored_reasons["low_signal"] + ignored_reasons["gibberish"],
    }


def build_creator_context(user):
    profile, _ = UserProfile.objects.get_or_create(user=user)
    recent_posts = list(
        SocialMediaPost.objects.filter(author=user)
        .order_by("-created_at")
        .values("title", "hashtags")[:8]
    )
    recent_hashtags = []
    for post in recent_posts:
        for tag in split_hashtags(post.get("hashtags", "")):
            if tag.lower() not in {existing.lower() for existing in recent_hashtags}:
                recent_hashtags.append(tag)
            if len(recent_hashtags) == POST_HASHTAGS_MAX_COUNT:
                break
        if len(recent_hashtags) == POST_HASHTAGS_MAX_COUNT:
            break

    return {
        "username": user.get_username(),
        "display_name": user.get_full_name().strip(),
        "bio": clean_profile_bio(profile.bio),
        "profile_links": profile.links_list,
        "recent_post_titles": [
            post.get("title")
            for post in recent_posts
            if post.get("title")
        ][:6],
        "recent_hashtags": recent_hashtags,
    }


def with_viewer_engagement_annotations(queryset, user):
    if not user.is_authenticated:
        return queryset.annotate(
            like_count=Count("engagements", filter=Q(engagements__kind=PostEngagement.Kind.LIKE), distinct=True),
            share_count=Count("engagements", filter=Q(engagements__kind=PostEngagement.Kind.SHARE), distinct=True),
        )

    user_likes = PostEngagement.objects.filter(
        post=OuterRef("pk"),
        user=user,
        kind=PostEngagement.Kind.LIKE,
    )
    user_shares = PostEngagement.objects.filter(
        post=OuterRef("pk"),
        user=user,
        kind=PostEngagement.Kind.SHARE,
    )
    return queryset.annotate(
        like_count=Count("engagements", filter=Q(engagements__kind=PostEngagement.Kind.LIKE), distinct=True),
        share_count=Count("engagements", filter=Q(engagements__kind=PostEngagement.Kind.SHARE), distinct=True),
        user_has_liked=Exists(user_likes),
        user_has_shared=Exists(user_shares),
    )


def public_published_posts(queryset=None):
    queryset = queryset if queryset is not None else SocialMediaPost.objects.all()
    return queryset.filter(
        status=SocialMediaPost.Status.PUBLISHED,
        visibility=SocialMediaPost.Visibility.PUBLIC,
    )


def create_notification(recipient, actor, kind, post=None, comment=None):
    if not recipient or not actor or recipient == actor:
        return None
    is_reply = bool(comment and comment.parent_id)
    user_settings, _ = UserSettings.objects.get_or_create(user=recipient)
    if not user_settings.allows_notification_kind(kind):
        return None
    return Notification.objects.create(
        recipient=recipient,
        actor=actor,
        kind=kind,
        post=post,
        comment=comment,
        is_reply=is_reply,
    )


NOTIFICATION_KIND_ALIASES = {
    "post_like": Notification.Kind.LIKE,
    "post_share": Notification.Kind.SHARE,
    "post_comment": Notification.Kind.COMMENT,
}

GROUPED_NOTIFICATION_KINDS = {
    Notification.Kind.LIKE,
    Notification.Kind.SHARE,
    Notification.Kind.COMMENT,
    Notification.Kind.COMMENT_REPLY,
    Notification.Kind.COMMENT_LIKE,
    "post_like",
    "post_share",
    "post_comment",
}

NOTIFICATION_KIND_GROUPS = {
    Notification.Kind.LIKE: [Notification.Kind.LIKE, "post_like"],
    Notification.Kind.SHARE: [Notification.Kind.SHARE, "post_share"],
    Notification.Kind.COMMENT: [Notification.Kind.COMMENT, "post_comment"],
    Notification.Kind.COMMENT_REPLY: [Notification.Kind.COMMENT_REPLY],
    Notification.Kind.COMMENT_LIKE: [Notification.Kind.COMMENT_LIKE],
}


@dataclass
class GroupedNotification:
    group_key: tuple
    latest_notification: Notification
    notifications: list
    actors: list
    actor_count: int
    other_count: int
    kind: str
    target_content_type: str
    target_object_id: int | None
    target_post: SocialMediaPost | None
    target_comment: PostComment | None
    display_prefix: str
    display_text: str
    target_title: str
    target_link_text: str
    target_url: str
    url: str
    latest_created_at: object
    is_unread: bool

    @property
    def actor(self):
        return self.latest_notification.actor


def get_notification_actor_name(user):
    return user.username or user.email


def normalize_notification_kind(kind):
    return NOTIFICATION_KIND_ALIASES.get(kind, kind)


def get_notification_target(notification):
    kind = normalize_notification_kind(notification.kind)
    if kind in {Notification.Kind.LIKE, Notification.Kind.SHARE}:
        return ("post", notification.post_id, notification.post, None)
    if kind == Notification.Kind.COMMENT:
        return ("post", notification.post_id, notification.post, notification.comment)
    if kind == Notification.Kind.COMMENT_LIKE:
        return ("comment", notification.comment_id, notification.post, notification.comment)
    if kind == Notification.Kind.COMMENT_REPLY:
        if notification.comment and notification.comment.parent_id:
            return ("comment", notification.comment.parent_id, notification.post, notification.comment.parent)
        return ("comment", notification.comment_id, notification.post, notification.comment)
    return ("single", notification.pk, notification.post, notification.comment)


def get_notification_group_key(notification):
    kind = normalize_notification_kind(notification.kind)
    target_content_type, target_object_id, _target_post, _target_comment = get_notification_target(notification)
    if notification.kind not in GROUPED_NOTIFICATION_KINDS or target_object_id is None:
        return (notification.recipient_id, "single", "notification", notification.pk)
    return (notification.recipient_id, kind, target_content_type, target_object_id)


def get_notification_group_filter(notification):
    kind = normalize_notification_kind(notification.kind)
    target_content_type, target_object_id, _target_post, _target_comment = get_notification_target(notification)
    kind_filter = Q(kind__in=NOTIFICATION_KIND_GROUPS.get(kind, [notification.kind]))
    base_filter = Q(recipient=notification.recipient) & kind_filter
    if notification.kind not in GROUPED_NOTIFICATION_KINDS or target_object_id is None:
        return Q(recipient=notification.recipient, pk=notification.pk)
    if target_content_type == "post":
        return base_filter & Q(post_id=target_object_id)
    if target_content_type == "comment" and kind == Notification.Kind.COMMENT_REPLY:
        return base_filter & (Q(comment_id=target_object_id) | Q(comment__parent_id=target_object_id))
    if target_content_type == "comment":
        return base_filter & Q(comment_id=target_object_id)
    return Q(recipient=notification.recipient, pk=notification.pk)


def get_notification_url(notification):
    if notification.kind == Notification.Kind.FOLLOW:
        return reverse("socialmanager:public_profile", args=[notification.actor_id])
    if notification.post_id:
        url = reverse(
            "socialmanager:post_detail",
            args=[notification.post_id, notification.post.slug],
        )
        if notification.comment_id:
            url = f"{url}#comment-{notification.comment_id}"
        return url
    return reverse("socialmanager:public_profile", args=[notification.actor_id])


def get_group_actor_names(notifications):
    actors = []
    seen_actor_ids = set()
    for notification in notifications:
        if notification.actor_id in seen_actor_ids:
            continue
        seen_actor_ids.add(notification.actor_id)
        actors.append(notification.actor)
    return actors


def build_grouped_notification_parts(kind, actors, other_count, target_post, target_comment):
    kind = normalize_notification_kind(kind)
    actor_names = [get_notification_actor_name(actor) for actor in actors[:2]]
    if kind == Notification.Kind.COMMENT and target_comment is None:
        return gettext("%(actor)s: Comment has been deleted") % {"actor": actor_names[0]}, "", ""
    elif kind == Notification.Kind.COMMENT_REPLY and target_comment is None:
        return gettext("%(actor)s: Reply has been deleted") % {"actor": actor_names[0]}, "", ""
    elif kind == Notification.Kind.COMMENT_LIKE and target_comment is None:
        return gettext("%(actor)s: Comment has been deleted") % {"actor": actor_names[0]}, "", ""

    template_lookup = {
        Notification.Kind.LIKE: (
            "%(actor)s liked your post",
            "%(actor1)s and %(actor2)s liked your post",
            "%(actor1)s, %(actor2)s, and %(other_count)s others liked your post",
        ),
        Notification.Kind.SHARE: (
            "%(actor)s shared your post",
            "%(actor1)s and %(actor2)s shared your post",
            "%(actor1)s, %(actor2)s, and %(other_count)s others shared your post",
        ),
        Notification.Kind.COMMENT: (
            "%(actor)s commented on your post",
            "%(actor1)s and %(actor2)s commented on your post",
            "%(actor1)s, %(actor2)s, and %(other_count)s others commented on your post",
        ),
        Notification.Kind.COMMENT_REPLY: (
            "%(actor)s replied to",
            "%(actor1)s and %(actor2)s replied to",
            "%(actor1)s, %(actor2)s, and %(other_count)s others replied to",
        ),
        Notification.Kind.COMMENT_LIKE: (
            "%(actor)s liked",
            "%(actor1)s and %(actor2)s liked",
            "%(actor1)s, %(actor2)s, and %(other_count)s others liked",
        ),
        Notification.Kind.FOLLOW: (
            "%(actor)s followed you",
            "%(actor1)s and %(actor2)s followed you",
            "%(actor1)s, %(actor2)s, and %(other_count)s others followed you",
        ),
    }
    templates = template_lookup.get(
        kind,
        (
            "%(actor)s sent you a notification",
            "%(actor1)s and %(actor2)s sent you a notification",
            "%(actor1)s, %(actor2)s, and %(other_count)s others sent you a notification",
        ),
    )

    if len(actor_names) == 1:
        display_prefix = gettext(templates[0]) % {"actor": actor_names[0]}
    elif len(actor_names) == 2 and other_count == 0:
        display_prefix = gettext(templates[1]) % {
            "actor1": actor_names[0],
            "actor2": actor_names[1],
        }
    else:
        display_prefix = gettext(templates[2]) % {
            "actor1": actor_names[0],
            "actor2": actor_names[1],
            "other_count": other_count,
        }
    target_title = target_post.title if kind in {Notification.Kind.LIKE, Notification.Kind.SHARE, Notification.Kind.COMMENT} and target_post else ""
    target_link_text = gettext("your comment") if kind in {Notification.Kind.COMMENT_LIKE, Notification.Kind.COMMENT_REPLY} and target_comment else ""
    return display_prefix, target_title, target_link_text


def build_grouped_notification_text(display_prefix, target_title, target_link_text):
    if target_title:
        return f"{display_prefix} {target_title}"
    if target_link_text:
        return f"{display_prefix} {target_link_text}"
    return display_prefix


def build_grouped_notifications(notifications):
    groups = {}
    for notification in notifications:
        key = get_notification_group_key(notification)
        groups.setdefault(key, []).append(notification)

    grouped_notifications = []
    for group_notifications in groups.values():
        group_notifications = sorted(group_notifications, key=lambda item: item.created_at, reverse=True)
        latest_notification = group_notifications[0]
        kind = normalize_notification_kind(latest_notification.kind)
        target_content_type, target_object_id, target_post, target_comment = get_notification_target(latest_notification)
        actors = get_group_actor_names(group_notifications)
        actor_count = len(actors)
        other_count = max(actor_count - 2, 0)
        display_prefix, target_title, target_link_text = build_grouped_notification_parts(
            kind,
            actors,
            other_count,
            target_post,
            target_comment,
        )
        target_url = reverse("socialmanager:notification_open", args=[latest_notification.pk]) if target_title or target_link_text else ""
        grouped_notifications.append(
            GroupedNotification(
                group_key=get_notification_group_key(latest_notification),
                latest_notification=latest_notification,
                notifications=group_notifications,
                actors=actors,
                actor_count=actor_count,
                other_count=other_count,
                kind=kind,
                target_content_type=target_content_type,
                target_object_id=target_object_id,
                target_post=target_post,
                target_comment=target_comment,
                display_prefix=display_prefix,
                display_text=build_grouped_notification_text(display_prefix, target_title, target_link_text),
                target_title=target_title,
                target_link_text=target_link_text,
                target_url=target_url,
                url=get_notification_url(latest_notification),
                latest_created_at=latest_notification.created_at,
                is_unread=any(not notification.is_read for notification in group_notifications),
            )
        )
    return sorted(grouped_notifications, key=lambda group: group.latest_created_at, reverse=True)


def limit_hashtags_text(hashtags_text, count):
    try:
        count = int(count)
    except (TypeError, ValueError):
        count = 5
    count = min(max(count, 1), 5)
    hashtags = [item for item in (hashtags_text or "").split() if item.strip()]
    return " ".join(hashtags[:count])


def limit_text(value, max_length):
    return (value or "")[:max_length]


def attach_viewer_engagement_state(posts, user):
    post_list = list(posts)
    for post in post_list:
        post.like_count = post.likes_count
        post.share_count = post.shares_count
        post.user_has_liked = False
        post.user_has_shared = False

    if not user.is_authenticated or not post_list:
        return post_list

    post_ids = [post.pk for post in post_list]
    engagements = PostEngagement.objects.filter(
        post_id__in=post_ids,
        user=user,
        kind__in=[PostEngagement.Kind.LIKE, PostEngagement.Kind.SHARE],
    ).values_list("post_id", "kind")
    liked_ids = {post_id for post_id, kind in engagements if kind == PostEngagement.Kind.LIKE}
    shared_ids = {post_id for post_id, kind in engagements if kind == PostEngagement.Kind.SHARE}

    for post in post_list:
        post.user_has_liked = post.pk in liked_ids
        post.user_has_shared = post.pk in shared_ids

    return post_list


def normalize_contact_value(value):
    return (value or "").strip()


def build_count_axis_ticks(max_value, top, plot_height, target_steps=4):
    max_value = max(int(max_value or 0), 0)
    if max_value <= target_steps:
        values = list(range(0, max_value + 1))
    else:
        raw_step = max_value / target_steps
        magnitude = 1
        while magnitude * 10 <= raw_step:
            magnitude *= 10
        step = magnitude
        for multiplier in (1, 2, 5, 10):
            candidate = multiplier * magnitude
            if candidate >= raw_step:
                step = candidate
                break
        axis_max = ((max_value + step - 1) // step) * step
        values = list(range(0, axis_max + 1, step))

    axis_max = max(values[-1] if values else 0, 1)
    ticks = [
        {
            "value": value,
            "y": round(top + (plot_height - ((value / axis_max) * plot_height)), 2),
        }
        for value in reversed(values)
    ]
    return ticks, axis_max


def get_safe_next_url(request):
    next_url = request.POST.get("next") or request.GET.get("next")
    try:
        allowed_hosts = {request.get_host()}
    except DisallowedHost:
        return ""
    if next_url and url_has_allowed_host_and_scheme(
        url=next_url,
        allowed_hosts=allowed_hosts,
        require_https=request.is_secure(),
    ):
        return next_url
    return ""


@login_required
@require_POST
def profile_username_search(request):
    fallback_url = get_safe_next_url(request) or reverse("socialmanager:dashboard")
    username = (request.POST.get("username") or "").strip()

    if not username:
        messages.error(request, _("No user found with that username."))
        return redirect(fallback_url)

    profile_user = User.objects.filter(username__iexact=username).only("pk").first()
    if profile_user:
        return redirect("socialmanager:public_profile", user_id=profile_user.pk)

    messages.error(request, _("No user found with that username."))
    return redirect(fallback_url)


AI_INSIGHT_PLATFORM = SocialMediaPost.Platform.INSTAGRAM
AI_ANALYSIS_CACHE_VERSION = "post_dynamic_sections_v5_video_basis"
CAMPAIGN_AI_CACHE_VERSION = "campaign_strategy_v1"
RETENTION_PROMPT_VERSION = "retention_v2_video_basis_v3"
AI_MEMBERS_ONLY_MESSAGE = _("AI features are available for members only.")


def sanitize_ai_insight_text(value):
    text = str(value or "").strip()
    emoji_pattern = re.compile(
        "["
        "\U0001F1E6-\U0001F1FF"
        "\U0001F300-\U0001F5FF"
        "\U0001F600-\U0001F64F"
        "\U0001F680-\U0001F6FF"
        "\U0001F700-\U0001F77F"
        "\U0001F780-\U0001F7FF"
        "\U0001F800-\U0001F8FF"
        "\U0001F900-\U0001F9FF"
        "\U0001FA70-\U0001FAFF"
        "\u2600-\u27BF"
        "]+",
        flags=re.UNICODE,
    )
    text = emoji_pattern.sub("", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    return re.sub(r"[ \t]+\n", "\n", text).strip()


def parse_ai_insight_sections(report):
    value = report
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped.startswith("{"):
            return []
        try:
            value = json.loads(stripped)
        except json.JSONDecodeError:
            return []
    if not isinstance(value, dict):
        return []
    sections = value.get("sections")
    if not isinstance(sections, list):
        return []
    normalized = []
    for section in sections:
        if not isinstance(section, dict):
            continue
        heading = str(section.get("heading") or "").strip()
        points = section.get("points")
        if not isinstance(points, list):
            continue
        clean_points = [str(point).strip() for point in points if str(point).strip()]
        if heading or clean_points:
            normalized.append({"heading": heading, "points": clean_points})
    return normalized


def render_ai_insight_html(report):
    structured_sections = parse_ai_insight_sections(report)
    if structured_sections:
        html_parts = []
        for section in structured_sections:
            section_html = ['<section class="ai-insight-section">']
            if section["heading"]:
                section_html.append(
                    f'<h3 class="ai-insight-heading">{escape(section["heading"])}</h3>'
                )
            if section["points"]:
                items = "".join(f"<li>{escape(point)}</li>" for point in section["points"])
                section_html.append(f'<ul class="ai-insight-list">{items}</ul>')
            section_html.append("</section>")
            html_parts.append("".join(section_html))
        return "".join(html_parts)

    text = sanitize_ai_insight_text(report)
    if not text:
        return ""

    known_headings = {
        "overall performance",
        "key trends",
        "growth opportunity",
        "suggested improvements",
        "content diagnosis",
        "content understanding",
        "visual and content signals",
        "visual & content analysis",
        "visual and content analysis",
        "audience behaviour",
        "audience behavior",
        "comment analysis",
        "summary",
        "campaign overview",
        "campaign performance",
        "campaign consistency",
        "content consistency",
        "audience response",
        "content strategy",
        "growth opportunities",
        "next campaign recommendation",
        "posting strategy",
        "improvement opportunities",
        "next content recommendation",
        "retention diagnosis",
        "engagement diagnosis",
        "main problems",
        "analysis basis",
        "整體表現",
        "重點趨勢",
        "成長機會",
        "建議改善方向",
        "內容診斷",
        "內容理解",
        "視覺與內容訊號",
        "視覺與內容分析",
        "受眾行為",
        "留言分析",
        "改善機會",
        "下一則內容建議",
        "總結",
        "活動總覽",
        "活動整體表現",
        "活動一致性",
        "內容一致性",
        "受眾反應",
        "內容策略",
        "下一波活動建議",
        "發文策略",
        "留存診斷",
        "互動診斷",
        "主要問題",
        "分析依據",
    }
    sections = []
    current_heading = ""
    current_lines = []
    heading_pattern = re.compile(r"^\s*(?:#+\s*)?(.{2,60}?):?\s*$")

    def is_heading(line):
        heading_match = heading_pattern.match(line)
        if not heading_match:
            return ""
        candidate = heading_match.group(1).strip().rstrip(":：").strip()
        if candidate.lower() in known_headings or candidate in known_headings:
            return candidate
        return ""

    def flush_section():
        if current_heading or current_lines:
            sections.append((current_heading, "\n".join(current_lines).strip()))

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            if current_lines and current_lines[-1] != "":
                current_lines.append("")
            continue
        heading = is_heading(line)
        if heading:
            flush_section()
            current_heading = heading
            current_lines = []
        else:
            current_lines.append(line)
    flush_section()

    html_parts = []
    if sections:
        for heading, body in sections:
            section_html = ['<section class="ai-insight-section">']
            if heading:
                section_html.append(f'<h3 class="ai-insight-heading">{escape(heading)}</h3>')
            if body:
                paragraphs = [paragraph.strip() for paragraph in re.split(r"\n\s*\n", body) if paragraph.strip()]
                for paragraph in paragraphs:
                    raw_lines = [item.strip() for item in paragraph.splitlines() if item.strip()]
                    bullet_pattern = r"^\s*(?:[-*\u2022]+|\d+[.)])\s+"
                    bullet_lines = [
                        re.sub(bullet_pattern, "", item).strip()
                        for item in raw_lines
                        if re.match(bullet_pattern, item)
                    ]
                    if raw_lines and len(bullet_lines) == len(raw_lines):
                        items = "".join(f"<li>{escape(item)}</li>" for item in bullet_lines if item)
                        section_html.append(f'<ul class="ai-insight-list">{items}</ul>')
                    else:
                        lines = [escape(item.strip().lstrip("-").strip()) for item in raw_lines]
                        section_html.append(f'<p class="ai-insight-body">{"<br>".join(lines)}</p>')
            section_html.append("</section>")
            html_parts.append("".join(section_html))
    else:
        html_parts.append(f'<p class="ai-insight-body">{escape(text)}</p>')
    return "".join(html_parts)


def get_user_ai_language(user):
    settings_obj, _ = UserSettings.objects.get_or_create(user=user)
    return settings_obj.ai_language


def ai_language_cache_key(language):
    return (language or "english").strip().lower().replace("-", "_").replace(" ", "_")


def uses_traditional_chinese_ai_language(language):
    return ai_language_cache_key(language) in {"traditional_chinese", "zh_hant"}


def dashboard_analysis_to_report(analysis, language="English"):
    if not analysis.get("has_enough_data"):
        return analysis.get("message", "")
    overall_summary = _localize_analysis_terms(analysis.get("overall_performance_summary", ""), language, analysis)
    trends = "\n".join(
        f"- {_localize_analysis_terms(trend, language, analysis)}"
        for trend in analysis.get("key_trends") or []
    )
    growth_opportunity = _localize_analysis_terms(analysis.get("growth_opportunity", ""), language, analysis)
    ai_recommendation = _localize_analysis_terms(analysis.get("ai_recommendation", ""), language, analysis)
    if uses_traditional_chinese_ai_language(language):
        return (
            "?湧?銵函\n"
            f"{overall_summary}\n\n"
            "銝餉?頞典\n"
            f"{trends}\n\n"
            "?璈?\n"
            f"{growth_opportunity}\n\n"
            "撱箄降?孵??孵?\n"
            f"{ai_recommendation}"
        )
    return (
        "Overall performance\n"
        f"{overall_summary}\n\n"
        "Key trends\n"
        f"{trends}\n\n"
        "Growth opportunity\n"
        f"{growth_opportunity}\n\n"
        "Suggested improvements\n"
        f"{ai_recommendation}"
    )


def get_cached_ai_insight(subscription, topic):
    return (
        AISuggestionHistory.objects.filter(subscription=subscription, topic=topic)
        .order_by("-created_at")
        .first()
    )


def cache_ai_insight(subscription, user, topic, report, tone):
    stored_report = report
    if parse_ai_insight_sections(report):
        if not isinstance(stored_report, str):
            stored_report = json.dumps(stored_report, ensure_ascii=False)
    else:
        stored_report = sanitize_ai_insight_text(report)
    return AISuggestionHistory.objects.create(
        subscription=subscription,
        requested_by=user,
        topic=topic,
        platform=AI_INSIGHT_PLATFORM,
        tone=tone,
        generated_caption=stored_report,
        generated_hashtags="",
    )


def ai_insight_json_response(report, cached):
    return JsonResponse(
        {
            "success": True,
            "insight_html": render_ai_insight_html(report),
            "cached": cached,
        }
    )


def get_retention_ai_insight_topic(post_id):
    return f"ai-insight:post_retention_insight_v2:{RETENTION_PROMPT_VERSION}:{post_id}"


def get_contact_url(value):
    value = normalize_contact_value(value)
    if not value:
        return ""
    if re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", value):
        return f"mailto:{value}"
    if re.match(r"^\+?[\d\s().-]{7,}$", value):
        phone_value = re.sub(r"[^\d+]", "", value)
        return f"tel:{phone_value}"
    if re.match(r"^https?://", value, re.IGNORECASE):
        return value
    if "." in value and not re.search(r"\s", value):
        return f"https://{value}"
    return ""


def get_contact_label(value):
    value = normalize_contact_value(value)
    if not value:
        return ""
    if re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", value):
        return value.split("@", 1)[0] or value
    if re.match(r"^\+?[\d\s().-]{7,}$", value):
        return value
    if "." not in value and "/" not in value:
        return value

    parsed = urlparse(value if re.match(r"^https?://", value, re.IGNORECASE) else f"https://{value}")
    domain = (parsed.netloc or parsed.path.split("/", 1)[0]).lower()
    domain = domain[4:] if domain.startswith("www.") else domain
    labels = {
        "instagram.com": "Instagram",
        "tiktok.com": "TikTok",
        "youtube.com": "YouTube",
        "youtu.be": "YouTube",
        "linkedin.com": "LinkedIn",
        "github.com": "GitHub",
    }
    for host, label in labels.items():
        if domain == host or domain.endswith(f".{host}"):
            return label
    return domain or value


def build_contact_items(values):
    items = []
    for value in values:
        value = normalize_contact_value(value)
        if not value or value == LINK_PLACEHOLDER_TEXT:
            continue
        url = get_contact_url(value)
        items.append(
            {
                "value": value,
                "label": get_contact_label(value),
                "url": url,
                "opens_new_tab": url.startswith(("http://", "https://")),
            }
        )
    return items


class LegacyPostImage:
    def __init__(self, image):
        self.pk = None
        self.image = image


class ActiveSubscriptionMixin(LoginRequiredMixin):
    subscription = None
    membership = None

    def get_active_membership(self, user):
        membership = (
            SubscriptionMembership.objects.select_related("subscription")
            .filter(user=user, subscription__is_archived=False)
            .order_by("joined_at")
            .first()
        )
        if membership:
            return membership

        owned_subscription = (
            SaaSSubscription.objects.filter(owner=user, is_archived=False)
            .order_by("created_at")
            .first()
        )
        if owned_subscription:
            membership, _ = SubscriptionMembership.objects.get_or_create(
                subscription=owned_subscription,
                user=user,
                defaults={"role": SubscriptionMembership.Role.ADMIN},
            )
            return membership

        if settings.DEBUG:
            return ensure_user_account_setup(user)

        return None

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        self.membership = self.get_active_membership(request.user)
        self.subscription = self.membership.subscription if self.membership else None
        if not self.subscription and hasattr(self, "handle_missing_subscription"):
            return self.handle_missing_subscription()
        return super().dispatch(request, *args, **kwargs)

    def get_subscription(self):
        return self.subscription

    def get_membership_role(self):
        return self.membership.role if self.membership else None

    def is_subscription_admin(self):
        return bool(
            self.subscription
            and self.membership
            and (self.membership.role == SubscriptionMembership.Role.ADMIN or self.request.user.is_staff)
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["active_subscription"] = self.subscription
        context["membership_role"] = self.get_membership_role()
        context["debug_mode"] = settings.DEBUG
        return context


class AIMemberRequiredMixin:
    def dispatch(self, request, *args, **kwargs):
        if not user_has_active_subscription(request.user):
            return ai_members_only_response()
        return super().dispatch(request, *args, **kwargs)


def ai_members_only_response():
    return JsonResponse({"success": False, "error": str(AI_MEMBERS_ONLY_MESSAGE)}, status=403)


class SubscriptionAdminRequiredMixin(ActiveSubscriptionMixin, UserPassesTestMixin):
    def test_func(self):
        return self.is_subscription_admin()


class TenantQuerysetMixin(ActiveSubscriptionMixin):
    subscription_field = "subscription"

    def get_queryset(self):
        queryset = super().get_queryset()
        if not self.subscription:
            return queryset.none()
        return queryset.filter(**{self.subscription_field: self.subscription})


class OwnerOrAdminMixin(ActiveSubscriptionMixin, UserPassesTestMixin):
    def test_func(self):
        obj = self.get_object()
        if self.is_subscription_admin():
            return True
        owner_field = getattr(obj, "author", None) or getattr(obj, "created_by", None)
        return owner_field == self.request.user


class SafeNextRedirectMixin:
    fallback_success_url = None

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["redirect_next_url"] = get_safe_next_url(self.request)
        return context

    def get_fallback_success_url(self):
        if self.fallback_success_url:
            return self.fallback_success_url
        return super().get_success_url()

    def get_success_url(self):
        return get_safe_next_url(self.request) or self.get_fallback_success_url()


class LandingPageView(TemplateView):
    template_name = "socialmanager/landing.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {
                "seo_title": "Creana | AI Social Media Content Management",
                "seo_description": (
                    "Create, schedule, and improve social media content with Creana's "
                    "AI caption tools, campaign planning, and performance analytics."
                ),
            }
        )
        return context

    def get(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect("socialmanager:post_list")
        return super().get(request, *args, **kwargs)


class PasswordLoginView(DjangoLoginView):
    authentication_form = LoginForm
    template_name = "socialmanager/auth/login.html"

    def form_valid(self, form):
        login(
            self.request,
            form.get_user(),
            backend="django.contrib.auth.backends.ModelBackend",
        )
        return HttpResponseRedirect(self.get_success_url())


class FullLogoutView(View):
    def post(self, request, *args, **kwargs):
        logout(request)
        request.session.flush()
        return redirect("socialmanager:login")

    def get(self, request, *args, **kwargs):
        logout(request)
        request.session.flush()
        return redirect("socialmanager:login")


class CurrentHostPasswordResetView(DjangoPasswordResetView):
    def form_valid(self, form):
        opts = {
            "use_https": self.request.is_secure(),
            "token_generator": self.token_generator,
            "from_email": self.from_email or settings.DEFAULT_FROM_EMAIL,
            "email_template_name": self.email_template_name,
            "subject_template_name": self.subject_template_name,
            "request": self.request,
            "html_email_template_name": self.html_email_template_name,
            "extra_email_context": self.extra_email_context,
            "domain_override": self.request.get_host(),
        }
        form.save(**opts)
        return HttpResponseRedirect(self.get_success_url())


class SignUpView(CreateView):
    template_name = "socialmanager/auth/signup.html"
    form_class = SignUpForm
    success_url = reverse_lazy("socialmanager:post_list")

    def form_valid(self, form):
        try:
            with transaction.atomic():
                self.object = form.save(commit=False)
                self.object.username = (form.cleaned_data.get("username") or "").strip()
                self.object.email = normalize_email(form.cleaned_data.get("email"))
                self.object.save()
                EmailAddress.objects.get_or_create(
                    user=self.object,
                    email=self.object.email,
                    defaults={"primary": True, "verified": True},
                )
                ensure_user_account_setup(
                    self.object,
                    workspace_name=form.cleaned_data["subscription_name"],
                )
        except IntegrityError:
            form.add_error(None, _("This username or email is already registered. Please check the form and try again."))
            return self.form_invalid(form)

        login(
            self.request,
            self.object,
            backend="django.contrib.auth.backends.ModelBackend",
        )
        messages.success(self.request, "Your Creana workspace is ready.")
        return HttpResponseRedirect(self.get_success_url())


class DashboardView(ActiveSubscriptionMixin, TemplateView):
    template_name = "socialmanager/dashboard.html"

    @staticmethod
    def _metric_int(value):
        try:
            return int(float(str(value).replace(",", "").strip()))
        except (TypeError, ValueError):
            return 0

    def get_dashboard_chart_data(self, subscription, start_date, end_date):
        grouped = {}
        current_date = start_date
        while current_date <= end_date:
            grouped[current_date] = {"views": 0, "likes": 0, "comments": 0, "shares": 0}
            current_date += timedelta(days=1)

        view_rows = (
            PostView.objects.filter(
                post__subscription=subscription,
                viewed_at__date__gte=start_date,
                viewed_at__date__lte=end_date,
            )
            .annotate(metric_date=TruncDate("viewed_at"))
            .values("metric_date")
            .annotate(total=Count("pk"))
        )
        like_rows = (
            PostEngagement.objects.filter(
                post__subscription=subscription,
                kind=PostEngagement.Kind.LIKE,
                created_at__date__gte=start_date,
                created_at__date__lte=end_date,
            )
            .annotate(metric_date=TruncDate("created_at"))
            .values("metric_date")
            .annotate(total=Count("pk"))
        )
        comment_rows = (
            PostComment.objects.filter(
                post__subscription=subscription,
                created_at__date__gte=start_date,
                created_at__date__lte=end_date,
            )
            .annotate(metric_date=TruncDate("created_at"))
            .values("metric_date")
            .annotate(total=Count("pk"))
        )
        share_rows = (
            PostEngagement.objects.filter(
                post__subscription=subscription,
                kind=PostEngagement.Kind.SHARE,
                created_at__date__gte=start_date,
                created_at__date__lte=end_date,
            )
            .annotate(metric_date=TruncDate("created_at"))
            .values("metric_date")
            .annotate(total=Count("pk"))
        )

        for row in view_rows:
            if row["metric_date"]:
                grouped[row["metric_date"]]["views"] = row["total"] or 0

        for row in like_rows:
            if row["metric_date"]:
                grouped[row["metric_date"]]["likes"] = row["total"] or 0

        for row in comment_rows:
            if row["metric_date"]:
                grouped[row["metric_date"]]["comments"] = row["total"] or 0

        for row in share_rows:
            if row["metric_date"]:
                grouped[row["metric_date"]]["shares"] = row["total"] or 0

        return [
            {
                "date": f"{metric_date:%b} {metric_date.day}",
                "views": values["views"],
                "likes": values["likes"],
                "comments": values["comments"],
                "shares": values["shares"],
            }
            for metric_date, values in sorted(grouped.items())
        ]

    def get_dashboard_summary_data(self, summary, metric_rows, trend_data, start_date, end_date):
        recent_post_metrics = []
        for row in metric_rows:
            post = row["post"]
            recent_post_metrics.append(
                {
                    "title": post.title,
                    "platform": post.get_platform_display() or "",
                    "campaigns": [campaign.name for campaign in post.campaign_groups.all()],
                    "views": self._metric_int(row.get("views")),
                    "likes": self._metric_int(row.get("likes")),
                    "comments": self._metric_int(row.get("comments")),
                    "shares": self._metric_int(row.get("shares")),
                }
            )

        return {
            "date_range": {
                "start": start_date.isoformat(),
                "end": end_date.isoformat(),
            },
            "totals": {
                "views": self._metric_int(summary.get("views")),
                "likes": self._metric_int(summary.get("likes")),
                "comments": self._metric_int(summary.get("comments")),
                "shares": self._metric_int(summary.get("shares")),
                "engagement_rate": summary.get("engagement_rate") or 0,
            },
            "recent_post_metrics": recent_post_metrics,
            "trend_7_days": [
                {
                    "date": row.get("date", ""),
                    "views": self._metric_int(row.get("views")),
                    "likes": self._metric_int(row.get("likes")),
                    "comments": self._metric_int(row.get("comments")),
                    "shares": self._metric_int(row.get("shares")),
                }
                for row in trend_data
            ],
        }

    def get_context_data(self, **kwargs):
        publish_due_scheduled_posts()
        context = super().get_context_data(**kwargs)
        context["summary"] = {
            "impressions": 0,
            "views": 0,
            "likes": 0,
            "comments": 0,
            "shares": 0,
            "engagement_rate": 0,
        }
        context["metric_rows"] = []
        context["dashboard_trend_data"] = []
        subscription = self.get_subscription()
        if subscription:
            start_date, end_date = get_recent_analytics_date_range()
            summary = get_dashboard_summary(subscription, start_date=start_date, end_date=end_date)
            trend_data = self.get_dashboard_chart_data(subscription, start_date, end_date)
            metric_rows = get_recent_post_metrics(subscription, start_date=start_date, end_date=end_date)
            dashboard_summary_data = self.get_dashboard_summary_data(
                summary,
                metric_rows,
                trend_data,
                start_date,
                end_date,
            )
            context["summary"] = summary
            context["dashboard_trend_data"] = trend_data
            context["metric_rows"] = metric_rows
        return context


class DashboardAIInsightView(AIMemberRequiredMixin, ActiveSubscriptionMixin, View):
    def get(self, request, *args, **kwargs):
        subscription = self.get_subscription()
        if not subscription:
            return JsonResponse({"success": False, "error": "No active subscription."}, status=400)

        ai_language = get_user_ai_language(request.user)
        topic = f"ai-insight:dashboard:{AI_ANALYSIS_CACHE_VERSION}:{subscription.pk}:{ai_language_cache_key(ai_language)}"

        try:
            start_date, end_date = get_recent_analytics_date_range()
            summary = get_dashboard_summary(subscription, start_date=start_date, end_date=end_date)
            trend_data = DashboardView().get_dashboard_chart_data(subscription, start_date, end_date)
            metric_rows = get_recent_post_metrics(subscription, start_date=start_date, end_date=end_date)
            dashboard_summary_data = DashboardView().get_dashboard_summary_data(
                summary,
                metric_rows,
                trend_data,
                start_date,
                end_date,
            )
            current_fallback = generate_dashboard_rule_based_analysis(dashboard_summary_data, language=ai_language)
            cached = get_cached_ai_insight(subscription, topic)
            has_stale_empty_cache = (
                cached
                and current_fallback.get("has_enough_data")
                and cached.generated_caption == NOT_ENOUGH_DASHBOARD_DATA_MESSAGE
            )
            if cached and not has_stale_empty_cache:
                return ai_insight_json_response(cached.generated_caption, True)
            try:
                analysis = generate_dashboard_analysis(dashboard_summary_data, language=ai_language)
            except Exception:
                analysis = current_fallback
            report = dashboard_analysis_to_report(analysis, language=ai_language)
            cache_ai_insight(subscription, request.user, topic, report, "Dashboard insight")
            return ai_insight_json_response(report, False)
        except Exception:
            return JsonResponse({"success": False, "error": "Unable to generate AI insight."}, status=500)


class ProfileSettingsView(ActiveSubscriptionMixin, TemplateView):
    template_name = "socialmanager/profile_settings.html"

    def get_context_data(self, **kwargs):
        publish_due_scheduled_posts()
        context = super().get_context_data(**kwargs)
        subscription = self.get_subscription()
        profile, _ = UserProfile.objects.get_or_create(user=self.request.user)
        profile_links = profile.links_list
        user_posts_base = (
            SocialMediaPost.objects.filter(subscription=subscription, author=self.request.user)
            if subscription
            else SocialMediaPost.objects.none()
        )
        user_posts = (
            with_viewer_engagement_annotations(user_posts_base, self.request.user)
            .select_related("campaign")
            .prefetch_related("campaign_groups")
            .annotate(
                comments_count=Count("comments", distinct=True),
                published_sort_at=Coalesce("published_at", "created_at"),
                profile_sort_at=Case(
                    When(status=SocialMediaPost.Status.PUBLISHED, then=Coalesce("published_at", "created_at")),
                    default=Coalesce("updated_at", "created_at"),
                    output_field=DateTimeField(),
                ),
            )
            .order_by("-profile_sort_at", "-created_at", "-id")
        )
        shared_posts = list(
            PostEngagement.objects.filter(
                user=self.request.user,
                kind=PostEngagement.Kind.SHARE,
                post__status=SocialMediaPost.Status.PUBLISHED,
                post__visibility=SocialMediaPost.Visibility.PUBLIC,
            )
            .select_related("post", "post__author", "post__author__profile", "post__campaign")
            .annotate(
                post_comments_count=Count("post__comments"),
                post_published_sort_at=Coalesce("post__published_at", "post__created_at"),
            )
            .order_by("-post_published_sort_at", "-post__created_at", "-post__id")
        )
        attach_viewer_engagement_state((shared_post.post for shared_post in shared_posts), self.request.user)
        for shared_post in shared_posts:
            shared_post.post.comments_count = shared_post.post_comments_count
        engagement_totals = user_posts_base.aggregate(shares=Sum("shares_count"))
        metric_totals = user_posts_base.aggregate(impressions=Sum("metrics__impressions"))
        following_count = self.request.user.following_relationships.count()
        context["profile_posts"] = user_posts
        context["published_posts"] = user_posts.filter(
            status=SocialMediaPost.Status.PUBLISHED,
        ).exclude(visibility=SocialMediaPost.Visibility.PRIVATE)
        context["draft_posts"] = user_posts.filter(
            status=SocialMediaPost.Status.DRAFT,
        ).exclude(visibility=SocialMediaPost.Visibility.PRIVATE)
        context["scheduled_posts"] = user_posts.filter(
            status=SocialMediaPost.Status.SCHEDULED,
        ).exclude(visibility=SocialMediaPost.Visibility.PRIVATE)
        context["private_posts"] = user_posts.filter(
            visibility=SocialMediaPost.Visibility.PRIVATE,
        )
        context["shared_posts"] = shared_posts
        context["profile_stats"] = {
            "posts": user_posts.count(),
            "drafts": user_posts.filter(status=SocialMediaPost.Status.DRAFT).count(),
            "scheduled": user_posts.filter(status=SocialMediaPost.Status.SCHEDULED).count(),
            "followers": self.request.user.follower_relationships.count(),
            "following": following_count,
            "shares": engagement_totals.get("shares") or 0,
            "impressions": metric_totals.get("impressions") or 0,
        }
        context["profile_record"] = profile
        context["profile_bio"] = clean_profile_bio(profile.bio)
        context["profile_links"] = profile_links
        context["profile_link_items"] = build_contact_items(profile_links)
        context["profile_avatar_data"] = profile.avatar.url if profile.avatar else self.request.session.get("profile_avatar_data", "")
        context["profile_field_errors"] = self.request.session.pop("profile_field_errors", {})
        self.request.session.modified = True
        return context

    def post(self, request, *args, **kwargs):
        user = request.user
        profile, _ = UserProfile.objects.get_or_create(user=user)
        submitted_username = (request.POST.get("username") or "").strip()
        if len(submitted_username) > USERNAME_MAX_LENGTH:
            request.session["profile_field_errors"] = {
                "username": f"Username must be {USERNAME_MAX_LENGTH} characters or fewer.",
            }
            return redirect("socialmanager:profile")
        if submitted_username and submitted_username != user.username:
            if User.objects.filter(username=submitted_username).exclude(pk=user.pk).exists():
                request.session["profile_field_errors"] = {
                    "username": "That username is already taken.",
                }
                return redirect("socialmanager:profile")
            user.username = submitted_username
            user.save(update_fields=["username"])

        submitted_email = normalize_email(request.POST.get("email", ""))
        if submitted_email and submitted_email != user.email:
            if user_email_exists(submitted_email, exclude_user=user):
                messages.error(request, _("This email is already registered. Please sign in or use password reset."))
                return redirect("socialmanager:profile")
            user.email = submitted_email
            user.save(update_fields=["email"])
            EmailAddress.objects.filter(user=user, primary=True).exclude(email__iexact=submitted_email).update(primary=False)
            EmailAddress.objects.update_or_create(
                user=user,
                email=submitted_email,
                defaults={"primary": True, "verified": True},
            )
        profile.bio = clean_profile_bio(request.POST.get("bio", ""))
        profile_links = [
            item
            for item in split_profile_links(request.POST.get("links", ""))
            if item != LINK_PLACEHOLDER_TEXT
        ]
        if len(profile.bio) > PROFILE_BIO_MAX_LENGTH:
            request.session["profile_field_errors"] = {
                "bio": f"Bio must be {PROFILE_BIO_MAX_LENGTH} characters or fewer.",
            }
            return redirect("socialmanager:profile")
        if len(profile_links) > PROFILE_LINKS_MAX_COUNT:
            request.session["profile_field_errors"] = {
                "links": f"Add no more than {PROFILE_LINKS_MAX_COUNT} profile links.",
            }
            return redirect("socialmanager:profile")
        profile.links = "|".join(profile_links)
        avatar = request.FILES.get("avatar")
        if avatar:
            profile.avatar = avatar
        try:
            profile.full_clean()
        except ValidationError as exc:
            if hasattr(exc, "message_dict"):
                request.session["profile_field_errors"] = {
                    field: ", ".join(str(message) for message in messages_for_field)
                    for field, messages_for_field in exc.message_dict.items()
                }
            else:
                request.session["profile_field_errors"] = {"bio": ", ".join(str(message) for message in exc.messages)}
            return redirect("socialmanager:profile")
        profile.save()
        request.session["profile_bio"] = profile.bio
        request.session["profile_links"] = profile.links_list
        if profile.avatar:
            request.session["profile_avatar_data"] = profile.avatar.url
        request.session.modified = True
        messages.success(request, "Profile updated.")
        return redirect("socialmanager:profile")


class SubscriptionListView(SubscriptionAdminRequiredMixin, ListView):
    model = SaaSSubscription
    template_name = "socialmanager/subscriptions/subscription_list.html"
    paginate_by = 5

    def get_queryset(self):
        return SaaSSubscription.objects.filter(owner=self.request.user).order_by("is_archived", "name")


class MembershipApplyView(LoginRequiredMixin, TemplateView):
    template_name = "socialmanager/subscriptions/membership_apply.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["membership_next_url"] = get_safe_next_url(self.request) or reverse("socialmanager:dashboard")
        context["is_already_member"] = user_has_active_subscription(self.request.user)
        return context

    def post(self, request, *args, **kwargs):
        if user_has_active_subscription(request.user):
            return redirect(get_safe_next_url(request) or "socialmanager:dashboard")
        if not settings.STRIPE_SECRET_KEY or not settings.STRIPE_MEMBERSHIP_PRICE_ID:
            messages.error(request, _("Stripe membership checkout is not configured."))
            return redirect("socialmanager:membership_apply")

        membership = ensure_user_account_setup(request.user, workspace_name="Creana Membership Workspace")
        next_url = get_safe_next_url(request) or reverse("socialmanager:dashboard")
        stripe.api_key = settings.STRIPE_SECRET_KEY
        site_url = (settings.SITE_URL or request.build_absolute_uri("/")).rstrip("/")

        try:
            checkout_session = stripe.checkout.Session.create(
                mode="subscription",
                line_items=[
                    {
                        "price": settings.STRIPE_MEMBERSHIP_PRICE_ID,
                        "quantity": 1,
                    }
                ],
                customer_email=request.user.email or None,
                client_reference_id=str(request.user.pk),
                metadata={
                    "user_id": str(request.user.pk),
                    "membership_id": str(membership.pk),
                },
                subscription_data={
                    "metadata": {
                        "user_id": str(request.user.pk),
                        "membership_id": str(membership.pk),
                    }
                },
                success_url=(
                    f"{site_url}{reverse('socialmanager:membership_success')}"
                    "?session_id={CHECKOUT_SESSION_ID}"
                ),
                cancel_url=f"{site_url}{reverse('socialmanager:membership_cancel')}",
            )
        except stripe.error.StripeError:
            messages.error(request, _("Unable to start Stripe Checkout. Please try again."))
            return redirect("socialmanager:membership_apply")

        membership.stripe_checkout_session_id = checkout_session.id
        membership.save(update_fields=["stripe_checkout_session_id"])
        return redirect(checkout_session.url)


class MembershipSuccessView(LoginRequiredMixin, TemplateView):
    template_name = "socialmanager/subscriptions/membership_success.html"

    def _stripe_value(self, stripe_object, field_name, default=""):
        if not stripe_object:
            return default
        value = getattr(stripe_object, field_name, default)
        if value != default:
            return value
        to_dict_recursive = getattr(stripe_object, "to_dict_recursive", None)
        if callable(to_dict_recursive):
            return to_dict_recursive().get(field_name, default)
        return default

    def get(self, request, *args, **kwargs):
        self.checkout_session = None
        self.checkout_message = _("Your checkout session was not found. If you completed payment, membership access will update after Stripe confirms it.")
        session_id = request.GET.get("session_id", "").strip()
        if not session_id:
            self.checkout_message = _("Checkout session details were missing. If you completed payment, membership access will update after Stripe confirms it.")
            return super().get(request, *args, **kwargs)

        if session_id and settings.STRIPE_SECRET_KEY:
            stripe.api_key = settings.STRIPE_SECRET_KEY
            try:
                self.checkout_session = stripe.checkout.Session.retrieve(session_id)
            except stripe.error.StripeError:
                self.checkout_session = None

            client_reference_id = self._stripe_value(self.checkout_session, "client_reference_id")
            session_status = self._stripe_value(self.checkout_session, "status")
            payment_status = self._stripe_value(self.checkout_session, "payment_status")
            if self.checkout_session and str(client_reference_id) == str(request.user.pk):
                self.checkout_message = _("Checkout completed. Membership access will update after Stripe confirms your subscription.")

            # Local test fallback only. Production membership activation is handled by the Stripe webhook.
            if (
                settings.DEBUG
                and self.checkout_session
                and str(client_reference_id) == str(request.user.pk)
                and session_status == "complete"
                and payment_status in {"paid", "no_payment_required"}
            ):
                membership = ensure_user_account_setup(request.user, workspace_name="Creana Membership Workspace")
                activate_membership(
                    membership,
                    stripe_customer_id=self._stripe_value(self.checkout_session, "customer"),
                    stripe_subscription_id=self._stripe_value(self.checkout_session, "subscription"),
                    stripe_checkout_session_id=self._stripe_value(self.checkout_session, "id"),
                )
                self.checkout_message = _("Checkout completed. Test fallback activated membership while the Stripe webhook is unavailable.")
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["checkout_message"] = self.checkout_message
        return context


class MembershipCancelView(LoginRequiredMixin, TemplateView):
    template_name = "socialmanager/subscriptions/membership_cancel.html"


@method_decorator(csrf_exempt, name="dispatch")
class StripeWebhookView(View):
    def post(self, request, *args, **kwargs):
        payload = request.body
        signature = request.headers.get("Stripe-Signature", "")

        if settings.STRIPE_WEBHOOK_SECRET:
            try:
                event = stripe.Webhook.construct_event(payload, signature, settings.STRIPE_WEBHOOK_SECRET)
            except (ValueError, stripe.error.SignatureVerificationError):
                return HttpResponseBadRequest("Invalid Stripe webhook payload")
        else:
            try:
                event = json.loads(payload.decode("utf-8"))
            except ValueError:
                return HttpResponseBadRequest("Invalid Stripe webhook payload")

        event_type = event.get("type")
        data_object = event.get("data", {}).get("object", {})

        if event_type == "checkout.session.completed":
            self._handle_checkout_completed(data_object)
        elif event_type in {"customer.subscription.deleted", "customer.subscription.updated"}:
            self._handle_subscription_changed(data_object)

        return HttpResponse(status=200)

    def _handle_checkout_completed(self, checkout_session):
        membership_id = checkout_session.get("metadata", {}).get("membership_id")
        user_id = checkout_session.get("metadata", {}).get("user_id") or checkout_session.get("client_reference_id")
        membership = None
        if membership_id:
            membership = SubscriptionMembership.objects.filter(pk=membership_id).first()
        if not membership and user_id:
            user = User.objects.filter(pk=user_id).first()
            if user:
                membership = ensure_user_account_setup(user, workspace_name="Creana Membership Workspace")
        if not membership:
            return

        activate_membership(
            membership,
            stripe_customer_id=checkout_session.get("customer") or "",
            stripe_subscription_id=checkout_session.get("subscription") or "",
            stripe_checkout_session_id=checkout_session.get("id") or "",
        )

    def _handle_subscription_changed(self, subscription):
        subscription_id = subscription.get("id")
        status = subscription.get("status")
        if status in {"active", "trialing"}:
            membership = SubscriptionMembership.objects.filter(stripe_subscription_id=subscription_id).first()
            if membership:
                activate_membership(
                    membership,
                    stripe_customer_id=subscription.get("customer") or "",
                    stripe_subscription_id=subscription_id,
                )
        elif status in {"canceled", "incomplete_expired", "unpaid"}:
            deactivate_membership_for_subscription(subscription_id)


class SubscriptionCreateView(SubscriptionAdminRequiredMixin, CreateView):
    model = SaaSSubscription
    form_class = SaaSSubscriptionForm
    template_name = "socialmanager/subscriptions/subscription_form.html"
    success_url = reverse_lazy("socialmanager:subscription_list")

    def form_valid(self, form):
        form.instance.owner = self.request.user
        response = super().form_valid(form)
        SubscriptionMembership.objects.get_or_create(
            subscription=self.object,
            user=self.request.user,
            defaults={"role": SubscriptionMembership.Role.ADMIN},
        )
        messages.success(self.request, "Subscription created.")
        return response


class SubscriptionUpdateView(SubscriptionAdminRequiredMixin, UpdateView):
    model = SaaSSubscription
    form_class = SaaSSubscriptionForm
    template_name = "socialmanager/subscriptions/subscription_form.html"
    success_url = reverse_lazy("socialmanager:subscription_list")

    def get_queryset(self):
        return SaaSSubscription.objects.filter(owner=self.request.user)

    def form_valid(self, form):
        messages.success(self.request, "Subscription updated.")
        return super().form_valid(form)


class SubscriptionArchiveView(SubscriptionAdminRequiredMixin, UpdateView):
    model = SaaSSubscription
    fields = []
    template_name = "socialmanager/subscriptions/subscription_archive.html"
    success_url = reverse_lazy("socialmanager:subscription_list")

    def get_queryset(self):
        return SaaSSubscription.objects.filter(owner=self.request.user, is_archived=False)

    def post(self, request, *args, **kwargs):
        subscription = self.get_object()
        subscription.archive()
        messages.success(request, "Subscription archived instead of deleted.")
        return redirect(self.success_url)


class CampaignListView(TenantQuerysetMixin, ListView):
    model = SocialMediaCampaign
    template_name = "socialmanager/campaigns/campaign_list.html"
    paginate_by = 8

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .prefetch_related("campaign_posts")
            .annotate(
                post_count=Count("campaign_posts", distinct=True),
                scheduled_count=Count(
                    "campaign_posts",
                    filter=Q(campaign_posts__status=SocialMediaPost.Status.SCHEDULED),
                    distinct=True,
                ),
            )
        )


class CampaignDetailView(TenantQuerysetMixin, DetailView):
    model = SocialMediaCampaign
    template_name = "socialmanager/campaigns/campaign_detail.html"

    def build_line_path(self, coordinates):
        if not coordinates:
            return ""
        if len(coordinates) == 1:
            x, y = coordinates[0]
            return f"M{x} {y}"
        segments = [f"M{coordinates[0][0]} {coordinates[0][1]}"]
        for index in range(1, len(coordinates)):
            previous_x, previous_y = coordinates[index - 1]
            current_x, current_y = coordinates[index]
            control_offset = round((current_x - previous_x) / 2, 2)
            segments.append(
                f"C{round(previous_x + control_offset, 2)} {previous_y}, "
                f"{round(current_x - control_offset, 2)} {current_y}, "
                f"{current_x} {current_y}"
            )
        return " ".join(segments)

    def get_campaign_trend_points(self, published_posts):
        end_date = timezone.localdate()
        start_date = end_date - timedelta(days=6)
        grouped = {}
        current_date = start_date
        while current_date <= end_date:
            grouped[current_date] = {"views": 0, "likes": 0, "comments": 0, "shares": 0}
            current_date += timedelta(days=1)

        post_ids = [post.pk for post in published_posts]
        if not post_ids:
            return []

        view_rows = (
            PostView.objects.filter(post_id__in=post_ids, viewed_at__date__gte=start_date, viewed_at__date__lte=end_date)
            .annotate(metric_date=TruncDate("viewed_at"))
            .values("metric_date")
            .annotate(total=Count("pk"))
        )
        engagement_rows = (
            PostEngagement.objects.filter(post_id__in=post_ids, created_at__date__gte=start_date, created_at__date__lte=end_date)
            .annotate(metric_date=TruncDate("created_at"))
            .values("metric_date", "kind")
            .annotate(total=Count("pk"))
        )
        comment_rows = (
            PostComment.objects.filter(post_id__in=post_ids, created_at__date__gte=start_date, created_at__date__lte=end_date)
            .annotate(metric_date=TruncDate("created_at"))
            .values("metric_date")
            .annotate(total=Count("pk"))
        )

        for row in view_rows:
            if row["metric_date"] in grouped:
                grouped[row["metric_date"]]["views"] = row["total"] or 0
        for row in engagement_rows:
            metric_date = row["metric_date"]
            if metric_date not in grouped:
                continue
            if row["kind"] == PostEngagement.Kind.LIKE:
                grouped[metric_date]["likes"] = row["total"] or 0
            elif row["kind"] == PostEngagement.Kind.SHARE:
                grouped[metric_date]["shares"] = row["total"] or 0
        for row in comment_rows:
            if row["metric_date"] in grouped:
                grouped[row["metric_date"]]["comments"] = row["total"] or 0

        return [
            {
                "date": f"{metric_date:%b} {metric_date.day}",
                "views": values["views"],
                "likes": values["likes"],
                "comments": values["comments"],
                "shares": values["shares"],
            }
            for metric_date, values in sorted(grouped.items())
        ]

    def get_campaign_trend_chart(self, trend_points):
        series_config = [
            {"key": "views", "label": gettext("Views"), "color": "#2563eb"},
            {"key": "likes", "label": gettext("Likes"), "color": "#10b981"},
            {"key": "comments", "label": gettext("Comments"), "color": "#f59e0b"},
            {"key": "shares", "label": gettext("Shares"), "color": "#8b5cf6"},
        ]
        chart_width = 900
        chart_height = 320
        left = 64
        right = 32
        top = 34
        bottom = 52
        plot_width = chart_width - left - right
        plot_height = chart_height - top - bottom
        max_value = max([point[key] for point in trend_points for key in ("views", "likes", "comments", "shares")] or [0])
        y_ticks, scale_max = build_count_axis_ticks(max_value, top, plot_height)
        x_step = plot_width / max(len(trend_points) - 1, 1)

        chart_series = []
        for config in series_config:
            coordinates = []
            point_values = []
            for index, point in enumerate(trend_points):
                value = point[config["key"]]
                x = round(left + (x_step * index), 2)
                y = round(top + (plot_height - ((value / scale_max) * plot_height)), 2)
                coordinates.append((x, y))
                point_values.append({"x": x, "y": y, "value": value, "date": point["date"]})
            chart_series.append({**config, "path": self.build_line_path(coordinates), "points": point_values})

        return {
            "width": chart_width,
            "height": chart_height,
            "left": left,
            "right": chart_width - right,
            "top": top,
            "bottom": chart_height - bottom,
            "series": chart_series,
            "x_ticks": [{"label": point["date"], "x": round(left + (x_step * index), 2)} for index, point in enumerate(trend_points)],
            "y_ticks": y_ticks,
        }

    def get_campaign_context_parts(self):
        related_posts = list(
            self.object.campaign_posts.select_related("author", "campaign")
            .prefetch_related("campaign_groups")
            .annotate(
                comments_count=Count("comments", distinct=True),
                view_count=Count("views", distinct=True),
            )
            .order_by("-updated_at")
        )
        now = timezone.now()
        scheduled_posts = [
            post for post in related_posts
            if post.status == SocialMediaPost.Status.SCHEDULED
            or (post.scheduled_for and post.scheduled_for > now)
        ]
        released_posts = [
            post for post in related_posts
            if post.status == SocialMediaPost.Status.PUBLISHED
        ]
        total_views = sum(post.view_count for post in related_posts)
        total_likes = sum(post.likes_count for post in related_posts)
        total_comments = sum(post.comments_count for post in related_posts)
        total_shares = sum(post.shares_count for post in related_posts)
        total_interactions = total_likes + total_comments + total_shares
        campaign_trend_points = self.get_campaign_trend_points(released_posts)
        campaign_summary = {
            "post_count": len(related_posts),
            "scheduled_count": len(scheduled_posts),
            "views": total_views,
            "likes": total_likes,
            "comments": total_comments,
            "shares": total_shares,
            "engagement_rate": min(round((total_interactions / total_views) * 100, 2), 100) if total_views else 0,
        }
        campaign_ai_payload = {
            "campaign": {
                "id": self.object.pk,
                "name": self.object.name,
                "objective": self.object.objective,
                "strategy": getattr(self.object, "strategy", "") or "",
                "description": getattr(self.object, "description", "") or "",
                "platform_focus": self.object.platform_focus_list,
                "platform": self.object.platform_focus_display,
                "status": str(self.object.effective_status_display),
                "start_date": self.object.start_date.isoformat() if self.object.start_date else "",
                "end_date": self.object.end_date.isoformat() if self.object.end_date else "",
            },
            "metrics": {
                "views": total_views,
                "likes": total_likes,
                "comments": total_comments,
                "shares": total_shares,
                "engagement_rate": campaign_summary["engagement_rate"],
                "released_count": len(released_posts),
                "scheduled_count": len(scheduled_posts),
            },
            "post_count": len(related_posts),
            "scheduled_count": len(scheduled_posts),
            "released_count": len(released_posts),
            "trend_points": campaign_trend_points,
            "posts": [
                {
                    "id": post.pk,
                    "title": post.title,
                    "status": post.status,
                    "content_format": post.content_format,
                    "post_type": post.content_format,
                    "platform": str(post.get_platform_display()),
                    "caption": post.caption,
                    "article_caption": post.article_caption,
                    "article_body": getattr(post, "article_body", "") or getattr(post, "body", ""),
                    "hashtags": post.hashtags,
                    "views": getattr(post, "view_count", 0),
                    "likes": post.likes_count,
                    "comments": getattr(post, "comments_count", 0),
                    "shares": post.shares_count,
                    "engagement_rate": min(
                        round(
                            (
                                (post.likes_count + getattr(post, "comments_count", 0) + post.shares_count)
                                / getattr(post, "view_count", 0)
                            )
                            * 100,
                            2,
                        ),
                        100,
                    ) if getattr(post, "view_count", 0) else 0,
                    "published_date": post.published_at.date().isoformat() if post.published_at else "",
                    "published_at": post.published_at.isoformat() if post.published_at else "",
                    "scheduled_for": post.scheduled_for.isoformat() if post.scheduled_for else "",
                    "scheduled_at": post.scheduled_for.isoformat() if post.scheduled_for else "",
                }
                for post in related_posts
            ],
        }
        return {
            "related_posts": released_posts,
            "released_posts": released_posts,
            "scheduled_posts": scheduled_posts,
            "campaign_trend_chart": self.get_campaign_trend_chart(campaign_trend_points) if released_posts else None,
            "campaign_summary": campaign_summary,
            "campaign_ai_payload": campaign_ai_payload,
        }

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {
                key: value
                for key, value in self.get_campaign_context_parts().items()
                if key != "campaign_ai_payload"
            }
        )
        return context


class CampaignAIInsightView(AIMemberRequiredMixin, ActiveSubscriptionMixin, View):
    model = SocialMediaCampaign

    def get(self, request, *args, **kwargs):
        self.object = get_object_or_404(SocialMediaCampaign.objects.filter(subscription=self.subscription), pk=kwargs.get("pk"))
        ai_language = get_user_ai_language(request.user)
        topic = f"ai-insight:campaign:{CAMPAIGN_AI_CACHE_VERSION}:{self.object.pk}:{ai_language_cache_key(ai_language)}"
        cached = get_cached_ai_insight(self.object.subscription, topic)
        if cached:
            return ai_insight_json_response(cached.generated_caption, True)

        try:
            detail_view = CampaignDetailView()
            detail_view.object = self.object
            payload = detail_view.get_campaign_context_parts()["campaign_ai_payload"]
            try:
                report = generate_campaign_analysis(payload, language=ai_language)
            except Exception:
                report = generate_campaign_rule_based_analysis(payload, language=ai_language)
            cache_ai_insight(self.object.subscription, request.user, topic, report, "Campaign insight")
            return ai_insight_json_response(report, False)
        except Exception:
            return JsonResponse({"success": False, "error": "Unable to generate AI insight."}, status=500)


class CampaignCreateView(ActiveSubscriptionMixin, CreateView):
    model = SocialMediaCampaign
    form_class = SocialMediaCampaignForm
    template_name = "socialmanager/campaigns/campaign_form.html"
    success_url = reverse_lazy("socialmanager:campaign_list")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["subscription"] = self.subscription
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        form.instance.subscription = self.subscription
        form.instance.created_by = self.request.user
        messages.success(self.request, _("Campaign created."))
        return super().form_valid(form)


class CampaignUpdateView(SafeNextRedirectMixin, OwnerOrAdminMixin, UpdateView):
    model = SocialMediaCampaign
    form_class = SocialMediaCampaignForm
    template_name = "socialmanager/campaigns/campaign_form.html"
    success_url = reverse_lazy("socialmanager:campaign_list")

    def get_queryset(self):
        return SocialMediaCampaign.objects.filter(subscription=self.subscription)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["subscription"] = self.subscription
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        messages.success(self.request, _("Campaign updated."))
        return super().form_valid(form)

    def get_fallback_success_url(self):
        return reverse("socialmanager:campaign_detail", kwargs={"pk": self.object.pk})


class CampaignDeleteView(SafeNextRedirectMixin, OwnerOrAdminMixin, DeleteView):
    model = SocialMediaCampaign
    template_name = "socialmanager/confirm_delete.html"
    success_url = reverse_lazy("socialmanager:campaign_list")

    def get_queryset(self):
        return SocialMediaCampaign.objects.filter(subscription=self.subscription)

    def get_success_url(self):
        next_url = get_safe_next_url(self.request)
        deleted_detail_url = reverse("socialmanager:campaign_detail", kwargs={"pk": self.object.pk})
        if next_url and next_url.split("?", 1)[0] != deleted_detail_url:
            return next_url
        return self.get_fallback_success_url()

    def form_valid(self, form):
        messages.success(self.request, _("Campaign deleted."))
        return super().form_valid(form)


class AnnouncementSuperuserRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    model = Announcement

    def test_func(self):
        return self.request.user.is_superuser


class AnnouncementCreateView(AnnouncementSuperuserRequiredMixin, CreateView):
    form_class = AnnouncementForm
    template_name = "socialmanager/announcements/announcement_form.html"
    success_url = reverse_lazy("socialmanager:post_list")

    def form_valid(self, form):
        form.instance.author = self.request.user
        form.instance.is_active = True
        messages.success(self.request, _("Announcement created."))
        return super().form_valid(form)


class AnnouncementUpdateView(AnnouncementSuperuserRequiredMixin, UpdateView):
    form_class = AnnouncementForm
    template_name = "socialmanager/announcements/announcement_form.html"
    success_url = reverse_lazy("socialmanager:post_list")

    def form_valid(self, form):
        messages.success(self.request, _("Announcement updated."))
        return super().form_valid(form)


class AnnouncementDeleteView(AnnouncementSuperuserRequiredMixin, DeleteView):
    template_name = "socialmanager/announcements/announcement_confirm_delete.html"
    success_url = reverse_lazy("socialmanager:post_list")

    def form_valid(self, form):
        messages.success(self.request, _("Announcement deleted."))
        return super().form_valid(form)


class PostListView(ActiveSubscriptionMixin, ListView):
    model = SocialMediaPost
    template_name = "socialmanager/posts/post_list.html"
    paginate_by = 10

    def dispatch(self, request, *args, **kwargs):
        timing_token = set_timing_path(request.path) if _is_posts_timing_request(request) else None
        if timing_token is not None:
            _log_posts_timing(request, "dispatch_start", 0)
        try:
            response = super().dispatch(request, *args, **kwargs)
        except Exception:
            if timing_token is not None:
                reset_timing_path(timing_token)
            raise
        if timing_token is None:
            return response
        if hasattr(response, "add_post_render_callback"):
            response.add_post_render_callback(lambda rendered_response: reset_timing_path(timing_token))
        else:
            reset_timing_path(timing_token)
        return response

    def get_queryset(self):
        publish_due_scheduled_posts()
        started_at = time.perf_counter()
        user_likes = PostEngagement.objects.filter(
            post=OuterRef("pk"),
            user=self.request.user,
            kind=PostEngagement.Kind.LIKE,
        )
        user_shares = PostEngagement.objects.filter(
            post=OuterRef("pk"),
            user=self.request.user,
            kind=PostEngagement.Kind.SHARE,
        )
        queryset = public_published_posts(SocialMediaPost.objects)
        hidden_user_ids = HiddenUser.objects.filter(owner=self.request.user).values("hidden_user_id")
        queryset = queryset.exclude(author_id__in=hidden_user_ids)
        queryset = (
            queryset
            .select_related("campaign", "author", "author__profile")
            .prefetch_related(
                Prefetch(
                    "images",
                    queryset=PostImage.objects.order_by("order", "created_at", "pk"),
                    to_attr="ordered_images",
                )
            )
            .annotate(
                # Feed cards render these counts and viewer-specific button states for every post.
                # Calculating them in the page query avoids per-card count/existence queries in the template.
                like_count=Count("engagements", filter=Q(engagements__kind=PostEngagement.Kind.LIKE), distinct=True),
                share_count=Count("engagements", filter=Q(engagements__kind=PostEngagement.Kind.SHARE), distinct=True),
                comments_count=Count("comments", distinct=True),
                view_count=Count("views", distinct=True),
                user_has_liked=Exists(user_likes),
                user_has_shared=Exists(user_shares),
                published_sort_at=Coalesce("published_at", "created_at"),
            )
            .order_by("-published_sort_at", "-created_at", "-id")
        )
        _log_posts_timing(self.request, "get_queryset_construct", time.perf_counter() - started_at)
        return queryset

    def paginate_queryset(self, queryset, page_size):
        started_at = time.perf_counter()
        paginator, page, object_list, is_paginated = super().paginate_queryset(queryset, page_size)
        eval_started_at = time.perf_counter()
        page_items = list(object_list)
        page.object_list = page_items
        cache_started_at = time.perf_counter()
        for post in page_items:
            _cache_post_media_urls(post, include_video=False)
        _log_posts_timing(
            self.request,
            "cached_media_urls",
            time.perf_counter() - cache_started_at,
            page_object_count=len(page_items),
        )
        _log_posts_timing(
            self.request,
            "queryset_page_evaluation",
            time.perf_counter() - eval_started_at,
            page_object_count=len(page_items),
        )
        _log_posts_timing(
            self.request,
            "paginate_queryset",
            time.perf_counter() - started_at,
            page_object_count=len(page_items),
            total_count=paginator.count,
        )
        return paginator, page, page_items, is_paginated

    def get_context_data(self, **kwargs):
        started_at = time.perf_counter()
        context = super().get_context_data(**kwargs)
        context["announcements"] = (
            Announcement.objects.filter(is_active=True)
            .select_related("author")
            .order_by("-created_at")
        )
        page_obj = context.get("page_obj")
        page_object_count = len(getattr(page_obj, "object_list", []) or context.get("object_list", []))
        _log_posts_timing(
            self.request,
            "get_context_data",
            time.perf_counter() - started_at,
            page_object_count=page_object_count,
        )
        return context

    def render_to_response(self, context, **response_kwargs):
        started_at = time.perf_counter()
        is_ajax = self.request.headers.get("x-requested-with") == "XMLHttpRequest"
        page_obj = context.get("page_obj")
        page_object_count = len(getattr(page_obj, "object_list", []) or context.get("object_list", []))

        if is_ajax:
            render_started_at = time.perf_counter()
            page_obj = context.get("page_obj")
            html = render_to_string(
                "socialmanager/partials/feed_posts.html",
                context,
                request=self.request,
            )
            _log_posts_timing(
                self.request,
                "template_render_ajax",
                time.perf_counter() - render_started_at,
                page_object_count=page_object_count,
            )
            _log_posts_timing(
                self.request,
                "render_to_response",
                time.perf_counter() - started_at,
                page_object_count=page_object_count,
                ajax=1,
            )
            return JsonResponse(
                {
                    "html": html,
                    "has_next": page_obj.has_next() if page_obj else False,
                    "next_page": page_obj.next_page_number() if page_obj and page_obj.has_next() else None,
                }
            )

        response = super().render_to_response(context, **response_kwargs)
        _log_posts_timing(
            self.request,
            "render_to_response",
            time.perf_counter() - started_at,
            page_object_count=page_object_count,
            ajax=0,
        )
        if hasattr(response, "add_post_render_callback"):
            render_wait_started_at = time.perf_counter()

            def log_template_render(rendered_response):
                _log_posts_timing(
                    self.request,
                    "template_render",
                    time.perf_counter() - render_wait_started_at,
                    page_object_count=page_object_count,
                )

            response.add_post_render_callback(log_template_render)
        return response


class PublicProfileView(ActiveSubscriptionMixin, TemplateView):
    template_name = "socialmanager/profile_public.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user_lookup = {"pk": kwargs.get("user_id")} if kwargs.get("user_id") else {"username": kwargs.get("username")}
        profile_user = get_object_or_404(User, **user_lookup)
        profile, _ = UserProfile.objects.get_or_create(user=profile_user)
        public_posts = (
            with_viewer_engagement_annotations(
                public_published_posts(SocialMediaPost.objects.filter(author=profile_user)),
                self.request.user,
            )
            .select_related("campaign", "author", "author__profile")
            .prefetch_related("campaign_groups")
            .annotate(
                comments_count=Count("comments", distinct=True),
                published_sort_at=Coalesce("published_at", "created_at"),
            )
            .order_by("-published_sort_at", "-created_at", "-id")
        )
        shared_posts = list(
            PostEngagement.objects.filter(
                user=profile_user,
                kind=PostEngagement.Kind.SHARE,
                post__status=SocialMediaPost.Status.PUBLISHED,
                post__visibility=SocialMediaPost.Visibility.PUBLIC,
            )
            .select_related("post", "post__author", "post__author__profile", "post__campaign")
            .annotate(
                post_comments_count=Count("post__comments"),
                post_published_sort_at=Coalesce("post__published_at", "post__created_at"),
            )
            .order_by("-post_published_sort_at", "-post__created_at", "-post__id")
        )
        attach_viewer_engagement_state((shared_post.post for shared_post in shared_posts), self.request.user)
        for shared_post in shared_posts:
            shared_post.post.comments_count = shared_post.post_comments_count
        context["profile_user"] = profile_user
        context["profile_record"] = profile
        context["profile_bio"] = clean_profile_bio(profile.bio)
        context["profile_links"] = profile.links_list if profile.links_public else []
        context["profile_link_items"] = build_contact_items(context["profile_links"])
        context["public_posts"] = public_posts
        context["shared_posts"] = shared_posts
        context["can_unshare_shared_posts"] = profile_user == self.request.user
        context["is_own_profile"] = profile_user == self.request.user
        context["is_following"] = UserFollow.objects.filter(
            follower=self.request.user,
            following=profile_user,
        ).exists()
        context["is_hidden_by_current_user"] = (
            not context["is_own_profile"]
            and HiddenUser.objects.filter(owner=self.request.user, hidden_user=profile_user).exists()
        )
        context["profile_stats"] = {
            "posts": public_posts.count(),
            "followers": profile_user.follower_relationships.count(),
            "following": profile_user.following_relationships.count(),
        }
        context.update(
            {
                "seo_title": f"{profile_user.get_username()} | Creana",
                "seo_description": clean_meta_description(
                    context["profile_bio"],
                    f"View {profile_user.get_username()}'s public profile and posts on Creana.",
                ),
            }
        )
        return context


class UserFollowToggleView(ActiveSubscriptionMixin, View):
    def post(self, request, *args, **kwargs):
        target_user = get_object_or_404(User, pk=kwargs.get("user_id"))
        if target_user == request.user:
            return redirect("socialmanager:profile")

        relationship, created = UserFollow.objects.get_or_create(
            follower=request.user,
            following=target_user,
        )
        if created:
            create_notification(
                recipient=target_user,
                actor=request.user,
                kind=Notification.Kind.FOLLOW,
            )
            messages.success(request, f"You are now following {target_user.username or target_user.email}.")
        else:
            relationship.delete()
            messages.success(request, f"You unfollowed {target_user.username or target_user.email}.")
        return redirect("socialmanager:public_profile", user_id=target_user.pk)


class HiddenUserToggleView(ActiveSubscriptionMixin, View):
    def post(self, request, *args, **kwargs):
        user_lookup = {"pk": kwargs.get("user_id")} if kwargs.get("user_id") else {"username": kwargs.get("username")}
        target_user = get_object_or_404(User, **user_lookup)
        if target_user == request.user:
            return redirect("socialmanager:profile")

        hidden_relation, created = HiddenUser.objects.get_or_create(
            owner=request.user,
            hidden_user=target_user,
        )
        if created:
            messages.success(request, _("You will no longer see this user’s posts in your feed."))
        else:
            hidden_relation.delete()
            messages.success(request, _("This user’s posts will appear in your feed again."))
        return redirect("socialmanager:public_profile", user_id=target_user.pk)


class NotificationListView(LoginRequiredMixin, ListView):
    model = Notification
    template_name = "socialmanager/notifications.html"
    context_object_name = "notifications"

    def get_queryset(self):
        return (
            Notification.objects.filter(recipient=self.request.user)
            .select_related("actor", "actor__profile", "post", "comment", "comment__parent")
        )

    def get_context_object_name(self, object_list):
        return "grouped_notifications"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        notifications = list(context["object_list"])
        grouped_notifications = build_grouped_notifications(notifications)
        context["grouped_notifications"] = grouped_notifications
        context["unread_count"] = self.get_queryset().filter(is_read=False).count()
        return context


class NotificationOpenView(LoginRequiredMixin, View):
    def get(self, request, pk, *args, **kwargs):
        notification = get_object_or_404(
            Notification.objects.select_related("actor", "post", "comment", "comment__parent"),
            pk=pk,
            recipient=request.user,
        )
        Notification.objects.filter(get_notification_group_filter(notification), is_read=False).update(is_read=True)
        return redirect(get_notification_url(notification))


class NotificationMarkAllReadView(LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        Notification.objects.filter(recipient=request.user, is_read=False).update(is_read=True)
        messages.success(request, _("Notifications marked as read."))
        return redirect("socialmanager:notifications")


class SettingsView(LoginRequiredMixin, TemplateView):
    template_name = "socialmanager/settings.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        settings_obj, _ = UserSettings.objects.get_or_create(user=self.request.user)
        context["active_nav"] = "settings"
        context["form"] = kwargs.get("form") or UserSettingsForm(instance=settings_obj)
        return context

    def post(self, request, *args, **kwargs):
        action = request.POST.get("action")
        if action == "delete_account":
            return self.delete_account(request)

        return HttpResponseBadRequest(_("Unsupported settings action."))

    def delete_account(self, request):
        password = request.POST.get("delete_password", "")
        confirmation = request.POST.get("delete_confirmation", "")

        if confirmation != "DELETE":
            messages.error(request, _("Type DELETE to confirm account deletion."))
            return redirect("socialmanager:settings")

        if not request.user.check_password(password):
            messages.error(request, _("Password confirmation failed."))
            return redirect("socialmanager:settings")

        user = request.user
        logout(request)
        user.delete()
        messages.success(request, _("Your account has been deleted."))
        return redirect("socialmanager:login")


class SettingsUpdateView(LoginRequiredMixin, View):
    allowed_fields = {
        "language",
        "notify_post_like",
        "notify_post_comment",
        "notify_post_share",
        "notify_comment_like",
        "notify_comment_reply",
        "notify_follow",
        "ai_tone",
        "ai_language",
        "ai_hashtag_count",
    }
    boolean_fields = {
        "notify_post_like",
        "notify_post_comment",
        "notify_post_share",
        "notify_comment_like",
        "notify_comment_reply",
        "notify_follow",
    }

    def post(self, request, *args, **kwargs):
        field = request.POST.get("field", "").strip()
        value = request.POST.get("value", "").strip()

        if field not in self.allowed_fields:
            return JsonResponse(
                {"success": False, "error": str(_("This setting cannot be updated."))},
                status=400,
            )

        settings_obj, settings_created = UserSettings.objects.get_or_create(user=request.user)
        cleaned_value, error = self.clean_value(field, value)
        if error:
            return JsonResponse({"success": False, "field": field, "error": error}, status=400)

        setattr(settings_obj, field, cleaned_value)
        settings_obj.save(update_fields=[field, "updated_at"])

        response_data = {
            "success": True,
            "field": field,
            "current_value": cleaned_value,
        }

        response = JsonResponse(response_data)
        if field == "language":
            translation.activate(cleaned_value)
            request.LANGUAGE_CODE = cleaned_value
            request.session["django_language"] = cleaned_value
            response.set_cookie("django_language", cleaned_value)

        return response

    def clean_value(self, field, value):
        if field in self.boolean_fields:
            return value in {"1", "true", "on", "yes"}, None

        if field == "language":
            allowed = {choice[0] for choice in UserSettings.Language.choices}
            if value not in allowed:
                return None, str(_("Choose a supported language."))
            return value, None

        if field == "ai_tone":
            allowed = {choice[0] for choice in UserSettings.AITone.choices}
            if value not in allowed:
                return None, str(_("Choose a supported tone."))
            return value, None

        if field == "ai_language":
            allowed = {choice[0] for choice in UserSettings.AILanguage.choices}
            if value not in allowed:
                return None, str(_("Choose a supported caption language."))
            return value, None

        if field == "ai_hashtag_count":
            try:
                count = int(value)
            except (TypeError, ValueError):
                return None, str(_("Choose between 1 and 5 hashtags."))
            if count < 1 or count > 5:
                return None, str(_("Choose between 1 and 5 hashtags."))
            return count, None

        return value, None


@method_decorator(ensure_csrf_cookie, name="dispatch")
class PostDetailView(ActiveSubscriptionMixin, DetailView):
    model = SocialMediaPost
    template_name = "socialmanager/posts/post_detail.html"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                return self.handle_no_permission()
            self.membership = None
            self.subscription = None
            return DetailView.dispatch(self, request, *args, **kwargs)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        queryset = with_viewer_engagement_annotations(
            SocialMediaPost.objects.select_related("campaign", "author", "author__profile")
            .prefetch_related(
                Prefetch(
                    "images",
                    queryset=PostImage.objects.order_by("order", "created_at", "pk"),
                    to_attr="ordered_images",
                )
            ),
            self.request.user,
        ).annotate(
            comments_count=Count("comments", distinct=True),
        )
        if self.request.user.is_staff:
            return queryset
        public_filter = Q(
            status=SocialMediaPost.Status.PUBLISHED,
            visibility=SocialMediaPost.Visibility.PUBLIC,
        )
        if not self.request.user.is_authenticated:
            return queryset.filter(public_filter)
        return queryset.filter(public_filter | Q(author=self.request.user))

    def redirect_unpublished_to_editor(self):
        if self.object.status in (SocialMediaPost.Status.DRAFT, SocialMediaPost.Status.SCHEDULED):
            if self.object.author == self.request.user or self.request.user.is_staff:
                url = reverse("socialmanager:post_update", kwargs={"pk": self.object.pk})
                if self.object.status == SocialMediaPost.Status.SCHEDULED:
                    url = f"{url}?focus=schedule#schedule-section"
                return redirect(url)
            return HttpResponseBadRequest("You do not have permission to view this post.")
        return None

    def redirect_to_canonical_url(self):
        if self.kwargs.get("slug") != self.object.slug:
            return redirect(
                "socialmanager:post_detail",
                pk=self.object.pk,
                slug=self.object.slug,
                permanent=True,
            )
        return None

    def format_comment_time(self, value):
        now = timezone.now()
        value = timezone.localtime(value) if timezone.is_aware(value) else timezone.make_aware(value, timezone.get_current_timezone())
        delta = now - value

        if delta.days >= 1:
            unit = "day" if delta.days == 1 else "days"
            return gettext("%(count)s %(unit)s ago") % {"count": delta.days, "unit": gettext(unit)}

        return gettext("%(age)s ago") % {"age": timesince(value, now)}

    def can_interact_with_post(self, post):
        return (
            post.status == SocialMediaPost.Status.PUBLISHED
            and post.visibility == SocialMediaPost.Visibility.PUBLIC
        ) or post.author == self.request.user or self.request.user.is_staff

    def can_manage_comment(self, comment):
        return comment.author_id == self.request.user.id

    def attach_comment_display_state(self, comment):
        comment.display_time = self.format_comment_time(comment.created_at)
        comment.likes_total = getattr(comment, "likes_total", comment.liked_by.count())
        comment.user_has_liked = any(user.pk == self.request.user.pk for user in comment.liked_by.all())
        comment.can_manage = self.can_manage_comment(comment)
        return comment

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        comment_form = kwargs.get("comment_form")
        comments = list(
            self.object.comments.select_related("author", "author__profile", "parent", "parent__author")
            .prefetch_related("liked_by")
            .annotate(likes_total=Count("liked_by", distinct=True))
        )
        comments_by_id = {comment.pk: comment for comment in comments}
        parent_comments = []
        replies_by_root = defaultdict(list)

        def get_thread_root(comment):
            root = comment
            seen = {comment.pk}
            while root.parent_id and root.parent_id in comments_by_id and root.parent_id not in seen:
                seen.add(root.parent_id)
                root = comments_by_id[root.parent_id]
            return root

        for comment in comments:
            self.attach_comment_display_state(comment)
            if comment.parent_id:
                replies_by_root[get_thread_root(comment).pk].append(comment)
            else:
                parent_comments.append(comment)
        for comment in parent_comments:
            comment.thread_replies = replies_by_root.get(comment.pk, [])

        context["latest_metric"] = self.object.metrics.first()
        context["comment_form"] = comment_form or PostCommentForm()
        context["comments"] = parent_comments
        context["comments_count"] = len(comments)
        prefetched_media_images = getattr(self.object, "ordered_images", None)
        media_images = (
            list(prefetched_media_images)
            if prefetched_media_images is not None
            else list(self.object.images.all())
        )
        if not media_images and self.object.image:
            media_images = [LegacyPostImage(self.object.image)]
        _cache_post_media_urls(self.object)
        context["media_images"] = media_images
        context["liked_by_user"] = getattr(self.object, "user_has_liked", False)
        context["shared_by_user"] = getattr(self.object, "user_has_shared", False)
        context["post_detail_field_errors"] = self.request.session.pop("post_detail_field_errors", {})
        self.request.session.modified = True
        related_posts = list(
            public_published_posts(SocialMediaPost.objects.filter(
                subscription=self.object.subscription,
                author=self.object.author,
            ))
            .exclude(pk=self.object.pk)
            .select_related("author", "campaign")
            .prefetch_related(
                Prefetch(
                    "images",
                    queryset=PostImage.objects.order_by("order", "created_at", "pk"),
                    to_attr="ordered_images",
                )
            )[:3]
        )
        for post in related_posts:
            _cache_post_media_urls(post, include_video=False)
        context["related_posts"] = related_posts
        context["show_more_posts_create_button"] = self.request.user == self.object.author
        context["can_analyze_video"] = self.object.author == self.request.user or self.is_subscription_admin()
        if (
            self.object.status == SocialMediaPost.Status.PUBLISHED
            and self.object.visibility == SocialMediaPost.Visibility.PUBLIC
        ):
            context.update(public_post_metadata(self.object, self.request))
        return context

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        canonical_redirect = self.redirect_to_canonical_url()
        if canonical_redirect:
            return canonical_redirect
        draft_redirect = self.redirect_unpublished_to_editor()
        if draft_redirect:
            return draft_redirect
        if request.user.is_authenticated and request.user != self.object.author:
            PostView.objects.get_or_create(post=self.object, viewer=request.user)
        return self.render_to_response(self.get_context_data())

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        canonical_redirect = self.redirect_to_canonical_url()
        if canonical_redirect:
            return canonical_redirect
        draft_redirect = self.redirect_unpublished_to_editor()
        if draft_redirect:
            return draft_redirect
        form = PostCommentForm(request.POST)
        if form.is_valid():
            comment = form.save(commit=False)
            comment.post = self.object
            comment.author = request.user
            parent_id = request.POST.get("parent_id")
            parent = None
            if parent_id:
                parent = get_object_or_404(
                    PostComment,
                    pk=parent_id,
                    post=self.object,
                )
                comment.parent = parent
            comment.save()
            create_notification(
                recipient=parent.author if parent else self.object.author,
                actor=request.user,
                kind=Notification.Kind.COMMENT_REPLY if parent else Notification.Kind.COMMENT,
                post=self.object,
                comment=comment,
            )
            if self.object.content_format == SocialMediaPost.Format.VIDEO:
                try:
                    video_second = max(round(float(request.POST.get("video_second", 0) or 0)), 0)
                except (TypeError, ValueError):
                    video_second = 0
                VideoEngagementEvent.objects.create(
                    post=self.object,
                    viewer=request.user,
                    kind=VideoEngagementEvent.Kind.COMMENT,
                    video_second=video_second,
                )
            messages.success(request, "Reply posted." if parent else "Comment posted.")
            return redirect("socialmanager:post_detail", pk=self.object.pk, slug=self.object.slug)
        return self.render_to_response(self.get_context_data(comment_form=form))


@method_decorator(ensure_csrf_cookie, name="dispatch")
class PostAnalyticsView(OwnerOrAdminMixin, DetailView):
    model = SocialMediaPost
    template_name = "socialmanager/posts/post_analytics.html"
    context_object_name = "post"

    def get_queryset(self):
        if not self.subscription:
            return SocialMediaPost.objects.none()
        return (
            SocialMediaPost.objects.filter(subscription=self.subscription)
            .select_related("campaign", "author")
            .prefetch_related("images")
        )

    def get_percent(self, value, views):
        if not views:
            return 0
        return min(round((value / views) * 100, 1), 100)

    def get_metric_totals(self):
        post = self.object
        latest_metric = post.metrics.order_by("-captured_at").first()
        views = post.views.count()
        likes = PostEngagement.objects.filter(post=post, kind=PostEngagement.Kind.LIKE).count()
        comments = post.comments.count()
        shares = PostEngagement.objects.filter(post=post, kind=PostEngagement.Kind.SHARE).count()

        if latest_metric:
            views = max(views, latest_metric.impressions or 0)
            likes = max(likes, latest_metric.likes or 0)
            comments = max(comments, latest_metric.comments or 0)
            shares = max(shares, latest_metric.shares or 0)

        return {
            "views": views,
            "likes": likes or post.likes_count,
            "comments": comments,
            "shares": shares or post.shares_count,
        }

    def get_first_ai_image_file(self):
        post = self.object
        if post.content_format == SocialMediaPost.Format.VIDEO:
            return None
        first_image = post.images.order_by("order", "created_at", "pk").first()
        if first_image and first_image.image:
            return first_image.image
        if post.image:
            return post.image
        return None

    def clean_comments_for_ai(self, limit=20):
        comments = (
            self.object.comments.select_related("author", "parent", "parent__author")
            .annotate(like_count=Count("liked_by"))
            .order_by("-created_at")[:80]
        )
        return _clean_comments_for_ai(comments, self.object.author_id, limit=limit)

    def get_trend_data(self):
        post = self.object
        end_date = timezone.localdate()
        start_date = end_date - timedelta(days=6)
        grouped = {}
        current_date = start_date
        while current_date <= end_date:
            grouped[current_date] = {"views": 0, "likes": 0, "comments": 0, "shares": 0}
            current_date += timedelta(days=1)

        view_rows = (
            PostView.objects.filter(post=post, viewed_at__date__gte=start_date, viewed_at__date__lte=end_date)
            .annotate(metric_date=TruncDate("viewed_at"))
            .values("metric_date")
            .annotate(total=Count("pk"))
        )
        engagement_rows = (
            PostEngagement.objects.filter(post=post, created_at__date__gte=start_date, created_at__date__lte=end_date)
            .annotate(metric_date=TruncDate("created_at"))
            .values("metric_date", "kind")
            .annotate(total=Count("pk"))
        )
        comment_rows = (
            PostComment.objects.filter(post=post, created_at__date__gte=start_date, created_at__date__lte=end_date)
            .annotate(metric_date=TruncDate("created_at"))
            .values("metric_date")
            .annotate(total=Count("pk"))
        )
        metric_rows = (
            post.metrics.filter(captured_at__date__gte=start_date, captured_at__date__lte=end_date)
            .annotate(metric_date=TruncDate("captured_at"))
            .values("metric_date")
            .annotate(
                views=Sum("impressions"),
                likes=Sum("likes"),
                comments=Sum("comments"),
                shares=Sum("shares"),
            )
        )

        for row in view_rows:
            if row["metric_date"] in grouped:
                grouped[row["metric_date"]]["views"] = row["total"] or 0
        for row in engagement_rows:
            metric_date = row["metric_date"]
            if metric_date not in grouped:
                continue
            if row["kind"] == PostEngagement.Kind.LIKE:
                grouped[metric_date]["likes"] = row["total"] or 0
            elif row["kind"] == PostEngagement.Kind.SHARE:
                grouped[metric_date]["shares"] = row["total"] or 0
        for row in comment_rows:
            if row["metric_date"] in grouped:
                grouped[row["metric_date"]]["comments"] = row["total"] or 0
        for row in metric_rows:
            metric_date = row["metric_date"]
            if metric_date not in grouped:
                continue
            grouped[metric_date]["views"] = max(grouped[metric_date]["views"], row["views"] or 0)
            grouped[metric_date]["likes"] = max(grouped[metric_date]["likes"], row["likes"] or 0)
            grouped[metric_date]["comments"] = max(grouped[metric_date]["comments"], row["comments"] or 0)
            grouped[metric_date]["shares"] = max(grouped[metric_date]["shares"], row["shares"] or 0)

        trend_points = [
            {
                "label": f"Day {index}",
                "date": f"{metric_date:%b} {metric_date.day}",
                "views": values["views"],
                "likes": values["likes"],
                "comments": values["comments"],
                "shares": values["shares"],
            }
            for index, (metric_date, values) in enumerate(sorted(grouped.items()), start=1)
        ]
        max_value = max(
            [point[key] for point in trend_points for key in ("views", "likes", "comments", "shares")] or [0]
        )
        for point in trend_points:
            for key in ("views", "likes", "comments", "shares"):
                point[f"{key}_width"] = round((point[key] / max_value) * 100, 1) if max_value else 0
        return trend_points

    def build_line_path(self, coordinates):
        if not coordinates:
            return ""
        if len(coordinates) == 1:
            x, y = coordinates[0]
            return f"M{x} {y}"
        segments = [f"M{coordinates[0][0]} {coordinates[0][1]}"]
        for index in range(1, len(coordinates)):
            previous_x, previous_y = coordinates[index - 1]
            current_x, current_y = coordinates[index]
            control_offset = round((current_x - previous_x) / 2, 2)
            segments.append(
                f"C{round(previous_x + control_offset, 2)} {previous_y}, "
                f"{round(current_x - control_offset, 2)} {current_y}, "
                f"{current_x} {current_y}"
            )
        return " ".join(segments)

    def build_area_path(self, coordinates, baseline_y):
        if not coordinates:
            return ""
        line_path = self.build_line_path(coordinates)
        first_x = coordinates[0][0]
        last_x = coordinates[-1][0]
        return f"{line_path} L{last_x} {baseline_y} L{first_x} {baseline_y} Z"

    def get_performance_chart_data(self, trend_points):
        series_config = [
            {"key": "views", "label": gettext("Views"), "color": "#2563eb"},
            {"key": "likes", "label": gettext("Likes"), "color": "#22c55e"},
            {"key": "comments", "label": gettext("Comments"), "color": "#f59e0b"},
            {"key": "shares", "label": gettext("Shares"), "color": "#8b5cf6"},
        ]
        chart_width = 900
        chart_height = 320
        left = 64
        right = 32
        top = 34
        bottom = 52
        plot_width = chart_width - left - right
        plot_height = chart_height - top - bottom
        max_value = max(
            [point[key] for point in trend_points for key in ("views", "likes", "comments", "shares")] or [0]
        )
        y_ticks, scale_max = build_count_axis_ticks(max_value, top, plot_height)
        x_step = plot_width / max(len(trend_points) - 1, 1)

        chart_series = []
        for config in series_config:
            coordinates = []
            point_values = []
            for index, point in enumerate(trend_points):
                value = point[config["key"]]
                x = round(left + (x_step * index), 2)
                y = round(top + (plot_height - ((value / scale_max) * plot_height)), 2)
                coordinates.append((x, y))
                point_values.append({"x": x, "y": y, "value": value, "date": point["date"]})
            chart_series.append(
                {
                    **config,
                    "path": self.build_line_path(coordinates),
                    "points": point_values,
                }
            )

        x_ticks = [
            {
                "label": point["date"],
                "x": round(left + (x_step * index), 2),
            }
            for index, point in enumerate(trend_points)
        ]

        return {
            "width": chart_width,
            "height": chart_height,
            "left": left,
            "right": chart_width - right,
            "top": top,
            "bottom": chart_height - bottom,
            "series": chart_series,
            "x_ticks": x_ticks,
            "y_ticks": y_ticks,
        }

    def get_video_insights_chart_data(self):
        post = self.object
        if post.content_format != SocialMediaPost.Format.VIDEO:
            return None

        watch_queryset = VideoWatchSession.objects.filter(post=post)
        viewer_rows = (
            watch_queryset
            .values("viewer_id")
            .annotate(max_seconds=Max("watched_seconds"))
        )
        viewer_watch_seconds = [max(row["max_seconds"] or 0, 0) for row in viewer_rows]
        viewer_count = len(viewer_watch_seconds)
        video_duration = (
            watch_queryset
            .aggregate(max_duration=Max("video_duration"))
            .get("max_duration")
            or 0
        )

        if not viewer_count:
            return None

        chart_width = 900
        chart_height = 320
        left = 64
        right = 32
        top = 34
        bottom = 52
        plot_width = chart_width - left - right
        plot_height = chart_height - top - bottom
        duration = max(video_duration, max(viewer_watch_seconds or [0]), 1)
        step = 5 if duration <= 60 else max(10, round(duration / 8 / 5) * 5)
        seconds = list(range(0, duration + 1, step))
        if seconds[-1] != duration:
            seconds.append(duration)

        event_rows = (
            VideoEngagementEvent.objects.filter(post=post)
            .values("video_second")
            .annotate(total=Count("pk"))
            .order_by("video_second")
        )
        event_counts_by_second = {}
        for row in event_rows:
            event_second = min(max(row["video_second"] or 0, 0), duration)
            event_counts_by_second[event_second] = (
                event_counts_by_second.get(event_second, 0) + (row["total"] or 0)
            )
        total_engagement_events = sum(event_counts_by_second.values())

        cumulative_engagement_count = 0
        last_engagement_percentage = 0

        points = []
        for second in seconds:
            retained_count = sum(1 for watched_seconds in viewer_watch_seconds if watched_seconds >= second)
            retention_percentage = round((retained_count / viewer_count) * 100, 1)
            if second > 0 and total_engagement_events:
                cumulative_engagement_count = sum(
                    total
                    for event_second, total in event_counts_by_second.items()
                    if event_second <= second
                )
                last_engagement_percentage = round((cumulative_engagement_count / total_engagement_events) * 100, 1)
            points.append(
                {
                    "label": f"{second}s",
                    "seconds": second,
                    "retained_count": retained_count,
                    "retention": retention_percentage,
                    "engagement": min(last_engagement_percentage, 100),
                    "engagement_count": cumulative_engagement_count,
                }
            )

        x_step = plot_width / max(len(points) - 1, 1)
        retention_coordinates = []
        engagement_coordinates = []
        chart_points = []

        for index, point in enumerate(points):
            x = round(left + (x_step * index), 2)
            y = round(top + (plot_height - ((point["retention"] / 100) * plot_height)), 2)
            engagement_y = round(top + (plot_height - ((point["engagement"] / 100) * plot_height)), 2)
            retention_coordinates.append((x, y))
            engagement_coordinates.append((x, engagement_y))
            chart_points.append({**point, "x": x, "retention_y": y, "engagement_y": engagement_y})

        y_ticks = []
        for index, value in enumerate((100, 75, 50, 25, 0)):
            y_ticks.append(
                {
                    "value": value,
                    "y": round(top + (plot_height * index / 4), 2),
                }
            )

        return {
            "width": chart_width,
            "height": chart_height,
            "left": left,
            "right": chart_width - right,
            "top": top,
            "bottom": chart_height - bottom,
            "viewer_count": viewer_count,
            "retention_path": self.build_line_path(retention_coordinates),
            "engagement_path": self.build_line_path(engagement_coordinates),
            "engagement_area_path": self.build_area_path(engagement_coordinates, chart_height - bottom),
            "points": chart_points,
            "x_ticks": [{"label": point["label"], "x": point["x"]} for point in chart_points],
            "y_ticks": y_ticks,
        }

    def get_post_ai_payload(self, metrics, rate_lookup):
        post = self.object
        image_file = self.get_first_ai_image_file()
        comment_payload = self.clean_comments_for_ai()
        return {
            "post": {
                "id": post.pk,
                "title": post.title,
                "content_format": post.content_format,
                "post_type": post.get_content_format_display(),
                "platform": post.get_platform_display(),
                "visibility": post.visibility,
                "caption": post.caption,
                "article_caption": post.article_caption,
                "article_body": getattr(post, "article_body", "") or getattr(post, "body", ""),
                "hashtags": post.hashtags,
                "created_at": post.created_at.isoformat() if post.created_at else "",
                "published_at": post.published_at.isoformat() if post.published_at else "",
                "status": post.status,
            },
            "media": {
                "media_type": "video" if post.content_format == SocialMediaPost.Format.VIDEO else "image" if image_file else "none",
                "has_supported_image_candidate": bool(image_file),
                "first_image_name": getattr(image_file, "name", "") if image_file else "",
            },
            "image_file": image_file,
            "creator_context": build_creator_context(post.author),
            "comments": comment_payload,
            "meaningful_comments": comment_payload["meaningful_comments"],
            "meaningful_comment_count": comment_payload["meaningful_comment_count"],
            "ignored_comment_count": comment_payload["ignored_comment_count"],
            "ignored_comment_reasons": comment_payload["ignored_comment_reasons"],
            "metrics": {
                "views": metrics["views"],
                "likes": metrics["likes"],
                "comments": metrics["comments"],
                "shares": metrics["shares"],
            },
            "rates": rate_lookup,
        }

    def get_retention_ai_payload(self, metrics, rate_lookup, video_insights_chart):
        return {
            "post": {
                "id": self.object.pk,
                "title": self.object.title,
                "platform": self.object.get_platform_display(),
            },
            "viewer_count": video_insights_chart.get("viewer_count", 0),
            "metrics": {
                "views": metrics["views"],
                "likes": metrics["likes"],
                "comments": metrics["comments"],
                "shares": metrics["shares"],
            },
            "rates": rate_lookup,
            "points": [
                {
                    "label": point.get("label"),
                    "seconds": point.get("seconds"),
                    "retention": point.get("retention"),
                    "engagement": point.get("engagement"),
                    "engagement_count": point.get("engagement_count"),
                    "retained_count": point.get("retained_count"),
                }
                for point in video_insights_chart.get("points", [])
            ],
        }

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        metrics = self.get_metric_totals()
        total_engagements = metrics["likes"] + metrics["comments"] + metrics["shares"]
        analytics_history = self.get_trend_data()
        performance_chart = self.get_performance_chart_data(analytics_history)
        video_insights_chart = self.get_video_insights_chart_data()
        engagement_breakdown = [
            {"label": "Engagement rate", "value": self.get_percent(total_engagements, metrics["views"])},
            {"label": "Like rate", "value": self.get_percent(metrics["likes"], metrics["views"])},
            {"label": "Comment rate", "value": self.get_percent(metrics["comments"], metrics["views"])},
            {"label": "Share rate", "value": self.get_percent(metrics["shares"], metrics["views"])},
        ]
        for item in engagement_breakdown:
            item["width"] = min(item["value"], 100)
        rate_lookup = {
            item["label"].lower().replace(" ", "_"): item["value"]
            for item in engagement_breakdown
        }
        has_video_retention_insight = bool(video_insights_chart)
        media_images = list(self.object.images.all())
        if not media_images and self.object.image:
            media_images = [LegacyPostImage(self.object.image)]
        context.update(
            {
                "metrics": metrics,
                "engagement_breakdown": engagement_breakdown,
                "analytics_history": analytics_history,
                "performance_chart": performance_chart,
                "has_performance_data": any(
                    point[key]
                    for point in analytics_history
                    for key in ("views", "likes", "comments", "shares")
                ),
                "retention_chart": video_insights_chart,
                "has_video_retention_insight": has_video_retention_insight,
                "media_images": media_images,
                "is_video_post": self.object.content_format == SocialMediaPost.Format.VIDEO,
            }
        )
        return context


class PostAIInsightView(AIMemberRequiredMixin, OwnerOrAdminMixin, View):
    model = SocialMediaPost

    def get_queryset(self):
        return SocialMediaPost.objects.filter(subscription=self.subscription)

    def get_object(self):
        return get_object_or_404(self.get_queryset(), pk=self.kwargs.get("pk"))

    def get(self, request, *args, **kwargs):
        post = self.get_object()
        ai_language = get_user_ai_language(request.user)
        topic = f"ai-insight:post:{AI_ANALYSIS_CACHE_VERSION}:{post.pk}:{ai_language_cache_key(ai_language)}"
        payload = None
        try:
            cached = get_cached_ai_insight(post.subscription, topic)
            if cached:
                return ai_insight_json_response(cached.generated_caption, True)
        except Exception as exc:
            logger.warning("Post AI insight cache/render failed: %s", exc.__class__.__name__)

        try:
            analytics_view = PostAnalyticsView()
            analytics_view.object = post
            metrics = analytics_view.get_metric_totals()
            total_engagements = metrics["likes"] + metrics["comments"] + metrics["shares"]
            engagement_breakdown = [
                {"label": "Engagement rate", "value": analytics_view.get_percent(total_engagements, metrics["views"])},
                {"label": "Like rate", "value": analytics_view.get_percent(metrics["likes"], metrics["views"])},
                {"label": "Comment rate", "value": analytics_view.get_percent(metrics["comments"], metrics["views"])},
                {"label": "Share rate", "value": analytics_view.get_percent(metrics["shares"], metrics["views"])},
            ]
            rate_lookup = {
                item["label"].lower().replace(" ", "_"): item["value"]
                for item in engagement_breakdown
            }
            payload = analytics_view.get_post_ai_payload(metrics, rate_lookup)
            try:
                report = generate_post_analysis(payload, language=ai_language)
            except Exception as exc:
                logger.warning("Post AI generation failed: %s", exc.__class__.__name__)
                report = generate_post_rule_based_analysis(payload, language=ai_language)
            if post.content_format == SocialMediaPost.Format.VIDEO:
                report = (
                    "Analysis basis\n"
                    "This insight uses the post title, caption, hashtags, metrics, and comments. "
                    "Gemini did not receive or inspect the video frames.\n\n"
                    f"{report}"
                )
        except Exception as exc:
            logger.warning("Post AI payload/fallback failed: %s", exc.__class__.__name__)
            report = (
                "AI insight is temporarily unavailable. The post analytics page is still available; "
                "please try generating the insight again later."
            )

        try:
            cache_ai_insight(post.subscription, request.user, topic, report, "Post analytics insight")
        except Exception as exc:
            logger.warning("Post AI insight cache write failed: %s", exc.__class__.__name__)

        try:
            return ai_insight_json_response(report, False)
        except Exception as exc:
            logger.warning("Post AI insight render failed: %s", exc.__class__.__name__)
            return JsonResponse(
                {
                    "success": False,
                    "error": "AI insight is temporarily unavailable. Please try again later.",
                },
                status=200,
            )


class PostVideoAnalysisView(AIMemberRequiredMixin, OwnerOrAdminMixin, View):
    model = SocialMediaPost

    def get_queryset(self):
        return SocialMediaPost.objects.filter(subscription=self.subscription)

    def get_object(self):
        return get_object_or_404(self.get_queryset(), pk=self.kwargs.get("pk"))

    def post(self, request, *args, **kwargs):
        post = self.get_object()
        if post.content_format != SocialMediaPost.Format.VIDEO or not post.video_file:
            return JsonResponse(
                {"success": False, "error": "Only uploaded video posts can be analyzed."},
                status=400,
            )

        source_object_name = post.video_file.name
        ai_language = get_user_ai_language(request.user)
        existing = VideoAnalysis.objects.filter(
            post=post,
            source_object_name=source_object_name,
            status=VideoAnalysis.Status.SUCCEEDED,
        ).first()
        if existing:
            if existing.guidance_language != ai_language:
                existing.creator_guidance = generate_video_content_guidance(
                    existing.result,
                    {
                        "title": post.title,
                        "caption": post.caption,
                        "hashtags": post.hashtags,
                        "platform": post.get_platform_display(),
                    },
                    language=ai_language,
                )
                existing.guidance_language = ai_language
                existing.save(update_fields=["creator_guidance", "guidance_language", "updated_at"])
            return JsonResponse(
                {
                    "success": True,
                    "cached": True,
                    "analysis": existing.result,
                    "guidance": existing.creator_guidance,
                }
            )

        record, _ = VideoAnalysis.objects.update_or_create(
            post=post,
            defaults={
                "source_object_name": source_object_name,
                "status": VideoAnalysis.Status.PROCESSING,
                "result": {},
                "creator_guidance": {},
                "guidance_language": "",
                "error_message": "",
                "analyzed_at": None,
            },
        )
        try:
            analysis = analyze_gcs_video(post)
            guidance = generate_video_content_guidance(
                analysis,
                {
                    "title": post.title,
                    "caption": post.caption,
                    "hashtags": post.hashtags,
                    "platform": post.get_platform_display(),
                },
                language=ai_language,
            )
            record.status = VideoAnalysis.Status.SUCCEEDED
            record.result = analysis
            record.creator_guidance = guidance
            record.guidance_language = ai_language
            record.error_message = ""
            record.analyzed_at = timezone.now()
            record.save()
            return JsonResponse(
                {"success": True, "cached": False, "analysis": analysis, "guidance": guidance}
            )
        except Exception as exc:
            logger.warning("Video analysis failed for post_id=%s: %s", post.pk, exc.__class__.__name__)
            record.status = VideoAnalysis.Status.FAILED
            record.error_message = "Video analysis is temporarily unavailable. Your post was not affected."
            record.analyzed_at = timezone.now()
            record.save(update_fields=["status", "error_message", "analyzed_at", "updated_at"])
            return JsonResponse(
                {"success": False, "error": record.error_message},
                status=200,
            )


class PostRetentionAIInsightView(AIMemberRequiredMixin, OwnerOrAdminMixin, View):
    model = SocialMediaPost

    def get_queryset(self):
        return SocialMediaPost.objects.filter(subscription=self.subscription)

    def get_object(self):
        return get_object_or_404(self.get_queryset(), pk=self.kwargs.get("pk"))

    def get(self, request, *args, **kwargs):
        post = self.get_object()
        ai_language = get_user_ai_language(request.user)
        topic = f"{get_retention_ai_insight_topic(post.pk)}:{ai_language_cache_key(ai_language)}"
        cached = get_cached_ai_insight(post.subscription, topic)
        if cached:
            return ai_insight_json_response(cached.generated_caption, True)

        try:
            analytics_view = PostAnalyticsView()
            analytics_view.object = post
            metrics = analytics_view.get_metric_totals()
            total_engagements = metrics["likes"] + metrics["comments"] + metrics["shares"]
            rate_lookup = {
                "engagement_rate": analytics_view.get_percent(total_engagements, metrics["views"]),
                "like_rate": analytics_view.get_percent(metrics["likes"], metrics["views"]),
                "comment_rate": analytics_view.get_percent(metrics["comments"], metrics["views"]),
                "share_rate": analytics_view.get_percent(metrics["shares"], metrics["views"]),
            }
            video_insights_chart = analytics_view.get_video_insights_chart_data()
            if not video_insights_chart:
                return JsonResponse({"success": False, "error": "No retention data is available yet."}, status=400)
            payload = analytics_view.get_retention_ai_payload(metrics, rate_lookup, video_insights_chart)
            try:
                report = generate_video_retention_analysis(payload, language=ai_language)
            except Exception:
                report = generate_video_retention_rule_based_analysis(payload, language=ai_language)
            report = (
                "Analysis basis\n"
                "This insight uses watch-retention and timed-engagement data. "
                "Gemini did not receive or inspect the video frames.\n\n"
                f"{report}"
            )
            cache_ai_insight(post.subscription, request.user, topic, report, "Post retention insight")
            return ai_insight_json_response(report, False)
        except Exception:
            return JsonResponse({"success": False, "error": "Unable to generate AI insight."}, status=500)


class VideoWatchTrackView(ActiveSubscriptionMixin, View):
    def post(self, request, *args, **kwargs):
        post_id = kwargs.get("post_id") or kwargs.get("pk")
        post_queryset = SocialMediaPost.objects.filter(
            pk=post_id,
            content_format=SocialMediaPost.Format.VIDEO,
        )
        if not request.user.is_staff:
            post_queryset = post_queryset.filter(
                Q(
                    status=SocialMediaPost.Status.PUBLISHED,
                    visibility=SocialMediaPost.Visibility.PUBLIC,
                )
                | Q(author=request.user)
            )
        post = get_object_or_404(post_queryset)

        try:
            watched_seconds = float(request.POST.get("watched_seconds", 0) or 0)
            video_duration = float(request.POST.get("video_duration", 0) or 0)
        except (TypeError, ValueError):
            return HttpResponseBadRequest("Invalid watch progress.")

        watched_seconds = max(watched_seconds, 0)
        video_duration = max(video_duration, 0)
        watched_percentage = 0
        if video_duration:
            watched_percentage = min((watched_seconds / video_duration) * 100, 100)
        watched_seconds = round(watched_seconds)
        video_duration = round(video_duration)
        watched_percentage = round(watched_percentage, 2)
        if watched_seconds <= 0 or video_duration <= 0:
            return JsonResponse(
                {
                    "ok": True,
                    "ignored": True,
                    "reason": "No watch progress to save.",
                }
            )

        watch_session = (
            VideoWatchSession.objects.filter(post=post, viewer=request.user)
            .order_by("-watched_seconds", "-updated_at")
            .first()
        )
        if not watch_session:
            watch_session = VideoWatchSession.objects.create(
                post=post,
                viewer=request.user,
                watched_seconds=watched_seconds,
                video_duration=video_duration,
                watched_percentage=watched_percentage,
            )
        elif watched_seconds > watch_session.watched_seconds:
            watch_session.watched_seconds = watched_seconds
            watch_session.video_duration = video_duration
            watch_session.watched_percentage = watched_percentage
            watch_session.save(update_fields=["watched_seconds", "video_duration", "watched_percentage", "updated_at"])

        return JsonResponse(
            {
                "ok": True,
                "watched_seconds": watch_session.watched_seconds,
                "video_duration": watch_session.video_duration,
                "watched_percentage": watch_session.watched_percentage,
            }
        )


class PostCommentUpdateView(ActiveSubscriptionMixin, View):
    def can_manage_comment(self, comment):
        return comment.author == self.request.user

    def post(self, request, *args, **kwargs):
        comment = get_object_or_404(
            PostComment.objects.select_related("post"),
            pk=kwargs.get("comment_id") or kwargs.get("pk"),
        )
        if not self.can_manage_comment(comment):
            return HttpResponseForbidden("You cannot modify this comment.")
        form = PostCommentForm(request.POST, instance=comment)

        if form.is_valid():
            comment = form.save(commit=False)
            comment.is_edited = True
            comment.save()
            messages.success(request, "Comment updated.")
        else:
            messages.error(request, "Please enter a comment before saving.")

        return redirect("socialmanager:post_detail", pk=comment.post_id, slug=comment.post.slug)


class PostCommentDeleteView(ActiveSubscriptionMixin, View):
    def can_manage_comment(self, comment):
        return comment.author == self.request.user

    def post(self, request, *args, **kwargs):
        comment = get_object_or_404(
            PostComment.objects.select_related("post"),
            pk=kwargs.get("comment_id") or kwargs.get("pk"),
        )
        if not self.can_manage_comment(comment):
            return HttpResponseForbidden("You cannot modify this comment.")
        post_id = comment.post_id
        post_slug = comment.post.slug
        comment.delete()
        messages.success(request, "Comment deleted.")
        return redirect("socialmanager:post_detail", pk=post_id, slug=post_slug)


class CommentLikeToggleView(ActiveSubscriptionMixin, View):
    def post(self, request, *args, **kwargs):
        comment = get_object_or_404(
            PostComment.objects.select_related("post"),
            pk=kwargs.get("comment_id") or kwargs.get("pk"),
        )
        post = comment.post
        if (
            (
                post.status != SocialMediaPost.Status.PUBLISHED
                or post.visibility != SocialMediaPost.Visibility.PUBLIC
            )
            and post.author != request.user
            and not request.user.is_staff
        ):
            return HttpResponseBadRequest("You do not have permission to like this comment.")

        if comment.liked_by.filter(pk=request.user.pk).exists():
            comment.liked_by.remove(request.user)
            active = False
        else:
            comment.liked_by.add(request.user)
            active = True
            create_notification(
                recipient=comment.author,
                actor=request.user,
                kind=Notification.Kind.COMMENT_LIKE,
                post=comment.post,
                comment=comment,
            )

        return JsonResponse(
            {
                "liked": active,
                "like_count": comment.liked_by.count(),
                "active": active,
                "likes_count": comment.liked_by.count(),
            }
        )


class PostDetailSectionUpdateView(ActiveSubscriptionMixin, View):
    def post(self, request, *args, **kwargs):
        if not self.subscription:
            return HttpResponseBadRequest("No active subscription.")

        post = get_object_or_404(
            SocialMediaPost,
            pk=kwargs.get("pk"),
            subscription=self.subscription,
        )

        if post.author != request.user and not request.user.is_staff:
            return HttpResponseBadRequest("You do not have permission to edit this post.")

        section = kwargs.get("section")
        update_fields = []
        replacement_video_uploaded = False

        if section == "article":
            title = request.POST.get("title", "").strip()
            if title:
                post.title = title
                update_fields.append("title")
            post.caption = sanitize_article_html(request.POST.get("caption", ""))
            post.article_caption = request.POST.get("article_caption", "").strip()
            post.hashtags = request.POST.get("hashtags", "").strip()
            update_fields.extend(["caption", "article_caption", "hashtags"])
        elif section == "meta":
            post.article_caption = request.POST.get("article_caption", "").strip()
            post.hashtags = request.POST.get("hashtags", "").strip()
            update_fields.extend(["article_caption", "hashtags"])
        elif section == "post":
            post.caption = request.POST.get("caption", "").strip()
            post.hashtags = request.POST.get("hashtags", "").strip()
            update_fields.extend(["caption", "hashtags"])

            if post.content_format in (SocialMediaPost.Format.IMAGE, SocialMediaPost.Format.CAROUSEL):
                deleted_ids = {
                    int(value)
                    for raw_value in request.POST.get("deleted_image_ids", "").split(",")
                    for value in [raw_value.strip()]
                    if value.isdigit()
                }
                new_images = request.FILES.getlist("new_images")
                if post.image and not post.images.exists():
                    PostImage.objects.create(post=post, image=post.image, order=0)

                existing_images = post.images.exclude(pk__in=deleted_ids)

                if not existing_images.exists() and not new_images:
                    request.session["post_detail_field_errors"] = {
                        "image": "At least one image is required.",
                    }
                    return redirect("socialmanager:post_detail", pk=post.pk, slug=post.slug)

                if deleted_ids:
                    post.images.filter(pk__in=deleted_ids).delete()

                next_order = post.images.count()
                for image in new_images:
                    PostImage.objects.create(post=post, image=image, order=next_order)
                    next_order += 1

                first_image = post.images.order_by("order", "created_at", "pk").first()
                if first_image:
                    post.image = first_image.image
                    update_fields.append("image")

            if post.content_format == SocialMediaPost.Format.VIDEO:
                replacement = request.FILES.get("replacement_video")
                if replacement:
                    try:
                        validate_video_upload(replacement)
                    except ValidationError as exc:
                        request.session["post_detail_field_errors"] = {
                            "replacement_video": str(exc.messages[0]),
                        }
                        return redirect("socialmanager:post_detail", pk=post.pk, slug=post.slug)
                    post.video_file = replacement
                    update_fields.append("video_file")
                    replacement_video_uploaded = True
                elif not post.video_file:
                    request.session["post_detail_field_errors"] = {
                        "replacement_video": "Please upload a video before saving this post.",
                    }
                    return redirect("socialmanager:post_detail", pk=post.pk, slug=post.slug)
        else:
            return HttpResponseBadRequest("Unsupported edit section.")

        if update_fields:
            update_fields.append("updated_at")
            try:
                post.full_clean()
            except ValidationError as exc:
                if hasattr(exc, "message_dict"):
                    request.session["post_detail_field_errors"] = {
                        field: ", ".join(str(message) for message in messages_for_field)
                        for field, messages_for_field in exc.message_dict.items()
                    }
                else:
                    request.session["post_detail_field_errors"] = {
                        "caption": ", ".join(str(message) for message in exc.messages),
                    }
                return redirect("socialmanager:post_detail", pk=post.pk, slug=post.slug)
            post.save(update_fields=update_fields)
            if replacement_video_uploaded:
                generate_video_thumbnail(post, force=True)
            messages.success(request, "Post updated.")

        return redirect("socialmanager:post_detail", pk=post.pk, slug=post.slug)


class PostEngagementToggleView(ActiveSubscriptionMixin, View):
    counter_fields = {
        PostEngagement.Kind.LIKE: "likes_count",
        PostEngagement.Kind.SHARE: "shares_count",
    }

    def post(self, request, *args, **kwargs):
        kind = kwargs.get("kind")
        counter_field = self.counter_fields.get(kind)

        if not counter_field:
            return HttpResponseBadRequest("Unsupported engagement action.")

        with transaction.atomic():
            post = get_object_or_404(
                SocialMediaPost.objects.select_for_update(),
                pk=kwargs.get("pk"),
            )
            if (
                (
                    post.status != SocialMediaPost.Status.PUBLISHED
                    or post.visibility != SocialMediaPost.Visibility.PUBLIC
                )
                and post.author != request.user
                and not request.user.is_staff
            ):
                return HttpResponseBadRequest("You do not have permission to engage with this post.")
            engagement, created = PostEngagement.objects.get_or_create(
                post=post,
                user=request.user,
                kind=kind,
            )

            if created:
                active = True
                notification_kind = (
                    Notification.Kind.SHARE
                    if kind == PostEngagement.Kind.SHARE
                    else Notification.Kind.LIKE
                )
                create_notification(
                    recipient=post.author,
                    actor=request.user,
                    kind=notification_kind,
                    post=post,
                )
                if post.content_format == SocialMediaPost.Format.VIDEO:
                    try:
                        video_second = max(round(float(request.POST.get("video_second", 0) or 0)), 0)
                    except (TypeError, ValueError):
                        video_second = 0
                    event_kind = (
                        VideoEngagementEvent.Kind.SHARE
                        if kind == PostEngagement.Kind.SHARE
                        else VideoEngagementEvent.Kind.LIKE
                    )
                    VideoEngagementEvent.objects.create(
                        post=post,
                        viewer=request.user,
                        kind=event_kind,
                        video_second=video_second,
                    )
            else:
                engagement.delete()
                active = False

            likes_count = PostEngagement.objects.filter(post=post, kind=PostEngagement.Kind.LIKE).count()
            shares_count = PostEngagement.objects.filter(post=post, kind=PostEngagement.Kind.SHARE).count()
            post.likes_count = likes_count
            post.shares_count = shares_count
            post.save(update_fields=["likes_count", "shares_count"])
            comments_count = post.comments.count()

        return JsonResponse(
            {
                "active": active,
                "likes_count": likes_count,
                "comments_count": comments_count,
                "shares_count": shares_count,
            }
        )


class PostAIFeedbackView(AIMemberRequiredMixin, ActiveSubscriptionMixin, View):
    allowed_feedback_types = {"title", "caption", "hashtags"}

    def post(self, request, *args, **kwargs):
        if request.content_type and request.content_type.startswith("multipart/form-data"):
            try:
                payload = json.loads(request.POST.get("payload") or "{}")
            except json.JSONDecodeError:
                return JsonResponse({"error": "Invalid JSON payload."}, status=400)
        else:
            try:
                payload = json.loads(request.body.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                return JsonResponse({"error": "Invalid JSON payload."}, status=400)

        feedback_type = payload.get("feedback_type")
        if feedback_type not in self.allowed_feedback_types:
            return JsonResponse({"error": "Choose title, caption, or hashtags feedback."}, status=400)

        article_text = (payload.get("article_text") or "").strip()
        user_text_context = any(
            (payload.get(field_name) or "").strip()
            for field_name in ("title", "caption", "article_caption", "hashtags", "current_value")
        )
        stored_post = None
        if payload.get("post_id") and self.subscription:
            stored_post_queryset = SocialMediaPost.objects.filter(
                pk=payload.get("post_id"),
                subscription=self.subscription,
            )
            if not request.user.is_staff:
                stored_post_queryset = stored_post_queryset.filter(author=request.user)
            stored_post = stored_post_queryset.first()

        image_file = request.FILES.get("image")
        if image_file and (
            not (getattr(image_file, "content_type", "") or "").startswith("image/")
            or getattr(image_file, "size", 0) > 2 * 1024 * 1024
        ):
            logger.info(
                "Create-post AI image omitted: name=%r content_type=%r size=%r",
                getattr(image_file, "name", ""),
                getattr(image_file, "content_type", ""),
                getattr(image_file, "size", 0),
            )
            image_file = None
        if not image_file and stored_post:
            if stored_post.content_format == SocialMediaPost.Format.VIDEO and stored_post.video_thumbnail:
                image_file = stored_post.video_thumbnail
            else:
                stored_image = stored_post.images.order_by("order", "created_at", "pk").first()
                if stored_image and stored_image.image:
                    image_file = stored_image.image
                elif stored_post.image:
                    image_file = stored_post.image

        video_file = request.FILES.get("video")
        is_video_post = (payload.get("post_type") or "").strip().lower() == "video"
        uploaded_video_object_name = (payload.get("uploaded_video_object_name") or "").strip()
        if is_video_post and not video_file and uploaded_video_object_name:
            expected_pattern = rf"^social_videos/user_{request.user.pk}/[0-9a-f]{{8}}-[0-9a-f]{{4}}-[1-5][0-9a-f]{{3}}-[89ab][0-9a-f]{{3}}-[0-9a-f]{{12}}\.(?:mp4|webm|mov)$"
            if not re.fullmatch(expected_pattern, uploaded_video_object_name, flags=re.IGNORECASE):
                return JsonResponse({"error": "The uploaded video reference is invalid. Please upload the video again."}, status=400)
            try:
                if default_storage.exists(uploaded_video_object_name):
                    video_file = default_storage.open(uploaded_video_object_name, "rb")
            except Exception:
                logger.warning("Create-post AI could not open the private uploaded video")
        if is_video_post and not video_file and stored_post and stored_post.video_file:
            video_file = stored_post.video_file

        if not article_text and not user_text_context and not image_file and not video_file:
            return JsonResponse(
                {"error": "Please add a short description, title, or caption before using AI feedback."},
                status=400,
            )
        user_settings, _ = UserSettings.objects.get_or_create(user=request.user)
        payload = {
            **payload,
            "creator_context": build_creator_context(request.user),
            "ai_tone": user_settings.get_ai_tone_display(),
            "ai_language": user_settings.ai_language,
            "ai_language_display": user_settings.get_ai_language_display(),
            "ai_hashtag_count": user_settings.ai_hashtag_count,
            "image_file": image_file,
            "video_file": video_file,
        }

        try:
            result = generate_post_field_feedback(payload)
        except GeminiQuotaError as exc:
            return JsonResponse(
                {
                    "error": ai_quota_limit_message(user_settings.ai_language, exc.retry_delay_seconds),
                    "retry_delay_seconds": exc.retry_delay_seconds,
                },
                status=429,
            )
        except ValueError as exc:
            logger.exception("Create-post AI feedback configuration failed")
            return JsonResponse(
                {
                    "error": str(exc) if settings.DEBUG else AI_TEMPORARILY_UNAVAILABLE_MESSAGE,
                },
                status=503,
            )
        except Exception as exc:
            logger.exception("Create-post AI feedback request failed")
            return JsonResponse(
                {
                    "error": str(exc) if settings.DEBUG else AI_TEMPORARILY_UNAVAILABLE_MESSAGE,
                },
                status=502,
            )

        suggestion = result.suggestion
        if feedback_type == "title":
            suggestion = limit_text(suggestion, POST_TITLE_MAX_LENGTH)
        elif feedback_type == "caption":
            suggestion = limit_text(suggestion, POST_CAPTION_MAX_LENGTH)
        elif feedback_type == "hashtags":
            suggestion = limit_hashtags_text(suggestion, user_settings.ai_hashtag_count)

        media_notice = ""
        used_video_input = bool(getattr(result, "used_video_input", False))
        fallback_reason = str(getattr(result, "fallback_reason", "") or "")
        if is_video_post:
            media_notice = (
                "Gemini analyzed the uploaded video frames for this suggestion."
                if used_video_input
                else "Video frames were not read. This suggestion uses the title, caption, hashtags, thumbnail, and available media metadata."
            )
        return JsonResponse({
            "suggestion": suggestion,
            "explanation": result.explanation,
            "used_video_input": used_video_input,
            "fallback_reason": fallback_reason,
            "media_notice": media_notice,
        })


@method_decorator(csrf_protect, name="dispatch")
class VideoUploadStartView(ActiveSubscriptionMixin, View):
    """Create a private GCS resumable session; video bytes never pass through Django."""

    def post(self, request, *args, **kwargs):
        if not settings.USE_GCS:
            return JsonResponse(
                {"error": "Direct video upload is unavailable in this environment."},
                status=503,
            )

        try:
            payload = json.loads(request.body.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            return JsonResponse({"error": "Invalid JSON payload."}, status=400)

        filename = Path(str(payload.get("filename") or "")).name
        content_type = str(payload.get("content_type") or "").lower().strip()
        extension = Path(filename).suffix.lower()
        try:
            size = int(payload.get("size"))
        except (TypeError, ValueError):
            size = 0

        if content_type not in SUPPORTED_VIDEO_UPLOAD_TYPES:
            return JsonResponse({"error": "Upload a supported video file: MP4, WebM, or MOV."}, status=400)
        if extension not in SUPPORTED_VIDEO_UPLOAD_EXTENSIONS:
            return JsonResponse({"error": "Upload a supported video file: MP4, WebM, or MOV."}, status=400)
        if SUPPORTED_VIDEO_UPLOAD_TYPES[content_type] != extension:
            return JsonResponse({"error": "The video filename does not match its content type."}, status=400)

        max_bytes = settings.VIDEO_UPLOAD_MAX_BYTES
        if size <= 0:
            return JsonResponse({"error": "Choose a non-empty video file."}, status=400)
        if size > max_bytes:
            max_mb = max_bytes // (1024 * 1024)
            return JsonResponse(
                {"error": f"This video is too large. Choose a video smaller than {max_mb} MB."},
                status=400,
            )

        object_name = f"social_videos/user_{request.user.pk}/{uuid.uuid4()}{extension}"
        origin = request.headers.get("Origin") or settings.SITE_URL.rstrip("/") or None
        try:
            blob = default_storage.bucket.blob(object_name)
            upload_url = blob.create_resumable_upload_session(
                content_type=content_type,
                size=size,
                origin=origin,
                if_generation_match=0,
            )
        except Exception:
            logger.exception("Could not create resumable video upload session for object=%r", object_name)
            return JsonResponse({"error": "Video upload could not be started. Please try again."}, status=502)

        # The session URL is a bearer capability and must never be logged.
        logger.info(
            "Created resumable video upload session: object_name=%r content_type=%r size=%r user_id=%r",
            object_name,
            content_type,
            size,
            request.user.pk,
        )
        response = JsonResponse({"upload_url": upload_url, "object_name": object_name})
        response["Cache-Control"] = "no-store"
        return response


class PostFormMixin(SafeNextRedirectMixin, ActiveSubscriptionMixin):
    model = SocialMediaPost
    form_class = SocialMediaPostForm
    template_name = "socialmanager/posts/post_form.html"
    success_url = reverse_lazy("socialmanager:post_list")
    max_illustration_images = 10

    def handle_missing_subscription(self):
        messages.error(
            self.request,
            "Your account is not connected to an active subscription workspace. Please create or join a workspace before saving posts.",
        )
        return redirect("socialmanager:profile")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["subscription"] = self.subscription
        kwargs["user_settings"] = UserSettings.objects.get_or_create(user=self.request.user)[0]
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["ai_result"] = kwargs.get("ai_result")
        form = context.get("form")
        current_format = None
        current_status = None
        scheduled_for = None

        if form:
            current_format = form["content_format"].value() or current_format
            current_status = form["status"].value() or current_status
            scheduled_raw = form["scheduled_for"].value()
            if scheduled_raw:
                try:
                    scheduled_for = timezone.datetime.fromisoformat(str(scheduled_raw))
                except ValueError:
                    scheduled_for = None

        if not current_format and getattr(self, "object", None):
            current_format = self.object.content_format
        if not current_status and getattr(self, "object", None):
            current_status = self.object.status
        if not scheduled_for and getattr(self, "object", None):
            scheduled_for = self.object.scheduled_for

        format_to_ui = {
            SocialMediaPost.Format.ARTICLE: "article",
            SocialMediaPost.Format.IMAGE: "illustration",
            SocialMediaPost.Format.VIDEO: "video",
            SocialMediaPost.Format.CAROUSEL: "illustration",
        }
        current_format = current_format or SocialMediaPost.Format.ARTICLE
        current_status = current_status or SocialMediaPost.Status.DRAFT
        localized_schedule = timezone.localtime(scheduled_for) if scheduled_for and timezone.is_aware(scheduled_for) else scheduled_for

        draft_queryset = (
            SocialMediaPost.objects.filter(
                subscription=self.subscription,
                author=self.request.user,
                status=SocialMediaPost.Status.DRAFT,
            )
            .select_related("campaign")
            .prefetch_related("campaign_groups")
            .order_by("-updated_at")
            if self.subscription
            else SocialMediaPost.objects.none()
        )
        if getattr(self, "object", None):
            draft_queryset = draft_queryset.exclude(pk=self.object.pk)

        context["draft_posts"] = draft_queryset[:8]
        context["initial_post_type"] = format_to_ui.get(current_format, "article")
        context["initial_post_status"] = current_status
        context["initial_schedule_date"] = localized_schedule.strftime("%Y-%m-%d") if localized_schedule else ""
        context["initial_schedule_time"] = localized_schedule.strftime("%H:%M") if localized_schedule else ""
        context["direct_video_upload_enabled"] = settings.USE_GCS
        context["direct_video_upload_max_bytes"] = settings.VIDEO_UPLOAD_MAX_BYTES
        context["video_upload_max_bytes"] = settings.VIDEO_FORM_UPLOAD_MAX_BYTES
        context["gemini_video_max_bytes"] = settings.GEMINI_VIDEO_MAX_BYTES
        context["gemini_video_max_seconds"] = settings.GEMINI_VIDEO_MAX_SECONDS
        return context

    def get_uploaded_video_object_name(self):
        object_name = (self.request.POST.get("uploaded_video_object_name") or "").strip()
        if not object_name:
            return ""
        expected_pattern = rf"^social_videos/user_{self.request.user.pk}/[0-9a-f]{{8}}-[0-9a-f]{{4}}-[1-5][0-9a-f]{{3}}-[89ab][0-9a-f]{{3}}-[0-9a-f]{{12}}\.(?:mp4|webm|mov)$"
        if not re.fullmatch(expected_pattern, object_name, flags=re.IGNORECASE):
            raise ValidationError("The uploaded video reference is invalid. Please upload the video again.")
        return object_name

    def get_fallback_success_url(self):
        return reverse("socialmanager:post_list")

    def get_success_url(self):
        # Successful publish, draft and schedule submissions always return to Feed.
        return reverse("socialmanager:post_list")

    def handle_ai_generation(self, form):
        if not user_has_active_subscription(self.request.user):
            return ai_members_only_response()

        topic = form.data.get("ai_topic", "").strip()
        platform = form.data.get("platform", "").strip()
        user_settings, _ = UserSettings.objects.get_or_create(user=self.request.user)
        tone = user_settings.get_ai_tone_display()
        hashtag_count = min(max(user_settings.ai_hashtag_count or 5, 1), POST_HASHTAGS_MAX_COUNT)
        image_file = self.get_first_ai_image_file()
        try:
            result = generate_caption_and_hashtags(
                topic,
                platform,
                tone,
                language=user_settings.ai_language,
                hashtag_count=hashtag_count,
                image_file=image_file,
                creator_context=build_creator_context(self.request.user),
            )
        except GeminiQuotaError as exc:
            messages.error(self.request, gettext(ai_quota_limit_message(user_settings.ai_language, exc.retry_delay_seconds)))
            return self.render_to_response(self.get_context_data(form=form))
        except (ValueError, RuntimeError):
            messages.error(self.request, gettext(AI_TEMPORARILY_UNAVAILABLE_MESSAGE))
            return self.render_to_response(self.get_context_data(form=form))
        result.caption = limit_text(result.caption, POST_CAPTION_MAX_LENGTH)
        result.hashtags_text = limit_hashtags_text(result.hashtags_text, hashtag_count)
        if self.subscription:
            store_suggestion_history(self.subscription, self.request.user, topic or "Untitled topic", platform or "instagram", tone or "Professional", result)
        data = form.data.copy()
        data["ai_tone"] = tone
        data["ai_caption_language"] = user_settings.ai_language
        data["ai_hashtag_count"] = hashtag_count
        data["caption"] = result.caption
        data["hashtags"] = result.hashtags_text
        new_form = self.form_class(
            data,
            self.request.FILES,
            subscription=self.subscription,
            user_settings=user_settings,
            instance=getattr(self, "object", None),
        )
        messages.success(self.request, gettext("Caption and hashtags generated. Review them before saving."))
        return self.render_to_response(self.get_context_data(form=new_form, ai_result=result))

    def get_first_ai_image_file(self):
        illustration_images = self.request.FILES.getlist("illustration_images")
        if illustration_images:
            return illustration_images[0]
        uploaded_image = self.request.FILES.get("image")
        if uploaded_image:
            return uploaded_image
        post = getattr(self, "object", None)
        if post and post.pk:
            first_image = post.images.order_by("order", "created_at", "pk").first()
            if first_image and first_image.image:
                return first_image.image
            if post.image:
                return post.image
        return None

    def sync_selected_campaign(self, post, selected_campaign):
        campaign_queryset = SocialMediaCampaign.objects.filter(
            subscription=self.subscription,
            created_by=self.request.user,
            campaign_posts=post,
        )
        if selected_campaign:
            for campaign in campaign_queryset.exclude(pk=selected_campaign.pk):
                campaign.campaign_posts.remove(post)
            selected_campaign.campaign_posts.add(post)
        else:
            for campaign in campaign_queryset:
                campaign.campaign_posts.remove(post)

    def form_valid(self, form):
        previous_status = None
        previous_published_at = None
        previous_video_file_name = ""
        if form.instance.pk:
            previous_post = (
                SocialMediaPost.objects.filter(pk=form.instance.pk)
                .only("status", "published_at", "video_file")
                .first()
            )
            if previous_post:
                previous_status = previous_post.status
                previous_published_at = previous_post.published_at
                previous_video_file_name = previous_post.video_file.name if previous_post.video_file else ""

        form.instance.subscription = self.subscription
        form.instance.author = self.request.user
        if form.instance.content_format == SocialMediaPost.Format.VIDEO:
            try:
                uploaded_video_object_name = self.get_uploaded_video_object_name()
            except ValidationError as exc:
                form.add_error("video_file", exc.message)
                return self.form_invalid(form)
            if uploaded_video_object_name:
                if settings.USE_GCS:
                    try:
                        uploaded_object_exists = default_storage.exists(uploaded_video_object_name)
                    except Exception:
                        logger.exception("Could not verify direct video object=%r", uploaded_video_object_name)
                        form.add_error("video_file", "The uploaded video could not be verified. Please try again.")
                        return self.form_invalid(form)
                    if not uploaded_object_exists:
                        form.add_error("video_file", "The uploaded video was not found. Please upload it again.")
                        return self.form_invalid(form)
                form.instance.video_file.name = uploaded_video_object_name
            elif not form.instance.video_file and not self.request.FILES.get("video_file"):
                form.add_error("video_file", "Please upload a video before saving this post.")
                return self.form_invalid(form)
        if form.instance.status == SocialMediaPost.Status.PUBLISHED:
            is_new_post = form.instance.pk is None
            was_unpublished = previous_status in {
                SocialMediaPost.Status.DRAFT,
                SocialMediaPost.Status.SCHEDULED,
            }
            if is_new_post or (was_unpublished and not previous_published_at):
                form.instance.published_at = timezone.now()

        illustration_images = self.request.FILES.getlist("illustration_images")
        if form.instance.content_format in (SocialMediaPost.Format.IMAGE, SocialMediaPost.Format.CAROUSEL):
            invalid_images = [
                image
                for image in illustration_images
                if not (getattr(image, "content_type", "") or "").startswith("image/")
            ]
            if invalid_images:
                form.add_error("image", "Only image files can be uploaded for image posts.")
                return self.form_invalid(form)
            if len(illustration_images) > self.max_illustration_images:
                form.add_error("image", f"Select up to {self.max_illustration_images} images for one post.")
                return self.form_invalid(form)

        schedule_result = validate_schedule(form.instance) if form.instance.status == SocialMediaPost.Status.SCHEDULED else None
        if schedule_result and not schedule_result.is_valid:
            form.add_error("scheduled_for", schedule_result.message)
            return self.form_invalid(form)
        messages.success(self.request, "Post saved.")
        response = super().form_valid(form)
        if self.object.image:
            _log_saved_upload("post image", self.request.FILES.get("image"), self.object.image)
        current_video_file_name = self.object.video_file.name if self.object.video_file else ""
        video_file_changed = current_video_file_name != previous_video_file_name
        if (
            self.object.content_format == SocialMediaPost.Format.VIDEO
            and self.object.video_file
            and (video_file_changed or not self.object.video_thumbnail)
        ):
            try:
                generate_video_thumbnail(self.object, force=video_file_changed)
            except Exception:
                logger.exception(
                    "Video thumbnail generation failed; keeping post_id=%r video=%r",
                    self.object.pk,
                    self.object.video_file.name,
                )
        if self.object.video_file and video_file_changed:
            _log_saved_upload("post video", self.request.FILES.get("video_file"), self.object.video_file)
        selected_campaign = form.cleaned_data.get("campaign")
        self.sync_selected_campaign(self.object, selected_campaign)
        if (
            self.object.content_format in (SocialMediaPost.Format.IMAGE, SocialMediaPost.Format.CAROUSEL)
            and illustration_images
        ):
            next_order = self.object.images.count()
            created_images = []
            for image in illustration_images:
                created_image = PostImage.objects.create(post=self.object, image=image, order=next_order)
                created_images.append(created_image)
                _log_saved_upload("post gallery image", image, created_image.image)
                next_order += 1
            if created_images and not self.object.image:
                self.object.image = created_images[0].image
                self.object.save(update_fields=["image", "updated_at"])

        if (
            self.object.content_format in (SocialMediaPost.Format.IMAGE, SocialMediaPost.Format.CAROUSEL)
            and self.object.image
            and not self.object.images.exists()
        ):
            PostImage.objects.create(post=self.object, image=self.object.image, order=0)
        return response


class PostCreateView(PostFormMixin, CreateView):
    def post(self, request, *args, **kwargs):
        self.object = None
        form = self.get_form()
        if "generate_ai" in request.POST:
            return self.handle_ai_generation(form)
        return super().post(request, *args, **kwargs)


class PostUpdateView(OwnerOrAdminMixin, PostFormMixin, UpdateView):
    def get_queryset(self):
        return SocialMediaPost.objects.filter(subscription=self.subscription)

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        form = self.get_form()
        if "generate_ai" in request.POST:
            return self.handle_ai_generation(form)
        return super().post(request, *args, **kwargs)


class PostDeleteView(SafeNextRedirectMixin, OwnerOrAdminMixin, DeleteView):
    model = SocialMediaPost
    template_name = "socialmanager/confirm_delete.html"
    success_url = reverse_lazy("socialmanager:profile")

    def get_queryset(self):
        return SocialMediaPost.objects.filter(subscription=self.subscription)

    def get_success_url(self):
        next_url = get_safe_next_url(self.request)
        deleted_detail_url = reverse(
            "socialmanager:post_detail",
            kwargs={"pk": self.object.pk, "slug": self.object.slug},
        )
        if next_url and next_url.split("?", 1)[0] != deleted_detail_url:
            return next_url
        return self.get_fallback_success_url()

    def form_valid(self, form):
        messages.success(self.request, "Post deleted.")
        return super().form_valid(form)


class AnalyticsDashboardView(ActiveSubscriptionMixin, TemplateView):
    def get(self, request, *args, **kwargs):
        return redirect("socialmanager:dashboard")

