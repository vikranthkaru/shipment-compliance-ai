import asyncio
from langchain_core.tools import tool
from services.salesforce_data_cloud_service import get_data_cloud_connection
# from services.vector_service import fetch_data_from_pinecone
from services.cockroach_vector_service import fetch_data_from_cockroach
def fetch_company_policy_from_data_cloud(search_text: str, limit: int = 10) -> list[dict]:
    """
    Search Salesforce Data Cloud vector index for internal company policy chunks.
    Use this to retrieve company SOP, policy, and compliance guidance.
    """
    connection = get_data_cloud_connection()
    cursor = connection.cursor()

    safe_search_text = search_text.replace("'", "''")

    sql = f"""
    SELECT
        index.score__c,
        chunk.Chunk__c
    FROM vector_search(
        TABLE(Knowledge_Article_Version_index__dlm),
        '{safe_search_text}',
        '',
        {limit}
    ) index
    JOIN Knowledge_Article_Version_chunk__dlm chunk
        ON index.RecordId__c = chunk.RecordId__c
    WHERE index.score__c >= 0.85
    ORDER BY index.score__c DESC
    """

    try:
        cursor.execute(sql)
        rows = cursor.fetchall()

        return [
            {
                "score": row[0],
                "content": row[1],
                "source_type": "company_policy",
            }
            for row in rows
        ]

    finally:
        cursor.close()


def search_government_regulations(
    query: str,
    country: str,
    route_type: str,
    shipment_id: str,
    similarity_top_k: int = 5,
) -> list:
    """
    Retrieves route-specific government-regulation chunks from the
    shipment namespace in cockroach.
    """
    namespace = f"shipment-{shipment_id}"

    nodes = fetch_data_from_cockroach(
        query_text=query,
        similarity_top_k=similarity_top_k,
        raw_nodes_only=True,
        namespace=namespace,
        metadata_filter={
            "country": country,
            "role": route_type,
        },
    )

    return [
        {
            "content": node.text,
            "score": getattr(node, "score", None),
            "metadata": node.metadata or {},
            "source_type": "government_regulation",
        }
        for node in nodes
        if getattr(node, "text", "").strip()
    ]


