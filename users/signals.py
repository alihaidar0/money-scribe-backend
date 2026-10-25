"""What the users app announces to the apps that depend on it (ADR 0005).

``email_verified`` is sent, inside the transaction that verifies an email, with the ``user`` whose address was just
accepted for the first time. A receiver that fails rolls the verification back, so what a new account needs (its
first accounts, for example) exists or the person verifies again.
"""

from django.dispatch import Signal

email_verified = Signal()
