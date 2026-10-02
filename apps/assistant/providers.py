"""Model providers behind one small interface — see docs/ASSISTANT.md
"Providers".

A provider turns (system prompt, tools, history) into one model turn and
knows its own wire format: what a user message, an assistant turn and a
batch of tool results look like. apps.assistant.services drives the tool
loop and stores each provider's messages verbatim (AssistantMessage.raw),
so nothing outside this module ever needs to know either format.

- `AnthropicProvider` — Claude through the official `anthropic` SDK.
- `OllamaProvider` — a self-hosted Ollama server's native /api/chat
  (plain `requests`, already a dependency; Ollama has no SDK worth adding
  for one endpoint). Nothing leaves the operator's own network.
"""

import json
import logging
from dataclasses import dataclass, field

import requests
from django.utils.translation import gettext as _

logger = logging.getLogger(__name__)

ANTHROPIC = "anthropic"
OLLAMA = "ollama"

# Models that accept the server-side refusal fallback (`fallbacks:
# "default"`, beta server-side-fallback-2026-07-01). When a safety
# classifier declines a request — health and nutrition talk can
# occasionally trip one — the API re-runs it on Anthropic's recommended
# fallback model within the same call instead of returning a refusal.
_FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
_FALLBACK_BETA = "server-side-fallback-2026-07-01"

# Streaming, so a long reply never hits an HTTP timeout; max_tokens only
# caps a reply, it isn't spent unless used.
_MAX_TOKENS = 64000
# A tool input the SDK couldn't parse at all (eager input streaming) is
# re-requested at most this many times in a row.
_MAX_JSON_RETRIES = 2


class ProviderError(Exception):
    """Something the user should see instead of a reply — always a
    translated, plain-language message, never a raw exception."""


@dataclass
class ToolCall:
    id: str
    name: str
    input: object


@dataclass
class ToolResult:
    call: ToolCall
    content: str
    is_error: bool = False


@dataclass
class TurnResult:
    # Provider-format message(s) to append to the history for this turn.
    raw: list
    text: str
    tool_calls: list = field(default_factory=list)
    # "end", "tool_use", "max_tokens" or "refusal".
    stop_reason: str = "end"
    input_tokens: int = 0
    output_tokens: int = 0


class AnthropicProvider:
    name = ANTHROPIC

    def __init__(self, *, api_key, model, effort="medium", refusal_fallback=True):
        self.api_key = api_key
        self.model = model
        self.effort = effort
        self.refusal_fallback = refusal_fallback

    def user_message(self, text):
        return {"role": "user", "content": [{"type": "text", "text": text}]}

    def tool_results_message(self, results):
        return [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": result.call.id,
                        "content": result.content,
                        **({"is_error": True} if result.is_error else {}),
                    }
                    for result in results
                ],
            }
        ]

    def _request_kwargs(self, system, tools, history):
        kwargs = {
            "model": self.model,
            "max_tokens": _MAX_TOKENS,
            "system": [{"type": "text", "text": system}],
            # Tool inputs (a whole week's diet plan) can be long — stream
            # them as they're generated. The server doesn't validate a
            # streamed input, so apps.assistant.tools validates every one
            # before running it.
            "tools": [{**tool, "eager_input_streaming": True} for tool in tools],
            "messages": history,
            # The tools and system prompt never change and the history is
            # append-only, so everything up to the newest message is a
            # cacheable prefix.
            "cache_control": {"type": "ephemeral"},
        }
        # Haiku 4.5 takes no effort parameter (it 400s); every current
        # Opus/Sonnet/Fable model does.
        if not self.model.startswith("claude-haiku"):
            kwargs["output_config"] = {"effort": self.effort}
        return kwargs

    def _open_stream(self, client, kwargs):
        if self.refusal_fallback and self.model in _FALLBACK_MODELS:
            return client.beta.messages.stream(
                **kwargs, betas=[_FALLBACK_BETA], fallbacks="default"
            )
        return client.messages.stream(**kwargs)

    def run_turn(self, *, system, tools, history, on_text):
        import anthropic

        client = anthropic.Anthropic(api_key=self.api_key, max_retries=2)
        kwargs = self._request_kwargs(system, tools, history)
        json_retries = 0
        while True:
            streamed_text = []
            try:
                with self._open_stream(client, kwargs) as stream:
                    for event in stream:
                        if event.type == "text":
                            streamed_text.append(event.text)
                            on_text("".join(streamed_text))
                    message = stream.get_final_message()
                break
            except ValueError:
                # A streamed tool input the SDK couldn't parse at all —
                # there's no tool_use block to answer, so re-issue the
                # turn (bounded). API errors aren't ValueErrors.
                json_retries += 1
                if json_retries > _MAX_JSON_RETRIES:
                    raise ProviderError(
                        _("The assistant sent a malformed reply. Please try again.")
                    )
                on_text("")
            except anthropic.AuthenticationError as exc:
                raise ProviderError(
                    _("The API key was rejected. Check it in your assistant settings.")
                ) from exc
            except anthropic.PermissionDeniedError as exc:
                raise ProviderError(_("This API key isn't allowed to use that model.")) from exc
            except anthropic.NotFoundError as exc:
                raise ProviderError(
                    _("The configured model (%(model)s) wasn't found.") % {"model": self.model}
                ) from exc
            except anthropic.RateLimitError as exc:
                raise ProviderError(
                    _("The AI service is rate limiting requests. Try again in a minute.")
                ) from exc
            except anthropic.BadRequestError as exc:
                logger.warning("Assistant request rejected: %s", exc)
                raise ProviderError(_("The AI service rejected the request.")) from exc
            except anthropic.APIStatusError as exc:
                logger.warning("Assistant API error %s: %s", exc.status_code, exc)
                raise ProviderError(
                    _("The AI service is having problems right now. Try again later.")
                ) from exc
            except anthropic.APIConnectionError as exc:
                raise ProviderError(_("Couldn't reach the AI service.")) from exc

        usage = message.usage
        input_tokens = (
            (usage.input_tokens or 0)
            + (getattr(usage, "cache_creation_input_tokens", 0) or 0)
            + (getattr(usage, "cache_read_input_tokens", 0) or 0)
        )
        if message.stop_reason == "refusal":
            # Discard any partial output — a refused reply isn't an answer.
            return TurnResult(
                raw=[],
                text="",
                stop_reason="refusal",
                input_tokens=input_tokens,
                output_tokens=usage.output_tokens or 0,
            )

        content = _echoable_content(message.content)
        tool_calls = [
            ToolCall(id=block["id"], name=block["name"], input=block.get("input"))
            for block in content
            if block.get("type") == "tool_use"
        ]
        text = "\n\n".join(
            block["text"] for block in content if block.get("type") == "text" and block["text"]
        )
        if message.stop_reason == "max_tokens":
            stop_reason = "max_tokens"
        elif tool_calls:
            stop_reason = "tool_use"
        else:
            stop_reason = "end"
        return TurnResult(
            raw=[{"role": "assistant", "content": content}],
            text=text,
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            input_tokens=input_tokens,
            output_tokens=usage.output_tokens or 0,
        )


_MODEL_INTERNAL_BLOCKS = {"thinking", "redacted_thinking", "tool_use"}


def _echoable_content(blocks):
    """The response's content blocks as plain dicts, ready to be stored
    and sent back unchanged on the next request (thinking blocks
    included — they're only valid if echoed exactly as received).

    One exception, from the refusal-fallback rules: when a model declined
    mid-reply and the fallback model took over, the blocks the declining
    model produced *before* the last `fallback` marker are omitted, apart
    from its text — they belong to a model that's no longer answering.
    """
    content = [block.to_dict(mode="json", exclude_none=True) for block in blocks]
    boundary = max(
        (index for index, block in enumerate(content) if block.get("type") == "fallback"),
        default=None,
    )
    if boundary is None:
        return content
    kept = [block for block in content[:boundary] if block.get("type") == "text"]
    return kept + content[boundary + 1 :]


class OllamaProvider:
    name = OLLAMA

    def __init__(self, *, base_url, model):
        self.base_url = base_url.rstrip("/")
        self.model = model

    def user_message(self, text):
        return {"role": "user", "content": text}

    def tool_results_message(self, results):
        return [
            {"role": "tool", "tool_name": result.call.name, "content": result.content}
            for result in results
        ]

    def run_turn(self, *, system, tools, history, on_text):
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, *history],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool["description"],
                        "parameters": tool["input_schema"],
                    },
                }
                for tool in tools
            ],
            "stream": True,
        }
        text_parts = []
        raw_tool_calls = []
        final = {}
        try:
            with requests.post(
                f"{self.base_url}/api/chat", json=body, stream=True, timeout=(10, 300)
            ) as response:
                if response.status_code == 404:
                    raise ProviderError(
                        _("The configured model (%(model)s) wasn't found.") % {"model": self.model}
                    )
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line:
                        continue
                    chunk = json.loads(line)
                    if chunk.get("error"):
                        raise ProviderError(_("The AI service rejected the request."))
                    message = chunk.get("message") or {}
                    if message.get("content"):
                        text_parts.append(message["content"])
                        on_text("".join(text_parts))
                    raw_tool_calls.extend(message.get("tool_calls") or [])
                    if chunk.get("done"):
                        final = chunk
        except requests.RequestException as exc:
            raise ProviderError(_("Couldn't reach the AI service.")) from exc
        except ValueError as exc:
            raise ProviderError(
                _("The assistant sent a malformed reply. Please try again.")
            ) from exc

        text = "".join(text_parts)
        tool_calls = [
            ToolCall(
                id=call.get("id") or f"call_{index}",
                name=(call.get("function") or {}).get("name", ""),
                input=(call.get("function") or {}).get("arguments"),
            )
            for index, call in enumerate(raw_tool_calls)
        ]
        assistant_message = {"role": "assistant", "content": text}
        if raw_tool_calls:
            assistant_message["tool_calls"] = raw_tool_calls
        if tool_calls:
            stop_reason = "tool_use"
        elif final.get("done_reason") == "length":
            stop_reason = "max_tokens"
        else:
            stop_reason = "end"
        return TurnResult(
            raw=[assistant_message],
            text=text,
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            input_tokens=final.get("prompt_eval_count") or 0,
            output_tokens=final.get("eval_count") or 0,
        )
