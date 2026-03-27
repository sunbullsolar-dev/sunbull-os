#!/bin/bash
# ============================================================
# SUNBULL OS - Solar Sales Operations Command Center
# One-command launch script
# ============================================================

echo ""
echo "  ☀️  SUNBULL OS - Solar Operations Command Center"
echo "  ================================================"
echo ""

# Navigate to backend
cd "$(dirname "$0")/backend"

# Check Python
if ! command -v python3 &> /dev/null && ! command -v python &> /dev/null; then
    echo "❌ Python not found. Please install Python 3.8+"
    exit 1
fi

PYTHON=$(command -v python3 || command -v python)
echo "  Using Python: $($PYTHON --version)"

# Install dependencies
echo "  📦 Installing dependencies..."
$PYTHON -m pip install -r requirements.txt -q --break-system-packages 2>/dev/null || \
$PYTHON -m pip install -r requirements.txt -q 2>/dev/null

# Clean old database for fresh start (remove this line to keep data)
# rm -f sunbull.db

echo ""
echo "  🚀 Starting Sunbull OS..."
echo ""
echo "  ┌─────────────────────────────────────────────┐"
echo "  │  Open in browser: http://localhost:8000      │"
echo "  │                                              │"
echo "  │  LOGIN CREDENTIALS:                          │"
echo "  │  ─────────────────                           │"
echo "  │  Admin:        admin@sunbull.com / admin123  │"
echo "  │  Rep (John):   rep1@sunbull.com  / rep123    │"
echo "  │  Rep (Jane):   rep2@sunbull.com  / rep123    │"
echo "  │  Rep (Carlos): rep3@sunbull.com  / rep123    │"
echo "  │  Confirm Team: confirm@sunbull.com / conf123 │"
echo "  │                                              │"
echo "  │  Homeowner Portal: http://localhost:8000/#/portal │"
echo "  │                                              │"
echo "  │  API Docs: http://localhost:8000/docs        │"
echo "  │  Press Ctrl+C to stop                        │"
echo "  └─────────────────────────────────────────────┘"
echo ""

$PYTHON -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
