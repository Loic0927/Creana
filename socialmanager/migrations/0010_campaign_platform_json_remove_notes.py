from django.db import migrations, models


def forwards_platforms(apps, schema_editor):
    SocialMediaCampaign = apps.get_model("socialmanager", "SocialMediaCampaign")

    for campaign in SocialMediaCampaign.objects.all():
        raw_value = campaign.platform_focus or ""
        platforms = [platform.strip() for platform in raw_value.split(",") if platform.strip()]
        campaign.platform_focus_tags = platforms
        campaign.save(update_fields=["platform_focus_tags"])


class Migration(migrations.Migration):

    dependencies = [
        ("socialmanager", "0009_campaign_status_youtube"),
    ]

    operations = [
        migrations.AddField(
            model_name="socialmediacampaign",
            name="platform_focus_tags",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.RunPython(forwards_platforms, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="socialmediacampaign",
            name="platform_focus",
        ),
        migrations.RenameField(
            model_name="socialmediacampaign",
            old_name="platform_focus_tags",
            new_name="platform_focus",
        ),
        migrations.RemoveField(
            model_name="socialmediacampaign",
            name="notes",
        ),
    ]
