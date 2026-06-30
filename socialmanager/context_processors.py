from .models import Notification, UserSettings
from .seo import DEFAULT_SEO_DESCRIPTION, absolute_site_url, fallback_open_graph_image_url
from .subscriptions import user_has_active_subscription


def notification_counts(request):
    if not request.user.is_authenticated:
        return {"unread_notification_count": 0}

    return {
        "unread_notification_count": Notification.objects.filter(
            recipient=request.user,
            is_read=False,
        ).count()
    }


def user_settings(request):
    if not request.user.is_authenticated:
        return {
            "current_user_settings": None,
        }

    settings_obj, _ = UserSettings.objects.get_or_create(user=request.user)
    return {
        "current_user_settings": settings_obj,
    }


def ai_membership(request):
    return {
        "current_user_is_ai_member": user_has_active_subscription(request.user),
    }


def site_metadata(request):
    is_landing_page = request.resolver_match and request.resolver_match.view_name == "socialmanager:landing"
    page_url = absolute_site_url(request.path, request)
    return {
        "site_url": absolute_site_url("", request).rstrip("/"),
        "seo_title": "Creana | AI Social Media Content Management",
        "seo_description": DEFAULT_SEO_DESCRIPTION,
        "seo_robots": "index, follow" if is_landing_page else "noindex, nofollow",
        "seo_type": "website",
        "seo_url": page_url,
        "canonical_url": page_url if is_landing_page else "",
        "open_graph_image_url": fallback_open_graph_image_url(request),
    }
