from django.apps import AppConfig


class LedgerConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "ledger"

    def ready(self) -> None:
        # The accounts a new person starts with are made when the email is verified. The users app announces that
        # with a signal and never imports this app (dependencies point one way, ADR 0005).
        from users.signals import email_verified

        from .signals import create_default_accounts

        email_verified.connect(
            create_default_accounts, dispatch_uid="ledger-default-accounts"
        )
