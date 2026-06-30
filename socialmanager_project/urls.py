from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from socialmanager import views as socialmanager_views
from socialmanager.seo_views import robots_txt, sitemap_xml


urlpatterns = [
    path("robots.txt", robots_txt, name="robots_txt"),
    path("sitemap.xml", sitemap_xml, name="sitemap_xml"),
    path("profiles/search/", socialmanager_views.profile_username_search, name="profile_username_search"),
    path("", include("socialmanager.urls")),
    path("accounts/", include("allauth.urls")),
    path("admin/", admin.site.urls),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
