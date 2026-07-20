import json
import logging

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.views import View

from .models import SaaSSubscription, SocialMediaPost, SubscriptionMembership, UserSettings, VideoAnalysis
from .services.ai_post_agent import PostAgentError, build_post_agent_input, generate_post_content
from .services.ai_post_agent_images import (
    MAX_AGENT_IMAGES,
    MAX_TOTAL_IMAGE_BYTES,
    AgentImageError,
    load_existing_post_images,
    prepare_agent_image,
)
from .subscriptions import user_has_active_subscription


logger = logging.getLogger(__name__)


def _error(code, message, status):
    return JsonResponse({"success": False, "error_code": code, "message": message}, status=status)


class PostAgentGenerateContentView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def handle_no_permission(self):
        return _error("permission_denied", "Authentication is required.", 401)

    def post(self, request, *args, **kwargs):
        if not user_has_active_subscription(request.user):
            return _error("membership_required", "An active AI membership is required.", 403)
        membership_queryset = (
            SubscriptionMembership.objects.select_related("subscription")
            .filter(user=request.user, subscription__is_archived=False)
        )
        if not request.user.is_superuser:
            membership_queryset = membership_queryset.filter(is_active_member=True)
        membership = membership_queryset.order_by("joined_at").first()
        workspace = membership.subscription if membership else None
        if request.user.is_superuser and workspace is None:
            workspace = (
                SaaSSubscription.objects.filter(owner=request.user, is_archived=False)
                .order_by("created_at")
                .first()
            )
        if workspace is None:
            return _error("membership_required", "An active workspace membership is required.", 403)
        if not request.content_type.startswith("multipart/form-data"):
            return _error("invalid_request", "Request body must be multipart form data.", 400)

        def json_field(name, fallback_name=None, default=None):
            raw = request.POST.get(name)
            if raw is None and fallback_name:
                raw = request.POST.get(fallback_name)
            if raw in (None, ""):
                return default
            try:
                value = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                raise PostAgentError("invalid_request", f"{name} must be valid JSON.", http_status=400)
            return value

        try:
            payload = {
                "context": request.POST.get("context", ""),
                "content_goal": request.POST.get("content_goal", ""),
                "custom_content_goal": request.POST.get("custom_content_goal", ""),
                "skipped_context": str(request.POST.get("skipped_context", "false")).lower() in {"1", "true", "yes"},
                "requested_fields": json_field("requested_fields", default=[]),
                "detected_media": json_field("detected_media_types", "detected_media", []),
                "article_text": request.POST.get("article_text", ""),
                "existing_title": request.POST.get("current_title", request.POST.get("existing_title", "")),
                "existing_caption": request.POST.get("current_caption", request.POST.get("existing_caption", "")),
                "existing_hashtags": request.POST.get("current_hashtags", request.POST.get("existing_hashtags", "")),
                "post_id": request.POST.get("post_id") or None,
            }
        except PostAgentError as exc:
            return _error(exc.error_code, exc.safe_message, exc.http_status)

        post = None
        post_id = payload.get("post_id")
        if post_id not in (None, ""):
            try:
                post = SocialMediaPost.objects.select_related("subscription", "author").get(pk=int(post_id))
            except (SocialMediaPost.DoesNotExist, TypeError, ValueError):
                return _error("permission_denied", "You cannot generate content for this post.", 403)
            can_edit = post.subscription_id == workspace.pk and (
                post.author_id == request.user.pk
                or (membership and membership.role == SubscriptionMembership.Role.ADMIN)
                or request.user.is_staff
                or request.user.is_superuser
            )
            if not can_edit:
                return _error("permission_denied", "You cannot generate content for this post.", 403)

        settings_obj = UserSettings.objects.filter(user=request.user).first()
        language = settings_obj.ai_language if settings_obj else UserSettings.AILanguage.ENGLISH
        if settings_obj and language == UserSettings.AILanguage.AUTO:
            language = (
                UserSettings.AILanguage.TRADITIONAL_CHINESE
                if settings_obj.language == UserSettings.Language.TRADITIONAL_CHINESE
                else UserSettings.AILanguage.ENGLISH
            )
        hashtag_count = settings_obj.ai_hashtag_count if settings_obj else 5
        media_summary = ""
        if post:
            analysis = VideoAnalysis.objects.filter(post=post, status=VideoAnalysis.Status.SUCCEEDED).only("result", "creator_guidance").first()
            if analysis:
                summary_source = analysis.creator_guidance or analysis.result
                media_summary = json.dumps(summary_source, ensure_ascii=False, default=str)

        image_warnings = []
        uploaded_images = request.FILES.getlist("image_files")
        if len(uploaded_images) > 10:
            return _error("too_many_images", "Upload no more than 10 images.", 400)
        if sum(getattr(image, "size", 0) or 0 for image in uploaded_images) > MAX_TOTAL_IMAGE_BYTES:
            return _error("image_too_large", "The combined image upload must be 25 MB or smaller.", 400)
        if len(uploaded_images) > MAX_AGENT_IMAGES:
            uploaded_images = uploaded_images[:MAX_AGENT_IMAGES]
            image_warnings.append(
                "僅分析前 4 張圖片。" if language == UserSettings.AILanguage.TRADITIONAL_CHINESE
                else "Only the first 4 images were analysed."
            )
        prepared_images = []
        for uploaded_image in uploaded_images:
            try:
                prepared_images.append(prepare_agent_image(uploaded_image))
            except AgentImageError as exc:
                logger.info(
                    "AI Post Agent image rejected code=%s filename=%r size_bytes=%r",
                    exc.code,
                    getattr(uploaded_image, "name", ""),
                    getattr(uploaded_image, "size", None),
                )
                return _error(exc.code, exc.safe_message, 400)
        if not prepared_images and post:
            try:
                prepared_images = load_existing_post_images(post)
            except AgentImageError as exc:
                logger.info(
                    "AI Post Agent stored image rejected code=%s post_id=%s",
                    exc.code,
                    post.pk,
                )
                return _error(exc.code, exc.safe_message, 400)
        if not prepared_images and any(kind in payload["detected_media"] for kind in ("image", "carousel")):
            if payload["context"].strip() or payload["article_text"].strip():
                image_warnings.append(
                    "圖片無法分析，因此內容是根據你的描述生成。"
                    if language == UserSettings.AILanguage.TRADITIONAL_CHINESE
                    else "The post was generated from your description because the image could not be analysed."
                )

        try:
            agent_input = build_post_agent_input(
                user=request.user,
                workspace=workspace,
                language=language,
                content_goal=payload.get("content_goal", ""),
                custom_content_goal=payload.get("custom_content_goal", ""),
                context=payload.get("context", ""),
                skipped_context=payload.get("skipped_context", False),
                detected_media=payload.get("detected_media", []),
                media_metadata=payload.get("media_metadata", []),
                article_text=payload.get("article_text", ""),
                existing_title=payload.get("existing_title", post.title if post else ""),
                existing_caption=payload.get("existing_caption", (post.article_caption or post.caption) if post else ""),
                existing_hashtags=payload.get("existing_hashtags", post.hashtags if post else ""),
                requested_fields=payload.get("requested_fields", []),
                preferred_hashtag_count=hashtag_count,
                media_summary=media_summary,
                images=prepared_images,
                additional_warnings=image_warnings,
            )
            result = generate_post_content(agent_input)
        except PostAgentError as exc:
            logger.warning(
                "AI Post Agent request failed category=%s user_id=%s workspace_id=%s",
                exc.error_code,
                request.user.pk,
                workspace.pk,
            )
            return _error(exc.error_code, exc.safe_message, exc.http_status)
        except Exception:
            logger.exception(
                "Unexpected AI Post Agent failure user_id=%s workspace_id=%s",
                request.user.pk,
                workspace.pk,
            )
            return _error("provider_error", "AI generation is temporarily unavailable.", 500)

        logger.info(
            "AI Post Agent endpoint success user_id=%s workspace_id=%s requested_fields=%s media_types=%s",
            request.user.pk,
            workspace.pk,
            list(agent_input.requested_fields),
            list(agent_input.detected_media),
        )
        return JsonResponse({"success": True, "data": result.as_dict()})
