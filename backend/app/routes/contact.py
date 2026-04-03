"""Site contact form API (minimal stub — wired from main.py)."""

from fastapi import APIRouter

router = APIRouter()


@router.post("/contact")
async def submit_contact():
    return {"ok": False, "detail": "Contact form is not configured on this deployment."}
