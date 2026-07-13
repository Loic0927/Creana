import logging

from allauth.account.adapter import DefaultAccountAdapter
from allauth.account.models import EmailAddress
from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from allauth.socialaccount.models import SocialAccount
from allauth.socialaccount.providers.base import AuthProcess
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import redirect
from django.utils.translation import gettext_lazy as _

from .account_identity import (
    generate_unique_username_from_email,
    get_active_users_for_email,
    get_users_for_email,
    normalize_email,
    user_email_exists,
)
from .services.account_setup import ensure_user_account_setup


USER_GOOGLE_EXISTS_MESSAGE = "This account already has a Google login linked."
GOOGLE_LINKED_ELSEWHERE_MESSAGE = "This Google account is already linked to another account."
AUTHENTICATED_GOOGLE_LOGIN_MESSAGE = "Please log out before signing in with another Google account."
ADMIN_GOOGLE_LINK_MESSAGE = "Admin accounts cannot link Google from this flow."
EMAIL_LINKED_ELSEWHERE_MESSAGE = "This Google email is already linked to another account."
EMAIL_ALREADY_REGISTERED_MESSAGE = _("This email is already registered. Please sign in or use password reset.")
GOOGLE_EMAIL_REQUIRED_MESSAGE = _("Google did not provide an email address. Please choose an account that shares its email.")

logger = logging.getLogger(__name__)


def _oauth_debug(event, **context):
    # Temporary Cloud Run diagnostics for Google OAuth/allauth routing.
    # Warning level is intentional so these appear even when DEBUG logs are filtered.
    logger.warning("[Google OAuth DEBUG] %s context=%s", event, context)


class SocialManagerAccountAdapter(DefaultAccountAdapter):
    def clean_email(self, email):
        email = normalize_email(super().clean_email(email))
        if user_email_exists(email):
            raise ValidationError(EMAIL_ALREADY_REGISTERED_MESSAGE)
        return email

    def validate_unique_email(self, email):
        email = normalize_email(email)
        if user_email_exists(email):
            raise ValidationError(EMAIL_ALREADY_REGISTERED_MESSAGE)
        return email

    def populate_username(self, request, user):
        if not user.username and user.email:
            user.username = generate_unique_username_from_email(user.email, exclude_user=user)
            return
        super().populate_username(request, user)


class SocialManagerSocialAccountAdapter(DefaultSocialAccountAdapter):
    def _sociallogin_context(self, request, sociallogin):
        account = getattr(sociallogin, "account", None)
        user = getattr(sociallogin, "user", None)
        return {
            "request_path": getattr(request, "path", ""),
            "request_user_id": getattr(getattr(request, "user", None), "pk", None),
            "request_user_authenticated": bool(getattr(getattr(request, "user", None), "is_authenticated", False)),
            "process": (getattr(sociallogin, "state", {}) or {}).get("process"),
            "sociallogin_is_existing": bool(getattr(sociallogin, "is_existing", False)),
            "sociallogin_user_pk": getattr(user, "pk", None),
            "sociallogin_user_email": getattr(user, "email", ""),
            "sociallogin_user_username": getattr(user, "username", ""),
            "socialaccount_provider": getattr(account, "provider", ""),
            "socialaccount_uid": getattr(account, "uid", ""),
            "socialaccount_adding": bool(getattr(getattr(account, "_state", None), "adding", False)),
        }

    def populate_user(self, request, sociallogin, data):
        _oauth_debug(
            "Entering populate_user()",
            **self._sociallogin_context(request, sociallogin),
            data_keys=sorted((data or {}).keys()),
        )
        user = super().populate_user(request, sociallogin, data)
        email = self._get_social_email(sociallogin) or normalize_email(data.get("email"))
        if email:
            user.email = normalize_email(email)
            user.username = generate_unique_username_from_email(email, exclude_user=user)
        _oauth_debug(
            "Leaving populate_user() before return",
            **self._sociallogin_context(request, sociallogin),
            populated_user_pk=getattr(user, "pk", None),
            populated_user_email=getattr(user, "email", ""),
            populated_user_username=getattr(user, "username", ""),
        )
        return user

    def save_user(self, request, sociallogin, form=None):
        _oauth_debug(
            "Entering save_user()",
            **self._sociallogin_context(request, sociallogin),
            has_form=bool(form),
        )
        email = self._get_social_email(sociallogin)
        if email:
            sociallogin.user.email = normalize_email(email)
            sociallogin.user.username = generate_unique_username_from_email(email, exclude_user=sociallogin.user)
        sociallogin.user.set_unusable_password()
        user = super().save_user(request, sociallogin, form=form)
        if email:
            self._ensure_email_address(user, email)
        ensure_user_account_setup(user)
        _oauth_debug(
            "Leaving save_user() before return",
            **self._sociallogin_context(request, sociallogin),
            saved_user_pk=getattr(user, "pk", None),
            saved_user_email=getattr(user, "email", ""),
            saved_user_username=getattr(user, "username", ""),
        )
        return user

    def is_open_for_signup(self, request, sociallogin):
        _oauth_debug(
            "Entering is_open_for_signup(); django-allauth reached social signup flow",
            **self._sociallogin_context(request, sociallogin),
        )
        result = super().is_open_for_signup(request, sociallogin)
        _oauth_debug(
            "Leaving is_open_for_signup() before return",
            **self._sociallogin_context(request, sociallogin),
            result=result,
        )
        return result

    def is_auto_signup_allowed(self, request, sociallogin):
        _oauth_debug(
            "Entering is_auto_signup_allowed(); allauth is evaluating social auto-signup",
            **self._sociallogin_context(request, sociallogin),
        )
        # Make our contract explicit: email is the only identity input needed
        # for auto-signup; username is generated in populate_user()/save_user().
        email = self._get_social_email(sociallogin)
        result = bool(email)
        _oauth_debug(
            "Leaving is_auto_signup_allowed() before return",
            **self._sociallogin_context(request, sociallogin),
            result=result,
        )
        return result

    def get_connect_redirect_url(self, request, socialaccount):
        _oauth_debug(
            "Entering get_connect_redirect_url(); redirecting successful Google connect to feed",
            request_path=getattr(request, "path", ""),
            request_user_id=getattr(getattr(request, "user", None), "pk", None),
            socialaccount_provider=getattr(socialaccount, "provider", ""),
            socialaccount_uid=getattr(socialaccount, "uid", ""),
        )
        return redirect("socialmanager:post_list").url

    def pre_social_login(self, request, sociallogin):
        _oauth_debug("Entering pre_social_login()", **self._sociallogin_context(request, sociallogin))
        provider = sociallogin.account.provider
        uid = sociallogin.account.uid
        email = self._get_social_email(sociallogin)
        email_users = get_users_for_email(email)[:2] if email else []
        existing_social_account = SocialAccount.objects.filter(provider=provider, uid=uid).select_related("user").first()
        _oauth_debug(
            "pre_social_login lookup results",
            **self._sociallogin_context(request, sociallogin),
            normalized_social_email=email,
            existing_user_found_by_email=bool(email_users),
            existing_user_id_by_email=getattr(email_users[0], "pk", None) if email_users else None,
            existing_user_count_sample=len(email_users),
            existing_social_account_exists=bool(existing_social_account),
            existing_social_account_user_id=getattr(existing_social_account, "user_id", None),
        )

        if request.user.is_authenticated:
            _oauth_debug("Branch: authenticated request", **self._sociallogin_context(request, sociallogin))
            self._validate_connect_request(request, provider, uid, email, sociallogin)
            _oauth_debug(
                "Leaving pre_social_login() before return after authenticated request branch",
                **self._sociallogin_context(request, sociallogin),
            )
            return

        if len(email_users) > 1:
            _oauth_debug("Branch: duplicate local users found for Google email", **self._sociallogin_context(request, sociallogin))
            self._block(request, EMAIL_LINKED_ELSEWHERE_MESSAGE, "socialmanager:login")

        if sociallogin.is_existing:
            if sociallogin.account._state.adding:
                _oauth_debug("Branch: existing email user", **self._sociallogin_context(request, sociallogin))
                existing_for_user = SocialAccount.objects.filter(
                    user=sociallogin.user,
                    provider=provider,
                ).first()
                _oauth_debug(
                    "Existing SocialAccount for selected user lookup",
                    **self._sociallogin_context(request, sociallogin),
                    existing_for_user_exists=bool(existing_for_user),
                    existing_for_user_uid=getattr(existing_for_user, "uid", ""),
                )
                if existing_for_user and existing_for_user.uid != uid:
                    _oauth_debug(
                        "Branch: existing email user blocked because user already has different Google account",
                        **self._sociallogin_context(request, sociallogin),
                    )
                    self._block(request, USER_GOOGLE_EXISTS_MESSAGE, "socialmanager:login")
                self._validate_email_address_ownership(
                    request,
                    email,
                    sociallogin.user,
                    "socialmanager:login",
                )
                if not self._ensure_email_address(sociallogin.user, email):
                    _oauth_debug(
                        "Branch: existing email user blocked because EmailAddress belongs elsewhere",
                        **self._sociallogin_context(request, sociallogin),
                    )
                    self._block(request, EMAIL_LINKED_ELSEWHERE_MESSAGE, "socialmanager:login")
            else:
                _oauth_debug("Branch: existing social account", **self._sociallogin_context(request, sociallogin))
            _oauth_debug(
                "Leaving pre_social_login() before return after existing social login branch",
                **self._sociallogin_context(request, sociallogin),
            )
            return

        if not email:
            _oauth_debug("Branch: new user blocked because Google supplied no email", **self._sociallogin_context(request, sociallogin))
            self._block(request, GOOGLE_EMAIL_REQUIRED_MESSAGE, "socialmanager:login")

        # A local account exists but allauth did not authenticate it by email.
        # This means the provider email was not verified (or the account is
        # inactive), so auto-connecting would be unsafe and creating another
        # user would violate the one-email/one-account rule.
        if email_users:
            _oauth_debug("Branch: unresolved existing local email blocked", **self._sociallogin_context(request, sociallogin))
            self._block(request, EMAIL_LINKED_ELSEWHERE_MESSAGE, "socialmanager:login")

        _oauth_debug("Branch: new user", **self._sociallogin_context(request, sociallogin))
        _oauth_debug(
            "Leaving pre_social_login() before implicit return for new user branch",
            **self._sociallogin_context(request, sociallogin),
        )

    def _validate_connect_request(self, request, provider, uid, email, sociallogin):
        _oauth_debug("Entering _validate_connect_request()", **self._sociallogin_context(request, sociallogin))
        if sociallogin.state.get("process") != AuthProcess.CONNECT:
            if SocialAccount.objects.filter(user=request.user, provider=provider, uid=uid).exists():
                _oauth_debug(
                    "Branch: authenticated same linked Google account; redirecting to feed",
                    **self._sociallogin_context(request, sociallogin),
                )
                raise ImmediateHttpResponse(redirect("socialmanager:post_list"))
            _oauth_debug(
                "Branch: authenticated different Google account outside CONNECT; blocking",
                **self._sociallogin_context(request, sociallogin),
            )
            self._block(request, AUTHENTICATED_GOOGLE_LOGIN_MESSAGE, "socialmanager:login")

        if request.user.is_staff or request.user.is_superuser:
            _oauth_debug("Branch: staff/superuser CONNECT blocked", **self._sociallogin_context(request, sociallogin))
            self._block(request, ADMIN_GOOGLE_LINK_MESSAGE, "socialmanager:settings")

        if SocialAccount.objects.filter(user=request.user, provider=provider).exists():
            _oauth_debug(
                "Branch: CONNECT blocked because user already has provider account",
                **self._sociallogin_context(request, sociallogin),
            )
            self._block(request, USER_GOOGLE_EXISTS_MESSAGE, "socialmanager:settings")

        if SocialAccount.objects.filter(provider=provider, uid=uid).exclude(user=request.user).exists():
            _oauth_debug(
                "Branch: CONNECT blocked because provider uid linked elsewhere",
                **self._sociallogin_context(request, sociallogin),
            )
            self._block(request, GOOGLE_LINKED_ELSEWHERE_MESSAGE, "socialmanager:settings")

        if email:
            existing_user = self._get_single_active_user_for_email(request, email, "socialmanager:settings")
            _oauth_debug(
                "CONNECT email lookup result",
                **self._sociallogin_context(request, sociallogin),
                existing_user_found_by_email=bool(existing_user),
                existing_user_id_by_email=getattr(existing_user, "pk", None),
            )
            if existing_user and existing_user != request.user:
                _oauth_debug(
                    "Branch: CONNECT blocked because email belongs to another user",
                    **self._sociallogin_context(request, sociallogin),
                )
                self._block(request, EMAIL_LINKED_ELSEWHERE_MESSAGE, "socialmanager:settings")
            self._validate_email_address_ownership(request, email, request.user, "socialmanager:settings")
        _oauth_debug("Leaving _validate_connect_request() before implicit return", **self._sociallogin_context(request, sociallogin))

    def _block(self, request, message, redirect_name):
        _oauth_debug("Blocking OAuth flow before ImmediateHttpResponse", blocked_message=message, redirect_name=redirect_name)
        messages.error(request, message)
        raise ImmediateHttpResponse(redirect(redirect_name))

    def _get_social_email(self, sociallogin):
        email = (getattr(sociallogin.user, "email", "") or "").strip()
        if email:
            return normalize_email(email)

        for email_address in sociallogin.email_addresses:
            email = (email_address.email or "").strip()
            if email:
                return normalize_email(email)

        return ""

    def _get_single_active_user_for_email(self, request, email, redirect_name):
        users = get_active_users_for_email(email)[:2]
        if len(users) > 1:
            self._block(request, EMAIL_LINKED_ELSEWHERE_MESSAGE, redirect_name)
        return users[0] if users else None

    def _validate_email_address_ownership(self, request, email, user, redirect_name):
        existing_email = (
            EmailAddress.objects.filter(email__iexact=email)
            .exclude(user=user)
            .select_related("user")
            .first()
        )
        if existing_email:
            self._block(request, EMAIL_LINKED_ELSEWHERE_MESSAGE, redirect_name)

    def _ensure_email_address(self, user, email):
        normalized_email = (email or "").strip().lower()
        if not normalized_email:
            return None

        if EmailAddress.objects.filter(email__iexact=normalized_email).exclude(user=user).exists():
            return None

        email_address = EmailAddress.objects.filter(
            user=user,
            email__iexact=normalized_email,
        ).first()
        if email_address is None:
            email_address, _ = EmailAddress.objects.get_or_create(
                user=user,
                email=normalized_email,
                defaults={"verified": True},
            )

        if not email_address.verified:
            email_address.verified = True
            email_address.save(update_fields=["verified"])
        if not email_address.primary:
            email_address.set_as_primary()

        return email_address
