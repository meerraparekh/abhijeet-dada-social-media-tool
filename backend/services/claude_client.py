"""Shared helper for structured-output Claude API calls, with retries on
transient network errors. Used by clip_suggester and translator - factored
out so both share the same retry/validation behavior instead of duplicating it.
"""
from __future__ import annotations

import json
import time
from typing import Type, TypeVar

import anthropic
import httpx2
import pydantic

from config import CLAUDE_MODEL

T = TypeVar("T", bound=pydantic.BaseModel)

# Errors that plausibly mean "the connection dropped mid-stream" rather than
# "the request itself is invalid" - worth a couple of automatic retries
# rather than surfacing a scary traceback for what's often a one-off network
# hiccup (flaky wifi, a VPN reconnect, a proxy that doesn't love long-lived
# streaming connections).
_TRANSIENT_NETWORK_ERRORS = (
    anthropic.APIConnectionError,
    anthropic.APITimeoutError,
    httpx2.RemoteProtocolError,
    httpx2.ReadError,
    httpx2.ConnectError,
    httpx2.ConnectTimeout,
)


def call_structured(
    system: str,
    user_content: str,
    schema: dict,
    result_model: Type[T],
    max_tokens: int = 32000,
    effort: str = "low",
    max_attempts: int = 3,
) -> T:
    """Ask Claude for a JSON response matching `schema`, parsed into
    `result_model`. Streams (required once max_tokens is large enough that
    the SDK estimates a plain request could run past ~10 minutes) and caps
    thinking effort, since these are extraction/translation tasks rather
    than hard reasoning problems - thinking otherwise eats into the same
    token budget as the JSON output itself."""
    client = anthropic.Anthropic()
    response = None
    for attempt in range(1, max_attempts + 1):
        try:
            with client.messages.stream(
                model=CLAUDE_MODEL,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user_content}],
                output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
            ) as stream:
                response = stream.get_final_message()
            break
        except _TRANSIENT_NETWORK_ERRORS as exc:
            if attempt == max_attempts:
                raise RuntimeError(
                    f"Lost connection to Claude {max_attempts} times in a row "
                    f"({exc!r}). This usually means something on this network "
                    "is dropping long-running connections (flaky wifi, VPN, "
                    "or a firewall/antivirus proxy) rather than a problem "
                    "with the request itself - try a different network if it "
                    "keeps happening."
                ) from exc
            time.sleep(3 * attempt)

    text = next((b.text for b in response.content if b.type == "text"), None)
    if text is None:
        raise RuntimeError(
            f"Claude's response didn't include any text (stop_reason={response.stop_reason!r})."
        )
    try:
        return result_model.model_validate_json(text)
    except (json.JSONDecodeError, pydantic.ValidationError) as exc:
        raise RuntimeError(
            f"Claude's response wasn't valid (stop_reason={response.stop_reason!r}): {exc}. "
            "This usually means the response was cut off before completing - try again, "
            "or if it keeps happening, the input may need to be shortened."
        ) from exc
