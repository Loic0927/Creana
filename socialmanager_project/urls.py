from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView, TemplateView

from socialmanager import views as socialmanager_views
from socialmanager.seo_views import robots_txt, sitemap_xml


urlpatterns = [
    path(
        "17ee77b817984a8daf13e82bc3e24523.txt",
        TemplateView.as_view(
            template_name="17ee77b817984a8daf13e82bc3e24523.txt",
            content_type="text/plain",
        ),
        name="indexnow_key",
    ),
    path(
        "BingSiteAuth.xml",
        TemplateView.as_view(
            template_name="BingSiteAuth.xml",
            content_type="application/xml",
        ),
        name="bing_site_auth",
    ),
    path("service-worker.js", socialmanager_views.ServiceWorkerView.as_view(), name="service_worker"),
    path("manifest.webmanifest", socialmanager_views.WebAppManifestView.as_view(), name="web_app_manifest"),
    path("pwa/icons/<str:filename>", socialmanager_views.PWAIconView.as_view(), name="pwa_icon"),
    path("robots.txt", robots_txt, name="robots_txt"),
    path("sitemap.xml", sitemap_xml, name="sitemap_xml"),
    path("profiles/search/", socialmanager_views.profile_username_search, name="profile_username_search"),
    path("", include("socialmanager.urls")),
    # Password signup belongs to the project's SignUpView.  allauth's signup
    # fields are intentionally email-only so a social fallback never asks for
    # a password; do not expose that form as a second password-signup route.
    path(
        "accounts/signup/",
        RedirectView.as_view(pattern_name="socialmanager:signup", permanent=False),
        name="account_signup",
    ),
    path("accounts/", include("allauth.urls")),
    path("admin/", admin.site.urls),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
