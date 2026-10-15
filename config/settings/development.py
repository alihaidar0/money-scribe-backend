import os

from .base import *

DEBUG = True
# Mailpit (SMTP) by default; set EMAIL_BACKEND to the database outbox to read codes in the admin.
EMAIL_BACKEND = os.environ.get(
    "EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend"
)
