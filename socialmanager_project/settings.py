from dotenv import load_dotenv
import dj_database_url
import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured


BASE_DIR = Path(__file__).resolve().parent.parent
# Real process environment variables (Cloud Run/Secret Manager) take priority.
load_dotenv(BASE_DIR / ".env", override=False)


def env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name, default=None):
    value = os.getenv(name)
    if not value:
        return default or []
    return [item.strip() for item in value.split(",") if item.strip()]


DEFAULT_LOCAL_SECRET_KEY = "django-insecure-socialmanager-assignment-key"
SECRET_KEY = os.getenv("SECRET_KEY", DEFAULT_LOCAL_SECRET_KEY)

DEBUG = env_bool("DEBUG", False)

ALLOWED_HOSTS = env_list(
    "ALLOWED_HOSTS",
    ["127.0.0.1", "localhost", "10.91.115.210", "testserver"]
)
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS")

if not DEBUG and SECRET_KEY == DEFAULT_LOCAL_SECRET_KEY:
    raise ImproperlyConfigured("Set SECRET_KEY before running with DEBUG=False.")
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.sites",
    "django.contrib.sitemaps",
    "django.contrib.staticfiles",
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.google",
    "socialmanager",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "socialmanager.middleware.SearchEngineIndexingMiddleware",
    "socialmanager.middleware.AdminPasswordOnlyMiddleware",
    "socialmanager.middleware.UserSettingsLocaleMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "socialmanager_project.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "socialmanager.context_processors.notification_counts",
                "socialmanager.context_processors.user_settings",
            "socialmanager.context_processors.ai_membership",
            "socialmanager.context_processors.site_metadata",
            ],
        },
    },
]

WSGI_APPLICATION = "socialmanager_project.wsgi.application"

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
if DATABASE_URL:
    DATABASES = {
        "default": dj_database_url.parse(
            DATABASE_URL,
            conn_max_age=int(os.getenv("DATABASE_CONN_MAX_AGE", "600")),
            conn_health_checks=True,
        )
    }
else:
    # Keep local development zero-configuration; Cloud Run must set DATABASE_URL.
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en"
LANGUAGES = [
    ("en", "English"),
    ("zh-hant", "繁體中文"),
]
LOCALE_PATHS = [BASE_DIR / "locale"]
TIME_ZONE = "Australia/Brisbane"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# Keep uploads below Cloud Run's request-body ceiling so Django can return a
# useful validation error instead of the platform rejecting the request first.
VIDEO_UPLOAD_MAX_BYTES = int(os.getenv("VIDEO_UPLOAD_MAX_BYTES", str(500 * 1024 * 1024)))
# A no-JavaScript/local fallback may still pass through Django. Keep that path
# safely below Cloud Run's request ceiling even when direct uploads are larger.
VIDEO_FORM_UPLOAD_MAX_BYTES = int(os.getenv("VIDEO_FORM_UPLOAD_MAX_BYTES", str(20 * 1024 * 1024)))

USE_GCS = env_bool("USE_GCS", False)
GS_BUCKET_NAME = os.getenv("GS_BUCKET_NAME", "").strip()
GS_MEDIA_BUCKET_NAME = os.getenv("GS_MEDIA_BUCKET_NAME", "").strip() or GS_BUCKET_NAME
GS_QUERYSTRING_AUTH = env_bool("GS_QUERYSTRING_AUTH", True)
GS_IAM_SIGN_BLOB = env_bool("GS_IAM_SIGN_BLOB", True)
GS_SA_EMAIL = os.getenv("GS_SA_EMAIL", "").strip()

if USE_GCS:
    if not GS_MEDIA_BUCKET_NAME:
        raise ImproperlyConfigured(
            "Set GS_BUCKET_NAME or GS_MEDIA_BUCKET_NAME when USE_GCS=True."
        )
    STORAGES = {
        "default": {
            "BACKEND": "socialmanager.storage.CachedSignedUrlGoogleCloudStorage",
            "OPTIONS": {
                "bucket_name": GS_MEDIA_BUCKET_NAME,
                "default_acl": None,
                # Signed URLs work for private buckets. Set this false only when
                # the bucket objects are intentionally public.
                "querystring_auth": GS_QUERYSTRING_AUTH,
                # Cloud Run ADC has no private key; IAM signBlob creates signed
                # media URLs using the runtime service account instead.
                "iam_sign_blob": GS_IAM_SIGN_BLOB,
                **({"sa_email": GS_SA_EMAIL} if GS_SA_EMAIL else {}),
            },
        },
        "staticfiles": {
            "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
        },
    }
else:
    STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {
            "BACKEND": (
                "django.contrib.staticfiles.storage.StaticFilesStorage"
                if DEBUG
                else "whitenoise.storage.CompressedManifestStaticFilesStorage"
            )
        },
    }

# Local development prints password reset emails, including the reset link, to
# the runserver terminal. Set EMAIL_BACKEND in production to use real SMTP.
EMAIL_BACKEND = os.getenv(
    "EMAIL_BACKEND",
    "django.core.mail.backends.console.EmailBackend",
)
EMAIL_HOST = os.getenv("EMAIL_HOST", "")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", EMAIL_HOST_USER or "Creana <no-reply@creana.local>")
SERVER_EMAIL = os.getenv("SERVER_EMAIL", DEFAULT_FROM_EMAIL)

WEB_PUSH_VAPID_PUBLIC_KEY = os.getenv("WEB_PUSH_VAPID_PUBLIC_KEY", "").strip()
WEB_PUSH_VAPID_PRIVATE_KEY = os.getenv("WEB_PUSH_VAPID_PRIVATE_KEY", "").strip()
WEB_PUSH_VAPID_EMAIL = os.getenv("WEB_PUSH_VAPID_EMAIL", "mailto:no-reply@creana.local").strip()

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

SITE_ID = 1

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

LOGIN_URL = "socialmanager:login"
LOGIN_REDIRECT_URL = "socialmanager:post_list"
LOGOUT_REDIRECT_URL = "socialmanager:login"

ACCOUNT_LOGIN_METHODS = {"username", "email"}
# Usernames are generated from the email address by SocialManagerAccountAdapter.
# Keeping username out of this list also prevents social auto-signup from falling
# back to allauth's username form.
# allauth's social SignupForm inherits these fields. Password signup uses the
# project's separate SignUpForm, so the allauth fallback must remain passwordless.
ACCOUNT_SIGNUP_FIELDS = ["email*"]
ACCOUNT_EMAIL_VERIFICATION = "none"
ACCOUNT_UNIQUE_EMAIL = True
ACCOUNT_ADAPTER = "socialmanager.adapters.SocialManagerAccountAdapter"
SOCIALACCOUNT_LOGIN_ON_GET = False
SOCIALACCOUNT_AUTO_SIGNUP = True
SOCIALACCOUNT_EMAIL_AUTHENTICATION = True
SOCIALACCOUNT_EMAIL_AUTHENTICATION_AUTO_CONNECT = True
SOCIALACCOUNT_ADAPTER = "socialmanager.adapters.SocialManagerSocialAccountAdapter"

# Google OAuth is configured from .env so local login does not depend on a
# Django admin SocialApp. Keep these credentials out of source control.
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")

SOCIALACCOUNT_PROVIDERS = {
    "google": {
        "APP": {
            "client_id": GOOGLE_CLIENT_ID,
            "secret": GOOGLE_CLIENT_SECRET,
            "key": "",
        },
        "SCOPE": ["profile", "email"],
        "AUTH_PARAMS": {
            "access_type": "online",
            "prompt": "select_account",
        },
    }
}

OPENAI_API_KEY = (
    os.getenv("OPENAI_API_KEY")
    or os.getenv("LLM_API_KEY")
)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_ENABLED = env_bool("GEMINI_ENABLED", True)
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash") or "gemini-2.5-flash"
GEMINI_VIDEO_MAX_BYTES = int(os.getenv("GEMINI_VIDEO_MAX_BYTES", str(50 * 1024 * 1024)))
GEMINI_VIDEO_MAX_SECONDS = int(os.getenv("GEMINI_VIDEO_MAX_SECONDS", "60"))
VIDEO_MAX_DURATION_SECONDS = int(os.getenv("VIDEO_MAX_DURATION_SECONDS", "60"))
VIDEO_DURATION_TOLERANCE_SECONDS = float(os.getenv("VIDEO_DURATION_TOLERANCE_SECONDS", "0.05"))
VIDEO_INTELLIGENCE_TIMEOUT_SECONDS = int(os.getenv("VIDEO_INTELLIGENCE_TIMEOUT_SECONDS", "300"))

STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY", "")
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_MEMBERSHIP_PRICE_ID = os.getenv("STRIPE_MEMBERSHIP_PRICE_ID", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
SITE_URL = os.getenv("SITE_URL", "")
INDEXNOW_KEY = os.getenv("INDEXNOW_KEY", "")
INDEXNOW_ENDPOINT = os.getenv("INDEXNOW_ENDPOINT", "https://api.indexnow.org/indexnow")
IS_LOCAL_HTTP_SITE = SITE_URL.startswith(("http://127.0.0.1", "http://localhost"))

# Cloud Run terminates TLS at its proxy and forwards the original scheme.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"

if IS_LOCAL_HTTP_SITE:
    SECURE_SSL_REDIRECT = False
    SESSION_COOKIE_SECURE = False
    CSRF_COOKIE_SECURE = False
    SECURE_HSTS_SECONDS = 0
    SECURE_HSTS_INCLUDE_SUBDOMAINS = False
    SECURE_HSTS_PRELOAD = False
elif not DEBUG:
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    # Keep HSTS opt-in until the final HTTPS domain has been verified.
    SECURE_HSTS_SECONDS = int(os.getenv("SECURE_HSTS_SECONDS", "0"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", False)
    SECURE_HSTS_PRELOAD = env_bool("SECURE_HSTS_PRELOAD", False)
else:
    SECURE_SSL_REDIRECT = False
    SESSION_COOKIE_SECURE = False
    CSRF_COOKIE_SECURE = False
    SECURE_HSTS_SECONDS = 0

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stderr",
            "level": "ERROR",
        },
    },
    "loggers": {
        "django.request": {
            "handlers": ["console"],
            "level": "ERROR",
            "propagate": True,
        },
    },
}
