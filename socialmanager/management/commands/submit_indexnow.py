from urllib.parse import urljoin

from django.core.management.base import BaseCommand, CommandError

from socialmanager.indexnow import submit_indexnow_urls
from socialmanager.sitemaps import PublicPostSitemap, StaticPublicSitemap


SITE_ROOT = "https://creana.app"


class Command(BaseCommand):
    help = "Submit sitemap-eligible public Creana URLs to IndexNow."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print eligible URLs without submitting them.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            help="Submit or print at most this many URLs.",
        )

    def handle(self, *args, **options):
        limit = options["limit"]
        if limit is not None and limit < 1:
            raise CommandError("--limit must be a positive integer.")

        urls = self._eligible_urls()
        if limit is not None:
            urls = urls[:limit]

        if options["dry_run"]:
            for url in urls:
                self.stdout.write(url)
            self.stdout.write(self.style.SUCCESS(f"Dry run: {len(urls)} URL(s) eligible."))
            return

        if submit_indexnow_urls(urls, diagnostic_callback=self.stdout.write):
            self.stdout.write(self.style.SUCCESS(f"Submitted {len(urls)} URL(s) to IndexNow."))

    @staticmethod
    def _eligible_urls():
        urls = []
        static_sitemap = StaticPublicSitemap()
        for item in static_sitemap.items():
            urls.append(urljoin(f"{SITE_ROOT}/", static_sitemap.location(item)))

        post_sitemap = PublicPostSitemap()
        for post in post_sitemap.items():
            urls.append(urljoin(f"{SITE_ROOT}/", post_sitemap.location(post)))
        return urls
