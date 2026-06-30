from dataclasses import dataclass
import json
import logging
import mimetypes
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time

from django.conf import settings
from google import genai
from google.genai import types

from ..models import AISuggestionHistory, POST_CAPTION_MAX_LENGTH, POST_HASHTAGS_MAX_COUNT, POST_TITLE_MAX_LENGTH, split_hashtags


TEXT_BASED_FEEDBACK_MESSAGE = "AI suggestions are based on your text input and selected platform."
AI_TEMPORARILY_UNAVAILABLE_MESSAGE = "AI suggestions are temporarily unavailable. Please try again later."
AI_USAGE_LIMIT_REACHED_MESSAGE = "AI usage limit reached. Please wait a moment and try again."
AI_USAGE_LIMIT_REACHED_MESSAGE_ZH_HANT = "AI 使用額度暫時已達上限，請稍候再試。"
SUPPORTED_IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}
GEMINI_TIMEOUT_MS = 20000
MAX_GEMINI_IMAGE_BYTES = 3 * 1024 * 1024
DEFAULT_MAX_GEMINI_VIDEO_BYTES = 50 * 1024 * 1024
DEFAULT_MAX_GEMINI_VIDEO_SECONDS = 60
GEMINI_VIDEO_PROCESSING_TIMEOUT_SECONDS = 90
logger = logging.getLogger(__name__)


@dataclass
class SuggestionResult:
    caption: str
    hashtags_text: str


@dataclass
class FieldFeedbackResult:
    suggestion: str
    explanation: str
    used_video_input: bool = False
    fallback_reason: str = ""


@dataclass
class GeminiImageInput:
    data: bytes
    mime_type: str


@dataclass
class GeminiVideoInput:
    data: bytes
    mime_type: str
    duration_seconds: float | None = None


def _video_ai_diagnostic(*, video_file_detected, video_input_used, api_call_success, fallback_reason=""):
    logger.info(
        "Gemini video diagnostics: video_file_detected=%s video_input_used=%s model=%s api_call_success=%s fallback_reason=%s",
        bool(video_file_detected),
        bool(video_input_used),
        _gemini_model_name(),
        bool(api_call_success),
        fallback_reason or "none",
    )


class GeminiQuotaError(RuntimeError):
    def __init__(self, message=AI_USAGE_LIMIT_REACHED_MESSAGE, retry_delay_seconds=None):
        super().__init__(message)
        self.retry_delay_seconds = retry_delay_seconds


NOT_ENOUGH_DASHBOARD_DATA_MESSAGE = (
    "Not enough performance data yet. Keep publishing and collecting engagement data for better AI analysis."
)
NO_POST_ANALYTICS_DATA_MESSAGE = (
    "No audience data is available yet. Once this post receives views or engagement, AI insights will appear here."
)
NOT_ENOUGH_CAMPAIGN_CONTENT_MESSAGE = "This campaign does not have enough published content yet for AI analysis."


def _gemini_model_name():
    return (getattr(settings, "GEMINI_MODEL", "gemini-2.5-flash") or "").strip() or "gemini-2.5-flash"


def _gemini_debug(message):
    logger.debug("[Gemini] %s", message)


def _gemini_warning(error_name, retry_delay_seconds=None):
    if retry_delay_seconds is None:
        logger.warning("Gemini request failed: %s", error_name)
    else:
        logger.warning("Gemini request failed: %s; retry in %s seconds", error_name, retry_delay_seconds)


def _gemini_pipeline_diagnostics(
    *,
    image_bytes_present=False,
    language="unknown",
    tone="unknown",
    hashtag_count="unknown",
    exception_text="",
    **_extra,
):
    if exception_text:
        _gemini_debug("request failed")


def _extract_retry_delay_seconds(exc):
    text = str(exc or "")
    patterns = (
        r"retryDelay['\"]?\s*[:=]\s*['\"]?(\d+(?:\.\d+)?)s",
        r"retry_delay['\"]?\s*[:=]\s*['\"]?(\d+(?:\.\d+)?)s",
        r"RetryInfo.*?(\d+(?:\.\d+)?)s",
        r"retry.*?in.*?(\d+(?:\.\d+)?)\s*seconds?",
        r"(\d+(?:\.\d+)?)s",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
        if match:
            try:
                return max(int(float(match.group(1))), 0)
            except (TypeError, ValueError):
                return None
    return None


def _is_gemini_quota_error(exc):
    text = f"{exc.__class__.__name__}: {exc}".lower()
    return (
        "429" in text
        or "resource_exhausted" in text
        or "quota exceeded" in text
        or "rate limit" in text
        or "generate_content_free_tier_requests" in text
    )


def _gemini_client(diagnostic_context=None):
    diagnostic_context = diagnostic_context or {}
    api_key = str(getattr(settings, "GEMINI_API_KEY", "") or "").strip()
    if not getattr(settings, "GEMINI_ENABLED", True):
        message = "Google Gemini is disabled by GEMINI_ENABLED."
        _gemini_pipeline_diagnostics(**diagnostic_context, exception_text=message)
        raise ValueError(message)
    if not api_key:
        message = "GEMINI_API_KEY is missing from the runtime environment."
        _gemini_pipeline_diagnostics(**diagnostic_context, exception_text=message)
        raise ValueError(message)
    try:
        client = genai.Client(api_key=api_key)
    except Exception as exc:
        _gemini_pipeline_diagnostics(**diagnostic_context, exception_text=str(exc))
        raise
    return client


def _gemini_generate_text(
    system_prompt,
    user_prompt,
    temperature=0.7,
    response_json=True,
    image_input=None,
    diagnostic_context=None,
):
    contents = [user_prompt]
    if image_input:
        contents.append(types.Part.from_bytes(data=image_input.data, mime_type=image_input.mime_type))

    try:
        client = _gemini_client(
            {
                **(diagnostic_context or {}),
                "image_bytes_present": bool(image_input),
            }
        )
        response = client.models.generate_content(
            model=_gemini_model_name(),
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=temperature,
                response_mime_type="application/json" if response_json else None,
            ),
        )
    except Exception as exc:
        request_diagnostics = {
            **(diagnostic_context or {}),
            "image_bytes_present": bool(image_input),
        }
        if _is_gemini_quota_error(exc):
            retry_delay_seconds = _extract_retry_delay_seconds(exc)
            _gemini_warning(exc.__class__.__name__, retry_delay_seconds)
            _gemini_pipeline_diagnostics(
                **request_diagnostics,
                exception_text=str(exc),
            )
            raise GeminiQuotaError(retry_delay_seconds=retry_delay_seconds) from exc
        _gemini_pipeline_diagnostics(
            **request_diagnostics,
            exception_text=str(exc),
        )
        logger.warning("Gemini request failed: %s", exc.__class__.__name__)
        raise

    text = _clean_ai_text(getattr(response, "text", "") or "")
    return text


def _supported_image_mime_type(image_file):
    content_type = _clean_ai_text(getattr(image_file, "content_type", ""))
    if content_type in SUPPORTED_IMAGE_MIME_TYPES:
        return content_type

    name = _clean_ai_text(getattr(image_file, "name", ""))
    guessed_type, _ = mimetypes.guess_type(name)
    if guessed_type in SUPPORTED_IMAGE_MIME_TYPES:
        return guessed_type
    return ""


def _read_image_bytes(image_file):
    try:
        path = getattr(image_file, "path", None)
    except (AttributeError, NotImplementedError, OSError, ValueError):
        # Remote storage backends such as GCS intentionally do not expose a
        # local filesystem path. Fall through to storage-backed open/read.
        path = None
    if path:
        try:
            with Path(path).open("rb") as source:
                return source.read(MAX_GEMINI_IMAGE_BYTES + 1)
        except (OSError, ValueError):
            pass

    if not hasattr(image_file, "read"):
        return b""

    position = None
    was_closed = bool(getattr(image_file, "closed", False))
    try:
        if hasattr(image_file, "open"):
            try:
                image_file.open("rb")
            except TypeError:
                image_file.open()
        if hasattr(image_file, "tell"):
            position = image_file.tell()
        if hasattr(image_file, "seek"):
            image_file.seek(0)
        data = image_file.read(MAX_GEMINI_IMAGE_BYTES + 1)
        if isinstance(data, str):
            data = data.encode()
        return data or b""
    except Exception as exc:
        logger.warning("Image could not be loaded")
        return b""
    finally:
        if position is not None and hasattr(image_file, "seek"):
            try:
                image_file.seek(position)
            except (OSError, ValueError):
                pass
        if was_closed and hasattr(image_file, "close"):
            try:
                image_file.close()
            except Exception:
                pass


def _first_supported_image_input(*candidates):
    for candidate in candidates:
        if not candidate:
            continue
        image_file = getattr(candidate, "image", candidate)
        mime_type = _supported_image_mime_type(image_file)
        if not mime_type:
            continue
        data = _read_image_bytes(image_file)
        if len(data) > MAX_GEMINI_IMAGE_BYTES:
            continue
        if data:
            return GeminiImageInput(data=data, mime_type=mime_type)
    return None


def _supported_video_mime_type(video_file):
    content_type = _clean_ai_text(getattr(video_file, "content_type", "")).lower()
    supported = {"video/mp4", "video/webm", "video/quicktime"}
    if content_type in supported:
        return content_type
    name = _clean_ai_text(getattr(video_file, "name", ""))
    guessed_type, _ = mimetypes.guess_type(name)
    return guessed_type if guessed_type in supported else ""


def _probe_video_duration(video_bytes, suffix):
    ffprobe_path = shutil.which("ffprobe")
    if not ffprobe_path:
        return None
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temp_file:
            temp_file.write(video_bytes)
            temp_path = temp_file.name
        completed = subprocess.run(
            [
                ffprobe_path,
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                temp_path,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        if completed.returncode != 0:
            return None
        return max(float((completed.stdout or "").strip()), 0)
    except (OSError, subprocess.SubprocessError, TypeError, ValueError):
        return None
    finally:
        if temp_path:
            try:
                Path(temp_path).unlink(missing_ok=True)
            except OSError:
                pass


def _load_video_for_ai(video_file, *, duration_seconds=None):
    """Load a storage-backed video without exposing its URL; return (input, fallback reason)."""
    if not video_file:
        return None, "video_file_missing"
    mime_type = _supported_video_mime_type(video_file)
    if not mime_type:
        return None, "unsupported_video_type"
    max_bytes = int(getattr(settings, "GEMINI_VIDEO_MAX_BYTES", DEFAULT_MAX_GEMINI_VIDEO_BYTES))
    max_seconds = float(getattr(settings, "GEMINI_VIDEO_MAX_SECONDS", DEFAULT_MAX_GEMINI_VIDEO_SECONDS))
    declared_size = int(getattr(video_file, "size", 0) or 0)
    if declared_size > max_bytes:
        return None, "video_too_large"

    data = bytearray()
    try:
        if hasattr(video_file, "open"):
            video_file.open("rb")
        if hasattr(video_file, "chunks"):
            chunks = video_file.chunks()
        else:
            chunks = iter(lambda: video_file.read(1024 * 1024), b"")
        for chunk in chunks:
            data.extend(chunk)
            if len(data) > max_bytes:
                return None, "video_too_large"
    except Exception:
        return None, "video_read_failed"
    finally:
        try:
            video_file.close()
        except Exception:
            pass
    if not data:
        return None, "video_empty"

    try:
        supplied_duration = float(duration_seconds) if duration_seconds not in (None, "") else None
    except (TypeError, ValueError):
        supplied_duration = None
    suffix = Path(getattr(video_file, "name", "video.mp4")).suffix or ".mp4"
    measured_duration = _probe_video_duration(bytes(data), suffix)
    duration = measured_duration if measured_duration is not None else supplied_duration
    if duration is None:
        return None, "video_duration_unavailable"
    if duration > max_seconds:
        return None, "video_too_long"
    return GeminiVideoInput(data=bytes(data), mime_type=mime_type, duration_seconds=duration), ""


def _gemini_generate_from_video(
    system_prompt,
    user_prompt,
    video_input,
    *,
    temperature=0.7,
    response_json=True,
    diagnostic_context=None,
):
    """Upload a bounded private video to Gemini Files API, generate, then delete the remote file."""
    client = _gemini_client(diagnostic_context or {})
    temp_path = None
    uploaded_file = None
    try:
        suffix = mimetypes.guess_extension(video_input.mime_type) or ".mp4"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temp_file:
            temp_file.write(video_input.data)
            temp_path = temp_file.name
        uploaded_file = client.files.upload(
            file=temp_path,
            config=types.UploadFileConfig(mime_type=video_input.mime_type),
        )
        deadline = time.monotonic() + GEMINI_VIDEO_PROCESSING_TIMEOUT_SECONDS
        while True:
            state_name = _clean_ai_text(getattr(getattr(uploaded_file, "state", None), "name", ""))
            if state_name in {"", "ACTIVE"}:
                break
            if state_name == "FAILED":
                raise RuntimeError("Gemini could not process the uploaded video.")
            if time.monotonic() >= deadline:
                raise TimeoutError("Gemini video processing timed out.")
            time.sleep(2)
            uploaded_file = client.files.get(name=uploaded_file.name)
        response = client.models.generate_content(
            model=_gemini_model_name(),
            contents=[uploaded_file, user_prompt],
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=temperature,
                response_mime_type="application/json" if response_json else None,
            ),
        )
        _video_ai_diagnostic(
            video_file_detected=True,
            video_input_used=True,
            api_call_success=True,
        )
        return _clean_ai_text(getattr(response, "text", "") or "")
    except Exception as exc:
        _video_ai_diagnostic(
            video_file_detected=True,
            video_input_used=True,
            api_call_success=False,
            fallback_reason=exc.__class__.__name__,
        )
        if _is_gemini_quota_error(exc):
            raise GeminiQuotaError(retry_delay_seconds=_extract_retry_delay_seconds(exc)) from exc
        raise
    finally:
        if uploaded_file and getattr(uploaded_file, "name", None):
            try:
                client.files.delete(name=uploaded_file.name)
            except Exception:
                logger.warning("Gemini uploaded video cleanup failed")
        if temp_path:
            try:
                Path(temp_path).unlink(missing_ok=True)
            except OSError:
                pass


def _analysis_language_label(language):
    language_key = (language or "English").strip().lower().replace("-", "_").replace(" ", "_")
    language_labels = {
        "english": "English",
        "en": "English",
        "traditional_chinese": "Traditional Chinese",
        "zh_hant": "Traditional Chinese",
        "auto": "Auto detect from supplied content; default to English if unclear",
    }
    return language_labels.get(language_key, language or "English")


def _analysis_uses_traditional_chinese(language):
    return _analysis_language_label(language) == "Traditional Chinese"


def _analysis_is_auto_language(language):
    return (language or "").strip().lower().replace("-", "_").replace(" ", "_") == "auto"


def _analysis_text_values(value):
    if isinstance(value, dict):
        values = []
        for item in value.values():
            values.extend(_analysis_text_values(item))
        return values
    if isinstance(value, (list, tuple)):
        values = []
        for item in value:
            values.extend(_analysis_text_values(item))
        return values
    if isinstance(value, str):
        return [value]
    return []


def _analysis_should_use_traditional_chinese(language, *payloads):
    if _analysis_uses_traditional_chinese(language):
        return True
    if not _analysis_is_auto_language(language):
        return False
    text = " ".join(
        text_value
        for payload in payloads
        for text_value in _analysis_text_values(payload)
    )
    return _cjk_ratio(text) > 0.25


def _analysis_language_rules(language_label):
    return [
        f"Output language must be exactly: {language_label}.",
        "Do not infer output language from visible metrics or page language unless language is Auto.",
        "If Traditional Chinese is selected, all headings and analysis text must be Traditional Chinese.",
        "If English is selected, all headings and analysis text must be English.",
        "Do not mix English and Traditional Chinese except platform names, usernames, hashtags, or post titles.",
        "If Traditional Chinese is selected, translate all analytical terms into natural Traditional Chinese.",
        "Do not leave English terms such as engagement spike, comments, likes, posting consistency, stronger hooks, share potential, audience retention.",
        "Platform names, usernames, hashtags, post titles, and campaign names may remain in their original language.",
        "Metric labels must be translated when Traditional Chinese is selected: views -> 瀏覽, likes -> 讚, comments -> 留言, shares -> 分享, engagement rate -> 互動率, engagement spike -> 互動高峰, posting consistency -> 發文穩定度, stronger hooks -> 更有吸引力的開頭, share potential -> 分享潛力, audience retention -> 受眾留存, audience interaction -> 受眾互動, content performance -> 內容表現.",
    ]


def _localize_analysis_terms(text, language, *payloads):
    value = _clean_ai_text(text)
    if not _analysis_should_use_traditional_chinese(language, *payloads):
        return value

    protected_tokens = {}

    def protect(match):
        placeholder = f"__AI_ANALYSIS_PROTECTED_{len(protected_tokens)}__"
        protected_tokens[placeholder] = match.group(0)
        return placeholder

    protected_value = re.sub(r"#[A-Za-z0-9_]+|@[A-Za-z0-9_]+", protect, value)
    protected_value = re.sub(
        r"\b(?:TikTok|Instagram|YouTube|Facebook|Twitter|Reddit|X)\b",
        protect,
        protected_value,
        flags=re.IGNORECASE,
    )

    replacements = (
        (r"(?<![#@])\bengagement\s+spikes?\b", "互動高峰"),
        (r"(?<![#@])\bengagement\s+quality\b", "互動品質"),
        (r"(?<![#@])\bengagement\s+rates?\b", "互動率"),
        (r"(?<![#@])\baudience\s+interactions?\b", "受眾互動"),
        (r"(?<![#@])\baudience\s+retention\b", "受眾留存"),
        (r"(?<![#@])\bposting\s+consistency\b", "發文穩定度"),
        (r"(?<![#@])\bstronger\s+hooks?\b", "更有吸引力的開頭"),
        (r"(?<![#@])\bshare\s+potential\b", "分享潛力"),
        (r"(?<![#@])\bcontent\s+performance\b", "內容表現"),
        (r"(?<![#@])\binteraction\s+trends?\b", "互動趨勢"),
        (r"(?<![#@])\bcomments?\b", "留言"),
        (r"(?<![#@])\blikes?\b", "讚"),
        (r"(?<![#@])\bshares?\b", "分享"),
        (r"(?<![#@])\bviews?\b", "瀏覽"),
    )

    localized = protected_value
    for pattern, target in replacements:
        localized = re.sub(pattern, target, localized, flags=re.IGNORECASE)

    for placeholder, token in protected_tokens.items():
        localized = localized.replace(placeholder, token)
    return localized


def _log_analysis_language(language, language_label):
    logger.debug("AI analysis language=%s label=%s", language, language_label)


def _localized_no_dashboard_data_message(language):
    if _analysis_uses_traditional_chinese(language):
        return "目前還沒有足夠的成效資料。請持續發文並累積互動數據，之後 AI 會提供更準確的分析。"
    return NOT_ENOUGH_DASHBOARD_DATA_MESSAGE


def _localized_no_post_data_message(language):
    if _analysis_uses_traditional_chinese(language):
        return "目前沒有可用的受眾資料。當這則貼文開始獲得瀏覽或互動後，AI 洞察會顯示在這裡。"
    return NO_POST_ANALYTICS_DATA_MESSAGE


def _localized_not_enough_campaign_message(language):
    if _analysis_uses_traditional_chinese(language):
        return "這個活動目前還沒有足夠的已發布內容可供 AI 分析。"
    return NOT_ENOUGH_CAMPAIGN_CONTENT_MESSAGE


def _empty_dashboard_analysis(language="English"):
    return {
        "has_enough_data": False,
        "message": _localized_no_dashboard_data_message(language),
        "overall_performance_summary": "",
        "key_trends": [],
        "growth_opportunity": "",
        "ai_recommendation": "",
        "source": "fallback",
    }


METRIC_KEY_ALIASES = {
    "views": (
        "views",
        "view",
        "view_count",
        "views_count",
        "total_views",
        "impressions",
        "impression_count",
        "impressions_count",
        "total_impressions",
    ),
    "likes": (
        "likes",
        "like",
        "like_count",
        "likes_count",
        "total_likes",
    ),
    "comments": (
        "comments",
        "comment",
        "comment_count",
        "comments_count",
        "total_comments",
    ),
    "shares": (
        "shares",
        "share",
        "share_count",
        "shares_count",
        "total_shares",
    ),
}


def _safe_int(value):
    try:
        return int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return 0


def _metric_value(row, metric):
    if not isinstance(row, dict):
        return 0
    for key in METRIC_KEY_ALIASES.get(metric, (metric,)):
        if key in row:
            return _safe_int(row.get(key))
    for nested_key in ("metrics", "totals", "analytics"):
        nested = row.get(nested_key)
        if isinstance(nested, dict):
            nested_value = _metric_value(nested, metric)
            if nested_value:
                return nested_value
    return 0


def _dashboard_has_enough_data(summary_data):
    totals = summary_data.get("totals") or {}
    recent_posts = summary_data.get("recent_post_metrics") or []
    trend_rows = summary_data.get("trend_7_days") or []
    metric_keys = ("views", "likes", "comments", "shares")
    result = any(
        _metric_value(row, key) > 0
        for row in (totals, *recent_posts, *trend_rows)
        for key in metric_keys
    )
    logger.debug("Dashboard AI has enough data=%s", result)
    return result


def generate_dashboard_rule_based_analysis(summary_data, language="English"):
    language_label = _analysis_language_label(language)
    _log_analysis_language(language, language_label)
    use_traditional_chinese = _analysis_should_use_traditional_chinese(language, summary_data)
    if not _dashboard_has_enough_data(summary_data):
        return _empty_dashboard_analysis(language)

    totals = summary_data.get("totals") or {}
    views = _metric_value(totals, "views")
    likes = _metric_value(totals, "likes")
    comments = _metric_value(totals, "comments")
    shares = _metric_value(totals, "shares")
    engagement_rate = float(totals.get("engagement_rate") or 0)
    metric_keys = ("views", "likes", "comments", "shares")
    performance_metrics = {
        "views": views,
        "likes": likes,
        "comments": comments,
        "shares": shares,
    }
    engagement_metrics = {
        "likes": likes,
        "comments": comments,
        "shares": shares,
    }
    best_metric = max(performance_metrics.items(), key=lambda item: item[1])[0]
    weakest_metric = min(engagement_metrics.items(), key=lambda item: item[1])[0]
    trend_rows = summary_data.get("trend_7_days") or []
    peak_row = None
    if trend_rows:
        peak_row = max(
            trend_rows,
            key=lambda row: sum(_metric_value(row, key) for key in metric_keys),
        )

    if use_traditional_chinese:
        metric_labels = {
            "views": "瀏覽",
            "likes": "讚",
            "comments": "留言",
            "shares": "分享",
        }
        if weakest_metric == "comments":
            growth_opportunity = "受眾互動需要更清楚的回覆提示與更強的開場，才能提升互動品質。"
        elif weakest_metric == "shares":
            growth_opportunity = "分享潛力可以透過更實用的重點與更明確的內容策略角度提升。"
        else:
            growth_opportunity = "互動品質可以透過測試更強的開場與更清楚的第一印象來改善。"
        summary = f"內容表現已出現可衡量的受眾互動，本週互動品質約為 {engagement_rate:.2f}%。"
        trend_text = (
            f"最明顯的互動趨勢出現在 {peak_row.get('date')} 左右。"
            if peak_row and sum(_metric_value(peak_row, key) for key in metric_keys)
            else "本週受眾互動仍較分散，還需要更多穩定訊號。"
        )
        recommendation = "維持更穩定的發文節奏，並使用更強的開場來提升受眾留存。"
        best_metric_text = f"{metric_labels.get(best_metric, best_metric)}是目前內容策略中最強的成效訊號。"
    else:
        if weakest_metric == "comments":
            growth_opportunity = "Audience interaction needs clearer reply prompts and stronger hooks to build engagement quality."
        elif weakest_metric == "shares":
            growth_opportunity = "Share potential can grow through more useful takeaways and sharper content strategy angles."
        else:
            growth_opportunity = "Engagement quality can improve by testing stronger hooks and clearer first moments."

        summary = (
            "Content performance shows measurable audience interaction, "
            f"with engagement quality around {engagement_rate:.2f}% this week."
        )
        trend_text = (
            f"The strongest interaction trend appeared around {peak_row.get('date')}."
            if peak_row and sum(_metric_value(peak_row, key) for key in metric_keys)
            else "Audience interaction is still spread thin across the week."
        )
        recommendation = "Improve posting consistency and use stronger hooks to stabilize audience retention."
        best_metric_text = f"{best_metric.title()} are the strongest engagement signal for current content strategy."

    summary = _localize_analysis_terms(summary, language, summary_data)
    trend_text = _localize_analysis_terms(trend_text, language, summary_data)
    best_metric_text = _localize_analysis_terms(best_metric_text, language, summary_data)
    growth_opportunity = _localize_analysis_terms(growth_opportunity, language, summary_data)
    recommendation = _localize_analysis_terms(recommendation, language, summary_data)

    return {
        "has_enough_data": True,
        "message": "",
        "overall_performance_summary": summary,
        "key_trends": [
            trend_text,
            best_metric_text,
        ],
        "growth_opportunity": growth_opportunity,
        "ai_recommendation": recommendation,
        "source": "fallback",
    }


def generate_dashboard_analysis(summary_data, language="English"):
    language_label = _analysis_language_label(language)
    _log_analysis_language(language, language_label)
    fallback = generate_dashboard_rule_based_analysis(summary_data, language=language)
    if not fallback.get("has_enough_data"):
        return fallback

    if not getattr(settings, "GEMINI_API_KEY", ""):
        return fallback

    prompt = {
        "dashboard_summary_data": summary_data,
        "instructions": [
            *_analysis_language_rules(language_label),
            "Analyze only the supplied real dashboard metrics.",
            "Do not invent or estimate missing metric values.",
            "Do not use emojis, icons, or decorative markers.",
            "Do not repeat raw metric lists already visible on the page.",
            "Keep the response concise with short section headings.",
            "Write in a concise TikTok or Instagram analytics commentary style.",
            "If English is selected, use phrases such as engagement spike, audience interaction, share potential, posting consistency, audience retention, stronger hooks, content performance, interaction trend, or engagement quality when relevant.",
            "If Traditional Chinese is selected, use natural Traditional Chinese equivalents for those analytical terms.",
            "Focus on engagement patterns, audience behaviour, strongest performing content, growth opportunities, and content strategy.",
            "Avoid generic AI phrases, long reports, and repeated metric descriptions.",
            "Write concise but complete points.",
            "Each paragraph or bullet should be one complete sentence.",
            "Avoid filler, repetition, generic advice, and unnecessary metric restatement.",
            "Be brief, specific, and actionable, but never truncate ideas.",
            "Return only valid JSON with these exact keys: overall_performance_summary, key_trends, growth_opportunity, ai_recommendation.",
            "key_trends must be an array of 1 or 2 short bullet strings.",
        ],
    }

    try:
        response_text = _gemini_generate_text(
            (
                "You are an AI social media performance analyst for a SaaS dashboard. "
                "Use the provided 7-day totals, recent post metrics, and trend data to produce a short, actionable analysis."
            ),
            (
                "Create dashboard analysis JSON from this real performance payload:\n\n"
                f"{json.dumps(prompt, default=str)}"
            ),
            temperature=0.4,
            response_json=True,
        )
        parsed = _parse_ai_json(response_text)
    except Exception:
        return fallback
    required_keys = (
        "overall_performance_summary",
        "key_trends",
        "growth_opportunity",
        "ai_recommendation",
    )
    analysis = {
        "has_enough_data": True,
        "message": "",
        "source": "ai",
    }
    for key in required_keys:
        if key == "key_trends":
            trends = parsed.get(key)
            if isinstance(trends, str):
                trends = [trends]
            if not isinstance(trends, list):
                return fallback
            trends = [
                _clean_complete_ai_point(_localize_analysis_terms(item, language, summary_data))
                for item in trends[:2]
                if _clean_ai_text(item)
            ]
            if not trends:
                return fallback
            analysis[key] = trends
        else:
            value = _clean_complete_ai_point(_localize_analysis_terms(parsed.get(key), language, summary_data))
            if not value:
                return fallback
            analysis[key] = value
    return analysis


def _post_has_audience_data(analytics_data):
    metrics = analytics_data.get("metrics") or {}
    return any(int(metrics.get(key) or 0) for key in ("views", "likes", "comments", "shares"))


def _empty_post_analysis():
    return NO_POST_ANALYTICS_DATA_MESSAGE


def _post_content_context(analytics_data):
    post = analytics_data.get("post") or {}
    title = _clean_ai_text(post.get("title"))
    caption = _clean_ai_text(post.get("caption") or post.get("description"))
    article_caption = _clean_ai_text(post.get("article_caption"))
    article_body = _clean_ai_text(post.get("article_body") or post.get("article_content"))
    content_text = " ".join(value for value in (title, caption, article_caption, article_body) if value)
    return {
        "title": title,
        "caption": caption,
        "article_caption": article_caption,
        "article_body": article_body,
        "content_word_count": len(content_text.split()),
    }


def _post_comment_context(analytics_data):
    comments = analytics_data.get("comments") or {}
    items = comments.get("items") if isinstance(comments, dict) else []
    if not isinstance(items, list):
        items = []
    return {
        "items": items,
        "meaningful_count": int((comments or {}).get("meaningful_count") or len(items)) if isinstance(comments, dict) else len(items),
        "skipped_spam_count": int((comments or {}).get("skipped_spam_count") or 0) if isinstance(comments, dict) else 0,
        "skipped_low_signal_count": int((comments or {}).get("skipped_low_signal_count") or 0) if isinstance(comments, dict) else 0,
    }


def _post_hashtag_list(analytics_data):
    post = analytics_data.get("post") or {}
    return split_hashtags(post.get("hashtags", ""))


def _content_feedback_lines(content):
    title = content["title"]
    caption = content["caption"] or content["article_caption"]
    article_body = content["article_body"]
    content_word_count = content["content_word_count"]

    if title and len(title.split()) <= 8:
        topic_line = "The title is easy to scan, but it should still promise a clear payoff."
    elif title:
        topic_line = "The title has context, but it may be too long to land quickly."
    else:
        topic_line = "The topic is harder to judge because the post does not have a clear title."

    if caption and len(caption.split()) >= 12:
        caption_line = "The caption gives some context; make sure the first line creates a reason to react."
    elif caption:
        caption_line = "The caption is quite short, so the next post may need a stronger hook or question."
    else:
        caption_line = "There is no caption text, so users get less direction on why they should engage."

    if article_body:
        reason_line = "The article body gives extra substance; pull the strongest point into the visible caption."
    elif content_word_count >= 18:
        reason_line = "The post has enough text to explain the idea, but the engagement reason should be sharper."
    else:
        reason_line = "The post content is brief, so users may need a clearer benefit, reveal, or prompt."

    return topic_line, caption_line, reason_line


def _post_insight_headings(use_traditional_chinese=False, include_comment_analysis=False):
    if use_traditional_chinese:
        headings = [
            "整體表現",
            "視覺與內容分析",
            "受眾行為",
            "改善機會",
            "下一則內容建議",
        ]
        if include_comment_analysis:
            headings.insert(3, "留言分析")
        return tuple(headings)
    headings = [
        "Overall performance",
        "Visual & content analysis",
        "Audience behaviour",
        "Improvement opportunities",
        "Next content recommendation",
    ]
    if include_comment_analysis:
        headings.insert(3, "Comment analysis")
    return tuple(headings)


def _serialize_post_insight_sections(sections):
    return json.dumps({"sections": sections}, ensure_ascii=False)


def _generate_post_rule_based_sections(analytics_data, language="English"):
    use_zh = _analysis_should_use_traditional_chinese(language, analytics_data)
    post = analytics_data.get("post") or {}
    metrics = analytics_data.get("metrics") or {}
    rates = analytics_data.get("rates") or {}
    comments = _post_comment_context(analytics_data)
    include_comment_analysis = comments["meaningful_count"] >= 2
    headings = _post_insight_headings(use_zh, include_comment_analysis)
    hashtags = _post_hashtag_list(analytics_data)
    media = analytics_data.get("media") or {}
    views = int(metrics.get("views") or 0)
    likes = int(metrics.get("likes") or 0)
    comment_count = int(metrics.get("comments") or 0)
    shares = int(metrics.get("shares") or 0)
    has_audience_data = _post_has_audience_data(analytics_data)
    engagement_rate = float(rates.get("engagement_rate") or 0)
    title = _clean_ai_text(post.get("title"))
    caption = _clean_ai_text(post.get("caption") or post.get("article_caption"))

    if use_zh:
        if not has_audience_data:
            performance = ["目前瀏覽、讚、留言與分享皆為零，尚無受眾成效資料可判讀。"]
        elif views < 10:
            performance = [
                f"目前只有 {views} 次瀏覽，樣本太小，所有成效數字都只能視為早期訊號。",
                "即使互動率偏高，也只能代表少量已觸及受眾有反應，仍需要更多曝光才能判斷。",
            ]
        else:
            performance = [
                f"這則貼文已有 {views} 次瀏覽，可開始觀察互動走向，但仍不宜只用單一比例下結論。",
                f"目前互動率約 {engagement_rate:.1f}%，應搭配留言與分享品質判讀。",
            ]
        visual_points = [
            "第一張圖片提供視覺脈絡，但因檔案過大可能改以文字訊號分析。"
            if media.get("media_type") == "image"
            else "目前沒有可用圖片，因此不推測構圖、角色定位或品牌用途。",
            f"目前使用 {len(hashtags)} 個 hashtag；應優先保留與主題及受眾搜尋意圖直接相關的標籤。"
            if hashtags
            else "目前沒有 hashtag，曝光與搜尋性主要依賴標題、文案與平台推薦。",
        ]
        if not _post_has_audience_data(analytics_data):
            audience_points = ["目前瀏覽與互動皆為零，尚無受眾行為訊號可判讀。"]
        elif comment_count > views and views:
            audience_points = ["留言高於瀏覽，可能包含回覆或重複互動，不代表廣泛觸及。"]
        elif views < 10:
            audience_points = ["已有早期互動，但觸及樣本仍小，應先增加曝光再判斷行為模式。"]
        else:
            audience_points = [f"目前有 {likes} 個讚、{comment_count} 則留言與 {shares} 次分享，互動類型仍偏集中。"]
        if comments["meaningful_count"] == 1:
            audience_points.append("目前只有 1 則有意義留言，留言傾向仍不可靠。")
        elif comments["meaningful_count"] == 0 and (
            comments["skipped_spam_count"] + comments["skipped_low_signal_count"]
        ):
            audience_points.append("部分留言因垃圾、亂碼或低訊號內容而忽略，不視為受眾興趣。")
        comment_points = [
            f"清理無效內容後，有 {comments['meaningful_count']} 則有意義留言，可作有限的質性參考。",
        ]
        opportunities = [
            "把第一句改成更清楚的主題承諾，讓陌生受眾立即知道內容價值。",
            "加入一個容易回答的具體問題，提升有內容的留言，而不是只增加短回覆。",
            "精簡並聚焦 hashtag，讓標籤同時對應主題、創作類型與目標受眾。",
        ]
        next_points = [
            "下一則可做創作過程或前後對照，先展示成果，再補一個關鍵設計決定。",
            "結尾用二選一問題邀請受眾評論，並觀察留言品質是否比單純按讚更明確。",
        ]
    else:
        if not has_audience_data:
            performance = ["Views, likes, comments, and shares are all zero, so no audience performance signal is available yet."]
        elif views < 10:
            performance = [
                f"With only {views} view(s), the sample is too small for reliable performance conclusions.",
                "A high engagement rate only shows resonance within a very small reached audience; more exposure is needed.",
            ]
        else:
            performance = [
                f"At {views} views, early interaction patterns are visible, but no single rate proves success.",
                f"The {engagement_rate:.1f}% engagement rate should be read alongside comment and share quality.",
            ]
        visual_points = [
            "The first image offers visual context, although oversized media may be skipped for Gemini analysis."
            if media.get("media_type") == "image"
            else "No image context is available, so visual or brand-role claims should not be inferred.",
            f"The post uses {len(hashtags)} hashtag(s); keep only tags tied to the subject and audience search intent."
            if hashtags
            else "Without hashtags, discoverability depends mostly on the title, caption, and platform distribution.",
        ]
        if not _post_has_audience_data(analytics_data):
            audience_points = ["Views and interactions are all zero, so no audience behaviour signal is available yet."]
        elif comment_count > views and views:
            audience_points = ["Comments exceed views, likely reflecting replies or repeated interactions rather than broad reach."]
        elif views < 10:
            audience_points = ["Early interactions exist, but reach is too small to treat the behaviour pattern as stable."]
        else:
            audience_points = [f"The post has {likes} like(s), {comment_count} comment(s), and {shares} share(s), with interaction concentrated in specific actions."]
        if comments["meaningful_count"] == 1:
            audience_points.append("Only one meaningful comment is available, so comment sentiment remains unreliable.")
        elif comments["meaningful_count"] == 0 and (
            comments["skipped_spam_count"] + comments["skipped_low_signal_count"]
        ):
            audience_points.append("Some comments were ignored as spam, gibberish, or low-signal text and do not indicate audience interest.")
        comment_points = [
            f"After filtering invalid text, {comments['meaningful_count']} meaningful comments remain for limited qualitative analysis.",
        ]
        opportunities = [
            "Open with a clearer promise so unfamiliar viewers understand the value immediately.",
            "Ask one specific, easy-to-answer question to encourage substantive comments instead of short reactions.",
            "Tighten hashtags around the subject, creative category, and intended audience.",
        ]
        next_points = [
            "Create a process or before-and-after post: show the result first, then explain one design decision.",
            "End with a two-option question and compare comment quality against simple likes.",
        ]
    section_points = [performance, visual_points, audience_points]
    if include_comment_analysis:
        section_points.append(comment_points)
    section_points.extend([opportunities, next_points])
    section_limits = (2, 2, 1, 1, 1, 1) if include_comment_analysis else (2, 2, 2, 1, 1)
    return _serialize_post_insight_sections(
        [
            {"heading": heading, "points": points[:limit]}
            for heading, points, limit in zip(headings, section_points, section_limits)
        ]
    )


def _normalize_post_insight_sections(parsed, language="English", analytics_data=None):
    sections = parsed.get("sections") if isinstance(parsed, dict) else None
    if not isinstance(sections, list):
        return ""
    use_zh = _analysis_uses_traditional_chinese(language)
    comment_context = _post_comment_context(analytics_data or {})
    include_comment_analysis = comment_context["meaningful_count"] >= 2
    expected = _post_insight_headings(use_zh, include_comment_analysis)
    section_limits = (2, 2, 1, 1, 1, 1) if include_comment_analysis else (2, 2, 2, 1, 1)

    def heading_key(value):
        return _clean_ai_text(value).lower().replace("&", "and").replace("／", "/")

    section_lookup = {}
    for section in sections:
        if not isinstance(section, dict):
            continue
        key = heading_key(section.get("heading"))
        if key in {"content understanding", "內容理解", "summary", "總結"}:
            continue
        section_lookup[key] = section

    normalized = []
    for index, expected_heading in enumerate(expected):
        expected_key = heading_key(expected_heading)
        section = section_lookup.get(expected_key)
        if section is None and expected_key == "visual and content analysis":
            section = section_lookup.get("visual and content signals")
        if not isinstance(section, dict):
            return ""
        points = section.get("points")
        if not isinstance(points, list):
            return ""
        clean_points = []
        for point in points:
            value = _clean_complete_ai_point(point)
            if not value:
                continue
            clean_points.append(value)
            if len(clean_points) == section_limits[index]:
                break
        if not clean_points:
            return ""
        normalized.append({"heading": expected_heading, "points": clean_points})
    return _serialize_post_insight_sections(normalized)


def generate_post_rule_based_analysis(analytics_data, language="English"):
    return _generate_post_rule_based_sections(analytics_data, language=language)

    language_label = _analysis_language_label(language)
    _log_analysis_language(language, language_label)
    use_traditional_chinese = _analysis_should_use_traditional_chinese(language, analytics_data)
    metrics = analytics_data.get("metrics") or {}
    rates = analytics_data.get("rates") or {}
    content = _post_content_context(analytics_data)
    comment_context = _post_comment_context(analytics_data)
    hashtag_list = _post_hashtag_list(analytics_data)
    media = analytics_data.get("media") or {}
    has_audience_data = _post_has_audience_data(analytics_data)
    views = int(metrics.get("views") or 0)
    likes = int(metrics.get("likes") or 0)
    comments = int(metrics.get("comments") or 0)
    shares = int(metrics.get("shares") or 0)
    engagement_rate = float(rates.get("engagement_rate") or 0)
    like_rate = float(rates.get("like_rate") or 0)
    comment_rate = float(rates.get("comment_rate") or 0)
    share_rate = float(rates.get("share_rate") or 0)
    total_engagements = likes + comments + shares
    topic_line, caption_line, reason_line = _content_feedback_lines(content)
    hashtag_line = (
        f"The post uses {len(hashtag_list)} hashtag(s); make sure they describe the content purpose, not only the format."
        if hashtag_list
        else "No hashtags are available, so discoverability relies mostly on the title and caption."
    )
    comment_line = (
        f"There are {comment_context['meaningful_count']} meaningful comment(s) after filtering low-signal or spam-like replies."
        if comment_context["meaningful_count"]
        else "There are no meaningful comments to analyze yet, so comment quality should not be over-interpreted."
    )
    visual_line = (
        "The first image can support the analysis as visual context."
        if media.get("media_type") == "image"
        else "This is a video post, so this post analysis does not inspect video frames."
        if media.get("media_type") == "video"
        else "No visual context is available for this post analysis."
    )

    if use_traditional_chinese:
        if not has_audience_data:
            performance_intro = "目前還沒有受眾資料，所以這份洞察會以內容回饋為主。"
            performance_line = _localized_no_post_data_message("traditional_chinese")
            next_action = "下一則貼文可以把開場、重點回報和互動提示寫得更明確。"
            summary = "總結\n目前沒有可用的受眾資料，因此下一步應先收緊內容表達，再判斷實際成效。"
        else:
            engagement_metrics = {"likes": likes, "comments": comments, "shares": shares}
            weakest_metric, _ = min(engagement_metrics.items(), key=lambda item: item[1])
            metric_labels = {"likes": "讚", "comments": "留言", "shares": "分享"}
            performance_intro = "這則貼文已經有真實受眾活動可以參考。"
            performance_line = "目前已有受眾活動，因此可以優先改善最需要提示的回應訊號。"
            if weakest_metric == "comments":
                next_action = "加入一個簡單問題或觀點提示，讓使用者更清楚知道可以怎麼回覆。"
            elif weakest_metric == "shares":
                next_action = "讓重點更實用或更有意外感，給使用者更明確的分享理由。"
            else:
                next_action = "強化開頭文字與視覺承諾，讓觀看者更快產生反應。"
            summary = f"總結\n下一則貼文應透過更清楚的回報重點來改善{metric_labels.get(weakest_metric, weakest_metric)}。"
        topic_line = "標題容易快速理解，但仍需要承諾更清楚的收穫。" if content["title"] else "這則貼文缺少清楚標題，因此主題較難快速判斷。"
        caption_line = "文案提供了一些脈絡；第一行可以再更明確地製造互動理由。" if content["caption"] or content["article_caption"] else "目前沒有文案文字，使用者比較難理解為什麼要互動。"
        reason_line = "內容有足夠資訊支撐想法，但互動理由還可以更銳利。" if content["content_word_count"] >= 18 else "內容偏短，可能需要更清楚的好處、揭示點或互動提示。"
        hashtag_line = f"這則貼文使用 {len(hashtag_list)} 個 hashtag；它們應該說明內容目的，而不只是貼文格式。" if hashtag_list else "目前沒有 hashtag，因此曝光與搜尋性主要依賴標題與文案。"
        comment_line = f"過濾低訊號或疑似垃圾留言後，有 {comment_context['meaningful_count']} 則有意義留言可參考。" if comment_context["meaningful_count"] else "目前沒有足夠有意義的留言可分析，因此不應過度解讀留言品質。"
        visual_line = "第一張圖片可作為視覺脈絡輔助分析。" if media.get("media_type") == "image" else "這是影片貼文，因此本篇貼文分析不檢查影片畫面。" if media.get("media_type") == "video" else "目前沒有可用的視覺脈絡。"
        return _localize_analysis_terms(
            (
            "整體表現\n"
            f"{performance_intro}\n\n"
            "內容理解\n"
            f"{performance_line}\n\n"
            f"{topic_line}\n"
            f"{caption_line}\n"
            f"{reason_line}\n\n"
            "視覺與內容訊號\n"
            f"{visual_line}\n"
            f"{hashtag_line}\n\n"
            "受眾行為\n"
            f"{comment_line}\n\n"
            "改善機會\n"
            f"{next_action}\n"
            "下一則貼文可以把最強的好處或揭示點放得更前面，並讓互動呼籲更自然。\n\n"
            f"{summary}"
            ),
            language,
            analytics_data,
        )

    if not has_audience_data:
        performance_intro = "There is no audience data yet, so this is mainly content feedback."
        performance_line = NO_POST_ANALYTICS_DATA_MESSAGE
        next_action = "Improve the next post by making the hook, payoff, and engagement prompt more obvious."
        summary = "Summary: No audience data is available yet, so the useful next step is tightening the content before judging performance."
    else:
        engagement_metrics = {"likes": likes, "comments": comments, "shares": shares}
        weakest_metric, _ = min(engagement_metrics.items(), key=lambda item: item[1])
        performance_intro = "This post has real audience activity to learn from."
        performance_line = "Audience activity is available, so focus on the response signal that needs the clearest prompt."
        if weakest_metric == "comments":
            next_action = "Add a simple question or opinion prompt so people know exactly how to reply."
        elif weakest_metric == "shares":
            next_action = "Make the takeaway more useful or surprising so people have a reason to share it."
        else:
            next_action = "Strengthen the opening line and visual promise so viewers react faster."
        summary = f"Summary: The next post should improve {weakest_metric} by making the payoff clearer."

    return _localize_analysis_terms(
        (
        "Overall performance\n"
        f"{performance_intro}\n\n"
        "Content diagnosis\n"
        f"{performance_line}\n\n"
        f"{topic_line}\n"
        f"{caption_line}\n"
        f"{reason_line}\n\n"
        "Visual and content signals\n"
        f"{visual_line}\n"
        f"{hashtag_line}\n\n"
        "Audience behaviour\n"
        f"{comment_line}\n\n"
        "Improvement opportunities\n"
        f"{next_action}\n"
        "For the next post, put the strongest benefit or reveal earlier and make the call to engage feel natural.\n\n"
        f"{summary}"
        ),
        language,
        analytics_data,
    )


def generate_post_analysis(analytics_data, language="English"):
    language_label = _analysis_language_label(language)
    _log_analysis_language(language, language_label)
    effective_language = "traditional_chinese" if _analysis_should_use_traditional_chinese(language, analytics_data) else language
    fallback = generate_post_rule_based_analysis(analytics_data, language=language)
    if not getattr(settings, "GEMINI_API_KEY", ""):
        return fallback

    image_input = _first_supported_image_input(analytics_data.get("image_file"))
    if image_input and len(image_input.data) > MAX_GEMINI_IMAGE_BYTES:
        logger.warning("Gemini image skipped because it exceeds the configured size limit")
        image_input = None
    prompt_payload = {
        key: value
        for key, value in analytics_data.items()
        if key != "image_file"
    }
    prompt_payload["media"] = {
        **(prompt_payload.get("media") or {}),
        "gemini_image_attached": bool(image_input),
        "image_instruction": (
            "Use the first image as visual context for content strategy, not as a simple image description task."
            if image_input
            else "No supported image is attached. Do not invent visual details."
        ),
    }
    comment_context = _post_comment_context(analytics_data)
    include_comment_analysis = comment_context["meaningful_count"] >= 2
    required_headings = _post_insight_headings(
        _analysis_uses_traditional_chinese(effective_language),
        include_comment_analysis,
    )
    prompt = {
        "post_analytics_data": prompt_payload,
        "instructions": [
            *_analysis_language_rules(language_label),
            "Act as a Social Media Content Consultant, not a template metrics reviewer.",
            "Do not only explain what happened. Explain what the post is trying to do, why performance happened, which element helped or hurt performance, and what the creator should do next.",
            "Return structured product insight for an analytics dashboard, not a long report.",
            f"Generate only these sections in this exact order: {', '.join(required_headings)}.",
            "Do not generate Content understanding or 內容理解.",
            "Do not generate Summary or 總結.",
            "Generate sections dynamically; never add an unsupported or low-value section.",
            "Default to exactly 1 concise point per section. Add a second point only when it contains a genuinely distinct, important insight.",
            "Never create a second point merely to fill a section. The entire insight should contain no more than 8 points.",
            "Write concise but complete points.",
            "Each point should be one complete sentence.",
            "Avoid filler, repetition, generic advice, and unnecessary metric restatement.",
            "Be brief, specific, and actionable, but never truncate ideas.",
            "Never repeat the same information across sections.",
            "Improvement opportunities and Next content recommendation must not overlap.",
            "If a section has no distinct new information, use only 1 useful point and never add filler.",
            "Avoid long paragraphs, filler words, and restating the post description.",
            "Prioritize why performance happened and what to do next.",
            "Do not use emojis, icons, or decorative markers.",
            "Do not include a Key data section.",
            "Do not repeat raw metric lists already visible in KPI cards or charts.",
            "Keep the report concise but specific. Use evidence from the supplied payload.",
            "Analyze post content: title, caption/description, article caption, article body, hashtags, platform, post type, visibility, created/published time when available.",
            "Analyze creator context: bio, display name, username, recent post titles, and recent hashtags if supplied. Do not quote private profile details unnecessarily.",
            "If a supported image is attached, analyze only the first image. Use it as visual strategy context: composition, subject, style, scroll-stopping potential, brand fit, and clarity.",
            "If media_type is video, do not inspect frames and do not analyze video retention here.",
            "Classify the likely content purpose when possible: artwork showcase, portfolio update, brand awareness, product/project update, community engagement, educational content, entertainment, campaign teaser, or personal update.",
            "Do not assume something is an official mascot, logo, or brand character unless title, caption, bio, campaign, or visual context supports that claim. If uncertain, say artwork, character illustration, or design work.",
            "Analyze hashtags for relevance, specificity, platform fit, and discoverability. Do not repeat every hashtag.",
            "Analyze audience behaviour across views, likes, comments, shares, engagement_rate, like_rate, comment_rate, and share_rate.",
            "Audience data exists when views > 0 OR likes > 0 OR comments > 0 OR shares > 0. Say no audience data only when all four values are zero.",
            "If views are under 10, treat all performance metrics as early signals and explicitly say the sample is too small for reliable conclusions.",
            "If views are under 10 but any interaction exists, call it a small-sample early signal, never no audience data.",
            "If engagement rate is high but views are under 10, say the content resonates with a very small reached audience, but more exposure is needed before judging performance.",
            "Never call a post highly successful or describe 100% engagement as exceptional when views are under 10.",
            "If views are low but meaningful comments exist, describe the response as promising but limited.",
            "If comments exceed views, explain that replies, direct interactions, or repeated engagement may be involved, so this is not broad reach.",
            "Compare signals carefully: high likes but low comments may mean easy appreciation but weak conversation prompt; high comments but low shares may mean discussion without shareable value; high shares but low comments may mean useful content with less conversation; high views but low engagement may mean the hook attracted attention but content did not convert.",
            "Analyze only meaningful comments supplied in comments.items. Treat skipped spam and low-signal counts as filtering notes, not audience sentiment.",
            f"The payload contains {comment_context['meaningful_count']} meaningful comment(s) and {comment_context['skipped_spam_count'] + comment_context['skipped_low_signal_count']} ignored comment(s).",
            "Generate Comment analysis only when meaningful_comment_count is at least 2.",
            "When meaningful_comment_count is 1, mention only in Audience behaviour that sentiment is unreliable.",
            "When meaningful_comment_count is 0 and ignored_comment_count is positive, mention only that invalid comments were ignored.",
            "When meaningful_comment_count is 0 or 1, never infer audience sentiment or claim the audience liked the content based on comments.",
            "Never treat ignored comments as audience interest and never mention or quote ignored comment text.",
            "Do not speculate about creator intent. Make claims only from title, caption, image, hashtags, creator bio, metrics, or meaningful comments.",
            "Do not call artwork or an avatar a mascot unless title, caption, bio, or campaign explicitly says mascot, official brand character, logo, or brand character.",
            "If title or caption says icon, avatar, or default avatar, use that term instead of mascot.",
            "Do not merely describe the image; explain why the visual matters for clarity, recognition, engagement, or discoverability.",
            "Cross-analyze image/media, title, caption, hashtags, metrics, meaningful comments, creator context, and platform.",
            "Give specific next steps. Do not give generic advice like improve hook without saying what kind of hook to use.",
            "Recommend one next content idea, such as process/before-after, a clearer reveal, a question prompt, a carousel breakdown, a stronger CTA, or improved hashtag direction when supported.",
            f"If there is no audience data and English is selected, include this exact sentence: {NO_POST_ANALYTICS_DATA_MESSAGE}",
            f"If there is no audience data and Traditional Chinese is selected, include this exact sentence: {_localized_no_post_data_message('traditional_chinese')}",
            "Do not analyze video retention data in this response.",
            "Do not invent or estimate missing metric values.",
            "Do not use AI score or numeric scoring.",
            "Return only valid JSON using this exact schema: {\"sections\":[{\"heading\":\"Overall performance\",\"points\":[\"one concise point\"]}]}.",
            "Do not return a Python dictionary, markdown, code fences, or explanatory text outside the JSON.",
        ],
    }
    try:
        response_text = _gemini_generate_text(
            (
                "You are an AI social media performance analyst. "
                "Give practical creator feedback from real metrics, post text, hashtags, first-image context when supplied, creator context, and meaningful comments. "
                "Avoid academic language and keep paragraphs short."
            ),
            (
                "Create post analytics JSON from this real performance payload:\n\n"
                f"{json.dumps(prompt, default=str)}"
            ),
            temperature=0.4,
            response_json=True,
            image_input=image_input,
        )
        parsed = _parse_ai_json(response_text)
    except Exception:
        return fallback
    structured_report = _normalize_post_insight_sections(
        parsed,
        language=effective_language,
        analytics_data=analytics_data,
    )
    if structured_report:
        if _post_has_audience_data(analytics_data):
            lower_report = structured_report.lower()
            if (
                "no audience data" in lower_report
                or NO_POST_ANALYTICS_DATA_MESSAGE.lower() in lower_report
                or _localized_no_post_data_message("traditional_chinese") in structured_report
            ):
                return fallback
        return structured_report
    try:
        report = _localize_analysis_terms(parsed.get("report"), language, analytics_data)
        if not report:
            return fallback
        has_audience_data = _post_has_audience_data(analytics_data)
        no_data_message = _localized_no_post_data_message(effective_language)
        if not has_audience_data and no_data_message not in report:
            return fallback
        if has_audience_data and (
            NO_POST_ANALYTICS_DATA_MESSAGE in report
            or _localized_no_post_data_message("traditional_chinese") in report
        ):
            return fallback
        if "summary" not in report.lower() and "總結" not in report:
            return fallback
    except Exception as exc:
        _gemini_debug(f"post analysis validation failed: {exc.__class__.__name__}")
        return fallback
    return report


def _campaign_has_engagement_data(campaign_data):
    metrics = campaign_data.get("metrics") or {}
    return any(int(metrics.get(key) or 0) for key in ("views", "likes", "comments", "shares"))


def _campaign_theme_words(campaign_data):
    campaign = campaign_data.get("campaign") or {}
    posts = campaign_data.get("posts") or []
    text_chunks = [
        campaign.get("name", ""),
        campaign.get("objective", ""),
        campaign.get("strategy", ""),
        campaign.get("description", ""),
    ]
    for post in posts:
        text_chunks.extend(
            [
                post.get("title", ""),
                post.get("caption", ""),
                post.get("article_caption", ""),
                post.get("article_body", ""),
                post.get("hashtags", ""),
            ]
        )
    stop_words = {
        "the", "and", "for", "with", "this", "that", "from", "your", "you", "are",
        "was", "were", "have", "has", "post", "posts", "campaign", "content", "about",
        "into", "our", "their", "they", "them", "will", "can", "all", "not", "but",
    }
    counts = {}
    for chunk in text_chunks:
        for raw_word in str(chunk or "").lower().replace("#", " ").split():
            word = "".join(char for char in raw_word if char.isalnum())
            if len(word) < 4 or word in stop_words:
                continue
            counts[word] = counts.get(word, 0) + 1
    return [word for word, _ in sorted(counts.items(), key=lambda item: item[1], reverse=True)[:5]]


def _campaign_insight_headings(use_traditional_chinese=False):
    if use_traditional_chinese:
        return (
            "活動整體表現",
            "活動一致性",
            "受眾行為",
            "內容策略",
            "改善機會",
            "下一波活動建議",
        )
    return (
        "Campaign performance",
        "Campaign consistency",
        "Audience behaviour",
        "Content strategy",
        "Growth opportunities",
        "Next campaign recommendation",
    )


def _campaign_released_posts(campaign_data):
    return [
        post for post in campaign_data.get("posts") or []
        if str(post.get("status") or "").lower() == "published"
    ]


def _campaign_post_engagements(post):
    return sum(_safe_int(post.get(key)) for key in ("likes", "comments", "shares"))


def _campaign_hashtag_sets(posts):
    return [
        {tag.lower() for tag in split_hashtags(post.get("hashtags", ""))}
        for post in posts
        if split_hashtags(post.get("hashtags", ""))
    ]


def _campaign_common_hashtags(posts):
    hashtag_sets = _campaign_hashtag_sets(posts)
    if len(hashtag_sets) < 2:
        return set()
    return set.intersection(*hashtag_sets)


def _campaign_has_cta(post):
    caption = _clean_ai_text(post.get("caption") or post.get("article_caption")).lower()
    return bool(re.search(r"\?|？|\b(comment|share|save|tell us|reply|choose|which|what do you think)\b", caption))


def _campaign_objective_kind(objective_text):
    objective = _clean_ai_text(objective_text).lower()
    keyword_groups = (
        ("conversion", ("conversion", "sales", "apply", "signup", "sign up", "membership", "subscription", "轉換", "銷售", "申請", "註冊", "會員", "訂閱")),
        ("engagement", ("engagement", "community", "comments", "comment", "互動", "社群", "留言")),
        ("awareness", ("awareness", "brand", "launch", "introduce", "recognition", "品牌", "發布", "推出", "介紹", "認知")),
    )
    for kind, keywords in keyword_groups:
        if any(keyword in objective for keyword in keywords):
            return kind
    return "general"


def _campaign_post_format(post):
    value = _clean_ai_text(post.get("post_type") or post.get("content_format")).lower()
    if value in {"image", "illustration", "photo", "artwork"}:
        return "image"
    if value in {"video", "reel", "short", "clip"}:
        return "video"
    if value in {"article", "blog", "text"}:
        return "article"
    if value in {"carousel", "slides"}:
        return "carousel"
    return "unknown"


def _campaign_next_recommendation(campaign_data, best_post, use_zh=False):
    campaign = campaign_data.get("campaign") or {}
    metrics = campaign_data.get("metrics") or {}
    released_posts = _campaign_released_posts(campaign_data)
    released_count = _safe_int(campaign_data.get("released_count")) or len(released_posts)
    scheduled_count = _safe_int(campaign_data.get("scheduled_count"))
    views = _metric_value(metrics, "views")
    engagements = sum(_metric_value(metrics, key) for key in ("likes", "comments", "shares"))
    objective = _clean_ai_text(
        campaign.get("objective") or campaign.get("strategy") or campaign.get("description")
    )
    objective_kind = _campaign_objective_kind(objective)
    common_hashtags = _campaign_common_hashtags(released_posts)
    cta_count = sum(1 for post in released_posts if _campaign_has_cta(post))
    best_format = _campaign_post_format(best_post)
    formats = {
        _campaign_post_format(post)
        for post in released_posts
        if _campaign_post_format(post) != "unknown"
    }
    campaign_name = _clean_ai_text(campaign.get("name"))
    best_title = _clean_ai_text(best_post.get("title")) or campaign_name

    if views < 10 or engagements == 0:
        if use_zh:
            return "先測試更清楚的共同主題與分發方式，固定 CTA 與活動 hashtag 後再判斷偏好。"
        return "Test a clearer shared theme and broader distribution; keep one CTA and campaign hashtag constant before judging audience preference."

    if released_count == 1:
        next_steps = {
            "image": ("製作過程或細節拆解", "a process or detail-breakdown post"),
            "video": ("短版延伸片段或開場測試", "a short follow-up clip or hook test"),
            "article": ("重點輪播或短篇視覺摘要", "a takeaway carousel or short visual summary"),
            "carousel": ("最強單張延伸或細節補充", "a strongest-slide follow-up or detail expansion"),
            "unknown": ("解釋核心想法的延伸貼文", "a follow-up post explaining the main idea"),
        }
        zh_step, en_step = next_steps[best_format]
        if objective_kind == "engagement":
            return f"用{zh_step}建立後續系列，並加入投票式互動問題。" if use_zh else f"Build a follow-up sequence with {en_step}, ending with a poll-style question."
        if objective_kind == "conversion":
            return f"用{zh_step}建立後續系列，補上效益證明與明確 CTA。" if use_zh else f"Build a follow-up sequence with {en_step}, adding benefit proof and a clear CTA."
        if objective_kind == "awareness":
            return f"用{zh_step}建立後續系列，固定辨識主題、hashtag 與 CTA。" if use_zh else f"Build a follow-up sequence with {en_step}, reinforcing recognition with one hashtag and CTA."
        return f"用{zh_step}建立後續系列，延伸「{best_title}」並加入明確 CTA。" if use_zh else f'Build a follow-up sequence with {en_step}, extending "{best_title}" with one clear CTA.'

    target = "下一則排程貼文" if use_zh and scheduled_count else "下一則貼文" if use_zh else "the next scheduled post" if scheduled_count else "the next post"
    if objective_kind == "conversion":
        action = "加入效益證明、FAQ 或比較內容，並統一明確 CTA" if use_zh else "add benefit proof, an FAQ or comparison, and one clear CTA"
    elif objective_kind == "engagement":
        action = "延伸最佳主題，加入投票或明確互動問題" if use_zh else "extend the strongest theme with a poll or explicit discussion question"
    elif objective_kind == "awareness":
        action = "延伸最佳主題，強化辨識元素與固定活動 hashtag" if use_zh else "extend the strongest theme with recognizable cues and one campaign hashtag"
    elif len(formats) > 1:
        action = "沿用最佳主題測試另一種格式，其他訊號保持一致" if use_zh else "test another format around the strongest theme while keeping other signals consistent"
    else:
        action = "延伸最佳主題並測試新的內容角度" if use_zh else "extend the strongest theme through a new content angle"

    additions = []
    if cta_count < released_count and objective_kind not in {"conversion", "engagement"}:
        additions.append("統一 CTA" if use_zh else "standardize the CTA")
    if not common_hashtags and objective_kind != "awareness":
        additions.append("固定活動 hashtag" if use_zh else "reuse one campaign hashtag")
    if additions:
        joiner = "並" if use_zh else " and "
        action = f"{action}，{joiner.join(additions)}" if use_zh else f"{action}; {joiner.join(additions)}"
    return f"請讓{target}{action}。" if use_zh else f"Use {target} to {action}."


def _trim_campaign_point(value, use_traditional_chinese=False):
    return _clean_complete_ai_point(value)


def _serialize_campaign_insight_sections(sections, use_traditional_chinese=False):
    normalized = []
    total_points = 0
    for heading, points in sections:
        remaining_sections = len(sections) - len(normalized) - 1
        point_limit = min(2, max(8 - total_points - remaining_sections, 1))
        clean_points = []
        for point in points:
            clean_point = _trim_campaign_point(point, use_traditional_chinese)
            if clean_point and clean_point not in clean_points:
                clean_points.append(clean_point)
            if len(clean_points) == point_limit:
                break
        if not clean_points:
            return ""
        normalized.append({"heading": heading, "points": clean_points})
        total_points += len(clean_points)
    return json.dumps({"sections": normalized}, ensure_ascii=False)


def _normalize_campaign_insight_sections(parsed, language="English"):
    sections = parsed.get("sections") if isinstance(parsed, dict) else None
    if not isinstance(sections, list):
        return ""
    use_zh = _analysis_uses_traditional_chinese(language)
    expected = _campaign_insight_headings(use_zh)

    def heading_key(value):
        return _clean_ai_text(value).lower().replace("&", "and").rstrip(":：")

    aliases = {
        "campaign overview": "campaign performance",
        "content consistency": "campaign consistency",
        "audience response": "audience behaviour",
        "posting strategy": "content strategy",
        "improvement opportunities": "growth opportunities",
        "活動總覽": "活動整體表現",
        "內容一致性": "活動一致性",
        "受眾反應": "受眾行為",
        "發文策略": "內容策略",
    }
    lookup = {}
    for section in sections:
        if not isinstance(section, dict):
            continue
        key = heading_key(section.get("heading"))
        if key in {"summary", "總結"}:
            continue
        lookup[heading_key(aliases.get(key, key))] = section

    normalized = []
    for heading in expected:
        section = lookup.get(heading_key(heading))
        if not isinstance(section, dict) or not isinstance(section.get("points"), list):
            return ""
        normalized.append((heading, section["points"]))
    return _serialize_campaign_insight_sections(normalized, use_zh)


def _generate_campaign_strategy_fallback(campaign_data, language="English"):
    use_zh = _analysis_should_use_traditional_chinese(language, campaign_data)
    released_posts = _campaign_released_posts(campaign_data)
    released_count = _safe_int(campaign_data.get("released_count")) or len(released_posts)
    if released_count == 0:
        effective_language = "traditional_chinese" if use_zh else language
        return _localized_not_enough_campaign_message(effective_language)

    campaign = campaign_data.get("campaign") or {}
    metrics = campaign_data.get("metrics") or {}
    views = _metric_value(metrics, "views")
    engagements = sum(_metric_value(metrics, key) for key in ("likes", "comments", "shares"))
    objective = _clean_ai_text(
        campaign.get("objective") or campaign.get("strategy") or campaign.get("description")
    )
    best_post = max(
        released_posts,
        key=lambda post: (_campaign_post_engagements(post), _safe_int(post.get("views"))),
        default={},
    )
    best_title = _clean_ai_text(best_post.get("title")) or ("未命名貼文" if use_zh else "Untitled post")
    weakest_post = min(
        released_posts,
        key=lambda post: (_campaign_post_engagements(post), _safe_int(post.get("views"))),
        default={},
    )
    weakest_title = _clean_ai_text(weakest_post.get("title")) or ("未命名貼文" if use_zh else "Untitled post")
    formats = {
        _clean_ai_text(post.get("post_type") or post.get("content_format"))
        for post in released_posts
        if _clean_ai_text(post.get("post_type") or post.get("content_format"))
    }
    common_hashtags = _campaign_common_hashtags(released_posts)
    cta_count = sum(1 for post in released_posts if _campaign_has_cta(post))
    published_dates = {
        post.get("published_at") or post.get("published_date")
        for post in released_posts
        if post.get("published_at") or post.get("published_date")
    }
    headings = _campaign_insight_headings(use_zh)
    recommendation = _campaign_next_recommendation(campaign_data, best_post, use_zh)

    if use_zh:
        performance = (
            "目前觸及與互動樣本有限，先判讀內容結構，不宜推論受眾偏好。"
            if views < 10 or engagements == 0
            else f"目前以「{best_title}」帶動最強反應，可作為後續系列基準。"
        )
        consistency = (
            "目前是單篇活動；若目標也未定義，系列主軸仍不明確。"
            if released_count == 1
            else "活動目標不夠明確，貼文即使相關也缺少共同承諾。"
            if not objective
            else f"多篇貼文已有共同方向，發布分布在 {len(published_dates)} 個時間點。"
        )
        audience = (
            "目前沒有互動，無法判斷受眾偏好，應先測試主題與分發。"
            if engagements == 0
            else f"「{best_title}」反應最強，「{weakest_title}」較弱，但仍需更多樣本驗證。"
        )
        strategy = (
            f"已測試 {len(formats)} 種格式；應固定主題與 hashtag 再比較差異。"
            if len(formats) > 1
            else "內容格式仍單一，尚未形成揭示、說明、證明與跟進的進程。"
        )
        if len(released_posts) > 1 and not common_hashtags:
            strategy = "各篇缺少共用活動 hashtag，系列辨識與追蹤訊號偏弱。"
        elif cta_count < len(released_posts):
            strategy = "各篇 CTA 不一致，受眾不容易理解系列期待的下一步。"
        growth = (
            "最大限制是觸及過低；先收緊共同主題並改善分發測試。"
            if views < 10
            else "最大限制是系列連貫性不足，內容尚未累積活動動能。"
        )
    else:
        performance = (
            "Reach and interaction are limited, so assess campaign structure before inferring audience preference."
            if views < 10 or engagements == 0
            else f'"{best_title}" currently drives the strongest response and should anchor the next campaign sequence.'
        )
        consistency = (
            "This is a single-post campaign; without a defined objective, its series direction remains unclear."
            if released_count == 1
            else "The campaign objective is unclear, leaving related posts without one explicit promise."
            if not objective
            else f"The posts share one direction across {len(published_dates)} publishing point(s), but must reinforce the same promise."
        )
        audience = (
            "With no engagement, audience preference is unknown; test a clearer theme and distribution approach first."
            if engagements == 0
            else f'"{best_title}" leads response while "{weakest_title}" trails, though more reach is needed to confirm the pattern.'
        )
        strategy = (
            f"The campaign tests {len(formats)} formats; hold the theme and campaign hashtag constant before comparing their response."
            if len(formats) > 1
            else "One format dominates, without a reveal, explanation, proof, and follow-up progression."
        )
        if len(released_posts) > 1 and not common_hashtags:
            strategy = "Posts lack a repeated campaign hashtag, weakening series recognition and cross-post discoverability."
        elif cta_count < len(released_posts):
            strategy = "CTAs vary across posts, so the audience receives no consistent next action."
        growth = (
            "Low reach is the main constraint; tighten the shared theme and test distribution before judging performance."
            if views < 10
            else "Weak series continuity is the main constraint, preventing individual responses from building campaign momentum."
        )
    return _serialize_campaign_insight_sections(
        list(zip(headings, ([performance], [consistency], [audience], [strategy], [growth], [recommendation]))),
        use_zh,
    )


def generate_campaign_rule_based_analysis(campaign_data, language="English"):
    return _generate_campaign_strategy_fallback(campaign_data, language=language)


def _generate_campaign_strategy_analysis(campaign_data, language="English"):
    language_label = _analysis_language_label(language)
    _log_analysis_language(language, language_label)
    effective_language = (
        "traditional_chinese"
        if _analysis_should_use_traditional_chinese(language, campaign_data)
        else language
    )
    fallback = _generate_campaign_strategy_fallback(campaign_data, language=language)
    if _safe_int(campaign_data.get("released_count")) == 0:
        return fallback
    if not getattr(settings, "GEMINI_API_KEY", ""):
        return fallback

    headings = _campaign_insight_headings(
        _analysis_uses_traditional_chinese(effective_language)
    )
    prompt = {
        "campaign_data": campaign_data,
        "instructions": [
            *_analysis_language_rules(language_label),
            "Treat the campaign as one connected marketing initiative, not isolated posts.",
            "Do not merely summarize metrics. Explain why the campaign performed this way.",
            "Analyze campaign objective alignment and whether posts build a coherent story.",
            "Analyze whether post topics feel connected or scattered.",
            "Analyze campaign momentum across time.",
            "Analyze posting rhythm using scheduled_at, published_at, and trend_points.",
            "Analyze hashtag consistency across posts.",
            "Analyze CTA consistency across captions.",
            "Analyze content format performance by post_type when data exists.",
            "Assess whether the series has progression such as reveal, explanation, proof, and follow-up.",
            "If data is limited, say so once and focus on structure, not performance certainty.",
            "Do not overstate conclusions from very low views or few released posts.",
            "If released_count is 1, call this a single-post campaign and recommend building a sequence.",
            "If released_count is 2 or more, evaluate whether the posts work together.",
            "If no engagement exists, do not infer audience preference.",
            "If engagement is limited, focus on content structure and the next useful test.",
            "Avoid repeating the same limitation across sections.",
            "Recommendations must be actionable and campaign-level.",
            "Do not recommend only changing one post unless it clearly supports the campaign strategy.",
            "Do not invent missing metrics, dates, campaign goals, or audience preferences.",
            "If the campaign objective is empty, evaluate the implied theme but say the objective is unclear.",
            "Do not repeat complete KPI lists already visible on the dashboard.",
            "Use the selected language exactly. Do not use emojis or decorative markers.",
            f"Return all six sections in this exact order: {', '.join(headings)}.",
            "Do not return Summary or 總結.",
            "Default to one point per section; add a second only for a distinct, important insight.",
            "The complete response should contain no more than 8 points.",
            "Write concise but complete points.",
            "Each point should be one complete sentence.",
            "Avoid filler, repetition, generic advice, and unnecessary metric restatement.",
            "Be brief, specific, and actionable, but never truncate ideas.",
            "Do not add filler to complete a section.",
            "Return valid JSON only, with this schema: {\"sections\":[{\"heading\":\"Campaign performance\",\"points\":[\"...\"]}]}",
            "Do not return a Python dictionary, markdown, code fences, or text outside the JSON.",
        ],
    }
    try:
        response_text = _gemini_generate_text(
            (
                "You are a campaign strategy consultant for social media creators. "
                "Evaluate connected campaign narrative, momentum, formats, CTAs, hashtags, and the next executable move."
            ),
            (
                "Create a concise campaign strategy insight from this real payload:\n\n"
                f"{json.dumps(prompt, ensure_ascii=False, default=str)}"
            ),
            temperature=0.4,
            response_json=True,
        )
        parsed = _parse_ai_json(response_text)
        normalized = _normalize_campaign_insight_sections(
            parsed,
            language=effective_language,
        )
        if normalized:
            return normalized
        if isinstance(parsed, dict) and "sections" in parsed:
            return fallback
        legacy_report = _localize_analysis_terms(
            parsed.get("report") if isinstance(parsed, dict) else "",
            language,
            campaign_data,
        )
        return legacy_report or fallback
    except Exception as exc:
        _gemini_debug(f"campaign strategy analysis failed: {exc.__class__.__name__}")
        return fallback


def generate_campaign_analysis(campaign_data, language="English"):
    return _generate_campaign_strategy_analysis(campaign_data, language=language)


def _engagement_timing_label(seconds, duration):
    if duration <= 0:
        return "unknown"
    position = seconds / duration
    if position < 1 / 3:
        return "early"
    if position < 2 / 3:
        return "middle"
    return "late"


def generate_video_retention_rule_based_analysis(retention_data, language="English"):
    language_label = _analysis_language_label(language)
    _log_analysis_language(language, language_label)
    use_traditional_chinese = _analysis_should_use_traditional_chinese(language, retention_data)
    points = retention_data.get("points") or []
    if not points:
        if use_traditional_chinese:
            return "目前沒有可用的留存資料。"
        return "No retention data is available yet."

    duration = max(int(point.get("seconds") or 0) for point in points)
    first_point = points[0]
    last_point = points[-1]
    start_retention = float(first_point.get("retention") or 0)
    final_retention = float(last_point.get("retention") or 0)
    retention_loss = max(start_retention - final_retention, 0)

    biggest_drop = None
    for previous, current in zip(points, points[1:]):
        drop = float(previous.get("retention") or 0) - float(current.get("retention") or 0)
        if biggest_drop is None or drop > biggest_drop["drop"]:
            biggest_drop = {
                "drop": max(drop, 0),
                "seconds": int(current.get("seconds") or 0),
                "retention": float(current.get("retention") or 0),
            }

    engagement_points = [
        point for point in points
        if int(point.get("engagement_count") or 0) > 0
    ]
    if engagement_points:
        peak_engagement = max(
            engagement_points,
            key=lambda point: int(point.get("engagement_count") or 0),
        )
        timing = _engagement_timing_label(int(peak_engagement.get("seconds") or 0), duration)
        if use_traditional_chinese:
            timing_labels = {"early": "前段", "middle": "中段", "late": "後段", "unknown": "不明"}
            engagement_timing = f"互動在影片{timing_labels.get(timing, '不明')}最強，代表觀眾在看到明確行動理由後才更容易回應。"
        else:
            engagement_timing = (
                f"Engagement is strongest {timing} in the video, which suggests viewers respond once the content gives them a clear reason to act."
            )
    else:
        timing = "unknown"
        engagement_timing = (
            "留存圖目前還沒有記錄到分秒層級的讚、留言或分享。"
            if use_traditional_chinese
            else "No timed likes, comments, or shares are recorded on the retention chart yet."
        )

    if use_traditional_chinese:
        if final_retention >= 70:
            opening_line = "這支影片目前能穩定留住注意力，開場大致發揮了作用。"
            pattern = "留存曲線在取樣觀看路徑中保持得相對穩定。"
        elif retention_loss >= 50:
            opening_line = "這支影片有吸引力，但開場還不足以留住觀眾。"
            pattern = "留存曲線出現明顯下滑，通常代表開場或前段回報不夠清楚。"
        else:
            opening_line = "這支影片有潛力，但中段需要更清楚的理由讓觀眾繼續看。"
            pattern = "留存曲線是逐步下滑而非崩落，因此節奏與中段價值是主要改善點。"
        if biggest_drop and biggest_drop["drop"] > 0:
            drop_off = f"主要流失點約在 {biggest_drop['seconds']} 秒，代表該段可能在清晰度、節奏或回報上變弱。"
        else:
            drop_off = "目前沒有明顯流失點；取樣區間中的留存大致持平。"
        problems = []
        if biggest_drop and biggest_drop["seconds"] <= max(duration / 3, 1) and biggest_drop["drop"] > 0:
            problems.append("前幾秒的開場可能還沒有製造足夠好奇心。")
        if biggest_drop and biggest_drop["seconds"] > duration / 2 and biggest_drop["drop"] > 0:
            problems.append("主要回報可能對部分觀眾來得太晚。")
        if retention_loss >= 50:
            problems.append("以目前節奏來看，影片可能偏長。")
        if timing == "late":
            problems.append("互動集中在後段，因此觀眾可能需要更早知道為什麼要在意。")
        if not problems:
            problems.append("整體結構大致可行，但下一則影片仍需要更明確的中段節奏點。")
        if retention_loss >= 50:
            suggestion = "把最強的揭示點提前，並刪掉主要回報前不必要的鋪陳。"
        elif timing == "late":
            suggestion = "更早預告後段回報，讓觀眾知道為什麼值得留下。"
        elif timing == "early":
            suggestion = "開場已經能帶來早期互動；接著加入中段節奏點來維持動能。"
        else:
            suggestion = "保留目前結構，再測試一個更清楚的中段觀看理由。"
        return _localize_analysis_terms(
            (
            "整體表現\n"
            f"{opening_line} {pattern}\n\n"
            "留存診斷\n"
            f"{drop_off} 可以用這段圖表回看畫面內容、想法推進速度，以及觀眾是否得到清楚的觀看理由。\n\n"
            "互動診斷\n"
            f"{engagement_timing}\n\n"
            "主要問題\n"
            + "\n".join(f"- {problem}" for problem in problems[:3])
            + "\n\n"
            "建議改善方向\n"
            f"{suggestion} 嘗試讓前 1-2 秒更直接、視覺化且具體，並在最弱段落前加入更清楚的轉場。\n\n"
            "總結\n"
            "主要機會是更早說清楚回報，然後用留存曲線確認下一次是否能讓觀眾停留更久。"
            ),
            language,
            retention_data,
        )

    if final_retention >= 70:
        opening_line = "This video is holding attention well, so the hook is likely doing its job."
        pattern = "The retention curve stays comparatively strong through the sampled watch path."
    elif retention_loss >= 50:
        opening_line = "This video has interest, but the opening is not strong enough to hold viewers."
        pattern = "The retention curve shows a steep falloff, which usually means the hook or early payoff is not clear enough."
    else:
        opening_line = "This video has potential, but the middle needs a clearer reason to keep watching."
        pattern = "The retention curve tapers rather than collapsing, so pacing and mid-video value are the main areas to improve."

    if biggest_drop and biggest_drop["drop"] > 0:
        drop_off = f"The main drop-off happens around {biggest_drop['seconds']}s, suggesting that moment may lose clarity, pace, or payoff."
    else:
        drop_off = "There is no clear drop-off point yet; retention is mostly flat across sampled moments."

    problems = []
    if biggest_drop and biggest_drop["seconds"] <= max(duration / 3, 1) and biggest_drop["drop"] > 0:
        problems.append("The hook may not create enough curiosity in the first few seconds.")
    if biggest_drop and biggest_drop["seconds"] > duration / 2 and biggest_drop["drop"] > 0:
        problems.append("The main payoff may arrive too late for some viewers.")
    if retention_loss >= 50:
        problems.append("The video may be too long for the current pacing.")
    if timing == "late":
        problems.append("Engagement happens late, so viewers may need an earlier reason to care.")
    if not problems:
        problems.append("The structure is mostly working, but the next post still needs a sharper mid-video beat.")
    problems = problems[:3]

    if retention_loss >= 50:
        suggestion = "Move the strongest reveal earlier and trim any setup before the main payoff."
    elif timing == "late":
        suggestion = "Tease the late payoff sooner so viewers know why to stay."
    elif timing == "early":
        suggestion = "The hook earns action early; add a mid-video beat to keep momentum."
    else:
        suggestion = "Keep the current structure, then test a clearer mid-video reason to keep watching."

    return _localize_analysis_terms(
        (
        "Overall performance\n"
        f"{opening_line} {pattern}\n\n"
        "Retention diagnosis\n"
        f"{drop_off} Use this part of the chart to inspect what appears on screen, how quickly the idea develops, and whether the viewer gets a clear reason to continue.\n\n"
        "Engagement diagnosis\n"
        f"{engagement_timing}\n\n"
        "Main problems\n"
        + "\n".join(f"- {problem}" for problem in problems)
        + "\n\n"
        "Suggested improvements\n"
        f"{suggestion} Try making the first 1-2 seconds more direct, visual, and specific. Add a clearer transition before the weakest moment so viewers understand what payoff is coming next.\n\n"
        "Summary\n"
        "The main opportunity is to make the payoff clearer earlier, then use the retention curve to see if viewers stay longer next time."
        ),
        language,
        retention_data,
    )


def generate_video_retention_analysis(retention_data, language="English"):
    language_label = _analysis_language_label(language)
    _log_analysis_language(language, language_label)
    fallback = generate_video_retention_rule_based_analysis(retention_data, language=language)
    if not (retention_data.get("points") or []):
        return fallback

    if not getattr(settings, "GEMINI_API_KEY", ""):
        return fallback


    prompt = {
        "retention_chart_data": retention_data,
        "instructions": [
            *_analysis_language_rules(language_label),
            "Return one plain-text report in the selected output language, not UI card fields.",
            "Use this exact English structure if English is selected: Overall performance, Retention diagnosis, Engagement diagnosis, Main problems, Suggested improvements, Summary.",
            "Use this exact Traditional Chinese structure if Traditional Chinese is selected: 整體表現, 留存診斷, 互動診斷, 主要問題, 建議改善方向, 總結.",
            "Use short paragraphs and clear section headings.",
            "Do not use emojis, icons, or decorative markers.",
            "Do not include a Key data section.",
            "Do not repeat raw metric lists already visible in KPI cards or charts.",
            "Do not list views, likes, comments, shares, exact retention percentages at every timestamp, or full second-by-second retention lists.",
            "Use the chart data to interpret audience behaviour rather than restating the chart.",
            "Keep the answer clear and useful, but not overly short.",
            "Analyze only the supplied real retention and timed engagement data.",
            "Analyze what the retention curve means, where the video holds attention, and where viewers may drop off.",
            "Explain why viewers may drop off based on likely content, pacing, hook, payoff, or transition issues.",
            "Explain whether engagement happens early, middle, late, or is missing when the data supports it.",
            "Give practical editing and content recommendations.",
            "Use practical creator feedback, without stating platform standards as fixed facts.",
            "If average watch time or completion rate is not supplied, phrase retention claims as based on the retention curve.",
            "Do not invent or estimate missing metric values.",
            "Return only valid JSON with this exact key: report.",
        ],
    }
    try:
        response_text = _gemini_generate_text(
            (
                "You are a short-form video performance analyst for creators. "
                "Base advice on retention and timed engagement behaviour only."
            ),
            (
                "Create video retention insight JSON from this real chart payload:\n\n"
                f"{json.dumps(prompt, default=str)}"
            ),
            temperature=0.45,
            response_json=True,
        )
        parsed = _parse_ai_json(response_text)
    except Exception:
        return fallback
    report = _localize_analysis_terms(parsed.get("report"), language, retention_data)
    if not report or ("summary" not in report.lower() and "總結" not in report):
        return fallback
    lower_report = report.lower()
    if _analysis_uses_traditional_chinese(language):
        required_sections = ("整體表現", "留存診斷", "互動診斷", "主要問題", "建議改善方向", "總結")
    else:
        required_sections = (
            "overall performance",
            "retention diagnosis",
            "engagement diagnosis",
            "main problems",
            "suggested improvements",
            "summary",
        )
    forbidden_sections = (
        "key data",
        "retention trend",
        "main weakness",
    )
    if _analysis_uses_traditional_chinese(language):
        if any(section not in report for section in required_sections):
            return fallback
    elif any(section not in lower_report for section in required_sections):
        return fallback
    if any(section in lower_report for section in forbidden_sections):
        return fallback
    return report


def generate_video_content_guidance(analysis_data, post_data, language="English"):
    """Turn Video Intelligence annotations into concise, structured creator advice."""
    labels = [_clean_ai_text(item.get("description")) for item in (analysis_data.get("labels") or []) if _clean_ai_text(item.get("description"))][:8]
    shot_count = int(analysis_data.get("shot_count") or 0)
    explicit_likelihood = _clean_ai_text((analysis_data.get("explicit_content") or {}).get("max_likelihood")) or "LIKELIHOOD_UNSPECIFIED"
    topic = ", ".join(labels[:3]) or _clean_ai_text(post_data.get("title")) or "the video topic"
    hashtag_candidates = []
    for label in labels:
        tag = re.sub(r"[^\w]", "", label, flags=re.UNICODE)
        if tag and tag.lower() not in {item.lower().lstrip("#") for item in hashtag_candidates}:
            hashtag_candidates.append(f"#{tag}")
    fallback = {
        "summary": f"The video mainly features {topic} and contains {shot_count} detected shot{'s' if shot_count != 1 else ''}.",
        "caption_ideas": [f"A closer look at {topic}.", f"What stands out to you about {topic}?"],
        "hashtags": hashtag_candidates[:5],
        "improvements": [
            "Make the opening frame state the value or payoff immediately.",
            "Use each shot change to advance the story and remove repeated setup.",
            "Match the caption and on-screen text to the video's clearest detected subject.",
        ],
    }
    if explicit_likelihood in {"POSSIBLE", "LIKELY", "VERY_LIKELY"}:
        fallback["improvements"].append("Review the flagged frames before publishing and apply the destination platform's audience controls if needed.")
    if not getattr(settings, "GEMINI_API_KEY", ""):
        return fallback
    language_label = _analysis_language_label(language)
    payload = {
        "post": post_data,
        "video_analysis": analysis_data,
        "instructions": [
            *_analysis_language_rules(language_label),
            "Write for the creator who uploaded the video.",
            "Use only the supplied labels, shot data, explicit-content likelihood, and post metadata.",
            "Do not claim to understand speech, music, sentiment, people, or story details that were not detected.",
            "Keep the summary to no more than two short sentences.",
            "Return 2 or 3 concise caption ideas, up to 5 relevant hashtags, and 2 to 4 actionable improvements.",
            "Prefix every hashtag with # and do not include spaces inside a hashtag.",
            "Treat explicit-content detection as a review signal, not a definitive moderation decision.",
            "Return valid JSON only with keys summary, caption_ideas, hashtags, improvements.",
        ],
    }
    try:
        response_text = _gemini_generate_text(
            "You are a concise video content coach for social media creators.",
            f"Create creator guidance from this payload:\n\n{json.dumps(payload, ensure_ascii=False, default=str)}",
            temperature=0.45,
            response_json=True,
        )
        parsed = _parse_ai_json(response_text)
        if not isinstance(parsed, dict):
            return fallback
        summary = _clean_ai_text(parsed.get("summary"))
        captions = [_clean_ai_text(item) for item in parsed.get("caption_ideas", []) if _clean_ai_text(item)][:3]
        hashtags = []
        for item in parsed.get("hashtags", []):
            tag = re.sub(r"\s+", "", _clean_ai_text(item))
            if tag:
                hashtags.append(tag if tag.startswith("#") else f"#{tag}")
        improvements = [_clean_ai_text(item) for item in parsed.get("improvements", []) if _clean_ai_text(item)][:4]
        if not summary or not captions or not improvements:
            return fallback
        return {"summary": summary, "caption_ideas": captions, "hashtags": hashtags[:5], "improvements": improvements}
    except Exception as exc:
        _gemini_debug(f"video content guidance failed: {exc.__class__.__name__}")
        return fallback


def _clean_ai_text(value):
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1].strip()
    return text


def _clean_complete_ai_point(value):
    text = _clean_ai_text(value)
    if not text:
        return ""
    text = re.sub(r"^\s*(?:[-*•]+|\d+[.)])\s*", "", text).strip()
    text = re.sub(r"\s+", " ", text)
    text = text.rstrip("，、；：,;: ")
    if text and text[-1] not in ".!?。！？":
        text += "。" if _cjk_ratio(text) > 0.25 else "."
    return text


def _first_sentence(value):
    text = _clean_ai_text(value)
    for marker in (". ", "! ", "? "):
        if marker in text:
            return text.split(marker, 1)[0].strip() + marker.strip()
    return text


def _parse_ai_json(value):
    text = _clean_ai_text(value)
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(text[start:end + 1])
        raise


def _field_payload_value(field_data, *keys):
    if not isinstance(field_data, dict):
        return ""
    for key in keys:
        value = _clean_ai_text(field_data.get(key))
        if value:
            return value
    return ""


def _limit_text(value, max_length):
    return (value or "")[:max_length]


def _limit_hashtags(value, max_count):
    return " ".join(split_hashtags(value)[:max_count])


def _payload_list(value):
    if not value:
        return []
    if isinstance(value, (list, tuple)):
        return [_clean_ai_text(item) for item in value if _clean_ai_text(item)]
    if isinstance(value, str):
        return [_clean_ai_text(item) for item in value.splitlines() if _clean_ai_text(item)]
    return []


def _creator_context_text_values(value):
    if not isinstance(value, dict):
        return []
    text_values = [
        value.get("bio", ""),
        value.get("display_name", ""),
        value.get("username", ""),
    ]
    text_values.extend(_payload_list(value.get("profile_links")))
    text_values.extend(_payload_list(value.get("recent_post_titles")))
    text_values.extend(_payload_list(value.get("recent_hashtags")))
    return text_values


def _context_words_from_payload(payload):
    text_values = [
        payload.get("current_value", ""),
        payload.get("title", ""),
        payload.get("caption", ""),
        payload.get("article_caption", ""),
        payload.get("article_text", ""),
        payload.get("hashtags", ""),
        payload.get("content_description", ""),
        payload.get("post_type", ""),
        payload.get("campaign", ""),
        payload.get("campaign_objective", ""),
        payload.get("campaign_strategy", ""),
    ]
    text_values.extend(_creator_context_text_values(payload.get("creator_context")))
    text_values.extend(_payload_list(payload.get("previous_post_titles")))
    text_values.extend(_payload_list(payload.get("previous_post_captions")))
    text_values.extend(_payload_list(payload.get("previous_post_hashtags")))
    stop_words = {
        "the", "and", "for", "with", "this", "that", "from", "your", "you", "are",
        "was", "were", "have", "has", "post", "posts", "image", "video", "photo",
        "caption", "title", "about", "into", "just", "more", "very", "new",
    }
    words = []
    for value in text_values:
        for raw_word in str(value or "").lower().replace("#", " ").replace("_", " ").split():
            word = "".join(char for char in raw_word if char.isalnum())
            if len(word) < 3 or word in stop_words:
                continue
            if word not in words:
                words.append(word)
    return words


def _field_feedback_context_is_limited(payload):
    words = _context_words_from_payload(payload)
    return len(words) <= 3


def _title_from_context_words(words):
    if "magic" in words and "pose" in words:
        return "One Pose, One Spell"
    if "pose" in words:
        return "Wait for the Pose"
    if words:
        phrase = " ".join(words[:3]).title()
        if len(words) == 1:
            return _limit_text(f"{phrase} Moment", POST_TITLE_MAX_LENGTH)
        return _limit_text(phrase, POST_TITLE_MAX_LENGTH)
    return "Stronger Post Hook"


def _field_feedback_language(payload):
    return payload.get("ai_language_display") or payload.get("ai_language") or "English"


def _is_traditional_chinese_language(language):
    language_key = (language or "").strip().lower().replace("-", "_").replace(" ", "_")
    return language_key in {"traditional_chinese", "zh_hant", "zh_hant_"}


def _field_feedback_language_key(payload):
    return (payload.get("ai_language") or "").strip().lower().replace("-", "_").replace(" ", "_")


def _is_auto_language(language_key):
    return language_key == "auto"


def _is_english_language(language, language_key=""):
    language_key = language_key or (language or "").strip().lower().replace("-", "_").replace(" ", "_")
    return language_key in {"english", "en"}


def _cjk_ratio(value):
    text = _clean_ai_text(value)
    letters = [char for char in text if char.isalpha()]
    if not letters:
        return 0
    cjk_count = sum(1 for char in letters if "\u3400" <= char <= "\u9fff")
    return cjk_count / len(letters)


def _ascii_alpha_ratio(value):
    text = _clean_ai_text(value)
    letters = [char for char in text if char.isalpha()]
    if not letters:
        return 0
    ascii_count = sum(1 for char in letters if "a" <= char.lower() <= "z")
    return ascii_count / len(letters)


def _field_feedback_language_mismatch(feedback_type, suggestion, explanation, language, language_key):
    if _is_auto_language(language_key):
        return False
    text_to_check = explanation if feedback_type == "hashtags" else f"{suggestion} {explanation}"
    if _is_english_language(language, language_key):
        return _cjk_ratio(text_to_check) > 0.35
    if _is_traditional_chinese_language(language):
        return _ascii_alpha_ratio(text_to_check) > 0.65 and _cjk_ratio(text_to_check) < 0.2
    return False


def _safe_field_feedback_fallback(feedback_type, payload, hashtag_count=POST_HASHTAGS_MAX_COUNT):
    words = _context_words_from_payload(payload)
    platform = (payload.get("platform") or "Instagram").strip()
    post_type = (payload.get("post_type") or "post").strip().lower()
    topic_title = _title_from_context_words(words)
    clean_hashtag_count = _coerce_hashtag_count(hashtag_count)
    use_traditional_chinese = _is_traditional_chinese_language(_field_feedback_language(payload))
    use_english = _is_english_language(_field_feedback_language(payload), _field_feedback_language_key(payload))

    if feedback_type == "title":
        if use_traditional_chinese:
            topic_title = "一個姿勢，一點魔法" if "magic" in words and "pose" in words else topic_title
        elif use_english and _cjk_ratio(topic_title) > 0.25:
            topic_title = "A Clearer Post Hook"
        return FieldFeedbackResult(
            suggestion=topic_title,
            explanation=(
                "這個標題更有好奇感，也容易快速理解。"
                if use_traditional_chinese
                else "This title creates more curiosity while staying easy to scan."
            ),
        )

    if feedback_type == "caption":
        if use_traditional_chinese:
            caption = "用一個更清楚的鉤子帶出重點。\n你最先注意到哪個細節？"
        elif use_english and _cjk_ratio(" ".join(words[:3])) > 0.25:
            caption = "A clearer hook for this post.\nWhat stands out most to you?"
        elif words:
            topic_text = " ".join(words[:3])
            caption = (
                f"{topic_text.title()} with a clearer hook.\n"
                "What stands out most to you?"
            )
        else:
            caption = "A clearer moment with one simple question.\nWhat stands out most to you?"
        return FieldFeedbackResult(
            suggestion=_limit_text(caption, POST_CAPTION_MAX_LENGTH),
            explanation=(
                "這段文案加入清楚問題來鼓勵留言，同時避免捏造細節。"
                if use_traditional_chinese
                else "This caption adds a clear question to encourage comments without inventing details."
            ),
        )

    hashtag_candidates = []
    if use_english and _cjk_ratio(" ".join(words)) > 0.25:
        words = []
    if len(words) >= 2:
        hashtag_candidates.append(f"#{''.join(words[:2])}")
    hashtag_candidates.extend(f"#{word}" for word in words[:4])
    if post_type and post_type not in {"post", "photo"}:
        hashtag_candidates.append(f"#{post_type.replace(' ', '')}")
    platform_key = _platform_key(platform)
    if platform_key in {"instagram", "tiktok", "youtube"}:
        hashtag_candidates.append(f"#{platform_key}")
    hashtag_candidates.extend(["#creator", "#community", "#post", "#update", "#social"])
    hashtags = _limit_hashtags(
        _hashtag_text_from_candidates(hashtag_candidates, clean_hashtag_count),
        clean_hashtag_count,
    )
    return FieldFeedbackResult(
        suggestion=hashtags,
        explanation=(
            "這組標籤更貼近主題，也避免捏造場景。"
            if use_traditional_chinese
            else "These hashtags are more topic-specific and avoid invented scenery."
        ),
    )


def _contains_forbidden_field_context(value):
    text = _clean_ai_text(value).lower()
    forbidden_phrases = (
        "a better ",
        "today",
        "this took longer than expected",
        "finally clicked",
        "peaceful place",
        "nature walk",
        "fresh air",
        "nature is the best reset",
        "garden",
        "travel",
        "trip",
        "journey",
        "beach",
        "mountain",
        "forest",
        "sunset",
        "scenery",
    )
    return any(phrase in text for phrase in forbidden_phrases)


def _has_weak_generic_hashtags(value):
    tags = {tag.lower() for tag in split_hashtags(value)}
    weak_tags = {
        "#instagramphoto",
        "#photopost",
        "#aestheticpost",
        "#visualstory",
    }
    return bool(tags & weak_tags)


def _sanitize_field_feedback(feedback_type, suggestion, explanation, payload, hashtag_count=POST_HASHTAGS_MAX_COUNT):
    clean_hashtag_count = _coerce_hashtag_count(hashtag_count)
    if not suggestion:
        return _safe_field_feedback_fallback(feedback_type, payload, clean_hashtag_count)
    if feedback_type == "title":
        suggestion = _limit_text(suggestion, POST_TITLE_MAX_LENGTH)
        if suggestion.lower().startswith("a better "):
            return _safe_field_feedback_fallback(feedback_type, payload, clean_hashtag_count)
    elif feedback_type == "caption":
        suggestion = _limit_text(suggestion, POST_CAPTION_MAX_LENGTH)
    elif feedback_type == "hashtags":
        suggestion = _limit_hashtags(suggestion, clean_hashtag_count)
        if len(split_hashtags(suggestion)) < clean_hashtag_count:
            fallback = _safe_field_feedback_fallback(feedback_type, payload, clean_hashtag_count)
            suggestion = _limit_hashtags(
                _hashtag_text_from_candidates(
                    split_hashtags(suggestion) + split_hashtags(fallback.suggestion),
                    clean_hashtag_count,
                ),
                clean_hashtag_count,
            )
        if _has_weak_generic_hashtags(suggestion) and _field_feedback_context_is_limited(payload):
            return _safe_field_feedback_fallback(feedback_type, payload, clean_hashtag_count)

    if _contains_forbidden_field_context(suggestion):
        return _safe_field_feedback_fallback(feedback_type, payload, clean_hashtag_count)

    explanation = _first_sentence(explanation)
    if not explanation or explanation == TEXT_BASED_FEEDBACK_MESSAGE:
        explanation = _safe_field_feedback_fallback(feedback_type, payload, clean_hashtag_count).explanation
    return FieldFeedbackResult(suggestion=suggestion, explanation=explanation)


def _coerce_hashtag_count(value):
    try:
        count = int(value)
    except (TypeError, ValueError):
        count = POST_HASHTAGS_MAX_COUNT
    return min(max(count, 1), POST_HASHTAGS_MAX_COUNT)


def _caption_language_label(language):
    language_key = (language or "English").strip().lower().replace("-", "_").replace(" ", "_")
    language_labels = {
        "english": "English",
        "en": "English",
        "traditional_chinese": "Traditional Chinese",
        "zh_hant": "Traditional Chinese",
        "zh_hant_": "Traditional Chinese",
        "auto": "Auto detect from the topic; default to English if unclear",
    }
    return language_labels.get(language_key, language or "English")


def _caption_generation_prompt(topic, platform, tone, language, hashtag_count):
    return {
        "topic": topic,
        "platform": platform,
        "tone": tone,
        "language": language,
        "hashtag_count": hashtag_count,
        "limits": {
            "caption_max_characters": POST_CAPTION_MAX_LENGTH,
            "hashtags_max_count": POST_HASHTAGS_MAX_COUNT,
        },
        "instructions": [
            "Return only valid JSON with exactly these keys: caption, hashtags.",
            "caption must be a string.",
            "hashtags must be an array of strings.",
            "Act as a social media content strategist, not an image description tool.",
            "Use the image as visual context for social media strategy, not as a simple image description task.",
            "Write a natural, human-written social media caption that feels ready to publish.",
            "The caption must include a hook, the strongest visual/content highlight, why it matters to the creator or audience, and a short interaction question or CTA.",
            "Match the selected platform and selected tone.",
            "If platform is unclear, default to Instagram style.",
            "Instagram style is natural, visual, and warm; one relevant emoji is allowed but not required.",
            "TikTok style is short, lively, and POV/meme-aware without becoming exaggerated.",
            "LinkedIn style is professional, brand-update oriented, and useful for project or product context.",
            "Facebook style is friendly and community-oriented.",
            "X/Twitter style is short, punchy, and easy to react to.",
            "Follow the selected language exactly.",
            "If language is Traditional Chinese, write every non-hashtag word in Traditional Chinese.",
            "If language is English, write every non-hashtag word in English.",
            "If language is Auto detect, choose the output language from the supplied topic and text context; if no text exists, use the selected language setting.",
            f"Keep caption under {POST_CAPTION_MAX_LENGTH} characters.",
            "Avoid generic AI phrases, abstract motivational filler, and overly formal essay wording.",
            "Do not include hashtags inside the caption.",
            "Make hashtags specific to the image, topic, creator bio, platform, and content purpose.",
            "Avoid overly generic hashtags unless directly supported.",
            "For LinkedIn, prefer professional tags such as #BrandIdentity or #ContentStrategy when relevant.",
            "For Instagram or TikTok, prefer visual and creator tags such as #CharacterDesign or #DigitalArt when relevant.",
            "Traditional Chinese output may use Chinese hashtags. English output should use English hashtags.",
            f"Return exactly {hashtag_count} hashtags.",
            f"Never return more than {hashtag_count} hashtags.",
            "Hashtags should be ready to join as space-separated text.",
        ],
        "schema": {
            "caption": "...",
            "hashtags": ["#tag1", "#tag2"],
        },
    }


def _format_ai_hashtags(value):
    if isinstance(value, list):
        tags = []
        for item in value:
            tag = _clean_ai_text(item).replace(" ", "")
            if not tag:
                continue
            if not tag.startswith("#"):
                tag = f"#{tag.lstrip('#')}"
            tags.append(tag)
        return " ".join(tags)
    return _clean_ai_text(value)


def _hashtag_text_from_candidates(candidates, hashtag_count):
    tags = []
    for item in candidates:
        tag = _clean_ai_text(item).replace(" ", "")
        if not tag:
            continue
        if not tag.startswith("#"):
            tag = f"#{tag.lstrip('#')}"
        tag_key = tag.lower()
        if tag_key in {existing.lower() for existing in tags}:
            continue
        tags.append(tag)
        if len(tags) == hashtag_count:
            break
    return " ".join(tags)


def _generate_caption_and_hashtags_fallback(topic, platform, tone, hashtag_count=POST_HASHTAGS_MAX_COUNT):
    clean_topic = (topic or "your next social media update").strip()
    clean_platform = (platform or "social").replace("_", " ").title()
    clean_tone = (tone or "professional").strip().lower()
    clean_hashtag_count = _coerce_hashtag_count(hashtag_count)

    if clean_tone == "casual":
        opener = f"Here is a quick {clean_platform} update on {clean_topic}."
    elif clean_tone == "bold":
        opener = f"{clean_topic} deserves attention, and this {clean_platform} post makes the message clear."
    else:
        opener = f"Sharing a focused {clean_platform} update about {clean_topic}."

    caption = (
        f"{opener} Highlight the main takeaway, explain why it matters to the audience, "
        "and close with a simple call to action."
    )

    hashtags = [
        f"#{word.lower()}"
        for word in clean_topic.replace("/", " ").replace("-", " ").split()
        if word.isalnum()
    ][:2]
    hashtags.extend(
        [
            f"#{clean_platform.lower().replace(' / ', '').replace(' ', '')}",
            "#contentstrategy",
            "#socialmedia",
            "#creator",
            "#community",
        ]
    )
    hashtags_text = _hashtag_text_from_candidates(hashtags, clean_hashtag_count)
    return SuggestionResult(
        caption=_limit_text(caption, POST_CAPTION_MAX_LENGTH),
        hashtags_text=_limit_hashtags(hashtags_text, clean_hashtag_count),
    )


def generate_caption_and_hashtags(
    topic,
    platform,
    tone,
    language="English",
    hashtag_count=POST_HASHTAGS_MAX_COUNT,
    image_file=None,
    creator_context=None,
):
    clean_topic = (topic or "your next social media update").strip()
    clean_platform = (platform or "social").replace("_", " ").title()
    clean_tone = (tone or "professional").strip()
    clean_hashtag_count = _coerce_hashtag_count(hashtag_count)
    clean_language = _caption_language_label(language)
    fallback = _generate_caption_and_hashtags_fallback(
        clean_topic,
        clean_platform,
        clean_tone,
        clean_hashtag_count,
    )

    prompt = _caption_generation_prompt(
        topic=clean_topic,
        platform=clean_platform,
        tone=clean_tone,
        language=clean_language,
        hashtag_count=clean_hashtag_count,
    )
    prompt["creator_context"] = creator_context or {}
    image_input = _first_supported_image_input(image_file)
    if image_input:
        prompt["image_context"] = (
            "Use the image as visual context for social media strategy, not as a simple image description task. "
            "Analyze the attached first image and combine visible image details with the topic, platform, tone, "
            "creator bio, and text context. Use only the first image. Do not mention that image analysis was used."
        )

    try:
        response_text = _gemini_generate_text(
            (
                "You are an AI social media caption assistant. "
                "Think like a social media content strategist. "
                "Create concise, platform-native captions and relevant hashtags that connect the visual, creator identity, audience, and platform. "
                "Avoid generic AI wording, formal essay style, and hashtags inside captions. "
                "If an image is attached, analyze only that first image and use it as visual context for social media strategy, not as a simple image description task. "
                "If no supported image is attached, use only the text context. "
                "Return only valid JSON in the requested schema."
            ),
            (
                "Generate a caption and hashtags from this request:\n\n"
                f"{json.dumps(prompt, default=str)}"
            ),
            temperature=0.7,
            response_json=True,
            image_input=image_input,
        )
        try:
            parsed = _parse_ai_json(response_text)
        except json.JSONDecodeError as exc:
            logger.warning("Unexpected provider exception: invalid caption response")
            raise RuntimeError(AI_TEMPORARILY_UNAVAILABLE_MESSAGE) from exc
        caption = _limit_text(_clean_ai_text(parsed.get("caption")), POST_CAPTION_MAX_LENGTH)
        hashtags_text = _limit_hashtags(_format_ai_hashtags(parsed.get("hashtags")), clean_hashtag_count)
        if not caption or len(split_hashtags(hashtags_text)) != clean_hashtag_count:
            return fallback
        return SuggestionResult(caption=caption, hashtags_text=hashtags_text)
    except (ValueError, GeminiQuotaError):
        raise
    except Exception:
        raise RuntimeError(AI_TEMPORARILY_UNAVAILABLE_MESSAGE)


def store_suggestion_history(subscription, user, topic, platform, tone, result):
    return AISuggestionHistory.objects.create(
        subscription=subscription,
        requested_by=user,
        topic=topic,
        platform=platform,
        tone=tone,
        generated_caption=result.caption,
        generated_hashtags=result.hashtags_text,
    )


def _platform_key(platform):
    return (platform or "instagram").lower().replace(" / ", " ").replace("/", " ").split()[0]


def generate_post_field_feedback_fallback(payload):
    """Return language-aware, text-only create-post feedback without a provider call."""
    return _safe_field_feedback_fallback(
        payload.get("feedback_type"),
        payload,
        _coerce_hashtag_count(payload.get("ai_hashtag_count")),
    )


def generate_post_field_feedback(payload):
    feedback_type = payload.get("feedback_type")
    current_value = (payload.get("current_value") or "").strip()
    action = "improve" if current_value else "generate"
    platform = (payload.get("platform") or "").strip() or "Instagram"
    campaign = (payload.get("campaign") or "").strip()
    content_description = (payload.get("content_description") or "").strip()
    campaign_objective = (payload.get("campaign_objective") or "").strip()
    campaign_strategy = (payload.get("campaign_strategy") or "").strip()
    previous_post_titles = _payload_list(payload.get("previous_post_titles"))
    previous_post_captions = _payload_list(payload.get("previous_post_captions"))
    previous_post_hashtags = _payload_list(payload.get("previous_post_hashtags"))
    creator_context = payload.get("creator_context") if isinstance(payload.get("creator_context"), dict) else {}
    clean_tone = payload.get("ai_tone") or "Professional"
    clean_language = payload.get("ai_language_display") or payload.get("ai_language") or "English"
    clean_language_key = payload.get("ai_language") or ""
    clean_language_mode = "auto_match_input" if _is_auto_language(_field_feedback_language_key(payload)) else "force_selected_language"
    clean_hashtag_count = _coerce_hashtag_count(payload.get("ai_hashtag_count"))
    platform_key = _platform_key(platform)
    image_input = _first_supported_image_input(payload.get("image_file"))
    is_video_post = _clean_ai_text(payload.get("post_type")).lower() == "video"
    video_file = payload.get("video_file") if is_video_post else None
    video_file_detected = bool(video_file)
    video_input = None
    video_fallback_reason = ""
    if is_video_post:
        video_input, video_fallback_reason = _load_video_for_ai(
            video_file,
            duration_seconds=payload.get("video_duration_seconds"),
        )
        if not video_input:
            _video_ai_diagnostic(
                video_file_detected=video_file_detected,
                video_input_used=False,
                api_call_success=False,
                fallback_reason=video_fallback_reason,
            )
    diagnostic_context = {
        "image_bytes_present": bool(image_input),
        "video_file_detected": video_file_detected,
        "video_input_used": bool(video_input),
        "language": clean_language,
        "tone": clean_tone,
        "hashtag_count": clean_hashtag_count,
    }
    api_key = str(getattr(settings, "GEMINI_API_KEY", "") or "").strip()
    if not getattr(settings, "GEMINI_ENABLED", True):
        message = "Google Gemini is disabled by GEMINI_ENABLED."
        _gemini_pipeline_diagnostics(**diagnostic_context, exception_text=message)
        raise ValueError(message)
    if not api_key:
        message = "GEMINI_API_KEY is missing from the runtime environment."
        _gemini_pipeline_diagnostics(**diagnostic_context, exception_text=message)
        raise ValueError(message)

    field_rules = {
        "title": [
            "Generate one stronger social media title that sounds like a social post title, campaign idea, or content theme.",
            f"Keep the title at {POST_TITLE_MAX_LENGTH} characters or fewer.",
            "Target 2-6 words for most platforms.",
            "A good title creates curiosity, is easy to scan, fits the platform, avoids generic wording, and does not simply describe the image.",
            "Infer a supported content purpose from the image and text, such as brand identity, new mascot, character reveal, campaign teaser, creator update, or project launch.",
            "Avoid overly plain titles such as My New Avatar, New Image, or New Post.",
            "Traditional Chinese example styles: Creana 小狐狸正式登場; 品牌新角色亮相; 藍色小狐狸帶來全新創作能量.",
            "English example styles: Meet Creana's New Mascot; A Fresh New Face for Creana; Blue Fox Energy Arrives.",
            "Use plain, natural phrasing. Reusable style examples include One Pose, One Spell; Small Moment, Strong Energy; Behind the Shot; Wait for the Pose; A Little Bit of Magic.",
            "Do not copy example titles unless they match the user's explicit context.",
            "Use the existing title, caption, hashtags, content description, post type, selected platform, creator context, campaign, campaign objective, campaign strategy, and previous post context when provided.",
            "Avoid awkward AI-style phrasing such as Unlock Your Next-Level Journey or Discover the Magic of Every Moment.",
            "Avoid generic phrases such as new post, check this out, amazing content, must see, unlock, elevate, discover the magic, or journey into.",
            "Avoid outputs like A Better [Original Title].",
            "Do not write title-case slogans unless that feels natural for the selected platform.",
            "Do not only repeat or lightly reword the current title.",
        ],
        "caption": [
            "Generate a natural social media caption, not an essay.",
            f"Keep the caption at {POST_CAPTION_MAX_LENGTH} characters or fewer.",
            "Use short sentence structures and conversational pacing.",
            "Do not merely describe what is in the image.",
            "A good caption includes a hook, a visual/content highlight, why it matters to the brand, creator, or audience, and a simple interaction question or CTA.",
            "A good caption feels like a real social post, matches the topic, feels human, avoids fake details, and encourages interaction.",
            "Prefer captions that help comments, saves, or shares depending on platform.",
            "Use short, natural lines for Instagram and TikTok.",
            "For Instagram, write naturally with visual energy and optionally one relevant emoji.",
            "For TikTok, keep it short, lively, and POV/meme-aware without overdoing it.",
            "For LinkedIn, use a professional brand update or project/product introduction tone.",
            "For Facebook, make it warm and community-oriented.",
            "For X/Twitter, make it short and punchy.",
            "If platform is unclear, default to Instagram style.",
            "When context is limited, use simple engagement prompts rather than invented stories.",
            "Keep it human-written, specific to the user's text, and easy to read aloud.",
            "Avoid formal transitions such as furthermore, additionally, in today's fast-paced world, or this serves as a reminder.",
            "Avoid repetitive emotional adjectives and over-polished motivational language.",
            "If a supported image is attached, use visible image details as grounded context for this field.",
            "If no supported image is attached, do not refer to uploaded images or media.",
            "Do not invent scenery, location, activity, objects, mood, or aesthetics that are not in the supplied text or attached image.",
            "Do not say today, this took longer than expected, finally clicked, peaceful place, nature walk, or similar invented context unless the user supplied that context.",
            "If the user input is short or vague, create a safer caption based on the visible text context only.",
            "Do not include hashtags in the caption.",
        ],
        "hashtags": [
            f"Generate exactly {clean_hashtag_count} concise, relevant hashtags separated by spaces.",
            "Base hashtags on explicit user context: title, caption, article text, content description, post type, platform, creator context, campaign, existing hashtags, and the first image when attached.",
            "Do not invent scenery, locations, activities, objects, moods, aesthetics, industries, or communities not present in the supplied context.",
            "A good hashtag set improves discoverability by using explicit topic, subject, community, post type, campaign, and platform signals.",
            "Prefer specific subject, community, post-type, and platform keywords that are directly supported by the supplied context.",
            "If context is limited, use safer broad-but-relevant hashtags tied to the post type or platform instead of guessing specific details.",
            "Avoid generic tags like #InstagramPhoto, #PhotoPost, #AestheticPost, #VisualStory, #product, #socialmedia, #content, #marketing, or #business unless directly relevant.",
            "If platform is LinkedIn, hashtags should be more professional, such as #BrandIdentity, #ContentStrategy, #ProductUpdate, or #CreativeStrategy when relevant.",
            "If platform is Instagram or TikTok, hashtags can lean visual and creative, such as #CharacterDesign, #DigitalArt, #MascotDesign, or #CreatorBranding when relevant.",
            "Traditional Chinese output may use Chinese hashtags. English output should use English hashtags.",
            "Do not invent scenery or location hashtags.",
            "Keep the existing hashtag quality: specific, discoverable, and platform-appropriate.",
        ],
    }
    platform_rules = {
        "instagram": "Visual, aesthetic, community-friendly, and comment-oriented. Use discoverable hashtags grounded in explicit context.",
        "tiktok": "Hook-based, casual, curiosity-driven, and short. Use trend/discovery-friendly hashtags without inventing details.",
        "reddit": "Specific, conversational, and community-aware. Avoid salesy tone, excessive emojis, and hashtag-heavy outputs.",
        "facebook": "Warm and community-oriented. Slightly longer captions are acceptable when the context supports them.",
        "linkedin": "Professional, clear, brand-update oriented, and useful for product, project, creator, or campaign positioning.",
        "x": "Short, punchy, and concise. Use minimal hashtags.",
        "twitter": "Short, punchy, and concise. Use minimal hashtags.",
        "youtube": "Searchable title and description style. Use topic-focused hashtags.",
    }
    writing_examples = {
        "better_titles": [
            "One Pose, One Spell",
            "Small Moment, Strong Energy",
            "Behind the Shot",
            "Wait for the Pose",
            "A Little Bit of Magic",
        ],
        "avoid_titles": [
            "A Better Magic Pose",
            "Unlock Your Next-Level Journey",
            "Discover the Magic of Every Moment",
            "Elevate Your Day With Endless Inspiration",
        ],
        "caption_shape": [
            "Start with a grounded hook from the supplied context.",
            "Add one short line that clarifies the topic or value.",
            "End with a simple question, save prompt, or share prompt.",
        ],
        "instruction": "Examples show style only. Do not copy them unless they match the user's explicit context.",
    }

    prompt = {
        "feedback_type": feedback_type,
        "requested_field_type": feedback_type,
        "mode": action,
        "post_type": payload.get("post_type", ""),
        "platform": platform,
        "platform_style": platform_rules.get(platform_key, platform_rules["instagram"]),
        "ai_tone": clean_tone,
        "ai_language": clean_language,
        "ai_language_key": clean_language_key,
        "clean_language_mode": clean_language_mode,
        "ai_hashtag_count": clean_hashtag_count,
        "writing_examples": writing_examples,
        "campaign": campaign or None,
        "campaign_objective": campaign_objective or None,
        "campaign_strategy": campaign_strategy or None,
        "previous_post_titles": previous_post_titles,
        "previous_post_captions": previous_post_captions,
        "previous_post_hashtags": previous_post_hashtags,
        "creator_context": creator_context,
        "content_description": content_description or None,
        "has_supported_image": bool(image_input),
        "has_supported_video": bool(video_input),
        "video_analysis_basis": (
            "Gemini is receiving the bounded uploaded video and may use its frames and temporal sequence."
            if video_input
            else "Video frames were not read. Use only title, caption, hashtags, thumbnail/image context, and media metadata."
            if is_video_post
            else None
        ),
        "video_fallback_reason": video_fallback_reason or None,
        "title": payload.get("title", ""),
        "caption": payload.get("caption", ""),
        "article_caption": payload.get("article_caption", ""),
        "hashtags": payload.get("hashtags", ""),
        "current_value": current_value,
        "article_text": payload.get("article_text", ""),
        "output_schema": {
            "title": {"text": "...", "feedback": "..."},
            "caption": {"text": "...", "feedback": "..."},
            "hashtags": {"text": "...", "feedback": "..."},
            "compatible_alternative": {
                "title": {"suggestion": "...", "reason": "..."},
                "caption": {"suggestion": "...", "reason": "..."},
                "hashtags": {"suggestion": "#tag1 #tag2", "reason": "..."},
            },
        },
        "instructions": {
            "general_rules": [
                "You are an AI social media content strategist, not a simple image description assistant.",
                "When has_supported_video is true, analyze the attached video itself and ground the requested title, caption, or hashtags in visible video content and sequence.",
                "When video_analysis_basis says video frames were not read, never imply that you watched, viewed, or analyzed the video.",
                "When a supported image is attached, analyze only the first attached image and combine visible details with the text context, creator bio, platform, language preference, and hashtag count.",
                "Use the image as visual context for social media strategy, not as a simple image description task.",
                "Do not produce content that only says what appears in the image.",
                "Infer a supported social content angle when appropriate: brand identity, mascot reveal, character reveal, campaign teaser, creator update, product/project introduction, or audience engagement moment.",
                "When no supported image is attached, this feature is text-based content assistance only.",
                "If no supported image is attached, do not claim to analyse, inspect, see, view, or understand uploaded images or media.",
                "When content_description is provided, treat it as a visual/context clue.",
                "Use previous_post_titles, previous_post_captions, and previous_post_hashtags only as pattern/context clues; do not copy them.",
                "Creator bio is the primary source of creator identity.",
                "Use creator bio to determine the creator's niche, audience, expertise, and content style.",
                "Maintain that creator identity consistently across title, caption, and hashtag suggestions unless the current post clearly belongs to another niche.",
                "Use recent post titles and recent hashtags only to refine the creator niche, not replace the identity implied by the bio.",
                "Use creator_context only to infer the creator's niche, tone, audience, expertise, content style, and recurring subject matter when helpful.",
                "If creator_context suggests a niche such as card tricks or close-up magic, lean titles, captions, and hashtags toward that niche, challenges, and audience interaction when relevant to the post.",
                "Do not reveal or quote private profile details, profile links, username, display name, or bio text in the generated post.",
                "Do not copy the creator bio directly.",
                "Do not invent identity claims beyond the supplied profile text.",
                "If creator_context is empty or unclear, ignore it.",
                "Generate or improve title, caption, and hashtags from the selected platform, post type, content description, existing title, caption, hashtags, article text, creator context, campaign, campaign objective, campaign strategy, and previous post context when provided.",
                "Never invent locations, travel experiences, achievements, events, timelines, emotions, scenery, objects, or activities unless explicitly provided.",
                "Do not invent scenery, location, activity, objects, mood, or aesthetics that are not included in the user text, attached image, content_description, title, caption, hashtags, creator_context, campaign, campaign objective, campaign strategy, previous post context, or post type.",
                "Do not say today, this took longer than expected, finally clicked, peaceful place, nature walk, or similar invented context unless the user supplied that context.",
                "If the user input is short or vague, create safer content based on the visible text context only.",
                "When context is limited, use simple engagement prompts rather than invented stories.",
                "Make the writing feel human, platform-native, and ready to post.",
                "Follow the selected AI tone.",
                "The output language must be exactly the selected AI language.",
                "The requested field text and feedback explanation must both use the selected AI language.",
                "Selected AI language is the highest-priority language rule.",
                "Do not infer output language from the user's input text.",
                "Do not follow the input language unless ai_language is set to Auto.",
                "Only use the user's input as meaning and context, not as the output language.",
                "If selected language is English, translate or rewrite non-English user input into natural English output.",
                "If selected language is Traditional Chinese, translate or rewrite English user input into natural Traditional Chinese output.",
                "If selected language is Auto, match the user's title, caption, hashtags, or article text. If there is no user text, follow the selected language setting.",
                "Do not mix English and Traditional Chinese except hashtags, brand names, usernames, or platform names.",
                "Feedback text should also use the selected AI language.",
                f"For hashtags, return exactly {clean_hashtag_count} hashtags.",
                f"Never return more than {clean_hashtag_count} hashtags.",
                f"Never return fewer than {clean_hashtag_count} hashtags.",
                "Do not repeat every hashtag inside the feedback explanation.",
                "Feedback explanation must explain the strategic benefit, such as engagement, clarity, brand identity, audience interaction, or discoverability.",
                "If Traditional Chinese is selected, translate those concepts naturally: engagement -> 互動率 or 互動表現, clarity -> 清楚度, brand identity -> 品牌識別, audience interaction -> 受眾互動, discoverability -> 曝光與搜尋性.",
                "If Traditional Chinese is selected, do not use English analysis terms like engagement, clarity, brand identity, audience interaction, or discoverability unless they are platform names or proper nouns.",
                "Avoid AI-generated prose patterns: big abstract claims, excessive adjectives, polished essay rhythm, and generic inspirational wording.",
                "Prefer concise, concrete language over dramatic or ornamental phrasing.",
                "If campaign is null or empty, ignore campaign entirely.",
                "Return only valid JSON with keys title, caption, and hashtags.",
                "Each of title, caption, and hashtags must be an object with text and feedback keys. You may use suggestion/reason only if you cannot use text/feedback.",
                "The preferred schema must stay exactly: title, caption, hashtags; each containing text and feedback.",
                "The requested field's text must contain only the actual ready-to-paste content.",
                "Do not include markdown formatting, bullets, labels, or code fences.",
                "If mode is generate, create a new field suggestion from the text context.",
                "If mode is improve, make the existing field clearly better instead of appending one word or repeating the same phrase.",
                "Feedback must be one short, product-like sentence that explains why the suggestion improves engagement, clarity, brand identity, audience interaction, or discoverability.",
                "Do not only say that suggestions are based on text input and selected platform.",
                "Good English feedback examples: This title strengthens brand identity while staying easy to scan. This caption gives the audience a clearer reason to respond. These hashtags improve discoverability without drifting away from the visual concept.",
                "Good Traditional Chinese feedback examples: 這個標題能強化品牌識別，也更容易一眼理解。這段文案讓受眾更清楚知道為什麼要互動。這組 hashtag 更聚焦主題，有助於提升曝光與搜尋性。",
                f"Use this idea only as supporting context when useful, not as the whole explanation: {TEXT_BASED_FEEDBACK_MESSAGE}",
            ],
            "field_rules": field_rules.get(feedback_type, []),
        },
    }

    def invoke_feedback_model(language_retry=False):
        nonlocal video_fallback_reason
        retry_text = ""
        if language_retry:
            retry_text = (
                "\n\nLanguage correction: The previous response used the wrong language. "
                f"Rewrite the requested field text and feedback explanation in {clean_language}. "
                "Do not copy the input language unless selected language is Auto. "
                "Keep hashtags readable and platform-native."
            )
        system_prompt = (
                "You are an AI social media content strategist for a SaaS social media manager. "
                "Provide platform-aware help for titles, captions, and hashtags. "
                "Do not behave like a simple image description tool. "
                "Always write the requested field and feedback in the selected AI language. "
                "Do not copy the input language unless selected language is Auto. "
                "If an image is attached, analyze only the first image and use it as visual context for social media strategy, not as a simple image description task. "
                "Connect the image to creator identity, brand identity, audience interaction, discoverability, and platform-native posting goals when supported. "
                "If no supported image is attached, do not refer to uploaded images or media. "
                "Prioritize explicit user context, especially content_description when supplied, then platform style, then engagement optimisation. "
                "Do not add unsupported visual details, settings, objects, activities, moods, events, timelines, achievements, or aesthetics. "
                "When context is sparse, stay simple and useful instead of inventing a story. "
                "If an attached video is provided, analyze its actual visual and temporal content. "
                "If no video input is attached, never claim to have watched the video. "
                "Return only valid JSON in the requested schema."
            )
        user_prompt = (
                "Generate structured JSON for this social post feedback request. "
                "Do not mention image analysis in the answer.\n\n"
                f"{json.dumps(prompt, default=str)}"
                f"{retry_text}"
        )
        if video_input:
            try:
                return _gemini_generate_from_video(
                    system_prompt,
                    user_prompt,
                    video_input,
                    temperature=0.7,
                    response_json=True,
                    diagnostic_context=diagnostic_context,
                )
            except GeminiQuotaError:
                raise
            except Exception as exc:
                video_fallback_reason = f"video_api_{exc.__class__.__name__.lower()}"
                _video_ai_diagnostic(
                    video_file_detected=True,
                    video_input_used=False,
                    api_call_success=False,
                    fallback_reason=video_fallback_reason,
                )
                fallback_prompt = {
                    **prompt,
                    "has_supported_video": False,
                    "video_analysis_basis": "Video frames were not read. Use only title, caption, hashtags, thumbnail/image context, and media metadata.",
                    "video_fallback_reason": video_fallback_reason,
                }
                user_prompt = (
                    "Generate structured JSON for this social post feedback request. "
                    "Do not mention image analysis in the answer.\n\n"
                    f"{json.dumps(fallback_prompt, default=str)}"
                    f"{retry_text}"
                )
        response_text = _gemini_generate_text(
            system_prompt,
            user_prompt,
            temperature=0.7,
            response_json=True,
            image_input=image_input,
            diagnostic_context=diagnostic_context,
        )
        if is_video_post:
            _video_ai_diagnostic(
                video_file_detected=video_file_detected,
                video_input_used=False,
                api_call_success=True,
                fallback_reason=video_fallback_reason,
            )
        return response_text

    try:
        response_text = invoke_feedback_model()
    except GeminiQuotaError:
        raise
    except Exception as exc:
        _gemini_pipeline_diagnostics(
            **diagnostic_context,
            exception_text=str(exc),
        )
        logger.warning("Gemini request failed: %s", exc.__class__.__name__)
        raise RuntimeError(AI_TEMPORARILY_UNAVAILABLE_MESSAGE) from exc

    try:
        parsed = _parse_ai_json(response_text)
    except json.JSONDecodeError as exc:
        _gemini_pipeline_diagnostics(
            **diagnostic_context,
            exception_text=str(exc),
        )
        logger.warning("Unexpected provider exception: invalid feedback response")
        raise RuntimeError(AI_TEMPORARILY_UNAVAILABLE_MESSAGE) from exc
    field_data = parsed.get(feedback_type) or {}
    suggestion = _field_payload_value(field_data, "text", "suggestion") or _clean_ai_text(parsed.get("suggestion"))
    explanation = _first_sentence(_field_payload_value(field_data, "feedback", "reason", "explanation") or parsed.get("explanation"))
    if _field_feedback_language_mismatch(
        feedback_type,
        suggestion,
        explanation,
        clean_language,
        _field_feedback_language_key(payload),
    ):
        try:
            response_text = invoke_feedback_model(language_retry=True)
            parsed = _parse_ai_json(response_text)
            field_data = parsed.get(feedback_type) or {}
            suggestion = _field_payload_value(field_data, "text", "suggestion") or _clean_ai_text(parsed.get("suggestion"))
            explanation = _first_sentence(_field_payload_value(field_data, "feedback", "reason", "explanation") or parsed.get("explanation"))
        except Exception as exc:
            logger.warning("Gemini request failed during language retry: %s", exc.__class__.__name__)
    result = _sanitize_field_feedback(feedback_type, suggestion, explanation, payload, clean_hashtag_count)
    if _field_feedback_language_mismatch(
        feedback_type,
        result.suggestion,
        result.explanation,
        clean_language,
        _field_feedback_language_key(payload),
    ):
        result = _safe_field_feedback_fallback(feedback_type, payload, clean_hashtag_count)

    return FieldFeedbackResult(
        suggestion=result.suggestion,
        explanation=result.explanation,
        used_video_input=bool(video_input) and not bool(video_fallback_reason),
        fallback_reason=video_fallback_reason,
    )
