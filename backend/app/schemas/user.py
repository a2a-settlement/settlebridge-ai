from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr

from app.models.user import UserType


class UserRegisterRequest(BaseModel):
    email: EmailStr
    password: str
    display_name: str
    user_type: UserType


class UserLoginRequest(BaseModel):
    email: EmailStr
    password: str


class LinkExchangeRequest(BaseModel):
    bot_name: str
    developer_id: str = ""


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str
    user_type: UserType
    exchange_bot_id: str | None = None
    exchange_balance_cached: float | None = None
    bio: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}
