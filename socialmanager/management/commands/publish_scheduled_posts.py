from django.core.management.base import BaseCommand

from socialmanager.services.scheduler import publish_due_scheduled_posts


class Command(BaseCommand):
    help = "Publish scheduled social posts whose scheduled time has passed."

    def handle(self, *args, **options):
        published_count = publish_due_scheduled_posts()
        self.stdout.write(self.style.SUCCESS(f"Published {published_count} scheduled post(s)."))
