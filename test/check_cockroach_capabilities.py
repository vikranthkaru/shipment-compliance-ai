import asyncio

from services.cockroach_mcp_service import _build_client


async def check_cockroach_write_capabilities():
    client = _build_client()

    tools = await client.get_tools()

    print("\nAvailable CockroachDB MCP tools:\n")

    tool_names = []

    for tool in tools:
        tool_names.append(tool.name)

        print(f"- {tool.name}")
        print(f"  {tool.description}\n")

    print("=" * 60)
    print("WRITE CAPABILITY CHECK")
    print("=" * 60)

    write_operations = {
        "insert": [],
        "update": [],
        "delete": [],
        "ddl": [],
    }

    for tool_name in tool_names:
        name = tool_name.lower()

        if "insert" in name:
            write_operations["insert"].append(tool_name)

        if "update" in name:
            write_operations["update"].append(tool_name)

        if "delete" in name:
            write_operations["delete"].append(tool_name)

        if (
            "create" in name
            or "alter" in name
            or "drop" in name
        ):
            write_operations["ddl"].append(tool_name)

    print(f"INSERT support: {write_operations['insert']}")
    print(f"UPDATE support: {write_operations['update']}")
    print(f"DELETE support: {write_operations['delete']}")
    print(f"DDL support: {write_operations['ddl']}")

    print("\nCapability result:")

    if write_operations["update"]:
        print("UPDATE capability is available.")
    else:
        print("No dedicated UPDATE capability found.")

    if write_operations["insert"]:
        print("INSERT capability is available.")
    else:
        print("No INSERT capability found.")


if __name__ == "__main__":
    asyncio.run(check_cockroach_write_capabilities())