from django.db import migrations, models


def copy_existing_campaign_posts(apps, schema_editor):
    SocialMediaPost = apps.get_model("socialmanager", "SocialMediaPost")

    for post in SocialMediaPost.objects.exclude(campaign_id=None).iterator():
        post.campaign.campaign_posts.add(post)


class Migration(migrations.Migration):

    dependencies = [
        ("socialmanager", "0011_postview"),
    ]

    operations = [
        migrations.AddField(
            model_name="socialmediacampaign",
            name="campaign_posts",
            field=models.ManyToManyField(
                blank=True,
                related_name="campaign_groups",
                to="socialmanager.socialmediapost",
            ),
        ),
        migrations.RunPython(copy_existing_campaign_posts, migrations.RunPython.noop),
    ]
