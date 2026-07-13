import json
import logging

from django.conf import settings
from pywebpush import WebPushException, webpush

from socialmanager.models import PushSubscription, UserSettings

logger = logging.getLogger(__name__)


def send_push_notification(user, title, body, url=None, notification_type=None):
    """Send one payload to all active browsers owned by a user."""
    user_settings, _ = UserSettings.objects.get_or_create(user=user)
    if not user_settings.allows_push_kind(notification_type):
        return 0
    if not settings.WEB_PUSH_VAPID_PRIVATE_KEY or not settings.WEB_PUSH_VAPID_PUBLIC_KEY:
        logger.info("Web push skipped because VAPID keys are not configured")
        return 0

    payload = json.dumps({
        "title": str(title),
        "body": str(body),
        "url": url or "/",
        "icon": "/static/socialmanager/images/icon.png",
        "badge": "/static/socialmanager/images/icon.png",
    })
    sent = 0
    subscriptions = PushSubscription.objects.filter(user=user, is_active=True)
    for subscription in subscriptions:
        try:
            webpush(
                subscription_info={
                    "endpoint": subscription.endpoint,
                    "keys": {"p256dh": subscription.p256dh_key, "auth": subscription.auth_key},
                },
                data=payload,
                vapid_private_key=settings.WEB_PUSH_VAPID_PRIVATE_KEY,
                vapid_claims={"sub": settings.WEB_PUSH_VAPID_EMAIL},
            )
            sent += 1
        except WebPushException as exc:
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            if status_code in {404, 410}:
                PushSubscription.objects.filter(pk=subscription.pk).update(is_active=False)
            logger.warning("Web push failed subscription_id=%s status=%s", subscription.pk, status_code)
        except Exception as exc:
            logger.warning("Web push failed subscription_id=%s error=%s", subscription.pk, exc.__class__.__name__)
    return sent


def send_notification_push(notification):
    actor = notification.actor.get_full_name() or notification.actor.username or "Someone"
    kind_copy = {
        "like": ("New like", f"{actor} liked your post."),
        "comment": ("New comment", f"{actor} commented on your post."),
        "comment_reply": ("New reply", f"{actor} replied to your comment."),
        "share": ("New share", f"{actor} shared your post."),
        "follow": ("New follower", f"{actor} followed you."),
        "comment_like": ("New comment like", f"{actor} liked your comment."),
    }
    title, body = kind_copy.get(notification.kind, ("New notification", f"You have an update from {actor}."))
    if notification.post_id:
        url = f"/posts/{notification.post_id}/"
    elif notification.kind == "follow":
        url = f"/profiles/{notification.actor_id}/"
    else:
        url = "/notifications/"
    return send_push_notification(notification.recipient, title, body, url, notification.kind)
