from allauth.account.signals import user_signed_up
from allauth.account.models import EmailAddress
from allauth.socialaccount.models import SocialAccount
from django.conf import settings
from django.db import transaction
from django.db.models.signals import post_save, pre_delete
from django.dispatch import receiver

from .models import Announcement, Notification, SubscriptionMembership, UserProfile, UserSettings
from .services.account_setup import ensure_user_account_setup


@receiver(user_signed_up)
def create_google_signup_workspace(request, user, **kwargs):
    ensure_user_account_setup(user)


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_user_settings(sender, instance, created, **kwargs):
    if created:
        UserSettings.objects.get_or_create(
            user=instance,
            defaults={"push_ai_finished": False},
        )


@receiver(post_save, sender=Notification)
def deliver_notification_push(sender, instance, created, **kwargs):
    if created:
        from .services.web_push import send_notification_push
        transaction.on_commit(lambda: send_notification_push(instance))


@receiver(post_save, sender=Announcement)
def deliver_announcement_push(sender, instance, created, **kwargs):
    if not created or not instance.is_active:
        return
    from django.contrib.auth import get_user_model
    from .services.web_push import send_push_notification

    def send_to_users():
        for user in get_user_model().objects.filter(is_active=True).iterator():
            send_push_notification(user, instance.title, instance.content[:180], "/posts/", "announcement")

    transaction.on_commit(send_to_users)


@receiver(pre_delete, sender=settings.AUTH_USER_MODEL)
def cleanup_deleted_user_identity(sender, instance, **kwargs):
    SocialAccount.objects.filter(user=instance).delete()
    EmailAddress.objects.filter(user=instance).delete()
    UserProfile.objects.filter(user=instance).delete()
    UserSettings.objects.filter(user=instance).delete()
    SubscriptionMembership.objects.filter(user=instance).delete()
