from types import SimpleNamespace
from urllib.parse import urlparse

from django.http import HttpResponse
from django.template.response import TemplateResponse

from .seo import get_site_url
from .sitemaps import sitemaps


ROBOTS_DISALLOW_PATHS = (
    "/admin/",
    "/dashboard/",
    "/settings/",
    "/campaigns/",
    "/notifications/",
    "/analytics/",
    "/profile/",
    "/posts/create/",
    "/posts/new/",
    "/posts/ai-feedback/",
    "/posts/video-upload/",
    "/posts/*/analytics/",
    "/posts/*/edit/",
    "/posts/*/delete/",
    "/posts/*/engagement/",
    "/posts/*/track-watch/",
    "/posts/*/detail-update/",
    "/posts/*/ai-insight/",
    "/posts/*/analyze-video/",
    "/posts/*/retention-ai-insight/",
    "/comments/",
    "/membership/",
    "/subscriptions/",
    "/stripe/",
    "/accounts/",
    "/login/",
    "/logout/",
    "/signup/",
    "/password-reset/",
    "/api/",
)


def robots_txt(request):
    lines = ["User-agent: *", "Allow: /"]
    lines.extend(f"Disallow: {path}" for path in ROBOTS_DISALLOW_PATHS)
    lines.extend(("", f"Sitemap: {get_site_url(request)}/sitemap.xml"))
    return HttpResponse("\n".join(lines) + "\n", content_type="text/plain; charset=utf-8")


def sitemap_xml(request):
    site_url = get_site_url(request)
    parsed_site_url = urlparse(site_url)
    protocol = parsed_site_url.scheme or request.scheme
    site = SimpleNamespace(domain=parsed_site_url.netloc or parsed_site_url.path)
    urls = []
    for sitemap_class in sitemaps.values():
        urls.extend(sitemap_class().get_urls(site=site, protocol=protocol))

    response = TemplateResponse(
        request,
        "sitemap.xml",
        {"urlset": urls},
        content_type="application/xml",
    )
    response["X-Robots-Tag"] = "noindex, follow"
    return response
