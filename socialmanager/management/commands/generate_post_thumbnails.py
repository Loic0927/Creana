from django.core.management.base import BaseCommand
from django.db.models import Q

from socialmanager.models import PostImage


class Command(BaseCommand):
    help = "Generate missing thumbnails for existing post images."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Regenerate thumbnails even when a thumbnail already exists.",
        )

    def handle(self, *args, **options):
        force = options["force"]
        post_images = PostImage.objects.exclude(image="").select_related("post").order_by("pk")
        if not force:
            post_images = post_images.filter(Q(thumbnail__isnull=True) | Q(thumbnail=""))

        total = post_images.count()
        succeeded = 0
        skipped = 0
        failed = 0

        action = "regenerating" if force else "missing"
        self.stdout.write(f"Found {total} post image(s) for {action} thumbnails.")

        for post_image in post_images.iterator():
            try:
                if post_image.generate_thumbnail(force=force):
                    succeeded += 1
                    self.stdout.write(
                        self.style.SUCCESS(f"Generated thumbnail for post image {post_image.pk}.")
                    )
                else:
                    skipped += 1
                    self.stdout.write(
                        self.style.WARNING(f"Skipped post image {post_image.pk}: no thumbnail generated.")
                    )
            except Exception as exc:
                failed += 1
                self.stdout.write(self.style.ERROR(f"Failed post image {post_image.pk}: {exc}"))

        self.stdout.write(
            self.style.SUCCESS(
                "Finished post thumbnail generation: "
                f"{succeeded} succeeded, {skipped} skipped, {failed} failed."
            )
        )
