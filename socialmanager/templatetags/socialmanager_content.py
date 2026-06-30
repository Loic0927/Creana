import time

from django import template
from django.templatetags.static import static

from socialmanager.utils.html_sanitizer import render_safe_article_html


register = template.Library()


def _log_posts_template_timing(stage, elapsed_seconds, **extra):
    return


@register.filter
def safe_article_html(value):
    return render_safe_article_html(value)


@register.filter
def avatar_url(user):
    started_at = time.perf_counter()
    result = static("socialmanager/images/default_avatar.webp")
    source = "default"
    try:
        if not user or not getattr(user, "is_authenticated", False):
            return result

        profile = getattr(user, "profile", None)
        avatar_thumbnail_url = getattr(profile, "avatar_thumbnail_url", "")
        if avatar_thumbnail_url:
            result = avatar_thumbnail_url
            source = "profile"
            return avatar_thumbnail_url
    except Exception:
        pass
    finally:
        _log_posts_template_timing(
            "avatar_url_filter",
            time.perf_counter() - started_at,
            user_id=getattr(user, "pk", None),
            source=source,
            has_value=1 if result else 0,
        )

    return result
