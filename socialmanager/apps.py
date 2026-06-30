from django.apps import AppConfig


class SocialmanagerConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "socialmanager"
    verbose_name = "Creana"

    def ready(self):
        import socialmanager.signals  # noqa: F401
