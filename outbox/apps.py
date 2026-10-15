from django.apps import AppConfig


class OutboxConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "outbox"

    def ready(self) -> None:
        from . import checks  # noqa: F401  (registers the system checks)
