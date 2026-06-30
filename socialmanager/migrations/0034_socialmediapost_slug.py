from django.db import migrations, models
from django.utils.text import slugify


def populate_post_slugs(apps, schema_editor):
    SocialMediaPost = apps.get_model("socialmanager", "SocialMediaPost")
    for post in SocialMediaPost.objects.only("pk", "title").iterator():
        post.slug = slugify(post.title) or f"post-{post.pk}"
        post.save(update_fields=["slug"])


class Migration(migrations.Migration):
    dependencies = [
        ("socialmanager", "0033_userprofile_avatar_thumbnail"),
    ]

    operations = [
        migrations.AddField(
            model_name="socialmediapost",
            name="slug",
            field=models.SlugField(blank=True, db_index=True, max_length=150),
        ),
        migrations.RunPython(populate_post_slugs, migrations.RunPython.noop),
    ]
