from .base import *

DEBUG = False
SECRET_KEY = "test-only-not-a-real-secret-key-0123456789abcdef0123456789"
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]  # fast tests only
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
CELERY_TASK_ALWAYS_EAGER = True
# No collectstatic in tests: plain storage instead of the hashed manifest.
STORAGES = {
    **STORAGES,
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
# In-memory cache: the lock and limit counters need no Redis in tests.
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
JWT_SIGNING_KEY = "test-only-signing-key-0123456789abcdef0123456789abcdef"
# No outbound calls in tests: the breach check is exercised with a stand-in for the service.
PWNED_PASSWORD_CHECK = False
# The device-key rules as staging and production have them, whatever the dev container exports (it switches
# attestation off for the emulator). A test that needs another value sets it itself.
DEVICE_KEY_REQUIRE_ATTESTATION = True
DEVICE_KEY_ANDROID_PACKAGE = ""
DEVICE_KEY_ANDROID_CERT_SHA256 = []
