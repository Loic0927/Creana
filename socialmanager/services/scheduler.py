from dataclasses import dataclass

from django.utils import timezone

from socialmanager.models import SocialMediaPost


@dataclass
class ScheduleValidationResult:
    is_valid: bool
    message: str


def validate_schedule(post):
    if not post.scheduled_for:
        return ScheduleValidationResult(False, "Choose a scheduled date and time before scheduling this post.")
    if post.scheduled_for <= timezone.now():
        return ScheduleValidationResult(False, "Scheduled time must be in the future.")
    return ScheduleValidationResult(True, "Post is ready to be scheduled.")


def upcoming_posts(subscription):
    return subscription.posts.filter(status="scheduled", scheduled_for__gte=timezone.now()).order_by("scheduled_for")


def publish_due_scheduled_posts(now=None):
    now = now or timezone.now()
    due_posts = SocialMediaPost.objects.filter(
        status=SocialMediaPost.Status.SCHEDULED,
        scheduled_for__lte=now,
    )
    from socialmanager.services.web_push import send_push_notification

    published = 0
    for post in due_posts.select_related("author"):
        try:
            post.status = SocialMediaPost.Status.PUBLISHED
            post.published_at = now
            post.save(update_fields=["status", "published_at", "updated_at"])
            send_push_notification(
                post.author,
                "Scheduled post published",
                f'“{post.title}” is now published.',
                f"/posts/{post.pk}/",
                "scheduled_post_published",
            )
            published += 1
        except Exception:
            send_push_notification(
                post.author,
                "Scheduled post failed",
                f'“{post.title}” could not be published.',
                f"/posts/{post.pk}/",
                "scheduled_post_failed",
            )
    return published


def build_dispatch_payload(post):
    return {
        "post_id": post.pk,
        "platform": post.platform,
        "caption": post.caption,
        "hashtags": post.hashtags,
        "scheduled_for": post.scheduled_for.isoformat() if post.scheduled_for else None,
        "image_url": post.image.url if post.image else None,
        "video_url": post.video_url,
    }
