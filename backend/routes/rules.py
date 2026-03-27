"""Automation rule engine routes."""
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import (
    AutomationRule, Lead, User, LeadStatus, RehashEntry,
)
from app.auth import get_current_user, require_role
from services.audit import create_audit_log
from datetime import datetime, timedelta
import json

router = APIRouter(prefix="/api/rules", tags=["rules"])


class RuleCreate(BaseModel):
    """Create automation rule request model."""

    name: str
    description: Optional[str] = None
    condition_field: str  # "bill_amount", "status", "quality_score", etc.
    condition_operator: str  # "gt", "lt", "eq", "contains", etc.
    condition_value: str
    action_type: str  # "assign_rep", "send_alert", "change_status", "create_followup"
    action_params: dict = {}
    priority: int = 100


class RuleUpdate(BaseModel):
    """Update automation rule request model."""

    name: Optional[str] = None
    description: Optional[str] = None
    condition_field: Optional[str] = None
    condition_operator: Optional[str] = None
    condition_value: Optional[str] = None
    action_type: Optional[str] = None
    action_params: Optional[dict] = None
    is_active: Optional[bool] = None


class RuleResponse(BaseModel):
    """Rule response model."""

    id: int
    name: str
    description: Optional[str]
    condition_field: str
    condition_operator: str
    condition_value: str
    action_type: str
    action_params: dict
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


@router.get("", response_model=List[RuleResponse])
def list_rules(
    active_only: bool = Query(True),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    List all automation rules.

    Args:
        active_only: Only return active rules
        current_user: Current authenticated user
        db: Database session

    Returns:
        List of AutomationRule records
    """
    query = db.query(AutomationRule)

    if active_only:
        query = query.filter(AutomationRule.is_active == True)

    rules = query.order_by(AutomationRule.created_at.desc()).all()

    return [
        {
            "id": r.id,
            "name": r.name,
            "description": r.description,
            "condition_field": r.condition_field,
            "condition_operator": r.condition_operator,
            "condition_value": r.condition_value,
            "action_type": r.action_type,
            "action_params": (json.loads(r.action_params) if isinstance(r.action_params, str) else r.action_params) or {},
            "is_active": r.is_active,
            "created_at": r.created_at,
        }
        for r in rules
    ]


@router.post("", response_model=RuleResponse)
def create_rule(
    rule_data: RuleCreate,
    current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """
    Create an automation rule (admin only).

    Args:
        rule_data: Rule creation data
        current_user: Current authenticated user (admin)
        db: Database session

    Returns:
        Created AutomationRule record
    """
    rule = AutomationRule(
        name=rule_data.name,
        description=rule_data.description,
        condition_field=rule_data.condition_field,
        condition_operator=rule_data.condition_operator,
        condition_value=rule_data.condition_value,
        action_type=rule_data.action_type,
        action_params=json.dumps(rule_data.action_params) if isinstance(rule_data.action_params, dict) else rule_data.action_params,
        is_active=True,
    )

    db.add(rule)
    db.flush()

    # Create audit log
    create_audit_log(
        db=db,
        user_id=current_user.id,
        action_type="create",
        entity_type="rule",
        entity_id=rule.id,
        new_value=rule.name,
        details=f"Automation rule created: {rule.name}",
    )

    db.commit()
    db.refresh(rule)

    return {
        "id": rule.id,
        "name": rule.name,
        "description": rule.description,
        "condition_field": rule.condition_field,
        "condition_operator": rule.condition_operator,
        "condition_value": rule.condition_value,
        "action_type": rule.action_type,
        "action_params": (json.loads(rule.action_params) if isinstance(rule.action_params, str) else rule.action_params) or {},
        "is_active": rule.is_active,
        "created_at": rule.created_at,
    }


@router.put("/{rule_id}", response_model=RuleResponse)
def update_rule(
    rule_id: int,
    rule_data: RuleUpdate,
    current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """
    Update an automation rule (admin only).

    Args:
        rule_id: ID of the rule
        rule_data: Updated rule data
        current_user: Current authenticated user (admin)
        db: Database session

    Returns:
        Updated AutomationRule record
    """
    rule = db.query(AutomationRule).filter(AutomationRule.id == rule_id).first()

    if not rule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found"
        )

    # Update fields
    if rule_data.name:
        rule.name = rule_data.name
    if rule_data.description is not None:
        rule.description = rule_data.description
    if rule_data.condition_field:
        rule.condition_field = rule_data.condition_field
    if rule_data.condition_operator:
        rule.condition_operator = rule_data.condition_operator
    if rule_data.condition_value:
        rule.condition_value = rule_data.condition_value
    if rule_data.action_type:
        rule.action_type = rule_data.action_type
    if rule_data.action_params:
        rule.action_params = rule_data.action_params
    if rule_data.is_active is not None:
        rule.is_active = rule_data.is_active

    db.add(rule)
    db.flush()

    # Create audit log
    create_audit_log(
        db=db,
        user_id=current_user.id,
        action_type="update",
        entity_type="rule",
        entity_id=rule.id,
        details=f"Rule '{rule.name}' updated",
    )

    db.commit()
    db.refresh(rule)

    return {
        "id": rule.id,
        "name": rule.name,
        "description": rule.description,
        "condition_field": rule.condition_field,
        "condition_operator": rule.condition_operator,
        "condition_value": rule.condition_value,
        "action_type": rule.action_type,
        "action_params": (json.loads(rule.action_params) if isinstance(rule.action_params, str) else rule.action_params) or {},
        "is_active": rule.is_active,
        "created_at": rule.created_at,
    }


@router.delete("/{rule_id}")
def deactivate_rule(
    rule_id: int,
    current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """
    Deactivate an automation rule (soft delete).

    Args:
        rule_id: ID of the rule
        current_user: Current authenticated user (admin)
        db: Database session

    Returns:
        Success message
    """
    rule = db.query(AutomationRule).filter(AutomationRule.id == rule_id).first()

    if not rule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found"
        )

    rule.is_active = False
    db.add(rule)
    db.flush()

    # Create audit log
    create_audit_log(
        db=db,
        user_id=current_user.id,
        action_type="deactivate",
        entity_type="rule",
        entity_id=rule.id,
        details=f"Rule '{rule.name}' deactivated",
    )

    db.commit()

    return {"message": "Rule deactivated"}


def _evaluate_condition(lead: Lead, condition: dict) -> bool:
    """
    Evaluate if a lead matches a rule condition.

    Args:
        lead: Lead to evaluate
        condition: Condition dict with field, operator, value

    Returns:
        True if condition matches, False otherwise
    """
    field = condition["condition_field"]
    operator = condition["condition_operator"]
    value = condition["condition_value"]

    # Get lead field value (canonical field names)
    if field in ("bill_amount", "average_monthly_bill"):
        lead_value = lead.average_monthly_bill
    elif field in ("status", "deal_status"):
        lead_value = lead.deal_status
    elif field in ("quality_score", "lead_quality_score"):
        lead_value = lead.lead_quality_score
    elif field == "city":
        lead_value = lead.city
    elif field == "state":
        lead_value = lead.state
    elif field == "source_type":
        lead_value = lead.source_type
    elif field == "homeowner_status":
        lead_value = lead.homeowner_status
    else:
        return False

    # Evaluate operator
    if operator == "gt":
        return lead_value > float(value)
    elif operator == "lt":
        return lead_value < float(value)
    elif operator == "eq":
        return str(lead_value) == str(value)
    elif operator == "contains":
        return value.lower() in str(lead_value).lower()
    elif operator == "gte":
        return lead_value >= float(value)
    elif operator == "lte":
        return lead_value <= float(value)

    return False


@router.post("/evaluate/{lead_id}")
def evaluate_rules(
    lead_id: int,
    current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """
    Evaluate all active rules against a lead.

    Loads all active rules sorted by priority, checks if trigger condition
    matches, and executes the action if match.

    Args:
        lead_id: ID of the lead to evaluate
        current_user: Current authenticated user (admin)
        db: Database session

    Returns:
        Evaluation results and actions taken

    Raises:
        HTTPException: 404 if lead not found
    """
    lead = db.query(Lead).filter(Lead.id == lead_id).first()

    if not lead:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found"
        )

    # Get all active rules sorted by priority
    rules = (
        db.query(AutomationRule)
        .filter(AutomationRule.is_active == True)
        .order_by(AutomationRule.created_at.asc())
        .all()
    )

    executed_actions = []

    for rule in rules:
        # Check if condition matches
        if _evaluate_condition(lead, {
            "condition_field": rule.condition_field,
            "condition_operator": rule.condition_operator,
            "condition_value": rule.condition_value,
        }):
            # Execute action
            params = json.loads(rule.action_params) if isinstance(rule.action_params, str) else (rule.action_params or {})
            if rule.action_type == "assign_rep":
                if "rep_id" in params:
                    lead.assigned_rep_id = params["rep_id"]
                    executed_actions.append(
                        f"Assigned to rep {params['rep_id']}"
                    )

            elif rule.action_type == "change_status":
                new_status = params.get("new_status") or params.get("status")
                if new_status:
                    lead.deal_status = new_status
                    executed_actions.append(
                        f"Status changed to {new_status}"
                    )

            elif rule.action_type == "create_rehash":
                # Create rehash entry
                rehash = RehashEntry(
                    lead_id=lead.id,
                    original_rep_id=lead.assigned_rep_id,
                    reason="Rule triggered rehash",
                    callback_at=datetime.utcnow() + timedelta(hours=1),
                    status="pending",
                )
                db.add(rehash)
                executed_actions.append("Added to rehash queue")

            # Log rule execution
            create_audit_log(
                db=db,
                user_id=current_user.id,
                action_type="rule_executed",
                entity_type="lead",
                entity_id=lead.id,
                details=f"Rule '{rule.name}' executed",
            )

    db.add(lead)
    db.commit()

    return {
        "lead_id": lead_id,
        "rules_evaluated": len(rules),
        "actions_executed": executed_actions,
    }
