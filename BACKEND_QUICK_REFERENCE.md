# Sunbull OS Backend - Quick Reference

## Files Created

### Core Application
- **main.py** - FastAPI app with routers, CORS, static/template serving, and DB initialization

### Service Layer
- **services/audit.py** - Audit logging for all data changes
- **services/scoring.py** - Lead quality scoring (0-100 scale)
- **services/notifications.py** - User notification management

### API Routes (9 route modules)
- **routes/auth.py** - Login & authentication
- **routes/leads.py** - Lead CRUD with quality scoring & locking
- **routes/appointments.py** - Appointment scheduling with 90-min spacing enforcement
- **routes/confirmation.py** - Confirmation workflow
- **routes/deals.py** - Pipeline management & commissions
- **routes/admin.py** - Dashboard & analytics
- **routes/rules.py** - Automation rule engine
- **routes/dispatch.py** - Intelligent lead assignment
- **routes/solar.py** - Solar sizing & proposal generation

## Authentication

### Login Flow
```
POST /api/auth/login
{
  "email": "rep1@sunbull.com",
  "password": "rep123"
}
→ Returns: {
  "access_token": "eyJ0eXAi...",
  "token_type": "bearer",
  "user": { id, email, full_name, role, ... }
}
```

### Seeded Users
- **Admin**: admin@sunbull.com / admin123
- **Rep 1**: rep1@sunbull.com / rep123 (close_rate: 35%, territory: 900)
- **Rep 2**: rep2@sunbull.com / rep123 (close_rate: 42%, territory: 901)
- **Confirmation**: confirmation@sunbull.com / conf123
- **Installer 1**: installer1@sunbull.com / install123
- **Installer 2**: installer2@sunbull.com / install123

## Key Features

### Lead Management
- Auto-calculated quality score (0-100)
- Lead locking (only assigned rep or admin can edit)
- Audit log for every field change
- Role-filtered views (reps see only their leads)

### Appointments
- 90-minute spacing enforcement
- Calendar view (next 30 days)
- Mandatory result submission (status, notes)

### Automation
- Rule engine with condition evaluation
- Auto-dispatch with weighted scoring:
  - Rep close rate: 40%
  - Availability: 30%
  - Lead quality match: 20%
  - Location proximity: 10%

### Solar
- System sizing (435W panels)
- 20-year loan calculations @ 5% interest
- Payback period analysis

### Analytics
- Rep performance metrics
- Installer rankings
- Pipeline overview
- Fraud flag tracking

## Database Models Used

From `/app/models.py`:
- User, Lead, Appointment, ConfirmationAttempt
- LeadTimeline (audit logs), Project, Commission
- AutomationRule, Notification, AccountabilityFlag, RehashEntry

## Running the App

```bash
cd /sessions/laughing-determined-knuth/sunbull-os/backend
pip install -r requirements.txt
uvicorn main:app --reload
```

## API Documentation

Once running:
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## Environment

- SQLite database: `./sunbull.db`
- Secret key: Change `SECRET_KEY` in `app/auth.py` for production
- CORS: Configured for all origins (change in `main.py` for production)

## Code Quality

✓ Full type hints throughout
✓ Pydantic validation for all requests
✓ Comprehensive error handling
✓ All data-modifying operations create audit logs
✓ Role-based access control on sensitive endpoints
✓ Password hashing with bcrypt
✓ JWT authentication with 24-hour expiration
