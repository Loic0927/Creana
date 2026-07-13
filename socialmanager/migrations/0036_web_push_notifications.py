from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("socialmanager", "0035_videoanalysis")]

    operations = [
        migrations.AddField(model_name="usersettings", name="enable_push_notifications", field=models.BooleanField(default=True)),
        migrations.AddField(model_name="usersettings", name="push_likes", field=models.BooleanField(default=True)),
        migrations.AddField(model_name="usersettings", name="push_comments", field=models.BooleanField(default=True)),
        migrations.AddField(model_name="usersettings", name="push_replies", field=models.BooleanField(default=True)),
        migrations.AddField(model_name="usersettings", name="push_shares", field=models.BooleanField(default=True)),
        migrations.AddField(model_name="usersettings", name="push_follows", field=models.BooleanField(default=True)),
        migrations.AddField(model_name="usersettings", name="push_announcements", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="usersettings", name="push_scheduled_post_published", field=models.BooleanField(default=True)),
        migrations.AddField(model_name="usersettings", name="push_scheduled_post_failed", field=models.BooleanField(default=True)),
        migrations.CreateModel(
            name="PushSubscription",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("endpoint", models.URLField(max_length=2000, unique=True)),
                ("p256dh_key", models.CharField(max_length=255)),
                ("auth_key", models.CharField(max_length=255)),
                ("user_agent", models.TextField(blank=True)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="push_subscriptions", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-updated_at"]},
        ),
    ]
