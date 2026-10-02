# AI assistant

`apps.assistant` is an optional chat assistant that helps a user with diet
plans, workout programs and questions about their own progress. It reads the
user's data through tools, and it can *propose* a diet plan or a program as a
card the user accepts or dismisses. It never changes anything by itself.

Everything here is off until an operator configures a provider or a user adds
their own API key, and until each user explicitly turns the assistant on.

## Product rules it follows

- **The user stays in control** (CLAUDE.md "Product principle"). The only tools
  that lead to a write are `propose_diet_plan` and `propose_program`. They
  store an `AssistantProposal`, and only the user's "Create" button turns one
  into a real plan. The plan is created **inactive** through the same domain
  services an import uses (`apps.nutrition.services.create_diet_plan`,
  `apps.programs.services.import_program`), so the user reviews and edits it
  like any other plan.
- **History stays trustworthy.** No tool touches workout sessions or sets at
  all. Accepting a program creates a new `Program`, and completed sessions
  snapshot their own data anyway (docs/ARCHITECTURE.md "Historical data rule").
- **Explicit opt-in.** `AssistantPreference.consented_at` stays empty until the
  user has read what is sent where (`templates/assistant/_consent_notice.html`)
  and pressed "Turn on". Until then none of their data is ever sent to a model.

## Providers

`apps/assistant/providers.py` puts each provider behind one small interface:
`run_turn(system, tools, history, on_text) -> TurnResult`, plus the provider's
own wire format for a user message and a batch of tool results.

| Provider | When | Notes |
|---|---|---|
| `AnthropicProvider` (Claude) | `ASSISTANT_PROVIDER=anthropic` (default) with `ASSISTANT_API_KEY`, or any user's own key | Official `anthropic` SDK, streaming. Default model `claude-opus-5-5` with adaptive thinking and `output_config.effort` (`ASSISTANT_EFFORT`, default `medium`). Prompt caching on (the system prompt and tools are constant, and history is append-only). Uses the server-side refusal fallback (`fallbacks: "default"`) on models that support it. |
| `OllamaProvider` | `ASSISTANT_PROVIDER=ollama` | A self-hosted Ollama server's `/api/chat` with tools, through `requests`. Nothing leaves the operator's network and no key is needed. Pick a model that supports tool calling (`ASSISTANT_OLLAMA_MODEL`, default `qwen3:8b`). Small local models write noticeably weaker plans than Claude. |

A conversation records its `provider`, `model` and `key_source` when it starts
and never switches: its stored history is in that provider's format.

## Who may use it, and with which key

`services.access_for(user)` decides:

1. The admin can turn the whole assistant off (`AssistantSettings.enabled`).
2. A user's **own key** (Profile → AI assistant → Settings) always wins. It is
   used only for their own conversations and billed to their Anthropic account.
3. Otherwise the **instance's provider** can be used if
   `AssistantSettings.shared_key_access` allows it: nobody, staff only,
   selected users, or everyone. Staff set this in Profile → Administration →
   AI assistant (`assistant:admin-settings`), which also shows token usage for
   the last 30 days.

**Limits on the instance's provider:** `AssistantSettings.daily_token_limit`
tokens per user per day (input + output, `UsageRecord`; 0 = no limit). A user's
own key is never limited. Everyone, whichever key they use, gets at most one
reply in progress at a time, a 4,000-character message limit, 40 user
messages per conversation, and at most 12 model turns per reply
(`services.MAX_*`).

## How a reply is written

A reply often takes longer than gunicorn's 30-second worker timeout (the
model thinks, calls a few tools, then writes), so it doesn't run in the web
request:

1. `services.start_conversation` / `send_message` stores the user's message
   and a `PENDING` assistant message.
2. The **`assistant-worker`** service (`manage.py assistant_worker`, a
   sleep-and-poll loop like the other schedulers) claims it with
   `SELECT … FOR UPDATE SKIP LOCKED` and runs `services.process_message`: the
   tool loop. Several workers can run at once
   (`docker compose up -d --scale assistant-worker=2`).
3. While the reply streams in, the worker writes the partial text and a short
   activity note ("Looking at your workouts…") to the message about twice a
   second. The page polls only that one bubble with HTMX
   (`assistant:message-fragment`, `hx-trigger="every 1s"`) until it's done.
4. Finished: `DONE`, with `raw` holding every API message the reply exchanged.
   Failed: `ERROR` with a plain-language error and an empty `raw`.

`ASSISTANT_RUN_INLINE=true` runs step 2 inside the request instead, which is
handy for `runserver` without the worker. Never use it in production. A reply
nobody claims for 20 seconds shows a "the worker may not be running" hint, and
a `RUNNING` reply untouched for 10 minutes (a crashed worker) is marked failed
on the worker's next loop.

**History is append-only.** Each request replays every `DONE` message's `raw`
in order. Rows aren't edited after they finish, and a failed reply contributes
nothing. That keeps Claude's thinking blocks valid across turns. The user's
context (name, language, units, start date —
`prompts.conversation_context`) goes into the conversation's first message,
not the system prompt, so the system prompt and tools stay identical for
every request and user, and cacheable.

## Tools

`apps/assistant/tools.py`. Every tool runs as the conversation's user and reads
only what that user can already see in the app, through the same visibility
queries the views use:

| Tool | Reads |
|---|---|
| `get_user_overview` | date, units, height, latest body weight, nutrition profile, goal and target, active diet plan |
| `get_workout_history` | completed sessions with working sets |
| `get_personal_records` | max-weight and estimated-1RM records |
| `get_body_measurements`, `get_activities` | measurements and activities over a period |
| `get_nutrition_overview` | median intake and recently logged days |
| `list_programs`, `get_program` | own, coach-shared and built-in programs |
| `list_diet_plans`, `get_diet_plan` | own diet plans |
| `search_exercises`, `search_foods`, `search_recipes`, `list_meal_slots` | the libraries a proposal draws from |
| `propose_diet_plan`, `propose_program` | — (records a proposal) |

Tool inputs are untrusted. Each tool validates its arguments, and a mistake
goes back to the model as an error result it can correct. It never becomes an
exception. Results are compact JSON in canonical units. The system prompt
(`prompts.SYSTEM_PROMPT`) tells the model which units the user reads, to reply
in the user's language, and to suggest a professional for medical topics.

## Proposals

`apps/assistant/proposals.py` validates a proposal twice: when the model
proposes it (so it can fix its own errors) and again on accept (the user's
library may have changed). The stored payload is the normalized validator
output.

- **Diet plan:** each item is an existing food (`food_id`), a recipe
  (`recipe_id`, quantity in servings) or a `new_food` with nutrition per serving.
  A new food is added to the user's own library on accept and labelled in the
  card as estimated. Its calories must roughly match its macros (4/4/9 kcal/g,
  ±35 %), which is a cheap guard against invented numbers. Meal slots must be
  ones the user has. The card shows the targets next to what the meals
  actually add up to.
- **Program:** exercises must come from the user's visible library. Rep
  ranges, set counts, RPE and progression method are range-checked, and the
  progression method defaults to double progression.

Names in a payload are stored canonical and translated when the card is
rendered.

## Security and privacy

- A user's own key is encrypted at rest (`EncryptedTextField` with
  `ASSISTANT_ENCRYPTION_KEY`, which is derived from `DJANGO_SECRET_KEY` when
  unset). It is never sent back to the browser: the form doesn't re-render it,
  and pages show only `api_key_hint` ("…1a2b").
- Model output is rendered with `formatting.render_reply`: HTML-escaped first,
  then only bold, code, lists and paragraphs. No links or images, because a
  reply can echo whatever ended up in its context.
- Every view scopes conversations, messages and proposals to `request.user`
  (404 otherwise). Tests cover each.
- The Django admin shows conversation metadata and usage, never message
  content.
- See docs/SECURITY.md "AI assistant" for what an operator should tell users.

## Configuration

| Setting | Default | Meaning |
|---|---|---|
| `ASSISTANT_PROVIDER` | `anthropic` | `anthropic` or `ollama` |
| `ASSISTANT_API_KEY` | — | the instance's Anthropic key (usage billed to the operator) |
| `ASSISTANT_MODEL` | `claude-opus-5-5` | Claude model, also used for users' own keys |
| `ASSISTANT_EFFORT` | `medium` | `low`…`max`; more thinking costs more tokens |
| `ASSISTANT_REFUSAL_FALLBACK` | `true` | re-run a classifier-declined request on Anthropic's fallback model (beta) |
| `ASSISTANT_OLLAMA_URL` / `ASSISTANT_OLLAMA_MODEL` | `http://ollama:11434` / `qwen3:8b` | Ollama server and model |
| `ASSISTANT_ENCRYPTION_KEY` | derived | encrypts users' saved keys (`manage.py generate_assistant_encryption_key`) |
| `ASSISTANT_RUN_INLINE` | `false` | write replies in the request (development only) |

Entry points in the UI: Profile → AI assistant; an "Ask the assistant" button
on the diet plan list and the program list (shown only to users who can use
it); Profile → Administration → AI assistant for staff.

## Not built (yet)

- No REST API endpoints for the assistant.
- No Server-Sent Events. Polling one bubble a second is simple and works
  through the existing nginx config, and the text still appears while it's
  written.
- Proposals cover diet plans and programs only. Nutrition targets, logging
  food or sets, and editing an existing plan in place are deliberately left
  out: they would act on the user's records rather than offer something new.
