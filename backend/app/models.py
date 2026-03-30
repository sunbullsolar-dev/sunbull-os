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
    # Real operational flow
    SUBMITTED = "submitted"           # Appointment just submitted by TM/canvasser
    NEW = "new"                       # Legacy / web leads without appointment
    QUALIFYING = "qualifying"
    CONFIRMING = "confirming"         # Confirmation team working it
    CONFIRMED = "confirmed"           # Confirmed by homeowner
    UNCONFIRMED = "unconfirmed"
    DISPATCH_READY = "dispatch_ready" # Ready for rep assignment + dispatch
    ASSIGNED = "assigned"             # Rep assigned, awaiting acknowledgment
    APPOINTED = "appointed"           # Legacy compatibility
    RESCHEDULE = "reschedule"
    EN_ROUTE = "en_route"             # Rep traveling
    ARRIVED = "arrived"               # Rep on site
    COMPLETED = "completed"           # Appointment done, outcome pending
    CLOSED_WON = "closed_won"         # Sold
    CLOSED_LOST = "closed_lost"       # Not sold
    FOLLOW_UP = "follow_up"           # Needs follow-up
    REHASH = "rehash"                 # Second attempt queue
    DEAD = "dead"                     # Dead / DNC
    DISPATCHED = "dispatched"         # Legacy compatibility


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
    geo_lat = Column(Float)
    geo_lng = Column(Float)

    # Energy Information
    average_monthly_bill = Column(Float)
    estimated_annual_kwh = Column(Float)
    cost_per_kwh = Column(Float)

    # Source & Campaign
    source_type = Column(String(20), nullable=False, default="web")
    source_rep_id = Column(Integer, ForeignKey("users.id"))
    campaign = Column(String(100))

    # Assignment & Ownership
    assigned_rep_id = Column(Integer, ForeignKey("users.id"), index=True)
    setter_id = Column(Integer, ForeignKey("users.id"))
    runner_id = Column(Integer, ForeignKey("users.id"))  # Rep who ran the appointment
    closer_id = Column(Integer, ForeignKey("users.id"))  # Rep who closed the deal
    rehash_rep_id = Column(Integer, ForeignKey("users.id"))  # Rep assigned for rehash

    # Lead Lifecycle
    deal_status = Column(String(30), nullable=False, default="new", index=True)
    lead_quality_score = Column(Integer, default=50)
    notes = Column(Text)

    # Held / Not Held tracking
    is_held = Column(Boolean, default=False)  # True if appointment was held (rep met homeowner)
    last_outcome = Column(String(30))  # Last appointment outcome

    # Follow-up
    follow_up_required = Column(Boolean, default=False)
    next_follow_up_date = Column(DateTime)
    follow_up_note = Column(Text)

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

    # Soft delete
    is_archived = Column(Boolean, default=False)
    archived_at = Column(DateTime, nullable=True)
    archived_by = Column(Integer, ForeignKey("users.id"), nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    assigned_rep = relationship("User", foreign_keys=[assigned_rep_id])
    setter = relationship("User", foreign_keys=[setter_id])
    runner = relationship("User", foreign_keys=[runner_id])
    closer = relationship("User", foreign_keys=[closer_id])
    rehash_rep = relationship("User", foreign_keys=[rehash_rep_id])


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

    # Appointment Type & Intake
    appointment_type = Column(String(20), default="in_person")  # in_person, zoom, phone
    zoom_email = Column(String(255))
    telemarketing_summary = Column(Text)
    recording_url = Column(String(500))
    submitted_by_name = Column(String(255))  # canvasser/telemarketer name
    submission_source = Column(String(30))  # telemarketing, canvasser, web, admin

    # Dispatch
    dispatch_status = Column(String(20), default="pending")  # pending, assigned, acknowledged, dispatched
    backup_rep_id = Column(Integer, ForeignKey("users.id"))
    rep_acknowledged_at = Column(DateTime)

    # Structured Execution (rep must fill before closing)
    homeowner_present = Column(Boolean)
    decision_maker_present = Column(Boolean)
    pitch_delivered = Column(Boolean)
    proposal_sent = Column(Boolean)
    follow_up_required_flag = Column(Boolean)
    follow_up_date = Column(Date)

    # Confirmation SLA
    confirmation_sla_deadline = Column(DateTime)  # must be touched by this time
    confirmation_overdue = Column(Boolean, default=False)

    # Dispatch timing
    dispatch_accept_deadline = Column(DateTime)  # rep must accept by this time
    rep_late = Column(Boolean, default=False)  # was rep late to appointment

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


class Action(Base):
    """Lead action timeline - tracks every operational step on a lead."""
    __tablename__ = "actions"

    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False, index=True)
    rep_id = Column(Integer, ForeignKey("users.id"), index=True)
    appointment_id = Column(Integer, ForeignKey("appointments.id"), index=True)
    action_type = Column(String(30), nullable=False, index=True)
    # action_type values: assigned, en_route, arrived, completed,
    #   closed, not_closed, rescheduled, no_show, status_change, created
    note = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    # Relationships
    lead = relationship("Lead")
    rep = relationship("User", foreign_keys=[rep_id])


class SolarEstimate(Base):
    """Full solar analysis result — the output of the solar engine.

    Stores the complete estimate with all inputs, outputs, and assumptions.
    One lead can have multiple estimates (re-runs with different inputs).
    """
    __tablename__ = "solar_estimates"

    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False, index=True)

    # Location
    latitude = Column(Float)
    longitude = Column(Float)
    utility_code = Column(String(20))
    utility_name = Column(String(100))
    solar_region = Column(String(30))

    # Solar Resource
    sun_hours_per_day = Column(Float)
    production_factor = Column(Float)  # kWh per kW per year

    # Usage
    annual_kwh = Column(Float)
    monthly_kwh = Column(Float)
    monthly_bill = Column(Float)
    rate_per_kwh = Column(Float)
    usage_calculation_method = Column(String(50))

    # Rate Structure
    peak_rate = Column(Float)
    off_peak_rate = Column(Float)
    nem_policy = Column(String(20))
    nem_export_rate = Column(Float)

    # System
    system_size_kw = Column(Float)
    panel_count = Column(Integer)
    annual_production_kwh = Column(Float)

    # Consumption Model
    self_consumption_pct = Column(Float)
    self_consumed_kwh = Column(Float)
    exported_kwh = Column(Float)

    # Savings (base scenario)
    monthly_bill_before = Column(Float)
    monthly_bill_after = Column(Float)
    monthly_savings = Column(Float)
    annual_savings = Column(Float)
    savings_percentage = Column(Float)

    # Savings scenarios (stored as JSON)
    scenarios_json = Column(Text)  # JSON string of all 3 scenarios

    # Financials
    system_cost_gross = Column(Float)
    itc_credit = Column(Float)
    net_system_cost = Column(Float)
    monthly_payment = Column(Float)
    cash_payback_years = Column(Float)

    # Confidence
    confidence_score = Column(String(10))  # HIGH / MED / LOW
    assumptions_json = Column(Text)  # JSON array of assumption strings
    missing_data_json = Column(Text)  # JSON array

    # Meta
    source = Column(String(20), default="engine")  # engine / manual / import
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    # Relationships
    lead = relationship("Lead")


class Task(Base):
    """Auto-generated tasks tied to leads and appointments.

    Tasks are the ENFORCEMENT layer. They are created automatically by
    process_outcome() and cannot be ignored. Every outcome that requires
    a next step generates a task.

    Types: follow_up, rehash, reschedule, admin, install_coordination
    """
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False, index=True)
    appointment_id = Column(Integer, ForeignKey("appointments.id"), index=True)
    assigned_to = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_by = Column(Integer, ForeignKey("users.id"))

    # Task classification
    task_type = Column(String(30), nullable=False, index=True)
    # follow_up | rehash | reschedule | admin | install_coordination
    title = Column(String(255), nullable=False)
    description = Column(Text)

    # Scheduling
    due_date = Column(DateTime, nullable=False, index=True)
    completed_at = Column(DateTime)

    # Status: pending → completed | overdue (set by system check)
    status = Column(String(20), default="pending", nullable=False, index=True)

    # Outcome linkage
    source_outcome = Column(String(30))  # The outcome that triggered this task
    notes = Column(Text)
    completion_notes = Column(Text)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    lead = relationship("Lead", foreign_keys=[lead_id])
    appointment = relationship("Appointment", foreign_keys=[appointment_id])
    assignee = relationship("User", foreign_keys=[assigned_to])
    creator = relationship("User", foreign_keys=[created_by])


class Project(Base):
    """Post-sale project pipeline. Created ONLY when outcome = Closed (Signed).

    Tracks the installation lifecycle from sale through funding and payment.
    One lead → one project (at most).
    """
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False, unique=True, index=True)
    appointment_id = Column(Integer, ForeignKey("appointments.id"))
    closer_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    # System details (populated from lead or manually)
    system_size_kw = Column(Float)
    panel_count = Column(Integer)
    deal_value = Column(Float)

    # Pipeline stages
    install_status = Column(String(30), default="pending_survey")
    # pending_survey → survey_scheduled → survey_complete →
    # permits_submitted → permits_approved → install_scheduled →
    # installed → inspection_passed → pto_received

    funding_status = Column(String(30), default="pending")
    # pending → submitted → approved → funded

    payment_status = Column(String(30), default="pending")
    # pending → invoiced → paid

    # Key dates
    sold_date = Column(DateTime, nullable=False)
    survey_date = Column(DateTime)
    permit_date = Column(DateTime)
    install_date = Column(DateTime)
    inspection_date = Column(DateTime)
    pto_date = Column(DateTime)
    funded_date = Column(DateTime)
    paid_date = Column(DateTime)

    # Assignment
    installer_id = Column(Integer, ForeignKey("users.id"))
    project_manager_id = Column(Integer, ForeignKey("users.id"))

    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    lead = relationship("Lead", foreign_keys=[lead_id])
    closer = relationship("User", foreign_keys=[closer_id])
    installer = relationship("User", foreign_keys=[installer_id])
    project_manager = relationship("User", foreign_keys=[project_manager_id])


class Violation(Base):
    """Automatic violation records for rep accountability.

    Generated by the enforcement engine when reps fail to meet requirements:
    - missed_task: overdue task not completed
    - late_completion: task completed past deadline
    - no_update: no outcome after appointment within time window
    - missing_notes: action taken without required notes
    - skipped_followup: follow-up task ignored past grace period

    Violations are IMMUTABLE — once created, they cannot be deleted.
    Admins can acknowledge them but they remain in the record.
    """
    __tablename__ = "violations"

    id = Column(Integer, primary_key=True, index=True)
    rep_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    violation_type = Column(String(30), nullable=False, index=True)
    # missed_task | late_completion | no_update | missing_notes | skipped_followup
    severity = Column(String(10), default="warning", nullable=False)
    # warning | critical | severe

    # Context
    lead_id = Column(Integer, ForeignKey("leads.id"), index=True)
    task_id = Column(Integer, ForeignKey("tasks.id"), index=True)
    appointment_id = Column(Integer, ForeignKey("appointments.id"), index=True)
    description = Column(Text, nullable=False)

    # Admin handling
    acknowledged = Column(Boolean, default=False)
    acknowledged_by = Column(Integer, ForeignKey("users.id"))
    acknowledged_at = Column(DateTime)
    admin_notes = Column(Text)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    # Relationships
    rep = relationship("User", foreign_keys=[rep_id])
    lead = relationship("Lead", foreign_keys=[lead_id])
    task = relationship("Task", foreign_keys=[task_id])


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


class Invite(Base):
    """Rep invite tokens for self-registration."""
    __tablename__ = "invites"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, nullable=False)
    full_name = Column(String(255), nullable=False)
    token = Column(String(64), unique=True, nullable=False, index=True)
    role = Column(String(20), default="rep")
    invited_by = Column(Integer, ForeignKey("users.id"))
    used = Column(Boolean, default=False)
    used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Comment(Base):
    """Comments on leads, appointments, and projects — universal update feed."""
    __tablename__ = "comments"

    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), index=True)
    appointment_id = Column(Integer, ForeignKey("appointments.id"), index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    body = Column(Text, nullable=False)
    comment_type = Column(String(30), default="note")  # note, field_update, telemarketing, admin_note, system
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    user = relationship("User")
    lead = relationship("Lead")
    appointment = relationship("Appointment")
    project = relationship("Project")


class FileUpload(Base):
    """File uploads (photos, voice memos, documents) for leads, appointments, and projects."""
    __tablename__ = "file_uploads"

    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), index=True)
    appointment_id = Column(Integer, ForeignKey("appointments.id"), index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), index=True)
    uploaded_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    file_name = Column(String(255), nullable=False)
    file_type = Column(String(30), nullable=False)  # photo, voice_memo, document
    file_url = Column(String(500), nullable=False)  # base64 data URI or path
    file_size = Column(Integer)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    uploader = relationship("User")
