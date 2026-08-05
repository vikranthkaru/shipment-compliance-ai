from typing import Any

from fastapi import APIRouter

from engine.compliance_engine import (
    resume_compliance,
    start_compliance,
)


router = APIRouter(
    prefix="/compliance",
    tags=["Compliance"],
)


@router.post("/start")
def start(payload: dict[str, Any]):
    print("Received payload:", payload)
    return start_compliance(payload)


@router.post("/resume")
def resume(payload: dict[str, Any]):
    return resume_compliance(payload)

@router.get("/health")
def health():
    return {"status": "UP"}