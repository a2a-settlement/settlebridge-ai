"""Text from Anthropic Messages API responses."""

from __future__ import annotations


def response_text(response) -> str:
    """The first text block. Claude 5.x models can put a thinking block first."""
    for block in getattr(response, "content", None) or []:
        if getattr(block, "type", "text") == "text" and isinstance(
            getattr(block, "text", None), str
        ):
            return block.text
    return ""
