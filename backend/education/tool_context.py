"""Per-request context shared by Agent tools."""

from contextvars import ContextVar


_teacher_username: ContextVar[str] = ContextVar("teacher_username", default="")


def set_teacher_username(username: str):
    """Set the current teacher username for tools and return a reset token."""
    return _teacher_username.set((username or "").strip())


def reset_teacher_username(token) -> None:
    _teacher_username.reset(token)


def get_teacher_username() -> str:
    return _teacher_username.get("")
