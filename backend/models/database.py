"""
Sunbull OS Database Models
Production-grade SQLAlchemy models with SQLite + PostgreSQL compatibility
"""

import json
from datetime import datetime
from typing import Optional, Dict, Any
from enum import Enum

from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    Float,
    Boolean,
    DateTime,
    Text,
    ForeignKey,
    Index,
    JSON,
    Enum as SQLEnum,
    CheckConstraint,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker, Session
from sqlalchemy.pool import StaticPool

Base = declarative_base()


# ============================================================================
# ENUMS
# ============================================================================

class UserRole(str, Enum):
    """User roles in the system"""
    ADMIN = "admin"
    REP = "rep"
    CONFIRMATION = "confirmation"
    INSTALLER = "installer"


class LeadStatus(str, Enum):
    """Lead status through the pipeline"""
    NEW = "new"
    CONFIRMING = "confirming"
    CONFIRMED = "confirmed"
    UNCONFIRMED = "unconfirmed"
    RESCHEDULE_NEEDED = "reschedule_needed"
    ASSIGNED = "assigned"
    APPOINTMENT_SET = "appointment_set"
    CLOSED_WON = "closed_won"
    CLOSED_LOST = "closed_lost"
    FOLLOW_UP = "follow_up"
    REHASH = "rehash"


class LeadSource(str, Enum):
    """Where the lead came from"""
    DOOR_TO_DOOR = "door_to_door"
    CALL_CENTER = "call_center"
    WEB = "web"


class AppointmentStatus(str, Enum):
    """Appointment status"""
    SCHEDULED = "scheduled"
    CONFIRMED = "confirmed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    NO_SHOW = "no_show"
    RESCHEDULED = "rescheduled"


class ConfirmationOutcome(str, Enum):
    """Outcome of confirmation attempt"""
    ANSWERED = "answered"
    NO_ANSWER = "no_answer"
    VOICEMAIL = "voicemail"
    BUSY = "busy"
    WRONG_NUMBER = "wrong_number"


class AppointmentResultStatus(str, Enum):
    """Result of appointment completion"""
    CLOSED_WON = "closed_won"
    CLOSED_LOST = "closed_lost"
    FOLLOW_UP = "follow_up"
    NO_SHOW = "no_show"


class FollowUpStatus(str, Enum):
    """Follow-up status"""
    PENDING = "pending"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class PipelineStage(str, Enum):
    """Deal pipeline stages"""
    SOLD = "sold"
    INSTALLED = "installed"
    SUBMITTED_FOR_FUNDING = "submitted_for_funding"
    FUNDED = "funded"
    PAID = "paid"


class CommissionStatus(str, Enum):
    """Commission payment status"""
    PENDING = "pending"
    APPROVED = "approved"
    PAID = "paid"


class InstallerJobStatus(str, Enum):
    """Installer job status"""
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class TriggerOperator(str, Enum):
    """Operators for automation rule triggers"""
    GT = "gt"
    LT = "lt"
    EQ = "eq"
    CONTAINS = "contains"
    NOT_EQ = "not_eq"


class ActionType(str, Enum):
    """Automation action types"""
    ASSIGN_REP = "assign_rep"
    SEND_ALERT = "send_alert"
    CHANGE_STATUS = "change_status"
    CREATE_FOLLOWUP = "create_followup"
    RESCHEDULE = "reschedule"


class NotificationType(str, Enum):
    """Notification types"""
    NEW_LEAD = "new_lead"
    APPOINTMENT_CONFIRMED = "appointment_confirmed"
    FOLLOWUP_DUE = "followup_due"
    MISSED_UPDATE = "missed_update"
    SYSTEM_ALERT = "system_alert"


class AuditActionType(str, Enum):
    """Audit log action types"""
    LEAD_CREATED = "lead_created"
    LEAD_UPDATED = "lead_updated"
    LEAD_ASSIGNED = "lead_assigned"
    STATUS_CHANGED = "status_changed"
    APPOINTMENT_CREATED = "appointment_created"
    APPOINTMENT_UPDATED = "appointment_updated"
    DEAL_CREATED = "deal_created"
    DEAL_STAGE_CHANGED = "deal_stage_changed"
    RULE_TRIGGERED = "rule_triggered"
    LOGIN = "login"
    LEAD_LOCKED = "lead_locked"
    LEAD_UNLOCKED = "lead_unlocked"
    FRAUD_FLAG_CREATED = "fraud_flag_created"
    FRAUD_FLAG_RESOLVED = "fraud_flag_resolved"


class LeadRoleInLead(str, Enum):
    """User's role in relation to a lead"""
    SETTER = "setter"
    RUNNER = "runner"
    CLOSER = "closer"
    CONFIRMER = "confirmer"


class FlagType(str, Enum):
    """Fraud flag types"""
    HIDDEN_LEAD = "hidden_lead"
    UNAUTHORIZED_EDIT = "unauthorized_edit"
    SUSPICIOUS_PATTERN = "suspicious_pattern"
    MISSED_UPDATE = "missed_update"


class FlagSeverity(str, Enum):
    """Fraud flag severity"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# ============================================================================
# MODELS
# ============================================================================

class User(Base):
    """System users (reps, admins, confirmers, installers)"""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    full_name = Column(String(255), nullable=False)
    role = Column(SQLEnum(UserRole), nullable=False, default=UserRole.REP)
    phone = Column(String(20), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    # Relationships
    assigned_leads = relationship(
        "Lead",
        foreign_keys="Lead.assigned_rep_id",
        back_populates="assigned_rep",
        cascade="save-update"
    )
    setter_leads = relationship(
        "Lead",
        foreign_keys="Lead.setter_id",
        back_populates="setter",
        cascade="save-update"
    )
    closer_leads = relationship(
        "Lead",
        foreign_keys="Lead.closer_id",
        back_populates="closer",
        cascade="save-update"
    )
    appointments = relationship(
        "Appointment",
        back_populates="rep",
        cascade="save-update"
    )
    confirmation_attempts = relationship(
        "ConfirmationAttempt",
        back_populates="confirmer",
        cascade="save-update"
    )
    appointment_results = relationship(
        "AppointmentResult",
        back_populates="rep",
        cascade="save-update"
    )
    follow_ups = relationship(
        "FollowUp",
        back_populates="assigned_rep",
        cascade="save-update"
    )
    deals = relationship(
        "Deal",
        back_populates="rep",
        cascade="save-update"
    )
    created_rules = relationship(
        "AutomationRule",
        back_populates="created_by_user",
        cascade="save-update"
    )
    notifications = relationship(
        "Notification",
        back_populates="user",
        cascade="save-update, delete"
    )
    audit_logs = relationship(
        "AuditLog",
        back_populates="user",
        cascade="save-update"
    )
    lead_ownership_history = relationship(
        "LeadOwnershipHistory",
        back_populates="user",
        cascade="save-update"
    )
    fraud_flags_created = relationship(
        "FraudFlag",
        foreign_keys="FraudFlag.user_id",
        back_populates="user",
        cascade="save-update"
    )
    fraud_flags_resolved = relationship(
        "FraudFlag",
        foreign_keys="FraudFlag.resolved_by",
        back_populates="resolved_by_user",
        cascade="save-update"
    )
    installer = relationship(
        "Installer",
        back_populates="user",
        uselist=False,
        cascade="save-update"
    )

    __table_args__ = (
        Index("idx_user_email_active", "email", "is_active"),
        Index("idx_user_role", "role"),
    )


class Lead(Base):
    """Sales leads"""
    __tablename__ = "leads"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, index=True)
    address = Column(String(255), nullable=False)
    city = Column(String(100), nullable=False)
    state = Column(String(2), nullable=False)
    zip_code = Column(String(10), nullable=False, index=True)
    phone = Column(String(20), nullable=False)
    email = Column(String(255), nullable=True)
    bill_amount = Column(Float, nullable=True)
    monthly_kwh = Column(Float, nullable=True)
    cost_per_kwh = Column(Float, nullable=True)
    annual_usage = Column(Float, nullable=True)
    avg_monthly_bill = Column(Float, nullable=True)
    lead_source = Column(SQLEnum(LeadSource), nullable=False)
    lead_quality_score = Column(Integer, nullable=True, default=0)
    status = Column(
        SQLEnum(LeadStatus),
        nullable=False,
        default=LeadStatus.NEW,
        index=True
    )
    notes = Column(Text, nullable=True)
    assigned_rep_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    setter_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    closer_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    locked_by_rep_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    is_locked = Column(Boolean, default=False, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    assigned_rep = relationship(
        "User",
        foreign_keys=[assigned_rep_id],
        back_populates="assigned_leads"
    )
    setter = relationship(
        "User",
        foreign_keys=[setter_id],
        back_populates="setter_leads"
    )
    closer = relationship(
        "User",
        foreign_keys=[closer_id],
        back_populates="closer_leads"
    )
    bill_analysis = relationship(
        "BillAnalysis",
        back_populates="lead",
        cascade="all, delete-orphan",
        uselist=False
    )
    solar_proposal = relationship(
        "SolarProposal",
        back_populates="lead",
        cascade="all, delete-orphan",
        uselist=False
    )
    appointments = relationship(
        "Appointment",
        back_populates="lead",
        cascade="all, delete-orphan"
    )
    confirmation_attempts = relationship(
        "ConfirmationAttempt",
        back_populates="lead",
        cascade="all, delete-orphan"
    )
    follow_ups = relationship(
        "FollowUp",
        back_populates="lead",
        cascade="all, delete-orphan"
    )
    deal = relationship(
        "Deal",
        back_populates="lead",
        uselist=False,
        cascade="all, delete-orphan"
    )
    ownership_history = relationship(
        "LeadOwnershipHistory",
        back_populates="lead",
        cascade="all, delete-orphan"
    )
    fraud_flags = relationship(
        "FraudFlag",
        back_populates="lead",
        cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("idx_lead_status_assigned_rep", "status", "assigned_rep_id"),
        Index("idx_lead_created_at", "created_at"),
        Index("idx_lead_zip_code", "zip_code"),
        CheckConstraint("lead_quality_score >= 0 AND lead_quality_score <= 100"),
    )


class BillAnalysis(Base):
    """Utility bill analysis for leads"""
    __tablename__ = "bill_analyses"

    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False, unique=True, index=True)
    original_file_path = Column(String(500), nullable=True)
    monthly_kwh_data = Column(Text, nullable=True)  # JSON stored as text for SQLite
    cost_per_kwh = Column(Float, nullable=True)
    annual_usage = Column(Float, nullable=True)
    avg_monthly_bill = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    lead = relationship("Lead", back_populates="bill_analysis")

    def set_monthly_kwh_data(self, data: Dict[str, Any]) -> None:
        """Serialize monthly_kwh_data as JSON"""
        self.monthly_kwh_data = json.dumps(data) if data else None

    def get_monthly_kwh_data(self) -> Optional[Dict[str, Any]]:
        """Deserialize monthly_kwh_data from JSON"""
        if self.monthly_kwh_data:
            return json.loads(self.monthly_kwh_data)
        return None


class SolarProposal(Base):
    """Solar system proposals for leads"""
    __tablename__ = "solar_proposals"

    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False, unique=True, index=True)
    system_size_kw = Column(Float, nullable=False)
    num_panels = Column(Integer, nullable=False)
    panel_wattage = Column(Float, nullable=False, default=435)
    offset_percentage = Column(Float, nullable=False)
    monthly_payment = Column(Float, nullable=False)
    monthly_savings = Column(Float, nullable=False)
    annual_savings = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    lead = relationship("Lead", back_populates="solar_proposal")

    __table_args__ = (
        CheckConstraint("system_size_kw > 0"),
        CheckConstraint("num_panels > 0"),
        CheckConstraint("offset_percentage >= 0 AND offset_percentage <= 100"),
    )


class Appointment(Base):
    """Scheduled appointments with leads"""
    __tablename__ = "appointments"

    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False, index=True)
    rep_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    scheduled_date = Column(DateTime, nullable=False, index=True)
    scheduled_time = Column(String(8), nullable=True)  # HH:MM format
    end_time = Column(String(8), nullable=True)  # HH:MM format
    status = Column(
        SQLEnum(AppointmentStatus),
        nullable=False,
        default=AppointmentStatus.SCHEDULED,
        index=True
    )
    location_lat = Column(Float, nullable=True)
    location_lng = Column(Float, nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    lead = relationship("Lead", back_populates="appointments")
    rep = relationship("User", back_populates="appointments")
    result = relationship(
        "AppointmentResult",
        back_populates="appointment",
        uselist=False,
        cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("idx_appointment_rep_date", "rep_id", "scheduled_date"),
        Index("idx_appointment_lead_status", "lead_id", "status"),
    )


class ConfirmationAttempt(Base):
    """Call confirmation attempts"""
    __tablename__ = "confirmation_attempts"

    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False, index=True)
    confirmer_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    attempt_number = Column(Integer, nullable=False, default=1)
    call_time = Column(DateTime, nullable=False, index=True)
    outcome = Column(SQLEnum(ConfirmationOutcome), nullable=False)
    notes = Column(Text, nullable=True)
    next_attempt_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    lead = relationship("Lead", back_populates="confirmation_attempts")
    confirmer = relationship("User", back_populates="confirmation_attempts")

    __table_args__ = (
        Index("idx_confirmation_lead_attempt", "lead_id", "attempt_number"),
        Index("idx_confirmation_next_attempt", "next_attempt_at"),
    )


class AppointmentResult(Base):
    """Results from completed appointments"""
    __tablename__ = "appointment_results"

    id = Column(Integer, primary_key=True, index=True)
    appointment_id = Column(Integer, ForeignKey("appointments.id"), nullable=False, unique=True, index=True)
    rep_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    status = Column(SQLEnum(AppointmentResultStatus), nullable=False)
    notes = Column(Text, nullable=False)
    photo_path = Column(String(500), nullable=False)
    voice_memo_path = Column(String(500), nullable=True)
    submitted_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    appointment = relationship("Appointment", back_populates="result")
    rep = relationship("User", back_populates="appointment_results")


class FollowUp(Base):
    """Follow-up tasks for leads"""
    __tablename__ = "follow_ups"

    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False, index=True)
    assigned_rep_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    reason = Column(String(255), nullable=False)
    scheduled_date = Column(DateTime, nullable=False, index=True)
    status = Column(
        SQLEnum(FollowUpStatus),
        nullable=False,
        default=FollowUpStatus.PENDING,
        index=True
    )
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)

    # Relationships
    lead = relationship("Lead", back_populates="follow_ups")
    assigned_rep = relationship("User", back_populates="follow_ups")

    __table_args__ = (
        Index("idx_followup_rep_status", "assigned_rep_id", "status"),
        Index("idx_followup_scheduled_date", "scheduled_date"),
    )


class Deal(Base):
    """Closed deals tracking commission and pipeline"""
    __tablename__ = "deals"

    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False, unique=True, index=True)
    rep_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    installer_id = Column(Integer, ForeignKey("installers.id"), nullable=True, index=True)
    deal_value = Column(Float, nullable=False)
    rep_commission_rate = Column(Float, nullable=False, default=0.14)
    rep_commission_amount = Column(Float, nullable=False)
    company_revenue = Column(Float, nullable=False)
    pipeline_stage = Column(
        SQLEnum(PipelineStage),
        nullable=False,
        default=PipelineStage.SOLD,
        index=True
    )
    stage_entered_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    days_in_stage = Column(Integer, nullable=True)
    responsible_party = Column(String(50), nullable=False)
    commission_status = Column(
        SQLEnum(CommissionStatus),
        nullable=False,
        default=CommissionStatus.PENDING,
        index=True
    )
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    lead = relationship("Lead", back_populates="deal")
    rep = relationship("User", back_populates="deals")
    installer = relationship("Installer", back_populates="deals")
    installer_jobs = relationship(
        "InstallerJob",
        back_populates="deal",
        cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("idx_deal_pipeline_stage", "pipeline_stage"),
        Index("idx_deal_rep_commission", "rep_id", "commission_status"),
        CheckConstraint("deal_value > 0"),
        CheckConstraint("rep_commission_rate >= 0"),
    )


class Installer(Base):
    """Installation contractors"""
    __tablename__ = "installers"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True, index=True)
    company_name = Column(String(255), nullable=False)
    jobs_completed = Column(Integer, nullable=False, default=0)
    avg_install_days = Column(Float, nullable=True)
    cancellation_rate = Column(Float, nullable=True)
    funding_delay_avg_days = Column(Float, nullable=True)
    performance_score = Column(Float, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    user = relationship("User", back_populates="installer")
    jobs = relationship(
        "InstallerJob",
        back_populates="installer",
        cascade="all, delete-orphan"
    )
    deals = relationship(
        "Deal",
        back_populates="installer",
        cascade="save-update"
    )

    __table_args__ = (
        CheckConstraint("jobs_completed >= 0"),
        CheckConstraint("cancellation_rate >= 0 AND cancellation_rate <= 1"),
    )


class InstallerJob(Base):
    """Individual installation jobs assigned to installers"""
    __tablename__ = "installer_jobs"

    id = Column(Integer, primary_key=True, index=True)
    installer_id = Column(Integer, ForeignKey("installers.id"), nullable=False, index=True)
    deal_id = Column(Integer, ForeignKey("deals.id"), nullable=False, unique=True, index=True)
    assigned_date = Column(DateTime, nullable=False, default=datetime.utcnow)
    started_date = Column(DateTime, nullable=True)
    completed_date = Column(DateTime, nullable=True)
    status = Column(
        SQLEnum(InstallerJobStatus),
        nullable=False,
        default=InstallerJobStatus.ASSIGNED,
        index=True
    )
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    installer = relationship("Installer", back_populates="jobs")
    deal = relationship("Deal", back_populates="installer_jobs")

    __table_args__ = (
        Index("idx_installer_job_status", "installer_id", "status"),
        Index("idx_installer_job_completed", "completed_date"),
    )


class AutomationRule(Base):
    """Rules for automated actions and alerts"""
    __tablename__ = "automation_rules"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, index=True)
    description = Column(Text, nullable=True)
    trigger_field = Column(String(100), nullable=False)
    trigger_operator = Column(SQLEnum(TriggerOperator), nullable=False)
    trigger_value = Column(String(500), nullable=False)
    action_type = Column(SQLEnum(ActionType), nullable=False)
    action_config = Column(Text, nullable=False)  # JSON stored as text
    is_active = Column(Boolean, default=True, nullable=False, index=True)
    priority = Column(Integer, nullable=False, default=5)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    created_by_user = relationship("User", back_populates="created_rules")

    def set_action_config(self, config: Dict[str, Any]) -> None:
        """Serialize action_config as JSON"""
        self.action_config = json.dumps(config)

    def get_action_config(self) -> Dict[str, Any]:
        """Deserialize action_config from JSON"""
        return json.loads(self.action_config) if self.action_config else {}

    __table_args__ = (
        Index("idx_automation_rule_active", "is_active"),
        Index("idx_automation_rule_priority", "priority"),
    )


class Notification(Base):
    """User notifications"""
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    type = Column(SQLEnum(NotificationType), nullable=False)
    title = Column(String(255), nullable=False)
    message = Column(Text, nullable=False)
    is_read = Column(Boolean, default=False, nullable=False, index=True)
    related_entity_type = Column(String(100), nullable=True)
    related_entity_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    # Relationships
    user = relationship("User", back_populates="notifications")

    __table_args__ = (
        Index("idx_notification_user_read", "user_id", "is_read"),
        Index("idx_notification_created", "created_at"),
    )


class AuditLog(Base):
    """Comprehensive audit trail for all actions"""
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    action_type = Column(SQLEnum(AuditActionType), nullable=False, index=True)
    entity_type = Column(String(100), nullable=False)
    entity_id = Column(Integer, nullable=False)
    previous_value = Column(Text, nullable=True)  # JSON stored as text
    new_value = Column(Text, nullable=True)  # JSON stored as text
    ip_address = Column(String(45), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    # Relationships
    user = relationship("User", back_populates="audit_logs")

    def set_previous_value(self, value: Optional[Dict[str, Any]]) -> None:
        """Serialize previous_value as JSON"""
        self.previous_value = json.dumps(value) if value else None

    def get_previous_value(self) -> Optional[Dict[str, Any]]:
        """Deserialize previous_value from JSON"""
        if self.previous_value:
            return json.loads(self.previous_value)
        return None

    def set_new_value(self, value: Optional[Dict[str, Any]]) -> None:
        """Serialize new_value as JSON"""
        self.new_value = json.dumps(value) if value else None

    def get_new_value(self) -> Optional[Dict[str, Any]]:
        """Deserialize new_value from JSON"""
        if self.new_value:
            return json.loads(self.new_value)
        return None

    __table_args__ = (
        Index("idx_audit_log_entity", "entity_type", "entity_id"),
        Index("idx_audit_log_user_action", "user_id", "action_type"),
        Index("idx_audit_log_action_timestamp", "action_type", "created_at"),
    )


class LeadOwnershipHistory(Base):
    """Historical tracking of lead ownership and roles"""
    __tablename__ = "lead_ownership_history"

    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    role_in_lead = Column(SQLEnum(LeadRoleInLead), nullable=False)
    started_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    ended_at = Column(DateTime, nullable=True)
    notes = Column(Text, nullable=True)

    # Relationships
    lead = relationship("Lead", back_populates="ownership_history")
    user = relationship("User", back_populates="lead_ownership_history")

    __table_args__ = (
        Index("idx_ownership_lead_user", "lead_id", "user_id"),
        Index("idx_ownership_started_at", "started_at"),
    )


class FraudFlag(Base):
    """Fraud detection and flagging"""
    __tablename__ = "fraud_flags"

    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    flag_type = Column(SQLEnum(FlagType), nullable=False, index=True)
    description = Column(Text, nullable=False)
    severity = Column(SQLEnum(FlagSeverity), nullable=False, index=True)
    is_resolved = Column(Boolean, default=False, nullable=False, index=True)
    resolved_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    # Relationships
    lead = relationship("Lead", back_populates="fraud_flags")
    user = relationship("User", foreign_keys=[user_id], back_populates="fraud_flags_created")
    resolved_by_user = relationship("User", foreign_keys=[resolved_by], back_populates="fraud_flags_resolved")

    __table_args__ = (
        Index("idx_fraud_flag_lead_resolved", "lead_id", "is_resolved"),
        Index("idx_fraud_flag_severity", "severity"),
    )


# ============================================================================
# DATABASE INITIALIZATION AND MANAGEMENT
# ============================================================================

class Database:
    """Database connection and session management"""

    def __init__(self, database_url: str = "sqlite:///./sunbull_os.db"):
        """
        Initialize database connection

        Args:
            database_url: SQLAlchemy database URL
                - SQLite: sqlite:///./sunbull_os.db
                - PostgreSQL: postgresql://user:password@localhost/sunbull_os
        """
        self.database_url = database_url

        # Use StaticPool for SQLite to avoid threading issues
        if "sqlite" in database_url:
            self.engine = create_engine(
                database_url,
                connect_args={"check_same_thread": False},
                poolclass=StaticPool,
            )
        else:
            # PostgreSQL or other databases
            self.engine = create_engine(
                database_url,
                pool_pre_ping=True,  # Verify connections before using them
                echo=False,
            )

        self.SessionLocal = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=self.engine,
        )

    def create_tables(self) -> None:
        """Create all tables in the database"""
        Base.metadata.create_all(bind=self.engine)
        print("Database tables created successfully")

    def drop_tables(self) -> None:
        """Drop all tables (use with caution!)"""
        Base.metadata.drop_all(bind=self.engine)
        print("Database tables dropped")

    def get_session(self) -> Session:
        """Get a new database session"""
        return self.SessionLocal()

    def close(self) -> None:
        """Close all connections"""
        self.engine.dispose()


# Global database instance (initialized in main app)
_db_instance: Optional[Database] = None


def init_db(database_url: str = "sqlite:///./sunbull_os.db") -> Database:
    """
    Initialize the global database instance

    Args:
        database_url: SQLAlchemy database URL

    Returns:
        Database instance
    """
    global _db_instance
    _db_instance = Database(database_url)
    _db_instance.create_tables()
    return _db_instance


def get_db() -> Session:
    """
    FastAPI dependency for getting database session

    Usage in FastAPI:
        @app.get("/leads/")
        async def get_leads(db: Session = Depends(get_db)):
            return db.query(Lead).all()
    """
    if _db_instance is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")

    db = _db_instance.get_session()
    try:
        yield db
    finally:
        db.close()


def close_db() -> None:
    """Close the global database instance"""
    global _db_instance
    if _db_instance:
        _db_instance.close()
        _db_instance = None


# ============================================================================
# INITIALIZATION FOR TESTING
# ============================================================================

if __name__ == "__main__":
    # Initialize in-memory SQLite database for testing
    db = init_db("sqlite:///:memory:")

    # Create a test session
    session = db.get_session()

    # Create a test user
    test_user = User(
        email="test@sunbull.com",
        password_hash="hashed_password",
        full_name="Test User",
        role=UserRole.ADMIN,
        phone="555-0000",
    )
    session.add(test_user)
    session.commit()

    print(f"Test user created: {test_user.email}")

    # Verify tables exist
    print("\nDatabase tables created successfully!")
    print("Tables:")
    for table in Base.metadata.tables.keys():
        print(f"  - {table}")

    session.close()
    db.close()
