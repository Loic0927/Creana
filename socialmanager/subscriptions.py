from .models import SubscriptionMembership


def user_has_active_subscription(user):
    if not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    return SubscriptionMembership.objects.filter(
        user=user,
        subscription__is_archived=False,
        is_active_member=True,
    ).exists()


def activate_membership(membership, *, stripe_customer_id="", stripe_subscription_id="", stripe_checkout_session_id=""):
    membership.is_active_member = True
    if stripe_customer_id:
        membership.stripe_customer_id = stripe_customer_id
    if stripe_subscription_id:
        membership.stripe_subscription_id = stripe_subscription_id
    if stripe_checkout_session_id:
        membership.stripe_checkout_session_id = stripe_checkout_session_id
    membership.save(
        update_fields=[
            "is_active_member",
            "stripe_customer_id",
            "stripe_subscription_id",
            "stripe_checkout_session_id",
        ]
    )
    return membership


def deactivate_membership_for_subscription(stripe_subscription_id):
    if not stripe_subscription_id:
        return 0
    return SubscriptionMembership.objects.filter(
        stripe_subscription_id=stripe_subscription_id,
        user__is_superuser=False,
    ).update(is_active_member=False)
