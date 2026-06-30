from django.db import migrations, models


def replace_linkedin_with_reddit(apps, schema_editor):
    SocialMediaCampaign = apps.get_model("socialmanager", "SocialMediaCampaign")
    SocialMediaPost = apps.get_model("socialmanager", "SocialMediaPost")
    AISuggestionHistory = apps.get_model("socialmanager", "AISuggestionHistory")

    SocialMediaPost.objects.filter(platform="linkedin").update(platform="reddit")
    AISuggestionHistory.objects.filter(platform="linkedin").update(platform="reddit")
    replace_campaign_platform(SocialMediaCampaign, "LinkedIn", "Reddit")


def replace_reddit_with_linkedin(apps, schema_editor):
    SocialMediaCampaign = apps.get_model("socialmanager", "SocialMediaCampaign")
    SocialMediaPost = apps.get_model("socialmanager", "SocialMediaPost")
    AISuggestionHistory = apps.get_model("socialmanager", "AISuggestionHistory")

    SocialMediaPost.objects.filter(platform="reddit").update(platform="linkedin")
    AISuggestionHistory.objects.filter(platform="reddit").update(platform="linkedin")
    replace_campaign_platform(SocialMediaCampaign, "Reddit", "LinkedIn")


def replace_campaign_platform(SocialMediaCampaign, old_label, new_label):
    for campaign in SocialMediaCampaign.objects.all().only("pk", "platform_focus"):
        platform_focus = campaign.platform_focus
        if not isinstance(platform_focus, list):
            continue

        changed = False
        next_platform_focus = []
        for platform in platform_focus:
            if str(platform).strip().lower() == old_label.lower():
                next_platform_focus.append(new_label)
                changed = True
            else:
                next_platform_focus.append(platform)

        if changed:
            campaign.platform_focus = next_platform_focus
            campaign.save(update_fields=["platform_focus"])


class Migration(migrations.Migration):
    dependencies = [
        ("socialmanager", "0016_socialmediapost_visibility"),
    ]

    operations = [
        migrations.RunPython(replace_linkedin_with_reddit, replace_reddit_with_linkedin),
        migrations.AlterField(
            model_name="aisuggestionhistory",
            name="platform",
            field=models.CharField(
                choices=[
                    ("tiktok", "TikTok"),
                    ("instagram", "Instagram"),
                    ("youtube", "YouTube"),
                    ("facebook", "Facebook"),
                    ("x", "X / Twitter"),
                    ("reddit", "Reddit"),
                ],
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="socialmediapost",
            name="platform",
            field=models.CharField(
                choices=[
                    ("tiktok", "TikTok"),
                    ("instagram", "Instagram"),
                    ("youtube", "YouTube"),
                    ("facebook", "Facebook"),
                    ("x", "X / Twitter"),
                    ("reddit", "Reddit"),
                ],
                max_length=20,
            ),
        ),
    ]
