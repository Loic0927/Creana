from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("socialmanager", "0037_usersettings_push_ai_finished"),
    ]

    operations = [
        migrations.CreateModel(
            name="PostTrackingSnapshot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("captured_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("views", models.PositiveIntegerField(default=0)),
                ("likes", models.PositiveIntegerField(default=0)),
                ("comments", models.PositiveIntegerField(default=0)),
                ("shares", models.PositiveIntegerField(default=0)),
                ("engagement_rate", models.FloatField(default=0)),
                ("retention_percent", models.FloatField(blank=True, null=True)),
                ("recommendation", models.TextField(blank=True)),
                ("created_by", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="post_tracking_snapshots", to=settings.AUTH_USER_MODEL)),
                ("post", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="tracking_snapshots", to="socialmanager.socialmediapost")),
                ("subscription", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="post_tracking_snapshots", to="socialmanager.saassubscription")),
            ],
            options={
                "ordering": ["-captured_at", "-pk"],
                "indexes": [
                    models.Index(fields=["post", "-captured_at"], name="post_track_post_time_idx"),
                    models.Index(fields=["subscription", "-captured_at"], name="post_track_sub_time_idx"),
                ],
            },
        ),
    ]
