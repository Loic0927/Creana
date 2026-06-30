import re

from django.contrib.auth import get_user_model
from django.db.models import Count
from django.db.models.functions import Lower

from .models import USERNAME_MAX_LENGTH


def normalize_email(email):
    return (email or "").strip().lower()


def user_email_exists(email, exclude_user=None):
    email = normalize_email(email)
    if not email:
        return False
    queryset = get_user_model().objects.filter(email__iexact=email)
    if exclude_user is not None and getattr(exclude_user, "pk", None):
        queryset = queryset.exclude(pk=exclude_user.pk)
    return queryset.exists()


def get_active_users_for_email(email):
    email = normalize_email(email)
    if not email:
        return []
    return [
        user
        for user in get_user_model().objects.filter(email__iexact=email, is_active=True).order_by("id")
        if normalize_email(user.email) == email
    ]


def get_duplicate_email_groups():
    return (
        get_user_model()
        .objects.exclude(email="")
        .annotate(normalized_email=Lower("email"))
        .values("normalized_email")
        .annotate(user_count=Count("id"))
        .filter(user_count__gt=1)
        .order_by("normalized_email")
    )


def username_base_from_email(email):
    local_part = normalize_email(email).split("@", 1)[0].strip()
    local_part = re.sub(r"\s+", "", local_part)
    return (local_part or "user")[:USERNAME_MAX_LENGTH]


def generate_unique_username_from_email(email, exclude_user=None):
    User = get_user_model()
    base = username_base_from_email(email)

    def exists(candidate):
        queryset = User.objects.filter(username__iexact=candidate)
        if exclude_user is not None and getattr(exclude_user, "pk", None):
            queryset = queryset.exclude(pk=exclude_user.pk)
        return queryset.exists()

    if not exists(base):
        return base

    for number in range(1, 100000):
        suffix = str(number)
        candidate = f"{base[:USERNAME_MAX_LENGTH - len(suffix)]}{suffix}"
        if not exists(candidate):
            return candidate

    raise RuntimeError("Unable to generate a unique username.")
