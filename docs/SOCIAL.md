# Friends, groups, and messaging

## Goal

A small, utility social layer for a self-hosted instance shared by a
household, gym group, or friend group: find and friend other accounts
on this instance, form groups, and message a friend or a group
directly. Friends/groups/messaging were explicitly out of scope
through v1 (`docs/PRODUCT_REQUIREMENTS.md` said "do not implement
unless explicitly requested") — it has now been requested, so this
document, and the `apps/social` app it describes, supersede that line
the same way `docs/NUTRITION.md` did for nutrition tracking.

## Visual style and scope

`docs/UI.md` "Visual style": *"The interface should feel like a
practical training tool rather than a social media application."*
`apps/social` follows that deliberately: no feed, no likes/reactions,
no gamification, and — the one item still explicitly out of scope,
`docs/PRODUCT_REQUIREMENTS.md` — no **public profiles**. There is no
browsable profile page for another user; the only way to find someone
is a plain username search among this instance's own accounts
(`apps/social/views_friends.py:friend_search`), and the only things a
user can do with another account are friend them, invite them to a
group, message them (once friends), or block them. Search excludes
anyone blocked in either direction, not just anyone already friends —
sending a request to a blocked user would fail anyway
(`services.send_friend_request`), so the point of having blocked them
is not needing to see them turn up as a result at all.

## Domain model (`apps/social/models.py`)

- **`FriendRequest`** — one user asking another. `PENDING` →
  `ACCEPTED`/`DECLINED`. If B already has a pending request to A and A
  sends one to B, the service (`services.send_friend_request`)
  auto-accepts the existing one instead of creating a second, dueling
  row.
- **`Friendship`** — a confirmed, mutual relationship, created only by
  `services.accept_friend_request`. Always stored as
  `(user_low, user_high)` ordered by primary key, so the same
  friendship is never stored twice in either direction.
- **`Block`** — one-directional (`blocker` blocked `blocked`), but
  every check in `services` (friend requests, direct messages, direct
  group invites) treats *either* side blocking as enough to stop 1:1
  interaction — the blocked side doesn't get to keep interacting just
  because they didn't initiate the block.
- **`Group`** / **`GroupMembership`** — a group has an owner, and each
  member has a role (`owner`/`admin`/`member`). `owner` on `Group`
  itself is `SET_NULL`, not `CASCADE`: deleting the account that
  created a group must not delete the group and its message history
  out from under everyone still in it — the same "history stays
  trustworthy" principle CLAUDE.md states for workout data, applied
  here. Ownership is transferable via role, not tied to that FK
  surviving. `GroupMembership.last_read_at` doubles as this member's
  per-group read-state for the unread-message badge, rather than a
  separate per-message read table — defaults to "now" at join time
  rather than being nullable, so "unread" is always one comparison
  (`GroupMessage.created_at > membership.last_read_at`) with no
  separate "never read anything" case to handle everywhere it's
  queried, and a message sent before someone joined is correctly
  never "unread" for them.
- **`GroupInvite`** — a direct, in-app invite to one specific
  *friend*, distinct from the group's own link (below) — `services.
  invite_to_group` requires the two to actually be friends, enforced
  server-side (not just by `group_detail.html`'s friends-only
  dropdown), so a crafted request can't invite an arbitrary account.
  This is also what `User.allow_group_invites` actually gates — the
  link itself can't be gated per-recipient, since whoever holds it
  can already use it.
- **`DirectMessage`** / **`GroupMessage`** — text-only, immutable (no
  editing, no delete flag): the confirmed scope for this first
  version. A block or a member leaving never retroactively hides
  message history that already happened — same principle as `Group.
  owner` above.

## The two privacy settings

Both live on `User` (`apps/accounts/models.py`), default **on** (an
opt-*out*, not opt-in), and are asked once during onboarding
(`apps.accounts.forms.OnboardingForm`) the same way `unit_system`/
`timezone` already are, then editable anytime from Profile
(`ProfileForm`):

- **`allow_friend_requests`** — off stops anyone else from sending a
  friend request to this account at all. Doesn't retroactively affect
  requests or friendships that already exist.
- **`allow_group_invites`** — off stops a group member from inviting
  this account directly. It does *not* stop the account from using a
  group's invite link itself — that's a deliberate, self-initiated
  join, not something being done *to* the account.

Neither setting defaults off the way `User.show_gravatar` does:
`show_gravatar` defaults off because turning it on causes an outbound
request to a third party (gravatar.com) the moment it's enabled.
Neither social setting causes anything to happen by itself — they only
gate whether *another* user on this instance can start something with
this account — so opt-out is the friendlier default.

## Groups: invite link vs. direct invite

A group can be joined two ways, and both exist because they answer
different questions:

- **The link** (`/group/invite/<code>/`) answers "how do I let anyone
  I hand this to join". `Group.invite_enabled` defaults **off** — an
  owner/admin has to deliberately turn it on
  (`services.enable_invite`), which also generates the code the first
  time (`services.disable_invite` leaves the code in place so
  re-enabling doesn't silently change everyone's existing link;
  `services.regenerate_invite_code` deliberately replaces it, for "I
  shared this with the wrong person" recovery).
- **The direct invite** (`GroupInvite`) answers "how do I add this
  *specific* person" — any current member can invite one of their own
  friends (`services.invite_to_group`), gated by the invited person's
  `allow_group_invites` setting, unlike the link.

### Invite code shape and threat model

`apps/social/crypto.generate_invite_code()` — 10 characters from a
31-symbol alphabet (`2-9`, `A-Z` minus `I`/`L`/`O`, excluded because
they're ambiguous when read aloud or hand-copied), ≈8.2×10¹⁴ possible
codes. Stored in the clear on `Group.invite_code`: unlike
`apps.api.crypto`'s API key secrets, this isn't hashed, because there's
nothing to verify it *against* — a group invite grants access to a
group's messages, not to an account, so it doesn't need
password-equivalent secrecy-at-rest treatment, the same way a
Discord/Slack invite code is just looked up directly. The real defense
is entropy plus a second, cheap layer: `/group/invite/<code>/`
(`apps/social/views_groups.py:group_invite_join`) throttles failed
lookups per client IP (`apps.core.request.client_ip`, the same helper
`apps.accounts.forms`'s login/password-reset rate limiting uses) —
not because the code is guessable, but because this codebase treats
that cheap second layer as the norm wherever a secret is looked up by
an anonymous-ish request (`docs/SECURITY.md`).

The join page is a GET-then-confirm flow, not an instant join on
first load: opening the link only shows the group's name/description
and a "Join" button; actually joining requires a POST. This matters
because a link preview fetch (Slack/Discord/iMessage unfurling a
shared link) is a GET request from infrastructure that isn't the
person who'll actually use the link — an instant-join-on-GET design
would let *that* fetch silently consume the invite.

`group_invite_join` is also the one view in `apps/social` deliberately
*not* behind `@login_required`, unlike every other one — someone
clicking a shared invite link is very often not logged in yet at all,
which is the whole point of a link anyone can open. An anonymous
request still sees the group's name and a "log in to join" prompt
instead of bouncing straight to the login page with no idea what the
link was even for; only the POST that actually joins requires being
authenticated (checked in the view itself, not by a decorator).

## Blocking and shared groups

Blocking someone removes any existing friendship and declines any
pending friend request between the two (either direction), and stops
new direct messages and direct group invites both ways. It
deliberately does **not** remove either user from a group they're
already both in — that's left to the group's own owner/admin
(`services.remove_group_member`), the same way, say, blocking someone
on a group chat platform doesn't silently kick them from every shared
server. A block is a statement about a 1:1 relationship, not a
delegation of moderation authority over every group the two happen to
share.

## Messaging: no separate "Conversation" model, no websockets

`DirectMessage`/`GroupMessage` share an abstract `BaseMessage`
(`sender`, `body`, `created_at`) rather than a generic polymorphic
`Conversation` model — normalized, but without the complexity a
message system that had to support more than "a friend" and "a group"
as the two possible threads would need.

Real-time-ish delivery is HTMX polling (`hx-trigger="load, every 4s"`
on the message-list container in `templates/social/message_thread.html`
/ `group_thread.html`), not WebSockets or Server-Sent Events —
consistent with CLAUDE.md's "no SPA framework without a strong
architectural reason" and this app's existing all-HTMX interaction
model. Sending a message is a normal `hx-post` into the *same*
container the poll targets, so there's one render path for both "a
message just arrived" and "I just sent one" — not two subtly
different code paths for the sender's own message appearing. The
send-form itself lives outside the polled container (so the poll
can't clobber text a user is still typing) and clears itself after a
successful send via `hx-on::after-request="this.reset()"`.

The polling trigger includes a `[document.visibilityState=='visible']`
filter — no point spending battery/data polling a chat nobody's
currently looking at on a backgrounded tab, mobile-first (`docs/UI.md`)
being a real constraint here, not just a workout-logging one. The
polled container also carries `aria-live="polite"`, so a screen reader
announces a newly-arrived message the same poll tick sighted users see
it appear, instead of only on the next full page load. Each fragment
caps at the most recent 100 messages
(`apps.social.views_messages.THREAD_MESSAGE_LIMIT`) — with no
pagination UI (not asked for, and a "smallest coherent implementation"
choice), re-querying and re-rendering a conversation's *entire*
history on every 4-second poll forever would only get slower the
longer two people keep talking.

**Unread counts are one query, not one query per group.** An earlier
version of `apps.social.services.unread_group_message_count` looped
over a user's `GroupMembership` rows in Python, issuing one `.count()`
query per group — harmless for a single group, but that function is
also what `apps.social.context_processors.social_badge` calls (via
`has_unread_group_messages`) on *every page load*, for every logged-in
user, site-wide, with no group filter — meaning a user in 20 groups
paid 20 extra queries on every single request anywhere in the app,
forever, whether or not they ever looked at Friends & groups. Rewritten
as a single query joining `GroupMessage` straight to its sender's
`GroupMembership` row and comparing `created_at` to that row's own
`last_read_at` via `F()` — one query regardless of how many groups a
user belongs to (`GroupMessageServiceTests` in the test suite pins
this with `assertNumQueries(1)`, the same discipline `apps.nutrition`'s
recipe-list test already applies elsewhere in this codebase). The same
fix gave `apps.social.views_groups.group_list` and `apps.social.
views_messages.message_list` a `unread_group_message_counts_by_group`
helper — one query for *every* group shown on the page, instead of
one call per row in a Python loop.

`social_badge` itself still costs four queries on every page for a
logged-in user (one each for pending friend requests, pending group
invites, unread direct messages, unread group messages — four
different tables, no single query answers "does any of these have a
match" without raw SQL) — accepted as a reasonable, flat, O(1) cost of
the feature rather than something to eliminate, the same call this
codebase already made for `apps.core.context_processors.seo`'s own
one-query addition to every page. Each of the four uses a dedicated
`has_*`/`.exists()` variant (`has_pending_friend_requests`,
`has_pending_group_invites`, `has_unread_direct_messages`,
`has_unread_group_messages`) rather than the `*_count` functions used
elsewhere for an actual number — `.exists()` stops at the first
matching row instead of counting every one, and the badge only ever
needed a boolean.

## Moderation

No bespoke reporting/moderation UI — Django admin
(`apps/social/admin.py`) is the moderation tool, the same as the rest
of this project's admin-configurable settings: an operator can delete
an abusive `Group`/`GroupMessage`/`DirectMessage`/`GroupMembership`
row directly. Building a reporting workflow wasn't asked for and would
be scope creep beyond what this feature needs for a small,
self-hosted, operator-trusted instance.
