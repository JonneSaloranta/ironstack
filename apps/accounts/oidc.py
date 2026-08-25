"""Authentik single sign-on — docs/SECURITY.md "Single sign-on
(Authentik / OIDC)". Only ever instantiated once AUTHENTIK_ENABLED is
True (config.settings.base appends this backend's dotted path to
AUTHENTICATION_BACKENDS only in that case), so nothing here runs on an
instance that hasn't configured Authentik.

mozilla_django_oidc.auth.OIDCAuthenticationBackend already does the
hard, security-critical part (authorization-code exchange, id_token
signature verification, nonce/PKCE checks) — this subclass only
customizes what happens once a set of verified claims comes back: how
a User is matched/created from them, and what gets written onto it.
"""

from django.conf import settings
from mozilla_django_oidc.auth import OIDCAuthenticationBackend


class IronStackOIDCAuthenticationBackend(OIDCAuthenticationBackend):
    """filter_users_by_claims (inherited, unchanged) already matches an
    existing account by email — the same identity a user would use to
    sign up or reset their password locally, so a local account and
    its owner's Authentik identity link up automatically the first
    time they use "Log in with Authentik", no separate account-linking
    step. create_user below only ever runs when that lookup finds
    nothing, per the answered product decision to auto-provision
    rather than require a pre-existing account."""

    def verify_claims(self, claims):
        # Base implementation just checks the "email" claim is present
        # (mozilla_django_oidc.auth.OIDCAuthenticationBackend.verify_claims,
        # since OIDC_RP_SCOPES includes "email") — this adds an
        # optional group-membership requirement on top, so having *any*
        # Authentik account on a shared instance (this app might not be
        # the only thing that Authentik authenticates for) isn't by
        # itself enough to reach IronStack. AUTHENTIK_REQUIRED_GROUP
        # unset (the default) skips this check entirely — access
        # control then rests solely on whatever's configured on the
        # Authentik side (an Application access-policy binding, e.g.
        # requiring the same group), which docs/SECURITY.md recommends
        # doing either way: this is defense in depth, not a substitute
        # for it, since a claim is only as trustworthy as the id_token
        # it came in (verified above, before this ever runs) — but
        # doesn't depend on that Authentik-side policy being configured
        # correctly, or at all.
        if not super().verify_claims(claims):
            return False
        required_group = settings.AUTHENTIK_REQUIRED_GROUP
        if required_group and required_group not in claims.get("groups", []):
            return False
        return True

    def get_username(self, claims):
        # Prefer Authentik's own preferred_username claim (its
        # username, human-readable) over the parent class's default —
        # a SHA1-of-email hash meant for a provider that has no
        # separate username concept at all, which Authentik does have.
        # Falls back to the default hash only if that claim is somehow
        # missing, so a user is never left without a username entirely.
        preferred_username = claims.get("preferred_username")
        if not preferred_username:
            return super().get_username(claims)
        username = preferred_username
        User = self.UserModel
        # preferred_username collides with an existing *local* account
        # that filter_users_by_claims' email match didn't already
        # catch (a different email, or Authentik's account has none
        # set) — suffix with a short counter rather than erroring the
        # whole login out, the same "just make it work" spirit as
        # Django's own admin username defaults.
        suffix = 1
        while User.objects.filter(username=username).exists():
            suffix += 1
            username = f"{preferred_username}{suffix}"
        return username

    def create_user(self, claims):
        user = super().create_user(claims)
        self._apply_claims(user, claims)
        # is_sso_user (apps.accounts.models.User) — set once, at
        # creation, so profile/admin UI can show "signed up via
        # Authentik" without having to infer it from having no usable
        # password (an admin could still set one later without that
        # changing this record of how the account originated).
        user.is_sso_user = True
        user.save(update_fields=["first_name", "is_sso_user"])
        return user

    def update_user(self, user, claims):
        # Runs on every subsequent Authentik login for an
        # already-matched user, local-password-created accounts
        # included (see this class's own docstring) — keeps
        # name/is_sso_user in sync with Authentik's own claims each
        # time, the same way a freshly created account would already
        # have them.
        self._apply_claims(user, claims)
        user.is_sso_user = True
        user.save(update_fields=["first_name", "is_sso_user"])
        return user

    @staticmethod
    def _apply_claims(user, claims):
        # Authentik has no first/last name distinction of its own —
        # its default OpenID 'profile' scope mapping sends the user's
        # single full-name field as both "name" and "given_name" (see
        # that mapping's own expression in the Authentik admin UI).
        # Stored on first_name alone rather than split on whitespace
        # into first_name/last_name, which would mangle any name that
        # isn't exactly "First Last" (a middle name, a single-word
        # name, ...) for no real benefit — nothing in this app treats
        # last_name as meaningful on its own (see User.public_display_name).
        name = claims.get("name") or claims.get("given_name")
        if name:
            user.first_name = name
