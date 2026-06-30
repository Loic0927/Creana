import re
from html import unescape
from urllib.parse import urljoin

from django.conf import settings
from django.templatetags.static import static
from django.urls import reverse
from django.utils.html import strip_tags
from django.utils.text import Truncator


DEFAULT_SEO_DESCRIPTION = (
    "Creana helps social media creators plan content, schedule posts, and improve "
    "performance with AI-powered insights and analytics."
)


def get_site_url(request=None):
    configured_url = settings.SITE_URL.strip().rstrip("/")
    if configured_url:
        return configured_url
    if not settings.DEBUG:
        return "https://creana.app"
    if request is not None:
        return request.build_absolute_uri("/").rstrip("/")
    return "https://creana.app"


def absolute_site_url(path, request=None):
    site_root = f"{get_site_url(request)}/"
    return urljoin(site_root, str(path or "").lstrip("/"))


def clean_meta_description(*values, max_length=160, fallback=DEFAULT_SEO_DESCRIPTION):
    value = next((item for item in values if item and str(item).strip()), fallback)
    value = unescape(strip_tags(str(value)))
    value = re.sub(r"\s+", " ", value).strip()
    return Truncator(value).chars(max_length)


def fallback_open_graph_image_url(request=None):
    return absolute_site_url(static("socialmanager/images/icon.webp"), request)


def public_post_open_graph_image_url(post, request=None):
    fallback = fallback_open_graph_image_url(request)
    if (
        post.status != post.Status.PUBLISHED
        or post.visibility != post.Visibility.PUBLIC
        or (settings.USE_GCS and settings.GS_QUERYSTRING_AUTH)
    ):
        return fallback

    if post.content_format == post.Format.VIDEO:
        media_url = post.video_thumbnail_url
    else:
        media_url = post.primary_image_url
    return absolute_site_url(media_url, request) if media_url else fallback


def public_post_metadata(post, request=None):
    canonical_path = reverse(
        "socialmanager:post_detail",
        kwargs={"pk": post.pk, "slug": post.slug},
    )
    return {
        "seo_title": f"{post.title} | Creana",
        "seo_description": clean_meta_description(
            post.article_caption,
            post.caption,
            f"View {post.title} on Creana.",
        ),
        "seo_robots": "index, follow",
        "seo_type": "article" if post.content_format == post.Format.ARTICLE else "website",
        "seo_url": absolute_site_url(canonical_path, request),
        "canonical_url": absolute_site_url(canonical_path, request),
        "open_graph_image_url": public_post_open_graph_image_url(post, request),
    }
