from allauth.account.signals import user_signed_up
from allauth.account.models import EmailAddress
from allauth.socialaccount.models import SocialAccount
from django.conf import settings
from django.db.models.signals import post_save, pre_delete
from django.dispatch import receiver

from .models import SubscriptionMembership, UserProfile, UserSettings
from .services.account_setup import ensure_user_account_setup


@receiver(user_signed_up)
def create_google_signup_workspace(request, user, **kwargs):
    ensure_user_account_setup(user)


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_user_settings(sender, instance, created, **kwargs):
    if created:
        UserSettings.objects.get_or_create(user=instance)


@receiver(pre_delete, sender=settings.AUTH_USER_MODEL)
def cleanup_deleted_user_identity(sender, instance, **kwargs):
    SocialAccount.objects.filter(user=instance).delete()
    EmailAddress.objects.filter(user=instance).delete()
    UserProfile.objects.filter(user=instance).delete()
    UserSettings.objects.filter(user=instance).delete()
    SubscriptionMembership.objects.filter(user=instance).delete()
