import os

from django.utils.csp import CSP

from .base import *

DEBUG = False
SECRET_KEY = os.environ["SECRET_KEY"]  # required — fail fast if missing
# Staging runs these settings too and sets APP_ENVIRONMENT=staging; anything unset is production.
APP_ENVIRONMENT = os.environ.get("APP_ENVIRONMENT", "production")

# Behind a TLS-terminating proxy / load balancer
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = os.environ.get("SECURE_SSL_REDIRECT", "True").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = int(
    os.environ.get("SECURE_HSTS_SECONDS", "0")
)  # raise once HTTPS is confirmed
SECURE_CONTENT_TYPE_NOSNIFF = True

# Content Security Policy — starts in report-only mode (violations are reported
# to the browser console, nothing is blocked). Tune it for the project's assets,
# then switch the setting name to SECURE_CSP to enforce it.
SECURE_CSP_REPORT_ONLY = {
    "default-src": [CSP.SELF],
    "img-src": [CSP.SELF, "data:"],
    "object-src": [CSP.NONE],
    "base-uri": [CSP.SELF],
    "frame-ancestors": [CSP.NONE],
}
