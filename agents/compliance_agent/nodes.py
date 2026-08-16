import json
from agents.compliance_agent.state import ComplianceState,RouteComplianceWorkerState
from langgraph.types import Command,interrupt
from langgraph.constants import END
from typing import Literal
import logging
logger = logging.getLogger(__name__)

from llm.factory import get_structured_chat_model
from agents.compliance_agent.schemas import (
    RegulationSearchPlan,
    RouteComplianceDecision,
    ShipmentComplianceDecision,
    ShipmentMemoryAnalysis
)

from agents.compliance_agent.prompts import (
    IDENTIFY_REGULATION_REQUIREMENTS_PROMPT,
    ROUTE_ANALYZER_PROMPT,
    FINAL_COMPLIANCE_SUMMARY_PROMPT,
    ANALYZE_SHIPMENT_MEMORY_PROMPT
)

from agents.compliance_agent.helpers import (
    helper_build_regulation_search_query,
    helper_search_regulatory_sources,
    helper_rerank_regulatory_sources,
    helper_extract_url_content_and_ingest,
    helper_get_route_check_status,
    helper_stringify_list,
    helper_delete_namespace_pinecone,
    helper_extract_cockroach_rows
)
from services.salesforce_service import save_route_check
from tools.rag_tools import ( fetch_company_policy_from_data_cloud, search_government_regulations )
from services.cockroach_mcp_service import (
    get_latest_shipment_memory_sync,
    save_compliance_memory_snapshot_sync,
)

#node-1
def validate_shipment_context(state: ComplianceState) -> dict:
    shipment_context = state.get("shipment_context") or {}
    errors: list[str] = []

    shipment = shipment_context.get("shipment")
    product = shipment_context.get("product")
    routes = shipment_context.get("route")
    documents = shipment_context.get("documents")

    if not isinstance(shipment, dict) or not shipment:
        errors.append("Shipment details are missing")

    if not isinstance(product, dict) or not product:
        errors.append("Product details are missing")

    if not isinstance(routes, list) or not routes:
        errors.append("Shipment route is missing")

    if not isinstance(documents, list):
        errors.append("Shipment documents are missing")

    if isinstance(shipment, dict) and shipment:
        if not shipment.get("shipmentId"):
            errors.append("Shipment ID is missing")

        if shipment.get("shipmentStatus") != "Pending Compliance":
            errors.append("Shipment status is not Pending Compliance")

        if shipment.get("complianceStatus") != "Pending":
            errors.append("Compliance status is not Pending")

    if isinstance(routes, list) and routes:
        route_types = {
            route.get("routeType")
            for route in routes
            if isinstance(route, dict)
        }

        if "Origin" not in route_types:
            errors.append("Origin country route is missing")

        if "Destination" not in route_types:
            errors.append("Destination country route is missing")

        for index, route in enumerate(routes, start=1):
            if not isinstance(route, dict):
                errors.append(f"Route {index} has an invalid structure")
                continue

            if not route.get("routeId"):
                errors.append(f"Route {index} ID is missing")

            if not route.get("country"):
                errors.append(f"Route {index} country is missing")

            if not route.get("routeType"):
                errors.append(f"Route {index} type is missing")
    
    shipment_context = state["shipment_context"]
    shipment_id = state["shipment_context"]["shipment"]["shipmentId"]
    product_id = state["shipment_context"]["product"]["productId"]
    insert_response = save_route_check(
        {
            "identifier": "SHIPMENT_COMPLIANCE",
            "shipmentCompliance": {
                "shipmentDetailId": shipment_id,
                "productId": product_id,
                "overallStatus": "In Progress"
            },
        }
    )
    if not insert_response.get("success"):
        errors.append(f"Compliance check record creation failed in Salesforce")
    

    return {
        "errors": errors
    }

def analyze_shipment_memory(
    state: ComplianceState,
) -> dict:
    """
    Fetches the latest compact natural-language compliance
    memory for the shipment and compares it with the current
    shipment context.

    This node is read-only. It does not create or update
    compliance memory.
    """

    shipment_context = state[
        "shipment_context"
    ]

    shipment = shipment_context.get(
        "shipment"
    ) or {}

    shipment_id = shipment.get(
        "shipmentId"
    )

    if not shipment_id:
        raise ValueError(
            "shipment_id is required for compliance memory analysis"
        )

    logger.info(
        "Fetching latest compliance memory for shipment %s",
        shipment_id,
    )

    # --------------------------------------------------
    # 1. FETCH LATEST MEMORY
    # --------------------------------------------------

    memory_result = get_latest_shipment_memory_sync(
        shipment_id=shipment_id,
    )

    shipment_memory_rows = helper_extract_cockroach_rows(
        memory_result
    )

    # The database stores one compact natural-language
    # memory summary per completed compliance run.
    previous_memory = None

    if shipment_memory_rows:
        previous_memory = shipment_memory_rows[0].get(
            "content"
        )

    logger.info(
        "Previous compliance memory found for shipment %s: %s",
        shipment_id,
        bool(previous_memory),
    )

    # --------------------------------------------------
    # 2. ANALYZE CURRENT SHIPMENT VS PREVIOUS MEMORY
    # --------------------------------------------------

    llm = get_structured_chat_model(
        ShipmentMemoryAnalysis
    )

    response = llm.invoke(
        ANALYZE_SHIPMENT_MEMORY_PROMPT.format(
            shipment_context=json.dumps(
                shipment_context,
                indent=2,
                default=str,
            ),
            shipment_memory=(
                previous_memory
                if previous_memory
                else (
                    "No previous compliance memory exists "
                    "for this shipment."
                )
            ),
        )
    )

    memory_analysis = response.model_dump(
        mode="json"
    )

    logger.info(
        "Shipment memory analysis completed: %s",
        memory_analysis,
    )

    # --------------------------------------------------
    # 3. CREATE ROUTE ACTION MAP
    # --------------------------------------------------

    route_memory_actions = {
        route_decision[
            "shipment_route_id"
        ]: {
            "action": route_decision[
                "action"
            ],
            "reason": route_decision[
                "reason"
            ],
            "previous_status": route_decision.get(
                "previous_status"
            ),
            "route_changed": route_decision.get(
                "route_changed",
                True,
            ),
        }
        for route_decision in memory_analysis.get(
            "route_decisions",
            [],
        )
    }

    return {
        "shipment_memory": previous_memory,
        "route_memory_actions": route_memory_actions,
    }

def identify_regulation_requirements(state:ComplianceState) -> ComplianceState:
    llm = get_structured_chat_model(RegulationSearchPlan)

    response = llm.invoke(
        IDENTIFY_REGULATION_REQUIREMENTS_PROMPT.format(
            shipment_context=state["shipment_context"]
        )
    )
    search_plan = response.model_dump(mode="json")
    logger.info(f"Regulation Search Plan: {search_plan}")

    return {
        "regulation_search_plan": search_plan
    }

#node-3
def index_regulation_content(state:ComplianceState)-> dict:
    """
    Node 3:
    Reads regulation_search_plan from state, searches/crawls official regulation content,
    chunks/enriches it, and stores it in Pinecone.

    This node performs a side-effect only.
    It does not update LangGraph state.
    """
    shipment_context = state["shipment_context"]
    shipment_id = state["shipment_context"]["shipment"]["shipmentId"]
    namespace = f"shipment-{shipment_id}"
    search_plan = state["regulation_search_plan"]
    requirements = search_plan.get("regulation_requirements", [])
    for req in requirements:
        country = req.get("country")
        route_type = req.get("route_type")
        regulation_topics = req.get("regulation_topics", [])
        why_this_applies = req.get("why_this_applies", [])


        search_query = helper_build_regulation_search_query(
            country=country,
            route_type=route_type,
            regulation_topics=regulation_topics,
            shipment_context=shipment_context,
        )
        logger.info(
            f"Regulation search query for {country}: "
            f"{search_query}"
        )
        ranked_results = helper_search_regulatory_sources(
            search_query=search_query,
            search_country=country,
            route_type=route_type,
            regulation_topics=regulation_topics,
        )
        reranked_results = helper_rerank_regulatory_sources(
            ranked_results=ranked_results,
            country=country,
            route_type=route_type,
            regulation_topics=regulation_topics,
            why_this_applies=why_this_applies,
            top_k=3,
            minimum_score=0.60,
        )
        logger.info(f"\n===== RERANKED SOURCES: {country} / {route_type} =====")
        for source in reranked_results:
            logger.info(
                {
                    "title": source.get("title"),
                    "url": source.get("url"),
                    "rank_score": source.get("rank_score"),
                    "rerank_score": source.get("rerank_score"),
                    "reason": source.get("rerank_reason"),
                }
            )

        helper_extract_url_content_and_ingest(
            sources=reranked_results,
            country=country,
            role=route_type,
            regulation_topics=regulation_topics,
            why_this_applies=why_this_applies,
            shipment_id=shipment_id,
            namespace=namespace,
        )

    return {}


def final_compliance_summary_node(
    state: ComplianceState,
) -> dict:

    shipment_context = state[
        "shipment_context"
    ]

    route_results = state.get(
        "route_compliance_results",
        {},
    )

    shipment = shipment_context.get(
        "shipment",
        {},
    )

    product = shipment_context.get(
        "product",
        {},
    )

    shipment_id = shipment.get(
        "shipmentId"
    )

    product_id = product.get(
        "productId"
    )

    if not shipment_id:
        raise ValueError(
            "shipment_id is required for final compliance summary"
        )

    namespace = f"shipment-{shipment_id}"

    # --------------------------------------------------
    # 1. FINAL LLM COMPLIANCE DECISION
    # --------------------------------------------------

    llm = get_structured_chat_model(
        ShipmentComplianceDecision
    )

    response = llm.invoke(
        FINAL_COMPLIANCE_SUMMARY_PROMPT.format(
            shipment_context=shipment_context,
            route_compliance_results=route_results,
        )
    )

    decision = response.model_dump(
        mode="json"
    )

    # --------------------------------------------------
    # 2. UPDATE SALESFORCE SHIPMENT COMPLIANCE
    # --------------------------------------------------

    save_route_check(
        {
            "identifier": "SHIPMENT_COMPLIANCE",
            "shipmentCompliance": {
                "shipmentDetailId": shipment_id,
                "productId": product_id,
                "overallStatus": decision[
                    "overall_status"
                ],
                "overallRiskLevel": decision[
                    "overall_risk_level"
                ],
                "aiReasoning": decision[
                    "ai_reasoning"
                ],
                "anomaliesFound": "\n".join(
                    decision.get(
                        "blocking_issues",
                        [],
                    )
                ),
                "evidenceSummary": "\n".join(
                    decision.get(
                        "evidence_summary",
                        [],
                    )
                ),
                "missingDocuments": "\n".join(
                    decision.get(
                        "missing_documents",
                        [],
                    )
                ),
                "recommendedAction": decision[
                    "recommended_next_action"
                ],
                "humanReviewRequired": decision[
                    "human_review_required"
                ],
                "confidenceScore": decision[
                    "confidence_score"
                ],
            },
        }
    )

    # --------------------------------------------------
    # 3. SAVE COMPACT NLP MEMORY
    # --------------------------------------------------

    save_compliance_memory_snapshot_sync(
        shipment_id=shipment_id,

        content=decision[
            "memory_summary"
        ],

        thread_id=state.get(
            "thread_id"
        ),
    )

    # --------------------------------------------------
    # 4. CLEAN TEMPORARY RETRIEVAL DATA
    # --------------------------------------------------

    helper_delete_namespace_pinecone(
        namespace=namespace
    )

    return {
        "compliance_result": decision
    }

#---------------------------Sub Graphs----------#
def fetch_company_policy_context_node(
    state: RouteComplianceWorkerState,
) -> dict:
    shipment_context = state["shipment_context"]
    requirement = state["regulation_requirement"]

    product = shipment_context["product"]
    shipment = shipment_context["shipment"]

    search_text = " ".join(
        part
        for part in [
            requirement["country"],
            requirement["route_type"],
            product["productName"],
            product["drugCategory"],
            "cold chain"
            if product.get("requiresColdChain")
            else "",
            shipment["transportMode"],
            *requirement.get("regulation_topics", []),
        ]
        if part
    ).strip()

    try:
        company_policy_context = (
            fetch_company_policy_from_data_cloud(
                    search_text=search_text,
                    limit=10,
            )
        )
        result_count = len(company_policy_context)
        logger.info(
            "Salesforce company-policy retrieval completed for "
            f"{requirement['country']} / "
            f"{requirement['route_type']}: "
            f"{result_count} results found."
        )

        return {
            "company_policy_context": company_policy_context,
            "internal_policy_fetched": bool(company_policy_context)
        }

    except Exception as exc:
        return {
            "company_policy_context": [],
            "internal_policy_fetched": False,
            "errors": state.get("errors", [])
            + [
                "Salesforce company-policy retrieval failed for "
                f"{requirement['country']} / "
                f"{requirement['route_type']}: {exc}"
            ],
        }


def fetch_external_policy_context_node(
    state: RouteComplianceWorkerState,
) -> dict:
    shipment_context = state["shipment_context"]
    requirement = state["regulation_requirement"]

    shipment_id = state["shipment_id"]
    country = requirement["country"]
    route_type = requirement["route_type"]
    regulation_topics = requirement.get("regulation_topics", [])

    query = helper_build_regulation_search_query(
        country=country,
        route_type=route_type,
        regulation_topics=regulation_topics,
        shipment_context=shipment_context,
    )


    try:
        government_regulation_context = search_government_regulations(
            query=query,
            country=country,
            route_type=route_type,
            shipment_id=shipment_id,
            similarity_top_k=5,
        )
        
        logger.info(
            "Government-regulation retrieval completed for "
            f"{country} / {route_type}: "
            f"{len(government_regulation_context)} results found."
        )

        return {
            "government_regulation_context": government_regulation_context,
            "external_policy_fetched": bool(
                government_regulation_context
            ),
        }

    except Exception as exc:
        logger.error(
            "Government-regulation retrieval failed for "
            f"{country} / {route_type}: {exc}"
        )

        return {
            "government_regulation_context": [],
            "external_policy_fetched": False,
            "errors": state.get("errors", [])
            + [
                "Government-regulation retrieval failed for "
                f"{country} / {route_type}: {exc}"
            ],
        }


def analyzer_node(state:RouteComplianceWorkerState) -> Command[Literal["human_intervention_node"]]:
    """Processes worker state and appends its data to the orchestrator lists."""
    iteration_count = state.get("iteration_count", 0) + 1


    req = state["regulation_requirement"]
    shipment_context = state["shipment_context"]
    shipment = shipment_context.get(
        "shipment"
    ) or {}

    shipment_id = shipment.get(
        "shipmentId"
    )
    country = req["country"]
    route_type = req["route_type"]
    route_id = state["route_id"]
   # Insert_New_Iteration --> call salesforce class with iteration count this will be like 1st iteration only insert, 2nd interatoon (1st iteratioon failed, 2nd iteration new), 3rd iteration (2nd iteration failed, 3rd iteration new)
    if iteration_count > 3:
        # Do not create iteration 4. and update iteration 3 to blocked
        max_iteration_decision = {
            "shipmentRouteId": route_id,
            "country": country,
            "routeType": route_type,
            "iterationNumber" : 3,
            "complianceStatus": "Blocked",
            "riskLevel": "HIGH",
            "confidenceScore": 0.3,
            "regulationSummary": f"Maximum human-review iterations reached. Unable to finalize compliance for {country} after three analysis iterations.",
            "recommendedAction": "Escalate the route to a senior compliance officer."
        }
        max_update_response = save_route_check(
            {
                "identifier": "ROUTE_COMPLIANCE",
                "routeCheck": max_iteration_decision,
            }
        )
        errors = state.get("errors", []) + [
            f"FAILED_MAX_ITERATIONS for {country}"
        ]
        if not max_update_response.get("success"):
            errors.append(
                "Unable to block the final route-check iteration: "
                f"{max_update_response.get('message')}"
            )


        #update 3rd iteration to compliance status maximum iteration failure
        return Command(
            update={
                "iteration_count": iteration_count,
                "route_decision": {
                    "country": country,
                    "route_type": route_type,
                    "compliance_status": "Blocked",
                    "confidence_level": "LOW",
                    "confidence_score": 0.3,
                    "human_intervention_required": False,
                    "risk_level": "HIGH",
                    "summary": "Maximum human review iterations reached.",
                    "reason": f"Unable to finalize compliance decision for {country} after 3 review attempts.",
                    "missing_documents": [],
                    "policy_conflicts": [],
                    "regulatory_concerns": [
                        "Manual compliance escalation required"
                    ],
                    "recommended_action": "Escalate to compliance officer for final decision.",
                    "evidence_sources": [],
                },
                "errors": errors,
            },
            goto=END,
        )

    
    # Create the current iteration before performing analysis. and old iteratioon to failed 
    insert_response = save_route_check(
        {
            "identifier": "ROUTE_COMPLIANCE",
            "routeCheck": {
                "shipmentRouteId": route_id,
                "country": country,
                "routeType": route_type,
                "iterationNumber": iteration_count,
                "complianceStatus": "In Progress",
            },
        }
    )
    if not insert_response.get("success"):
        return Command(
            update={
                "iteration_count": iteration_count,
                "errors": state.get("errors", [])
                + [
                    f"Unable to create route-check iteration "
                    f"{iteration_count} for {country}: "
                    f"{insert_response.get('message')}"
                ],
            },
            goto=END,
        )
    
    payload = {
        "country": country,
        "route_type": route_type,
        "regulation_requirement": req,
        "shipment_context": shipment_context,
        "company_policy_context": state.get("company_policy_context", []),
        "government_regulation_context": state.get("government_regulation_context", []),
        "human_feedback": state.get("human_feedback"),
        "iteration_count": iteration_count,
    }

    #analysis
    llm = get_structured_chat_model(RouteComplianceDecision)

    result = llm.invoke(
        ROUTE_ANALYZER_PROMPT.format(
            payload=payload
        )
    )

    decision = result.model_dump(mode="json")

    worker_update = {
        "iteration_count": iteration_count,
        "route_decision": decision,
    }


    if decision.get("human_intervention_required"):
        return Command(
            update=worker_update,
            goto="human_intervention_node",
        )

    #If analysis shows no human review required: complete the current iteration.
    final_status = helper_get_route_check_status(decision)
    update_response = save_route_check(
        {
            "identifier": "ROUTE_COMPLIANCE",
            "routeCheck": {
                "shipmentRouteId": route_id,
                "country": country,
                "routeType": route_type,
                "iterationNumber": iteration_count,
                "complianceStatus": final_status,
                "riskLevel": decision["risk_level"],
                "confidenceScore": decision["confidence_score"],
                "requiredDocuments": helper_stringify_list(
                    decision.get("required_documents", [])
                ),
                "missingDocuments": helper_stringify_list(
                    decision.get("missing_documents", [])
                ),
                "regulationSummary": decision["summary"],
                "companyPolicyResult": helper_stringify_list(
                    decision.get("policy_conflicts", [])
                ),
                "evidenceReference": helper_stringify_list(
                    decision.get("evidence_sources", [])
                ),
                "recommendedAction": decision[
                    "recommended_action"
                ],
            },
        }
    )

    if not update_response.get("success"):
        worker_update["errors"] = state.get("errors", []) + [
            f"Unable to complete route-check iteration "
            f"{iteration_count} for {country}: "
            f"{update_response.get('message')}"
        ]


    return Command(
        update=worker_update,
        goto=END,
    )



def human_intervention_node(state: RouteComplianceWorkerState) -> Command[Literal["analyzer_node"]]:
    """
    Pauses one route worker for human compliance review.

    After Salesforce publishes a re-review event, the structured
    reviewer response is returned by interrupt(). The feedback is
    appended to the worker state, and the route is sent back to the
    analyzer for re-evaluation.
    """
    requirement = state["regulation_requirement"]
    decision = state.get("route_decision", {})
    reviewer_response = interrupt(
        {
            "message": "Human compliance review required.",
            "shipment_id": state["shipment_id"],
            "shipment_route_id": state["route_id"],
            "thread_id": state["shipment_id"],
            "iteration_number": state["iteration_count"],
            "country": requirement["country"],
            "route_type": requirement["route_type"],
            "current_decision": decision,
            "question": (
                "Please provide clarification, approval, missing "
                "document details, or exception approval."
            ),
        }
    )

    feedback_entry = {
        "iteration_number": state["iteration_count"],
        "country": requirement["country"],
        "route_type": requirement["route_type"],
        "human_input": reviewer_response.get("reviewer_comments"),
        "reviewer_decision": reviewer_response.get("reviewer_decision"),
        "reviewed_by": reviewer_response.get("reviewed_by"),
        "reviewed_at": reviewer_response.get("reviewed_at"),
    }

    updated_feedback = [
        *state.get("human_feedback", []),
        feedback_entry,
    ]

    return Command(
        update={
            "human_feedback": updated_feedback,
        },
        goto="analyzer_node",
    )


def subgraph_reducer_node(state: RouteComplianceWorkerState):
    """Maps the final validated sub-graph state of each fan to the parent's array structure."""
    req = state["regulation_requirement"]

    country = req["country"]
    route_type = req["route_type"]

    route_key = f"{country}_{route_type}"

    route_decision = state.get("route_decision")

    if hasattr(route_decision, "model_dump"):
        route_decision = route_decision.model_dump()

    final_data = {
        "country": country,
        "route_type": route_type,
        "regulation_requirement": req,
        "route_decision": route_decision,
        "human_feedback": state.get("human_feedback"),
        "iteration_count": state.get("iteration_count", 0),
        "internal_policy_fetched": state.get("internal_policy_fetched", False),
        "external_policy_fetched": state.get("external_policy_fetched", False),
        "errors": state.get("errors", []),
    }

    return {
        "route_compliance_results": {
            route_key: final_data
        }
    }