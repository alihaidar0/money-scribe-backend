"""What every person starts with, and the icons a custom category may use (``docs/api-fields.md``)."""

# The two accounts made when an email is verified, in the language of the account. In Arabic they are the app's own
# labels of the two kinds, so a row's name and its kind label never differ.
DEFAULT_ACCOUNTS = {
    "en": (("Card", "card"), ("Cash", "cash")),
    "ar": (("بطاقة", "card"), ("نقدًا", "cash")),
}

# The icons the app's picker offers for a custom category. A built-in category has an icon of its own (its key),
# and the response always carries a plain slug, so the app falls back to ``other`` for one it does not know.
CUSTOM_ICONS = (
    "gifts",
    "health",
    "entertainment",
    "shopping",
    "education",
    "travel",
    "subscriptions",
    "other",
)
