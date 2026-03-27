"""Authentication routes for login and user management."""
from datetime import timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.auth import (
    create_access_token,
    verify_password,
    get_current_user,
    ACCESS_TOKEN_EXPIRE_HOURS,
)
from app.database import get_db
from app.models import User

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    """Login request model."""

    email: str
    password: str


class LoginResponse(BaseModel):
    """Login response model."""

    access_token: str
    token_type: str
    user: dict

    class Config:
        from_attributes = True


class UserResponse(BaseModel):
    """User response model."""

    id: int
    email: str
    full_name: str
    role: str
    phone: Optional[str] = None
    is_active: bool
    close_rate: float = 0.0
    total_deals: int = 0
    territory: Optional[str] = None

    class Config:
        from_attributes = True


@router.post("/login", response_model=LoginResponse)
def login(request: LoginRequest, db: Session = Depends(get_db)):
    """
    Login endpoint - returns JWT token and user info.

    Args:
        request: Login request with email and password
        db: Database session

    Returns:
        Access token and user information

    Raises:
        HTTPException: 401 if credentials are invalid
    """
    user = db.query(User).filter(User.email == request.email).first()

    if not user or not verify_password(request.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is disabled",
        )

    access_token = create_access_token(
        data={"sub": user.email},
        expires_delta=timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS),
    )

    user_data = {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role,
        "phone": user.phone,
        "is_active": user.is_active,
        "close_rate": user.close_rate,
        "total_deals": user.total_deals,
        "territory": user.territory,
    }

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": user_data,
    }


@router.get("/me", response_model=UserResponse)
def get_me(current_user: User = Depends(get_current_user)):
    """
    Get current authenticated user information.

    Args:
        current_user: Current authenticated user (dependency injection)

    Returns:
        Current user information
    """
    return current_user
