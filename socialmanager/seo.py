import json
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


def json_ld_dumps(value):
    """Serialize JSON-LD safely for an HTML script element."""
    return (
        json.dumps(value, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


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
    canonical_url = absolute_site_url(canonical_path, request)
    description = clean_meta_description(
        post.article_caption,
        post.caption,
        f"View {post.title} on Creana.",
    )
    image_url = public_post_open_graph_image_url(post, request)
    site_url = absolute_site_url("", request).rstrip("/")
    author_name = post.author.get_username()
    author_url = absolute_site_url(
        reverse("socialmanager:public_profile_username", kwargs={"username": author_name}),
        request,
    )
    date_published = post.published_at or post.created_at
    structured_data = {
        "@context": "https://schema.org",
        "@type": "BlogPosting",
        "@id": f"{canonical_url}#blogposting",
        "headline": post.title,
        "description": description,
        "author": {
            "@type": "Person",
            "name": author_name,
            "url": author_url,
        },
        "image": image_url,
        "datePublished": date_published.isoformat(),
        "dateModified": post.updated_at.isoformat(),
        "publisher": {
            "@type": "Organization",
            "@id": f"{site_url}/#organization",
            "name": "Creana",
            "url": f"{site_url}/",
            "logo": {
                "@type": "ImageObject",
                "url": fallback_open_graph_image_url(request),
            },
        },
        "mainEntityOfPage": {
            "@type": "WebPage",
            "@id": canonical_url,
        },
    }
    return {
        "seo_title": f"{post.title} | Creana",
        "seo_description": description,
        "seo_robots": "index, follow",
        "seo_type": "article" if post.content_format == post.Format.ARTICLE else "website",
        "seo_url": canonical_url,
        "canonical_url": canonical_url,
        "open_graph_image_url": image_url,
        "structured_data": json_ld_dumps(structured_data),
    }
