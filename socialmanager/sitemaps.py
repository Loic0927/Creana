from django.contrib.sitemaps import Sitemap
from django.urls import reverse

from .models import SocialMediaPost
from .geo_content import PUBLIC_PAGE_ORDER, PUBLIC_PAGES


class StaticPublicSitemap(Sitemap):
    changefreq = "weekly"
    priority = 1.0

    def items(self):
        return ["socialmanager:landing"] + [
            f"socialmanager:{PUBLIC_PAGES[key]['route_name']}"
            for key in PUBLIC_PAGE_ORDER
        ]

    def location(self, item):
        return reverse(item)


class PublicPostSitemap(Sitemap):
    changefreq = "weekly"
    priority = 0.7

    def items(self):
        return SocialMediaPost.objects.filter(
            status=SocialMediaPost.Status.PUBLISHED,
            visibility=SocialMediaPost.Visibility.PUBLIC,
            subscription__is_archived=False,
            author__is_active=True,
        ).order_by("pk")

    def location(self, post):
        return reverse(
            "socialmanager:post_detail",
            kwargs={"pk": post.pk, "slug": post.slug},
        )

    def lastmod(self, post):
        return post.updated_at or post.published_at or post.created_at


sitemaps = {
    "static": StaticPublicSitemap,
    "posts": PublicPostSitemap,
}
