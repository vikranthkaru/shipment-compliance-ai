import os
import json
import asyncio

from dotenv import load_dotenv
from langchain_mcp_adapters.client import (
    MultiServerMCPClient,
)


load_dotenv()


DATABASE_NAME = "defaultdb"


# ==========================================================
# MCP CLIENT
# ==========================================================

def _build_client() -> MultiServerMCPClient:
    """
    Creates the CockroachDB MCP client.
    """

    return MultiServerMCPClient(
        {
            "cockroachdb": {
                "transport": "streamable_http",
                "url": os.getenv(
                    "COCKROACH_MCP_URL"
                ),
                "headers": {
                    "Authorization": (
                        f"Bearer "
                        f"{os.getenv('COCKROACH_MCP_API_KEY')}"
                    ),
                    "mcp-cluster-id": os.getenv(
                        "COCKROACH_MCP_CLUSTER_ID"
                    ),
                },
            }
        }
    )


async def get_cockroach_mcp_tools() -> dict:
    """
    Connects to CockroachDB MCP and returns
    the tools required by compliance memory.
    """

    client = _build_client()

    tools = await client.get_tools()

    return {
        "insert_rows": next(
            tool
            for tool in tools
            if tool.name == "insert_rows"
        ),
        "select_query": next(
            tool
            for tool in tools
            if tool.name == "select_query"
        ),
    }


# ==========================================================
# SQL HELPERS
# ==========================================================

def _escape_sql_string(
    value: str | None,
) -> str:
    """
    Safely converts a Python string into
    a SQL string literal.
    """

    if value is None:
        return "NULL"

    escaped_value = str(value).replace(
        "'",
        "''",
    )

    return f"'{escaped_value}'"


# ==========================================================
# MCP RESULT PARSER
# ==========================================================

def _extract_cockroach_rows(
    result,
) -> list[dict]:
    """
    Extracts rows from CockroachDB MCP responses.
    """

    if not result:
        return []

    rows = []

    for item in result:

        if isinstance(item, dict):
            text = item.get("text")

        else:
            text = getattr(
                item,
                "text",
                None,
            )

        if not text:
            continue

        try:
            parsed = json.loads(text)

            result_rows = parsed.get(
                "rows",
                [],
            )

            if isinstance(
                result_rows,
                list,
            ):
                rows.extend(
                    result_rows
                )

        except (
            json.JSONDecodeError,
            TypeError,
            AttributeError,
        ):
            continue

    return rows


# ==========================================================
# GET LATEST MEMORY
# ==========================================================

async def get_latest_shipment_memory(
    shipment_id: str,
) -> dict:
    """
    Retrieves the latest compact compliance memory
    for a shipment.
    """

    tools = await get_cockroach_mcp_tools()

    select_tool = tools[
        "select_query"
    ]

    safe_shipment_id = shipment_id.replace(
        "'",
        "''",
    )

    query = f"""
        SELECT
            id,
            shipment_id,
            memory_version,
            content,
            thread_id,
            created_at
        FROM shipment_compliance_memory
        WHERE shipment_id = '{safe_shipment_id}'
        ORDER BY memory_version DESC
        LIMIT 1
    """

    result = await select_tool.ainvoke(
        {
            "database": DATABASE_NAME,
            "query": query,
        }
    )

    return result


def get_latest_shipment_memory_sync(
    shipment_id: str,
) -> dict:
    """
    Synchronous wrapper for LangGraph nodes.
    """

    return asyncio.run(
        get_latest_shipment_memory(
            shipment_id=shipment_id,
        )
    )


# ==========================================================
# GET NEXT MEMORY VERSION
# ==========================================================

async def get_next_memory_version(
    shipment_id: str,
) -> int:
    """
    Determines the next memory version
    for a shipment.

    Example:

    No previous memory -> 1
    Latest version 1   -> 2
    Latest version 2   -> 3
    """

    tools = await get_cockroach_mcp_tools()

    select_tool = tools[
        "select_query"
    ]

    safe_shipment_id = shipment_id.replace(
        "'",
        "''",
    )

    query = f"""
        SELECT
            COALESCE(
                MAX(memory_version),
                0
            ) + 1 AS next_memory_version
        FROM shipment_compliance_memory
        WHERE shipment_id = '{safe_shipment_id}'
    """

    result = await select_tool.ainvoke(
        {
            "database": DATABASE_NAME,
            "query": query,
        }
    )

    rows = _extract_cockroach_rows(
        result
    )

    if not rows:
        return 1

    return int(
        rows[0][
            "next_memory_version"
        ]
    )


# ==========================================================
# SAVE MEMORY SNAPSHOT
# ==========================================================

async def save_compliance_memory_snapshot(
    shipment_id: str,
    content: str,
    thread_id: str | None = None,
) -> dict:
    """
    Saves one compact natural-language compliance
    memory snapshot for a shipment.

    Each completed compliance run creates a new
    incremented memory version.
    """

    memory_version = await get_next_memory_version(
        shipment_id=shipment_id,
    )

    tools = await get_cockroach_mcp_tools()

    insert_tool = tools[
        "insert_rows"
    ]

    query = f"""
        INSERT INTO shipment_compliance_memory (
            shipment_id,
            memory_version,
            content,
            thread_id
        )
        VALUES (
            {_escape_sql_string(shipment_id)},
            {memory_version},
            {_escape_sql_string(content)},
            {_escape_sql_string(thread_id)}
        )
    """

    result = await insert_tool.ainvoke(
        {
            "database": DATABASE_NAME,
            "query": query,
        }
    )

    return result


def save_compliance_memory_snapshot_sync(
    shipment_id: str,
    content: str,
    thread_id: str | None = None,
) -> dict:
    """
    Synchronous wrapper for LangGraph nodes.
    """

    return asyncio.run(
        save_compliance_memory_snapshot(
            shipment_id=shipment_id,
            content=content,
            thread_id=thread_id,
        )
    )