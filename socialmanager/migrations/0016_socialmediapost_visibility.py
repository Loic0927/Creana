from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("socialmanager", "0015_rename_socialmanag_post_id_96b7f9_idx_socialmanag_post_id_6cd86f_idx_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="socialmediapost",
            name="visibility",
            field=models.CharField(
                choices=[("public", "Public"), ("private", "Private")],
                default="public",
                max_length=20,
            ),
        ),
    ]
