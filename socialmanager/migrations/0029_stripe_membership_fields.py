from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("socialmanager", "0028_subscriptionmembership_is_active_member"),
    ]

    operations = [
        migrations.AlterField(
            model_name="subscriptionmembership",
            name="is_active_member",
            field=models.BooleanField(
                default=False,
                help_text="Allows this user to access member-only AI tools.",
                verbose_name="Active member",
            ),
        ),
        migrations.AddField(
            model_name="subscriptionmembership",
            name="stripe_customer_id",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="subscriptionmembership",
            name="stripe_subscription_id",
            field=models.CharField(blank=True, db_index=True, max_length=255),
        ),
        migrations.AddField(
            model_name="subscriptionmembership",
            name="stripe_checkout_session_id",
            field=models.CharField(blank=True, max_length=255),
        ),
    ]
