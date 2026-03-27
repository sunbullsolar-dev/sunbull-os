"""
Sunbull OS - Email Alert Service
Sends email alerts when leads are created and appointments are booked.
Uses SMTP (Gmail-compatible). Falls back to console logging if not configured.

To enable real emails:
  export SMTP_USER=sunbullsolar@gmail.com
  export SMTP_PASS=your-gmail-app-password
"""
import os
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional

logger = logging.getLogger("sunbull.email")

# SMTP config from environment
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")  # Gmail app password
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "sunbullsolar@gmail.com")
FROM_EMAIL = os.environ.get("FROM_EMAIL", SMTP_USER or "noreply@sunbull.com")


def _is_configured() -> bool:
    return bool(SMTP_USER and SMTP_PASS)


def _send_email(to: str, subject: str, html_body: str) -> bool:
    """Send an email via SMTP. Returns True on success."""
    if not _is_configured():
        logger.info(f"[EMAIL PREVIEW] To: {to} | Subject: {subject}")
        print(f"\n{'='*50}")
        print(f"  EMAIL (not sent - SMTP not configured)")
        print(f"  To: {to}")
        print(f"  Subject: {subject}")
        print(f"{'='*50}\n")
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = FROM_EMAIL
        msg["To"] = to
        msg["Subject"] = subject
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.send_message(msg)

        logger.info(f"Email sent to {to}: {subject}")
        return True
    except Exception as e:
        logger.error(f"Email failed to {to}: {e}")
        print(f"\n  EMAIL FAILED to {to}: {e}\n")
        return False


def notify_new_lead(
    lead_name: str,
    phone: str,
    email: Optional[str],
    city: str,
    monthly_bill: float,
    lead_id: int,
):
    """Notify admin when a new lead comes in from the website."""
    subject = f"New Lead: {lead_name} - ${monthly_bill:.0f}/mo bill"
    body = f"""
    <div style="font-family:-apple-system,sans-serif;max-width:500px;margin:0 auto;">
        <h2 style="color:#FF9F0A;margin-bottom:20px;">New Lead from Sunbull Website</h2>
        <table style="width:100%;border-collapse:collapse;">
            <tr><td style="padding:8px 12px;color:#86868b;border-bottom:1px solid #f0f0f0;">Name</td><td style="padding:8px 12px;font-weight:600;border-bottom:1px solid #f0f0f0;">{lead_name}</td></tr>
            <tr><td style="padding:8px 12px;color:#86868b;border-bottom:1px solid #f0f0f0;">Phone</td><td style="padding:8px 12px;font-weight:600;border-bottom:1px solid #f0f0f0;">{phone}</td></tr>
            <tr><td style="padding:8px 12px;color:#86868b;border-bottom:1px solid #f0f0f0;">Email</td><td style="padding:8px 12px;border-bottom:1px solid #f0f0f0;">{email or 'not provided'}</td></tr>
            <tr><td style="padding:8px 12px;color:#86868b;border-bottom:1px solid #f0f0f0;">City</td><td style="padding:8px 12px;border-bottom:1px solid #f0f0f0;">{city}</td></tr>
            <tr><td style="padding:8px 12px;color:#86868b;border-bottom:1px solid #f0f0f0;">Monthly Bill</td><td style="padding:8px 12px;font-weight:700;color:#FF453A;border-bottom:1px solid #f0f0f0;">${monthly_bill:.2f}</td></tr>
            <tr><td style="padding:8px 12px;color:#86868b;">Lead ID</td><td style="padding:8px 12px;">#{lead_id}</td></tr>
        </table>
        <p style="margin-top:20px;color:#86868b;font-size:13px;">Log into Sunbull OS to view and assign this lead.</p>
    </div>
    """
    _send_email(ADMIN_EMAIL, subject, body)


def notify_appointment_booked(
    lead_name: str,
    phone: str,
    address: str,
    date: str,
    time: str,
    rep_name: str,
    rep_email: str,
    monthly_bill: float,
    appointment_id: int,
):
    """Notify the assigned rep AND admin when an appointment is booked."""
    subject = f"Appointment Booked: {lead_name} on {date} at {time}"
    body = f"""
    <div style="font-family:-apple-system,sans-serif;max-width:500px;margin:0 auto;">
        <h2 style="color:#30D158;margin-bottom:20px;">Appointment Booked</h2>
        <table style="width:100%;border-collapse:collapse;">
            <tr><td style="padding:8px 12px;color:#86868b;border-bottom:1px solid #f0f0f0;">Homeowner</td><td style="padding:8px 12px;font-weight:600;border-bottom:1px solid #f0f0f0;">{lead_name}</td></tr>
            <tr><td style="padding:8px 12px;color:#86868b;border-bottom:1px solid #f0f0f0;">Phone</td><td style="padding:8px 12px;font-weight:600;border-bottom:1px solid #f0f0f0;">{phone}</td></tr>
            <tr><td style="padding:8px 12px;color:#86868b;border-bottom:1px solid #f0f0f0;">Address</td><td style="padding:8px 12px;border-bottom:1px solid #f0f0f0;">{address}</td></tr>
            <tr><td style="padding:8px 12px;color:#86868b;border-bottom:1px solid #f0f0f0;">Date</td><td style="padding:8px 12px;font-weight:600;border-bottom:1px solid #f0f0f0;">{date}</td></tr>
            <tr><td style="padding:8px 12px;color:#86868b;border-bottom:1px solid #f0f0f0;">Time</td><td style="padding:8px 12px;font-weight:600;border-bottom:1px solid #f0f0f0;">{time}</td></tr>
            <tr><td style="padding:8px 12px;color:#86868b;border-bottom:1px solid #f0f0f0;">Monthly Bill</td><td style="padding:8px 12px;color:#FF453A;border-bottom:1px solid #f0f0f0;">${monthly_bill:.2f}</td></tr>
            <tr><td style="padding:8px 12px;color:#86868b;border-bottom:1px solid #f0f0f0;">Assigned Rep</td><td style="padding:8px 12px;font-weight:600;border-bottom:1px solid #f0f0f0;">{rep_name}</td></tr>
            <tr><td style="padding:8px 12px;color:#86868b;">Appointment ID</td><td style="padding:8px 12px;">#{appointment_id}</td></tr>
        </table>
        <p style="margin-top:20px;color:#86868b;font-size:13px;">Auto-dispatched by Sunbull OS.</p>
    </div>
    """
    # Notify the assigned rep
    _send_email(rep_email, subject, body)
    # Also notify admin
    if rep_email != ADMIN_EMAIL:
        _send_email(ADMIN_EMAIL, subject, body)
