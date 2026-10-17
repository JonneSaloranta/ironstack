"""Email confirmation for new signups (settings.SIGNUP_EMAIL_VERIFICATION,
docs/SECURITY.md "Registration").

A signup made while it's on is marked `email_verification_pending` and
emailed a link; it can't log in until it opens it. A login attempt with
the right password while still pending sends the link again (at most
every RESEND_INTERVAL_SECONDS), so a lost email is never a dead end.
"""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.core.cache import cache
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode

RESEND_INTERVAL_SECONDS = 5 * 60


class EmailVerificationTokenGenerator(PasswordResetTokenGenerator):
    """Its own salt, so a confirmation link's token is never also a
    valid password-reset token. Like a reset token it stops working
    once used (the confirmation changes the state hashed below) and
    after PASSWORD_RESET_TIMEOUT."""

    key_salt = "apps.accounts.email_verification.EmailVerificationTokenGenerator"

    def _make_hash_value(self, user, timestamp):
        return f"{super()._make_hash_value(user, timestamp)}{user.email_verification_pending}"


token_generator = EmailVerificationTokenGenerator()


def is_required(user):
    return settings.SIGNUP_EMAIL_VERIFICATION and user.email_verification_pending


def send_verification_email(request, user):
    """Email `user` a link that confirms their address."""
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = token_generator.make_token(user)
    link = request.build_absolute_uri(
        reverse("verify-email", kwargs={"uidb64": uid, "token": token})
    )
    context = {"user": user, "link": link, "site_name": request.get_host()}
    subject = "".join(
        render_to_string("registration/verify_email_subject.txt", context).splitlines()
    )
    body = render_to_string("registration/verify_email.txt", context)
    send_mail(subject, body, None, [user.email])
    cache.set(_resend_key(user), True, RESEND_INTERVAL_SECONDS)


def resend_if_due(request, user):
    """Send the link again unless one went out in the last few minutes."""
    if cache.get(_resend_key(user)):
        return
    send_verification_email(request, user)


def confirm(uidb64, token):
    """The user whose address this link confirms, now confirmed - or
    None for a link that's invalid, expired or already used."""
    User = get_user_model()
    try:
        user = User.objects.get(pk=force_str(urlsafe_base64_decode(uidb64)))
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        return None
    if not user.email_verification_pending or not token_generator.check_token(user, token):
        return None
    user.email_verification_pending = False
    user.save(update_fields=["email_verification_pending"])
    return user


def _resend_key(user):
    return f"email-verification-sent:{user.pk}"
