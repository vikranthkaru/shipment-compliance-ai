# test/rerun_final_node.py

from agents.compliance_agent.graph import build_compliance_graph
from agents.compliance_agent.nodes import (
    final_compliance_summary_node,
)


THREAD_ID = "0cff58d6-7e19-4013-aed2-108ac5db3798"


def main():
    graph = build_compliance_graph()

    config = {
        "configurable": {
            "thread_id": THREAD_ID,
        }
    }

    # Get completed graph state
    state_snapshot = graph.get_state(config)

    if not state_snapshot.values:
        raise ValueError(
            f"No state found for thread {THREAD_ID}"
        )

    print("\n" + "=" * 60)
    print("LOADED GRAPH STATE")
    print("=" * 60)

    state = state_snapshot.values

    print(
        f"Shipment ID: "
        f"{state['shipment_context']['shipment']['shipmentId']}"
    )

    print(
        f"Route results: "
        f"{len(state.get('route_compliance_results', {}))}"
    )

    print("\n" + "=" * 60)
    print("RERUNNING FINAL COMPLIANCE NODE")
    print("=" * 60)

    result = final_compliance_summary_node(state)

    print("\n" + "=" * 60)
    print("FINAL NODE RESULT")
    print("=" * 60)

    print(result)


if __name__ == "__main__":
    main()