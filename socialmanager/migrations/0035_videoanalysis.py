from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("socialmanager", "0034_socialmediapost_slug"),
    ]

    operations = [
        migrations.CreateModel(
            name="VideoAnalysis",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("source_object_name", models.CharField(max_length=1024)),
                ("status", models.CharField(choices=[("processing", "Processing"), ("succeeded", "Succeeded"), ("failed", "Failed")], default="processing", max_length=20)),
                ("result", models.JSONField(blank=True, default=dict)),
                ("creator_guidance", models.JSONField(blank=True, default=dict)),
                ("guidance_language", models.CharField(blank=True, max_length=30)),
                ("error_message", models.CharField(blank=True, max_length=255)),
                ("analyzed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("post", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="video_analysis", to="socialmanager.socialmediapost")),
            ],
            options={"ordering": ["-updated_at"]},
        ),
    ]
