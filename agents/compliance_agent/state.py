from enum import Enum
from typing import Annotated, TypedDict, Dict, Any, List, Optional

def merge_route_compliance_results(
    left: dict[str, Any] | None,
    right: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        **(left or {}),
        **(right or {}),
    }
# ===========================
# Main Graph State
# ===========================
class ComplianceState(TypedDict):
    event: Dict[str, Any]
    shipment_context: Dict[str, Any]

    # Output of compliance memory analysis
    shipment_memory: List[Dict[str, Any]] | None
    # Route-level decision produced by memory analysis.
    #
    # Example:
    # {
    #     "a02g500000APQ6PAAX": {
    #         "action": "SKIP",
    #         "reason": "...",
    #         "previous_status": "COMPLIANT",
    #         "route_changed": False,
    #     }
    # }
    route_memory_actions: Dict[str, Dict[str, Any]]

     # Output of Node 2
    regulation_search_plan: Dict | None

    # Aggregated route decisions after fan-in
    route_compliance_results: Annotated[
        dict[str, Any],
        merge_route_compliance_results,
    ]


    compliance_result: Dict | None

    # Temporary retrieval testing (can remove later)
    retrieval_test_result: dict | None


    errors: List[str]


# ===========================
# Worker Graph State
# ===========================
class RouteComplianceWorkerState(TypedDict): 
    shipment_id: str
    shipment_context: Dict[str, Any]
    route_id : str
    # One regulation requirement assigned to this worker
    regulation_requirement: Dict[str, Any]

     # RAG Retrieval Results
    company_policy_context: List[Dict[str, Any]]
    government_regulation_context: List[Dict[str, Any]]

    internal_policy_fetched: bool
    external_policy_fetched: bool

    human_intervention_required: bool
 
    # LLM Decision
    route_decision: Optional[Dict[str, Any]]


    # Human-in-the-loop
    human_feedback: list[dict]
    iteration_count: int

     # Worker Errors
    errors: List[str]
