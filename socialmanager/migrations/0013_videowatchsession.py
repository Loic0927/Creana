from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("socialmanager", "0012_campaign_posts_m2m"),
    ]

    operations = [
        migrations.CreateModel(
            name="VideoWatchSession",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("watched_seconds", models.PositiveIntegerField(default=0)),
                ("video_duration", models.PositiveIntegerField(default=0)),
                ("watched_percentage", models.FloatField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "post",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="video_watch_sessions",
                        to="socialmanager.socialmediapost",
                    ),
                ),
                (
                    "viewer",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="video_watch_sessions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-updated_at"],
                "indexes": [models.Index(fields=["post", "viewer", "-updated_at"], name="socialmanag_post_id_1d0239_idx")],
            },
        ),
    ]
