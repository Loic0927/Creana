from django.core.management.base import BaseCommand
from django.db.models import Q

from socialmanager.models import UserProfile


class Command(BaseCommand):
    help = "Generate missing thumbnails for existing profile avatars."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Regenerate avatar thumbnails even when one already exists.",
        )

    def handle(self, *args, **options):
        force = options["force"]
        profiles = UserProfile.objects.exclude(avatar="").select_related("user").order_by("pk")
        if not force:
            profiles = profiles.filter(Q(avatar_thumbnail__isnull=True) | Q(avatar_thumbnail=""))

        total = profiles.count()
        succeeded = 0
        skipped = 0
        failed = 0

        action = "regenerating" if force else "missing"
        self.stdout.write(f"Found {total} profile avatar(s) for {action} thumbnails.")

        for profile in profiles.iterator():
            try:
                if profile.generate_avatar_thumbnail(force=force):
                    succeeded += 1
                    self.stdout.write(
                        self.style.SUCCESS(f"Generated avatar thumbnail for profile {profile.pk}.")
                    )
                else:
                    skipped += 1
                    self.stdout.write(
                        self.style.WARNING(f"Skipped profile {profile.pk}: no thumbnail generated.")
                    )
            except Exception as exc:
                failed += 1
                self.stdout.write(self.style.ERROR(f"Failed profile {profile.pk}: {exc}"))

        self.stdout.write(
            self.style.SUCCESS(
                "Finished avatar thumbnail generation: "
                f"{succeeded} succeeded, {skipped} skipped, {failed} failed."
            )
        )
