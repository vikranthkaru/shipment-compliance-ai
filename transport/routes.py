import logging
from typing import Any

from fastapi import APIRouter

from engine.compliance_engine import (
    resume_compliance,
    start_compliance,
)
logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/compliance",
    tags=["Compliance"],
)


@router.post("/start")
def start(payload: dict[str, Any]):
    logger.info("Received compliance start request")
    return start_compliance(payload)


@router.post("/resume")
def resume(payload: dict[str, Any]):
    logger.info("Received compliance resume request")
    return resume_compliance(payload)
