from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from socialmanager.account_identity import get_duplicate_email_groups


class Command(BaseCommand):
    help = "List duplicate User emails case-insensitively without modifying any accounts."

    def handle(self, *args, **options):
        User = get_user_model()
        duplicate_groups = list(get_duplicate_email_groups())

        if not duplicate_groups:
            self.stdout.write(self.style.SUCCESS("No duplicate User emails found."))
            return

        self.stdout.write(self.style.WARNING("Duplicate User emails found. No accounts were changed."))
        for group in duplicate_groups:
            normalized_email = group["normalized_email"]
            self.stdout.write("")
            self.stdout.write(f"Email: {normalized_email} ({group['user_count']} users)")
            users = (
                User.objects.filter(email__iexact=normalized_email)
                .order_by("id")
                .values("id", "username", "email", "is_active", "is_staff", "is_superuser", "date_joined", "last_login")
            )
            for user in users:
                self.stdout.write(
                    "  "
                    f"id={user['id']} "
                    f"username={user['username']} "
                    f"email={user['email']} "
                    f"active={user['is_active']} "
                    f"staff={user['is_staff']} "
                    f"superuser={user['is_superuser']} "
                    f"date_joined={user['date_joined']} "
                    f"last_login={user['last_login']}"
                )

        self.stdout.write("")
        self.stdout.write(
            "Review these users manually in Django admin or with a data migration before enforcing a database-level unique constraint."
        )
