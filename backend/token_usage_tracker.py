from __future__ import annotations

from contextvars import ContextVar, Token
from copy import deepcopy
from typing import Any

from cache import cache

TOKEN_USAGE_TTL_SECONDS = 24 * 60 * 60
_fallback_store: dict[str, dict[str, int]] = {}
_active_session: ContextVar[tuple[str, str] | None] = ContextVar("token_usage_active_session", default=None)


def _key(user_id: str, session_id: str) -> str:
    return f"token_usage:{user_id}:{session_id}"


def _empty_usage() -> dict[str, int]:
    return {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "requests": 0,
    }


def get_session_token_usage(user_id: str, session_id: str) -> dict[str, int]:
    key = _key(user_id, session_id)
    usage = cache.get_json(key)
    if usage is None:
        usage = _fallback_store.get(key)
    base = _empty_usage()
    if isinstance(usage, dict):
        for name in base:
            base[name] = int(usage.get(name) or 0)
    return base


def delete_session_token_usage(user_id: str, session_id: str) -> None:
    key = _key(user_id, session_id)
    cache.delete(key)
    _fallback_store.pop(key, None)


def set_active_token_usage_session(user_id: str, session_id: str) -> Token:
    return _active_session.set((user_id, session_id))


def reset_active_token_usage_session(token: Token) -> None:
    _active_session.reset(token)


def add_session_token_usage(user_id: str, session_id: str, usage: dict[str, int] | None) -> dict[str, int]:
    if not usage:
        return get_session_token_usage(user_id, session_id)

    key = _key(user_id, session_id)
    current = get_session_token_usage(user_id, session_id)
    current["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
    current["completion_tokens"] += int(usage.get("completion_tokens") or 0)
    current["total_tokens"] += int(usage.get("total_tokens") or 0)
    current["requests"] += 1

    _fallback_store[key] = deepcopy(current)
    cache.set_json(key, current, ttl=TOKEN_USAGE_TTL_SECONDS)
    return current


def extract_token_usage(message: Any) -> dict[str, int] | None:
    """Extract provider token usage from LangChain messages/chunks when available."""
    usage = getattr(message, "usage_metadata", None)
    if isinstance(usage, dict):
        prompt_tokens = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
        total_tokens = int(usage.get("total_tokens") or (prompt_tokens + completion_tokens) or 0)
        if total_tokens:
            return {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
            }

    response_metadata = getattr(message, "response_metadata", None)
    if not isinstance(response_metadata, dict):
        return None

    token_usage = response_metadata.get("token_usage") or response_metadata.get("usage")
    if not isinstance(token_usage, dict):
        return None

    prompt_tokens = int(token_usage.get("prompt_tokens") or token_usage.get("input_tokens") or 0)
    completion_tokens = int(token_usage.get("completion_tokens") or token_usage.get("output_tokens") or 0)
    total_tokens = int(token_usage.get("total_tokens") or (prompt_tokens + completion_tokens) or 0)
    if not total_tokens:
        return None
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


def record_token_usage_from_message(user_id: str, session_id: str, message: Any) -> dict[str, int] | None:
    usage = extract_token_usage(message)
    if not usage:
        return None
    return add_session_token_usage(user_id, session_id, usage)


def record_active_session_token_usage_from_message(message: Any) -> dict[str, int] | None:
    active = _active_session.get()
    if not active:
        return None
    user_id, session_id = active
    return record_token_usage_from_message(user_id, session_id, message)


def record_token_usage_from_messages(user_id: str, session_id: str, messages: list[Any]) -> dict[str, int] | None:
    latest = None
    for message in messages:
        latest = record_token_usage_from_message(user_id, session_id, message) or latest
    return latest
