from uuid import uuid4
from typing import Any
from langgraph.types import Command

from agents.compliance_agent.graph import build_compliance_graph
from agents.compliance_agent.helpers import helper_stringify_list
from app.routes.events import (
    handle_shipment_event,
)
from services.salesforce_service import (
    save_route_check,
)

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger(__name__)

def handle_new_compliance_event(
    event_payload: dict,
) -> dict:
    """
    Starts a brand new compliance workflow.
    """
    logger.info(
        "Starting compliance workflow for shipment %s",
        event_payload.get("ShipmentId__c", "Unknown"),
    )

    shipment_result = handle_shipment_event(event_payload)

    if shipment_result["status"] != "shipment_context_fetched":
        raise RuntimeError(
            shipment_result.get("reason")
        )

    initial_state = {
        "event": shipment_result["event"],
        "shipment_context": shipment_result["shipmentContext"],
    }

    thread_id = str(uuid4())

    config = {
        "configurable": {
            "thread_id": thread_id,
        }
    }

    graph = build_compliance_graph()

    result = graph.invoke(
        initial_state,
        config=config,
    )

    logger.info("Thread ID: %s", thread_id)
    logger.info("Interrupts: %s", result.get("__interrupt__", []))

    process_interrupts(
        result=result,
        thread_id=thread_id,
    )

    return result


def handle_resume_compliance_event(
    event_message: dict[str, Any],
) -> dict:
    """
    Resumes one interrupted route-compliance worker.

    Supports both formats:

    1. Full Salesforce Pub/Sub message:
       {
           "schema": "...",
           "payload": {
               "ThreadId__c": "...",
               "InterruptId__c": "...",
               ...
           },
           "event": {...}
       }

    2. Flattened event payload:
       {
           "ThreadId__c": "...",
           "InterruptId__c": "...",
           ...
       }
    """
    event_payload = event_message.get(
        "payload",
        event_message,
    )
    event_type = event_payload.get("EventType__c")
    if event_type != "ROUTE_COMPLIANCE_RETRIGGERED":
        raise ValueError(
            "Unsupported compliance resume event type: "
            f"{event_type}"
        )
    thread_id = event_payload.get("ThreadId__c")
    interrupt_id = event_payload.get("InterruptId__c")
    route_check_id = event_payload.get(
        "ComplianceRouteCheckId__c"
    )
    reviewer_comments = event_payload.get(
        "ReviewerComments__c"
    )
    missing_fields = []

    if not thread_id:
        missing_fields.append("ThreadId__c")

    if not interrupt_id:
        missing_fields.append("InterruptId__c")

    if not route_check_id:
        missing_fields.append(
            "ComplianceRouteCheckId__c"
        )

    if not reviewer_comments:
        missing_fields.append("ReviewerComments__c")

    if missing_fields:
        raise ValueError(
            "Missing required resume event fields: "
            + ", ".join(missing_fields)
        )

    config = {
        "configurable": {
            "thread_id": thread_id,
        }
    }

    reviewer_response = {
        "event_type": event_type,
        "event_id": event_payload.get("Event_Id__c"),
        "route_check_id": route_check_id,
        "reviewer_comments": reviewer_comments,
        "reviewed_by": event_payload.get(
            "Triggered_By__c"
        ),
        "reviewed_at": event_payload.get(
            "Triggered_At__c"
        ),
        "reason": event_payload.get("Reason__c"),
        "shipment_id": event_payload.get(
            "ShipmentId__c"
        ),
        "shipment_number": event_payload.get(
            "ShipmentNumber__c"
        ),
        "compliance_check_id": event_payload.get(
            "complianceCheckId__c"
        ),
    }

    # Resume only the worker associated with this interrupt ID.
    resume_payload = {
        interrupt_id: reviewer_response
    }

    graph = build_compliance_graph()

    result = graph.invoke(
        Command(
            resume=resume_payload,
        ),
        config=config,
    )

    process_interrupts(
        result=result,
        thread_id=thread_id,
    )

    return result


def process_interrupts(
    result: dict,
    thread_id: str,
) -> None:
    """
    Saves every LangGraph interrupt back into Salesforce.
    """

    if "__interrupt__" not in result:
        return

    for current_interrupt in result["__interrupt__"]:

        interrupt_payload = current_interrupt.value
        decision = interrupt_payload["current_decision"]
        logger.info(
            "Saving interrupt %s for %s/%s",
            current_interrupt.id,
            interrupt_payload["country"],
            interrupt_payload["route_type"],
        )
        update_response = save_route_check(
            {
                "identifier": "ROUTE_COMPLIANCE",
                "routeCheck": {
                    "shipmentRouteId": interrupt_payload[
                        "shipment_route_id"
                    ],
                    "country": interrupt_payload["country"],
                    "routeType": interrupt_payload["route_type"],
                    "iterationNumber": interrupt_payload[
                        "iteration_number"
                    ],
                    "threadId": thread_id,
                    "interruptId": current_interrupt.id,
                    "complianceStatus": "Review Required",
                    "riskLevel": decision["risk_level"],
                    "confidenceScore": decision[
                        "confidence_score"
                    ],
                    "missingDocuments": helper_stringify_list(
                        decision.get(
                            "missing_documents",
                            [],
                        )
                    ),
                    "regulationSummary": decision["summary"],
                    "companyPolicyResult": helper_stringify_list(
                        decision.get(
                            "policy_conflicts",
                            [],
                        )
                    ),
                    "evidenceReference": helper_stringify_list(
                        decision.get(
                            "evidence_sources",
                            [],
                        )
                    ),
                    "recommendedAction": decision[
                        "recommended_action"
                    ],
                },
            }
        )

        if not update_response.get("success"):
            raise RuntimeError(
                "Unable to update Salesforce review record: "
                f"{update_response.get('message')}"
            )