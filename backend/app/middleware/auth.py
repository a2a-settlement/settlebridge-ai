from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.user import User, UserType

logger = logging.getLogger(__name__)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer()


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(user_id: uuid.UUID) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=settings.JWT_EXPIRATION_HOURS)
    payload = {"sub": str(user_id), "exp": expire}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> uuid.UUID:
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
        user_id = payload.get("sub")
        if user_id is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
        return uuid.UUID(user_id)
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")


async def _validate_exchange_key(ate_key: str) -> dict:
    """Validate an ate_ key against the exchange and return full account info."""
    base = settings.A2A_EXCHANGE_URL.rstrip("/")
    headers = {"Authorization": f"Bearer {ate_key}"}

    async with httpx.AsyncClient(timeout=10.0) as client:
        bal_resp = await client.get(f"{base}/v1/exchange/balance", headers=headers)
        if bal_resp.status_code == 401:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid exchange API key")
        bal_resp.raise_for_status()
        account_id = bal_resp.json()["account_id"]

        acct_resp = await client.get(f"{base}/v1/accounts/{account_id}")
        acct_resp.raise_for_status()
        return acct_resp.json()


async def _get_or_create_exchange_user(ate_key: str, db: AsyncSession) -> User:
    """Authenticate via exchange ate_ key, auto-creating a shadow user if needed."""
    exchange_acct = await _validate_exchange_key(ate_key)
    account_id = exchange_acct["id"]
    contact_email = exchange_acct.get("contact_email", "")
    bot_name = exchange_acct.get("bot_name", "agent")

    # 1. Try existing user by exchange_bot_id
    result = await db.execute(select(User).where(User.exchange_bot_id == account_id))
    user = result.scalar_one_or_none()
    if user:
        return user

    # 2. Try existing user by contact_email (human registered first)
    if contact_email:
        result = await db.execute(select(User).where(User.email == contact_email))
        user = result.scalar_one_or_none()
        if user:
            user.exchange_bot_id = account_id
            user.exchange_api_key = ate_key
            await db.commit()
            await db.refresh(user)
            logger.info("Merged exchange account %s into existing user %s", account_id, user.id)
            return user

    # 3. Create shadow user from exchange identity
    email = contact_email or f"{account_id}@exchange.a2a-settlement.org"
    user = User(
        email=email,
        password_hash=None,
        display_name=bot_name,
        user_type=UserType.BOTH,
        exchange_bot_id=account_id,
        exchange_api_key=ate_key,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    logger.info("Created shadow user %s for exchange account %s", user.id, account_id)
    return user


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User:
    token = credentials.credentials
    if token.startswith("ate_"):
        return await _get_or_create_exchange_user(token, db)

    user_id = decode_token(token)
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


def require_role(*roles: UserType):
    """Dependency factory that checks the user has one of the allowed roles."""

    async def checker(user: User = Depends(get_current_user)) -> User:
        if user.user_type not in roles and UserType.BOTH not in (user.user_type,):
            if user.user_type != UserType.BOTH:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Requires role: {', '.join(r.value for r in roles)}",
                )
        return user

    return checker
