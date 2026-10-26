import os

from .base import *

DEBUG = True
# A key for local work only (the compose file sets its own); staging and production must set a real one.
SECRET_KEY = (
    os.environ.get("SECRET_KEY")
    or "dev-only-not-a-real-secret-key-change-in-production"
)
# Mailpit (SMTP) by default; set EMAIL_BACKEND to the database outbox to read codes in the admin.
EMAIL_BACKEND = os.environ.get(
    "EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend"
)
# A key for local work only, so `manage.py` runs in a container that predates the compose variable.
# Never in base.py or production.py: staging and production must set their own (users.E001).
JWT_SIGNING_KEY = (
    os.environ.get("JWT_SIGNING_KEY")
    or "dev-only-signing-key-change-in-production-0123456789abcdef"
)
