from langgraph.graph import StateGraph, START, END
from agents.compliance_agent.state import ComplianceState,RouteComplianceWorkerState
from agents.compliance_agent.nodes import (
        analyze_shipment_memory,
        validate_shipment_context,
        identify_regulation_requirements,
        index_regulation_content,
        fetch_company_policy_context_node,
        fetch_external_policy_context_node,
        subgraph_reducer_node,
        analyzer_node,
        human_intervention_node,
        final_compliance_summary_node
)
from agents.compliance_agent.edges import (
    route_validation_edge,
    route_splitter
)

from memory import get_memory
from langgraph.types import RetryPolicy

memory = get_memory()

retry_policy = RetryPolicy(
    max_attempts=3,
    backoff_factor=2.0
)

def build_compliance_subgraph():
    subgraph = StateGraph(RouteComplianceWorkerState)
    subgraph.add_node("salesforce_node",fetch_company_policy_context_node)
    subgraph.add_node("external_policy_node", fetch_external_policy_context_node)
    subgraph.add_node("analyzer_node", analyzer_node)
    subgraph.add_node("human_intervention_node", human_intervention_node,retry=retry_policy)

    subgraph.add_edge(START, "salesforce_node")
    subgraph.add_edge("salesforce_node", "external_policy_node")
    subgraph.add_edge("external_policy_node", "analyzer_node")
    subgraph.add_edge("human_intervention_node", "analyzer_node")
    subgraph.add_edge("analyzer_node", END)

    return subgraph.compile()

def build_compliance_graph():
        graph = StateGraph(ComplianceState)
        graph.add_node("validate_shipment_context", validate_shipment_context)
        graph.add_node("analyze_shipment_memory",analyze_shipment_memory)
        graph.add_node("identify_regulation_requirements", identify_regulation_requirements)
        graph.add_node("index_regulation_content", index_regulation_content)
        graph.add_node("final_compliance_summary_node", final_compliance_summary_node)

        compliance_subgraph = build_compliance_subgraph()
        graph.add_node("compliance_parallel_subgraph", compliance_subgraph | subgraph_reducer_node)
        graph.add_edge(START, "validate_shipment_context")
        graph.add_conditional_edges(
            "validate_shipment_context",
            route_validation_edge,
            {
                "continue": "analyze_shipment_memory",
                "end": END,
            },
        )

        graph.add_edge(
            "analyze_shipment_memory",
            "identify_regulation_requirements",
        )
        graph.add_edge(
            "identify_regulation_requirements", "index_regulation_content"
        )
        graph.add_conditional_edges(
            "index_regulation_content", route_splitter, ["compliance_parallel_subgraph"]
        )
        graph.add_edge("compliance_parallel_subgraph", "final_compliance_summary_node")
        graph.add_edge("final_compliance_summary_node", END)
        return graph.compile(
            checkpointer=memory
        )