"""Verify a user's GitHub account through the GitHub App's user OAuth flow."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.github import GitHubIdentity


def build_authorize_url(user_id: uuid.UUID) -> str:
    raise NotImplementedError


async def complete_oauth(db: AsyncSession, code: str, state: str) -> GitHubIdentity:
    raise NotImplementedError


async def get_identity_for_user(db: AsyncSession, user_id: uuid.UUID) -> GitHubIdentity | None:
    raise NotImplementedError
