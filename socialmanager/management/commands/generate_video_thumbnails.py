from django.core.management.base import BaseCommand

from socialmanager.models import SocialMediaPost
from socialmanager.services.video_thumbnail import generate_video_thumbnail, get_ffmpeg_path


class Command(BaseCommand):
    help = "Regenerate thumbnails for all existing video posts using the current frame-selection rule."

    def handle(self, *args, **options):
        posts = (
            SocialMediaPost.objects.filter(
                content_format=SocialMediaPost.Format.VIDEO,
            )
            .exclude(video_file="")
            .order_by("pk")
        )

        total = posts.count()
        succeeded = 0
        failed = 0
        ffmpeg_path = get_ffmpeg_path()

        self.stdout.write(f"Found {total} video post(s) to regenerate thumbnails for.")
        if ffmpeg_path:
            self.stdout.write(f"Using ffmpeg: {ffmpeg_path}")
        else:
            self.stdout.write(self.style.WARNING("ffmpeg is not available; thumbnails cannot be generated."))

        for post in posts:
            try:
                if generate_video_thumbnail(post, force=True):
                    succeeded += 1
                    self.stdout.write(self.style.SUCCESS(f"Generated thumbnail for post {post.pk}."))
                else:
                    failed += 1
                    self.stdout.write(self.style.WARNING(f"Skipped post {post.pk}: thumbnail could not be generated."))
            except Exception as exc:
                failed += 1
                self.stdout.write(self.style.ERROR(f"Failed post {post.pk}: {exc}"))

        self.stdout.write(
            self.style.SUCCESS(
                f"Finished video thumbnail generation: {succeeded} succeeded, {failed} failed."
            )
        )
