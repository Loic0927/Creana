from django.db import transaction

from socialmanager.models import SaaSSubscription, SubscriptionMembership, UserProfile, UserSettings


@transaction.atomic
def ensure_user_account_setup(user, workspace_name=None):
    UserProfile.objects.get_or_create(user=user)
    UserSettings.objects.get_or_create(user=user)

    membership = (
        SubscriptionMembership.objects.select_related("subscription")
        .filter(user=user, subscription__is_archived=False)
        .order_by("joined_at")
        .first()
    )
    if membership:
        return membership

    owned_subscription = (
        SaaSSubscription.objects.filter(owner=user, is_archived=False)
        .order_by("created_at")
        .first()
    )
    if owned_subscription:
        membership, _ = SubscriptionMembership.objects.get_or_create(
            subscription=owned_subscription,
            user=user,
            defaults={
                "role": SubscriptionMembership.Role.ADMIN,
                "is_active_member": False,
            },
        )
        return membership

    subscription = SaaSSubscription.objects.create(
        name=workspace_name or f"{user.get_username()}'s Workspace",
        owner=user,
    )
    return SubscriptionMembership.objects.create(
        subscription=subscription,
        user=user,
        role=SubscriptionMembership.Role.ADMIN,
        is_active_member=False,
    )
