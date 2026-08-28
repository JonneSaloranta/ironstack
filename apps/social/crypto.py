"""Group invite code generation — isolated the same way
apps.api.crypto isolates API secret generation, but simpler: an invite
code grants access to one group's messages, not a whole account, so it
doesn't need password-equivalent secrecy-at-rest treatment. It's
stored in the clear on Group.invite_code (there's nothing to hash
against) — the same way a Discord/Slack invite code is just looked up
directly, not verified against a stored digest.
"""

import secrets

# Excludes 0/O and 1/I/L — ambiguous when read aloud or hand-copied,
# the same concern apps.api.crypto's KEY_PREFIX comment doesn't have
# to deal with (an API key is pasted, never retyped by hand).
_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"  # 31 symbols
CODE_LENGTH = 10  # 31**10 ≈ 8.2×10^14 combinations — see docs/SOCIAL.md


def generate_invite_code():
    return "".join(secrets.choice(_ALPHABET) for _ in range(CODE_LENGTH))
