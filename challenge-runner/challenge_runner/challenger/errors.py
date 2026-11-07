"""Errors that stay inside the challenger. Messages never carry model text."""

from __future__ import annotations


class ChallengerError(ValueError):
    """The bundle or output path cannot be used safely."""


class ChatClientError(RuntimeError):
    """The model HTTP call failed. The message never includes the API key."""
