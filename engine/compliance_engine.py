from typing import Any

from app.routes.compliance_event_handler import (
    handle_new_compliance_event,
    handle_resume_compliance_event,
)


def start_compliance(
    event_payload: dict[str, Any],
) -> dict[str, Any]:
    """
    Starts a brand-new compliance workflow.
    """

    return handle_new_compliance_event(
        event_payload,
    )


def resume_compliance(
    event_payload: dict[str, Any],
) -> dict[str, Any]:
    """
    Resumes an interrupted compliance workflow.
    """

    return handle_resume_compliance_event(
        event_payload,
    )