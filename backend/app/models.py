"""
Sunbull OS - Unified Database Models
CANONICAL SCHEMA - THE SINGLE SOURCE OF TRUTH
All models for the solar sales operations platform.
SQLite-compatible, PostgreSQL-migration-ready.
"""
from sqlalchemy import (
    Column, Integer, String, Float, Boolean, DateTime, Text, Date, Time,
    ForeignKey, Index, JSON
)
from sqlalchemy.orm import relationship
from app.database import Base
from datetime import datetime
import enum
import json


# ============================================================================
# ENUMS
# ============================================================================

class UserRole(str, enum.Enum):
    ADMIN = "admin"
    REP = "rep"
    CONFIRMATION = "confirmation"
    INSTALLER = "installer"


class LeadStatus(str, enum.Enum):
    NEW = "new"
    QUALIFYING = "qualifying"
    CONFIRMING = "confirming"
    CONFIRMED = "confirmed"
    UNCONFIRMED = "unconfirmed"
    RESCHEDULE = "reschedule"
    APPOINTED = "appointed"
    CLOSED_WON = "closed_won"
    CLOSED_LOST = "closed_lost"
    FOLLOW_UP = "follow_up"
    REHASH = "rehash"


class LeadSource(str, enum.Enum):
    DOOR_TO_DOOR = "door_to_door"
    CALL_CENTER = "call_center"
    WEB = "web"
    REFERRAL = "referral"


class AppointmentStatus(str, enum.Enum):
    NEW = "new"
    SCHEDULED = "scheduled"
    CONFIRMING = "confirming"
    CONFIRMED = "confirmed"
    EN_ROUTE = "en_route"
    ARRIVED = "arrived"
    COMPLETED = "completed"
    NO_SHOW = "no_show"
    RESCHEDULED = "rescheduled"
    CANCELLED = "cancelled"


class DealStage(str, enum.Enum):
    SOLD = "sold"
    INSTALLED = "installed"
    SUBMITTED_FOR_FUNDING = "submitted_for_funding"
    FUNDED = "funded"
    PAID = "paid"


class CommissionStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    PAID = "paid"


# ============================================================================
# MODELS
# ============================================================================

class User(Base):
    """System users: admins, reps, confirmation team, installers"""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(255), nullable=False)
    role = Column(String(20), nullable=False, default="rep")
    phone = Column(String(20))
    is_active = Column(Boolean, default=True)
    close_rate = Column(Float, default=0.0)
    total_deals = Column(Integer, default=0)
    territory = Column(String(20))
    created_at = Column(DateTime, default=datetime.utcnow)


class Lead(Base):
    """Sales leads - the core entity of the application"""
    __tablename__ = "leads"

    id = Column(Integer, primary_key=True, index=True)

    # Contact Information
    first_name = Column(String(100), nullable=False)
    last_name = Column(String(100), nullable=False)
    phone = Column(String(20), nullable=False)
    email = Column(String(255))

    # Property Information
    property_address = Column(String(255), nullable=False)
    city = Column(String(100))
    state = Column(String(2))
    zip_code = Column(String(10))
    homeowner_status = Column(String(20))
    property_type = Column(String(30))
    roof_type = Column(String(30))
    utility_company = Column(String(100))

    # Energy Information
    average_monthly_bill = Column(Float)
    estimated_annual_kwh = Column(Float)
    cost_per_kwh = Column(Float)

    # Source & Campaign
    source_type = Column(String(20), nullable=False, default="web")
    source_rep_id = Column(Integer, ForeignKey("users.id"))
    campaign = Column(String(100))

    # Assignment
    assigned_rep_id = Column(Integer, ForeignKey("users.id"), index=True)
    setter_id = Column(Integer, ForeignKey("users.id"))
    closer_id = Column(Integer, ForeignKey("users.id"))

    # Lead Lifecycle
    deal_status = Column(String(30), nullable=False, default="new", index=True)
    lead_quality_score = Column(Integer, default=50)
    notes = Column(Text)

    # Follow-up
    follow_up_required = Column(Boolean, default=False)
    next_follow_up_date = Column(DateTime)

    # Pipeline Reference
    project_stage = Column(String(30))
    installer_id = Column(Integer, ForeignKey("users.id"))

    # Solar / Deal Sizing
    system_size_kw = Column(Float)
    panel_count = Column(Integer)
    offset_percentage = Column(Float)
    estimated_monthly_payment = Column(Float)
    estimated_monthly_savings = Column(Float)
    deal_value = Column(Float)
    commission_amount = Column(Float)

    # Locking
    is_locked = Column(Boolean, default=False)
    locked_by_rep_id = Column(Integer, ForeignKey("users.id"))

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    assigned_rep = relationship("User", foreign_keys=[assigned_rep_id])
    setter = relationship("User", foreign_keys=[setter_id])
    closer = relationship("User", foreign_keys=[closer_id])


class Appointment(Base):
    """Scheduled appointments with leads"""
    __tablename__ = "appointments"

    id = Column(Integer, primary_key=True, index=True)

    # Core
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False, index=True)
    assigned_rep_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    # Schedule
    appointment_date = Column(Date, nullable=False)
    appointment_time = Column(Time, nullable=False)
    appointment_end_time = Column(Time)
    timezone = Column(String(50), default="America/New_York")
    scheduled_duration_minutes = Column(Integer, default=90)

    # Status & Confirmation
    appointment_status = Column(String(20), default="scheduled", index=True)
    confirmation_status = Column(String(20), default="pending")
    confirmation_attempts_count = Column(Integer, default=0)
    last_confirmation_attempt_at = Column(DateTime)
    confirmed_by_user_id = Column(Integer, ForeignKey("users.id"))

    # Assignment & Routing
    assigned_at = Column(DateTime)
    route_order_index = Column(Integer)
    estimated_travel_time_minutes = Column(Integer)

    # Location
    appointment_address = Column(String(255))
    geo_lat = Column(Float)
    geo_lng = Column(Float)

    # Check-in/out
    actual_start_time = Column(DateTime)
    actual_end_time = Column(DateTime)
    rep_checked_in_at = Column(DateTime)
    rep_checked_out_at = Column(DateTime)

    # Outcome
    outcome = Column(String(30))
    notes = Column(Text)
    photo_proof_url = Column(String(500))
    voice_memo_url = Column(String(500))

    # History
    reschedule_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    lead = relationship("Lead")
    assigned_rep_rel = relationship("User", foreign_keys=[assigned_rep_id])


class ConfirmationAttempt(Base):
    """Call confirmation attempts"""
    __tablename__ = "confirmation_attempts"

    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False, index=True)
    appointment_id = Column(Integer, ForeignKey("appointments.id"))
    agent_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    attempt_number = Column(Integer, nullable=False)
    outcome = Column(String(30))
    notes = Column(Text)
    called_at = Column(DateTime, default=datetime.utcnow)
    next_attempt_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    lead = relationship("Lead")
    agent = relationship("User")


class FollowUp(Base):
    """Follow-up tasks for leads"""
    __tablename__ = "follow_ups"

    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False, index=True)
    assigned_rep_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    reason = Column(String(255))
    scheduled_date = Column(DateTime, index=True)
    status = Column(String(20), default="pending", index=True)
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime)

    # Relationships
    lead = relationship("Lead")
    assigned_rep = relationship("User")


class RehashEntry(Base):
    """Rehash queue for incomplete deals"""
    __tablename__ = "rehash_queue"

    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False, index=True)
    original_rep_id = Column(Integer, ForeignKey("users.id"))
    assigned_rep_id = Column(Integer, ForeignKey("users.id"))
    reason = Column(String(255))
    callback_at = Column(DateTime, index=True)
    attempts = Column(Integer, default=0)
    status = Column(String(20), default="pending", index=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    lead = relationship("Lead")


class Deal(Base):
    """Deal pipeline tracking (Sold → Installed → Submitted → Funded → Paid)"""
    __tablename__ = "deals"

    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False)
    rep_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    installer_id = Column(Integer, ForeignKey("users.id"))
    deal_value = Column(Float, nullable=False)
    pipeline_stage = Column(String(30), default="sold", index=True)
    responsible_party = Column(String(30))

    # Stage Timestamps
    sold_at = Column(DateTime)
    installed_at = Column(DateTime)
    submitted_at = Column(DateTime)
    funded_at = Column(DateTime)
    paid_at = Column(DateTime)
    stage_entered_at = Column(DateTime)

    # Delay Tracking
    delay_reason = Column(String(255))
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    lead = relationship("Lead")
    rep = relationship("User", foreign_keys=[rep_id])
    installer = relationship("User", foreign_keys=[installer_id])


class Commission(Base):
    """Commission tracking per deal"""
    __tablename__ = "commissions"

    id = Column(Integer, primary_key=True, index=True)
    deal_id = Column(Integer, ForeignKey("deals.id"), nullable=False)
    rep_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    deal_value = Column(Float, nullable=False)
    commission_rate = Column(Float, default=0.14)
    commission_amount = Column(Float, nullable=False)
    company_revenue = Column(Float, nullable=False)
    status = Column(String(20), default="pending", index=True)
    paid_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    deal = relationship("Deal")
    rep = relationship("User")


class InstallerProfile(Base):
    """Installer performance tracking"""
    __tablename__ = "installer_profiles"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True)
    company_name = Column(String(255))
    license_number = Column(String(100))
    jobs_assigned = Column(Integer, default=0)
    jobs_completed = Column(Integer, default=0)
    avg_install_days = Column(Float, default=0)
    cancellation_rate = Column(Float, default=0)
    funding_delay_avg_days = Column(Float, default=0)
    performance_score = Column(Float, default=0)
    tier = Column(String(10), default="bronze")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    user = relationship("User")


class AutomationRule(Base):
    """Configurable automation rules (admin-editable)"""
    __tablename__ = "automation_rules"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text)
    condition_field = Column(String(50), nullable=False)
    condition_operator = Column(String(20), nullable=False)
    condition_value = Column(String(255), nullable=False)
    action_type = Column(String(30), nullable=False)
    action_params = Column(JSON)
    is_active = Column(Boolean, default=True, index=True)
    priority = Column(Integer, default=100)
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def get_action_params(self):
        if self.action_params:
            return json.loads(self.action_params)
        return {}

    def set_action_params(self, params):
        self.action_params = json.dumps(params)


class AuditLog(Base):
    """Immutable audit log for all actions - RENAMED from LeadTimeline"""
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    entity_type = Column(String(50), nullable=False, index=True)
    entity_id = Column(Integer, nullable=False)
    action = Column(String(50), nullable=False, index=True)
    previous_value = Column(Text)
    new_value = Column(Text)
    details = Column(Text)
    ip_address = Column(String(45))
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    # Relationships
    user = relationship("User")


class Notification(Base):
    """In-app notifications"""
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    message = Column(Text, nullable=False)
    type = Column(String(30), default="info")
    is_read = Column(Boolean, default=False, index=True)
    entity_type = Column(String(50))
    entity_id = Column(Integer)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    # Relationships
    user = relationship("User")


class AccountabilityFlag(Base):
    """Fraud/behavior flags for reps"""
    __tablename__ = "accountability_flags"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    flag_type = Column(String(30), nullable=False, index=True)
    severity = Column(String(10), default="medium")
    appointment_id = Column(Integer, ForeignKey("appointments.id"))
    lead_id = Column(Integer, ForeignKey("leads.id"))
    details = Column(Text)
    resolved = Column(Boolean, default=False)
    resolved_by = Column(Integer, ForeignKey("users.id"))
    resolved_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    # Relationships
    user = relationship("User", foreign_keys=[user_id])


class LeadOwnershipHistory(Base):
    """Track who set, ran, and closed each lead"""
    __tablename__ = "lead_ownership_history"

    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    role_in_lead = Column(String(20), nullable=False)
    started_at = Column(DateTime, default=datetime.utcnow)
    ended_at = Column(DateTime)
    notes = Column(Text)

    # Relationships
    lead = relationship("Lead")
    user = relationship("User")


class BillAnalysis(Base):
    """First-class bill analysis / savings estimate records.

    Persists the result of every bill analysis and savings plan calculation.
    Linked to a lead when available.
    """
    __tablename__ = "bill_analyses"

    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), index=True)

    # Input Data
    bill_upload_url = Column(String(500))
    extracted_usage_kwh = Column(Float)
    annual_kwh = Column(Float)
    monthly_kwh = Column(Float)
    cost_per_kwh = Column(Float)
    average_monthly_bill = Column(Float)
    state = Column(String(2))

    # Computed Solar Sizing
    system_size_kw = Column(Float)
    panel_count = Column(Integer)
    offset_percentage = Column(Float)

    # Financial Estimates
    estimated_monthly_payment = Column(Float)
    estimated_monthly_savings = Column(Float)
    annual_savings = Column(Float)
    payback_period_years = Column(Float)
    system_cost = Column(Float)

    # Metadata
    good_sunlight_location = Column(Boolean, default=False)
    source = Column(String(20), default="web")  # web, portal, rep
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    lead = relationship("Lead")


class WebsitePage(Base):
    """Website / acquisition funnel page content.

    Stores content and configuration for public-facing pages:
    homepage, bill_upload, qualification_form, appointment_booking, faq.
    Admin-editable so marketing can update without code changes.
    """
    __tablename__ = "website_pages"

    id = Column(Integer, primary_key=True, index=True)
    slug = Column(String(50), unique=True, nullable=False, index=True)
    title = Column(String(255), nullable=False)
    subtitle = Column(String(500))
    body_content = Column(Text)
    meta_description = Column(String(500))
    hero_image_url = Column(String(500))
    cta_text = Column(String(100))
    cta_link = Column(String(255))
    sort_order = Column(Integer, default=0)
    is_published = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
