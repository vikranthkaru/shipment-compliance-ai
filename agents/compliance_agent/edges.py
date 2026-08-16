import logging
from langgraph.types import Send
from agents.compliance_agent.state import ComplianceState
logger = logging.getLogger(__name__)

def route_validation_edge(state: ComplianceState) -> str:
    if state.get("errors"):
        return "end"

    return "continue"


def route_splitter(state: ComplianceState):
    """
    Fan-out node.

    Creates a compliance worker only for routes that
    need to be processed based on shipment memory analysis.

    Routes that previously PASSED and have not changed
    are skipped.
    """
    print("State in route_splitter:", state)  # Debugging line
    shipment_context = state["shipment_context"]

    shipment_id = shipment_context[
        "shipment"
    ]["shipmentId"]

    regulation_requirements = state[
        "regulation_search_plan"
    ]["regulation_requirements"]

    route_memory_actions = state.get(
        "route_memory_actions",
        {},
    )

    def normalize(value: str | None) -> str:
        return (value or "").strip().lower()

    routes = shipment_context.get(
        "route",
        [],
    )

    def find_route_id(
        country: str,
        route_type: str,
    ) -> str | None:

        for route in routes:

            if (
                normalize(
                    route.get("country")
                )
                == normalize(country)
                and normalize(
                    route.get("routeType")
                )
                == normalize(route_type)
            ):
                return route.get("routeId")

        return None

    sends = []

    for requirement in regulation_requirements:

        route_id = find_route_id(
            country=requirement["country"],
            route_type=requirement["route_type"],
        )

        if route_id is None:
            raise ValueError(
                "Unable to match Salesforce route for "
                f"{requirement['country']} / "
                f"{requirement['route_type']}"
            )

        # ----------------------------------------------
        # CHECK PREVIOUS MEMORY DECISION
        # ----------------------------------------------

        memory_action = route_memory_actions.get(
            route_id
        )

        # Default behavior:
        # New route or no previous memory → PROCESS
        action = (
            memory_action.get("action")
            if memory_action
            else "REPROCESS"
        )

        # ----------------------------------------------
        # SKIP PREVIOUSLY PASSED + UNCHANGED ROUTES
        # ----------------------------------------------

        if action == "SKIP":

            logger.info(
                "Skipping route %s (%s / %s) based on "
                "previous compliance memory. Reason: %s",
                route_id,
                requirement["country"],
                requirement["route_type"],
                memory_action.get("reason"),
            )

            continue

        # ----------------------------------------------
        # SEND ROUTE FOR COMPLIANCE PROCESSING
        # ----------------------------------------------

        logger.info(
            "Creating compliance worker for route %s "
            "(%s / %s). Memory action: %s",
            route_id,
            requirement["country"],
            requirement["route_type"],
            action,
        )

        sends.append(
            Send(
                "compliance_parallel_subgraph",
                {
                    "shipment_id": shipment_id,
                    "shipment_context": shipment_context,
                    "route_id": route_id,
                    "regulation_requirement": requirement,

                    "company_policy_context": [],
                    "government_regulation_context": [],

                    "internal_policy_fetched": False,
                    "external_policy_fetched": False,

                    "route_decision": None,

                    "human_feedback": [],

                    # New compliance cycle starts from
                    # iteration 0. Analyzer increments to 1.
                    "iteration_count": 0,

                    "errors": [],

                    # Optional but useful for downstream
                    # nodes to know why this route was processed.
                    "memory_action": action,
                    "memory_reason": (
                        memory_action.get("reason")
                        if memory_action
                        else "No previous compliance memory found."
                    ),
                },
            )
        )

    logger.info(
        "Created %d parallel compliance workers",
        len(sends),
    )

    return sends