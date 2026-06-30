from django.contrib import admin

from .models import (
    AISuggestionHistory,
    Announcement,
    HiddenUser,
    Notification,
    PostComment,
    PostEngagement,
    PostImage,
    PostMetric,
    PostView,
    SaaSSubscription,
    SocialMediaCampaign,
    SocialMediaPost,
    SubscriptionMembership,
    UserFollow,
    UserProfile,
    UserSettings,
)


@admin.register(Announcement)
class AnnouncementAdmin(admin.ModelAdmin):
    list_display = ("title", "author", "is_active", "created_at", "updated_at")
    list_filter = ("is_active", "created_at")
    search_fields = ("title", "content")


@admin.register(SaaSSubscription)
class SaaSSubscriptionAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "plan", "is_archived", "created_at")
    list_filter = ("plan", "is_archived")
    search_fields = ("name", "owner__username", "owner__email")

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser or request.user.has_perm("socialmanager.delete_saassubscription")


@admin.register(SubscriptionMembership)
class SubscriptionMembershipAdmin(admin.ModelAdmin):
    list_display = ("subscription", "user", "role", "is_active_member", "stripe_subscription_id", "joined_at")
    list_editable = ("is_active_member",)
    list_filter = ("role", "is_active_member")
    search_fields = (
        "subscription__name",
        "user__username",
        "user__email",
        "stripe_customer_id",
        "stripe_subscription_id",
        "stripe_checkout_session_id",
    )


@admin.register(UserFollow)
class UserFollowAdmin(admin.ModelAdmin):
    list_display = ("follower", "following", "created_at")
    search_fields = ("follower__username", "follower__email", "following__username", "following__email")


@admin.register(HiddenUser)
class HiddenUserAdmin(admin.ModelAdmin):
    list_display = ("owner", "hidden_user", "created_at")
    search_fields = ("owner__username", "hidden_user__username")


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "links_public", "updated_at")
    list_filter = ("links_public",)
    search_fields = ("user__username", "user__email", "bio", "links")


@admin.register(UserSettings)
class UserSettingsAdmin(admin.ModelAdmin):
    list_display = ("user", "language", "ai_tone", "ai_language", "ai_hashtag_count", "updated_at")
    list_filter = ("language", "ai_tone", "ai_language")
    search_fields = ("user__username", "user__email")


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("recipient", "actor", "kind", "post", "is_read", "created_at")
    list_filter = ("kind", "is_read", "created_at")
    search_fields = ("recipient__username", "recipient__email", "actor__username", "actor__email", "post__title")


@admin.register(PostComment)
class PostCommentAdmin(admin.ModelAdmin):
    list_display = ("post", "author", "parent", "is_edited", "created_at")
    list_filter = ("is_edited", "created_at")
    search_fields = ("body", "post__title", "author__username", "author__email")


@admin.register(SocialMediaCampaign)
class SocialMediaCampaignAdmin(admin.ModelAdmin):
    list_display = ("name", "subscription", "campaign_status", "platforms", "start_date", "end_date")
    list_filter = ("status",)
    search_fields = ("name", "subscription__name")

    def campaign_status(self, obj):
        return obj.effective_status_display

    campaign_status.short_description = "Status"

    def platforms(self, obj):
        return obj.platform_focus_display or "No platform selected"


@admin.register(SocialMediaPost)
class SocialMediaPostAdmin(admin.ModelAdmin):
    list_display = ("title", "platform", "status", "visibility", "subscription", "author", "likes_count", "shares_count", "scheduled_for")
    list_filter = ("platform", "status", "visibility", "content_format")
    search_fields = ("title", "caption", "hashtags")


@admin.register(PostMetric)
class PostMetricAdmin(admin.ModelAdmin):
    list_display = ("post", "captured_at", "impressions", "likes", "comments", "shares")
    list_filter = ("captured_at",)


@admin.register(PostView)
class PostViewAdmin(admin.ModelAdmin):
    list_display = ("post", "viewer", "viewed_at")
    list_filter = ("viewed_at",)
    search_fields = ("post__title", "viewer__username", "viewer__email")


@admin.register(PostImage)
class PostImageAdmin(admin.ModelAdmin):
    list_display = ("post", "order", "created_at")
    search_fields = ("post__title",)


@admin.register(PostEngagement)
class PostEngagementAdmin(admin.ModelAdmin):
    list_display = ("post", "user", "kind", "created_at")
    list_filter = ("kind", "created_at")
    search_fields = ("post__title", "user__username", "user__email")


@admin.register(AISuggestionHistory)
class AISuggestionHistoryAdmin(admin.ModelAdmin):
    list_display = ("topic", "platform", "tone", "requested_by", "created_at")
    list_filter = ("platform", "tone")
    search_fields = ("topic", "requested_by__username")
