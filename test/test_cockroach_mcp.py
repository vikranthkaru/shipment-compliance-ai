import asyncio
import json

from services.cockroach_mcp_service import (
    get_shipment_memory,
    save_compliance_memory,
)


async def main():
    shipment_id = "TEST-SERVICE-SHIPMENT-001"

    print("\n" + "=" * 60)
    print("TEST 1: SAVING COMPLIANCE MEMORY")
    print("=" * 60)

    save_result = await save_compliance_memory(
        shipment_id=shipment_id,
        event_type="ROUTE_ANALYZED",
        content=(
            "Route was analyzed successfully. "
            "No compliance violations were found. "
            "The route passed compliance validation."
        ),
        compliance_check_id="TEST-COMPLIANCE-001",
        shipment_route_id="TEST-ROUTE-001",
        country="UAE",
        route_type="IMPORT",
        route_position=1,
        iteration_number=1,
        compliance_status="PASSED",
        risk_level="LOW",
        confidence_score=0.95,
        route_snapshot={
            "country": "UAE",
            "route_type": "IMPORT",
            "route_position": 1,
        },
        decision={
            "status": "PASSED",
            "risk_level": "LOW",
            "summary": (
                "No compliance violations "
                "or missing documents found."
            ),
        },
        metadata={
            "source": "cockroach_mcp_service_test",
        },
        thread_id="test-service-thread-001",
        route_check_id="test-service-route-check-001",
    )

    print("\nSave Result:")
    print(save_result)

    print("\n" + "=" * 60)
    print("TEST 2: FETCHING SHIPMENT MEMORY")
    print("=" * 60)

    memory_result = await get_shipment_memory(
        shipment_id=shipment_id,
    )

    print("\nShipment Memory Result:")
    print(memory_result)

    print("\n" + "=" * 60)
    print("TEST COMPLETED")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())