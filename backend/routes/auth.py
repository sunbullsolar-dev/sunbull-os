"""Authentication routes for login and user management."""
from datetime import timedelta, datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.auth import (
    create_access_token,
    verify_password,
    get_current_user,
    hash_password,
    require_role,
    ACCESS_TOKEN_EXPIRE_HOURS,
)
from app.database import get_db
from app.models import User, Invite
from services.email_alerts import send_invite_email
import secrets
import os

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


class InviteRequest(BaseModel):
    """Invite creation request model."""

    email: str
    full_name: str
    role: str = "rep"


class RegisterRequest(BaseModel):
    """Registration request model."""

    token: str
    password: str


@router.post("/invite")
def create_invite(
    req: InviteRequest,
    current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Admin creates an invite for a new rep.

    Args:
        req: Invite request with email, full_name, and optional role
        current_user: Current authenticated admin user
        db: Database session

    Returns:
        Invite details with token and invite link
    """
    # Check if email already exists as user
    existing = db.query(User).filter(User.email == req.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="User with this email already exists")

    # Check for existing unused invite
    existing_invite = (
        db.query(Invite)
        .filter(Invite.email == req.email, Invite.used == False)
        .first()
    )
    if existing_invite:
        # Return existing invite token
        return {
            "invite_id": existing_invite.id,
            "token": existing_invite.token,
            "email": existing_invite.email,
            "full_name": existing_invite.full_name,
            "invite_link": f"/register?token={existing_invite.token}",
        }

    token = secrets.token_urlsafe(32)
    invite = Invite(
        email=req.email,
        full_name=req.full_name,
        token=token,
        role=req.role,
        invited_by=current_user.id,
    )
    db.add(invite)
    db.commit()
    db.refresh(invite)

    # Build full invite URL and send email
    base_url = os.environ.get("BASE_URL", "https://web-production-1d7ec.up.railway.app")
    full_link = f"{base_url}/register?token={invite.token}"
    email_sent = send_invite_email(
        rep_name=invite.full_name,
        rep_email=invite.email,
        invite_link=full_link,
    )

    return {
        "invite_id": invite.id,
        "token": invite.token,
        "email": invite.email,
        "full_name": invite.full_name,
        "invite_link": f"/register?token={invite.token}",
        "email_sent": email_sent,
    }


@router.get("/invite/{token}")
def get_invite_info(token: str, db: Session = Depends(get_db)):
    """Public: Get invite details so registration form can pre-fill.

    Args:
        token: Invite token
        db: Database session

    Returns:
        Invite information (email, full_name, role)
    """
    invite = (
        db.query(Invite)
        .filter(Invite.token == token, Invite.used == False)
        .first()
    )
    if not invite:
        raise HTTPException(status_code=404, detail="Invite not found or already used")
    return {
        "email": invite.email,
        "full_name": invite.full_name,
        "role": invite.role,
    }


@router.post("/register")
def register_with_invite(
    req: RegisterRequest,
    db: Session = Depends(get_db),
):
    """Public: Rep registers using invite token.

    Args:
        req: Registration request with token and password
        db: Database session

    Returns:
        Access token and user information
    """
    invite = (
        db.query(Invite)
        .filter(Invite.token == req.token, Invite.used == False)
        .first()
    )
    if not invite:
        raise HTTPException(status_code=404, detail="Invite not found or already used")

    # Check if user already exists
    existing = db.query(User).filter(User.email == invite.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="User already exists")

    # Create user
    user = User(
        email=invite.email,
        hashed_password=hash_password(req.password),
        full_name=invite.full_name,
        role=invite.role,
        is_active=True,
    )
    db.add(user)

    # Mark invite as used
    invite.used = True
    invite.used_at = datetime.utcnow()

    db.commit()
    db.refresh(user)

    # Auto-login: return token
    access_token = create_access_token(
        data={"sub": user.email},
        expires_delta=timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS),
    )

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "email": user.email,
            "full_name": user.full_name,
            "role": user.role,
        },
    }
