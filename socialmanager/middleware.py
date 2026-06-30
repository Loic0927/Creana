from django.contrib.auth import logout
from django.shortcuts import redirect
from django.utils import translation

from .models import UserSettings


PASSWORD_AUTH_BACKEND = "django.contrib.auth.backends.ModelBackend"
NOINDEX_PATH_PREFIXES = (
    "/admin/",
    "/accounts/",
    "/login/",
    "/logout/",
    "/signup/",
    "/password-reset/",
    "/dashboard/",
    "/analytics/",
    "/settings/",
    "/profile/",
    "/campaigns/",
    "/notifications/",
    "/subscriptions/",
    "/membership/",
    "/stripe/",
    "/comments/",
    "/api/",
)


class SearchEngineIndexingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.path.startswith(NOINDEX_PATH_PREFIXES):
            response["X-Robots-Tag"] = "noindex, nofollow"
        return response


class AdminPasswordOnlyMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith("/accounts/"):
            return self.get_response(request)

        if (
            request.path.startswith("/admin/")
            and request.user.is_authenticated
            and request.user.is_staff
            and request.session.get("_auth_user_backend") != PASSWORD_AUTH_BACKEND
        ):
            logout(request)
            return redirect(f"/admin/login/?next={request.path}")

        return self.get_response(request)


class UserSettingsLocaleMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            settings_obj, _ = UserSettings.objects.get_or_create(user=request.user)
            translation.activate(settings_obj.language)
            request.LANGUAGE_CODE = settings_obj.language

        response = self.get_response(request)

        if request.user.is_authenticated:
            response.set_cookie("django_language", request.LANGUAGE_CODE)

        return response
