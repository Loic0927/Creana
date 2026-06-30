from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone

from ..models import PostComment, PostEngagement, PostView


def get_recent_analytics_date_range(days=7):
    today = timezone.localdate()
    start_date = today - timedelta(days=days - 1)
    return start_date, today


def get_dashboard_summary(subscription, start_date=None, end_date=None):
    start_date, end_date = _normalize_date_range(start_date, end_date)
    posts = subscription.posts.all()
    date_filter = {"created_at__date__gte": start_date, "created_at__date__lte": end_date}
    views = PostView.objects.filter(
        post__subscription=subscription,
        viewed_at__date__gte=start_date,
        viewed_at__date__lte=end_date,
    ).count()
    likes = PostEngagement.objects.filter(
        post__subscription=subscription,
        kind=PostEngagement.Kind.LIKE,
        **date_filter,
    ).count()
    comments = PostComment.objects.filter(
        post__subscription=subscription,
        **date_filter,
    ).count()
    shares = PostEngagement.objects.filter(
        post__subscription=subscription,
        kind=PostEngagement.Kind.SHARE,
        **date_filter,
    ).count()
    total_interactions = likes + comments + shares
    engagement_rate = min(round((total_interactions / views) * 100, 2), 100) if views else 0
    return {
        "campaign_count": subscription.campaigns.count(),
        "post_count": posts.count(),
        "scheduled_count": posts.filter(status="scheduled").count(),
        "views": views,
        "impressions": views,
        "likes": likes,
        "comments": comments,
        "shares": shares,
        "engagement_rate": engagement_rate,
    }


def get_recent_post_metrics(subscription, limit=5, start_date=None, end_date=None):
    start_date, end_date = _normalize_date_range(start_date, end_date)
    recent_posts = (
        subscription.posts.select_related("campaign")
        .prefetch_related("campaign_groups")
        .annotate(
            real_likes=Count(
                "engagements",
                filter=Q(
                    engagements__kind=PostEngagement.Kind.LIKE,
                    engagements__created_at__date__gte=start_date,
                    engagements__created_at__date__lte=end_date,
                ),
                distinct=True,
            ),
            real_shares=Count(
                "engagements",
                filter=Q(
                    engagements__kind=PostEngagement.Kind.SHARE,
                    engagements__created_at__date__gte=start_date,
                    engagements__created_at__date__lte=end_date,
                ),
                distinct=True,
            ),
            real_comments=Count(
                "comments",
                filter=Q(
                    comments__created_at__date__gte=start_date,
                    comments__created_at__date__lte=end_date,
                ),
                distinct=True,
            ),
            real_views=Count(
                "views",
                filter=Q(
                    views__viewed_at__date__gte=start_date,
                    views__viewed_at__date__lte=end_date,
                ),
                distinct=True,
            ),
        )[:limit]
    )
    rows = []
    for post in recent_posts:
        views = post.real_views or 0
        likes = post.real_likes or 0
        comments = post.real_comments or 0
        shares = post.real_shares or 0
        rows.append(
            {
                "post": post,
                "views": views,
                "impressions": views,
                "likes": likes,
                "comments": comments,
                "shares": shares,
                "has_engagement": any((views, likes, comments, shares)),
            }
        )
    return rows


def _normalize_date_range(start_date=None, end_date=None):
    if start_date and end_date:
        return start_date, end_date
    return get_recent_analytics_date_range()
