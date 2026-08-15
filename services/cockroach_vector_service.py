import json
import os
from typing import Any

from psycopg_pool import ConnectionPool

from config.settings import settings
from llm.factory import get_embedding_model


VECTOR_INDEX_NAME = os.getenv(
    "VECTOR_INDEX_NAME",
    "governmentregulationsqwen",
)

VECTOR_DIMENSION = int(
    os.getenv("VECTOR_DIMENSION", "1024")
)

TABLE_NAME = os.getenv(
    "TABLE_NAME",
    "regulatory_chunks",
)


# ============================================================
# Dedicated connection pool for regulatory vector operations
# ============================================================

vector_pool = ConnectionPool(
    conninfo=settings.cockroachdb_uri,
    min_size=1,
    max_size=10,
    kwargs={
        "autocommit": True,
    },
)


def ensure_cockroach_index() -> None:
    """
    Create the regulatory chunks table and vector index
    if they do not already exist.
    """

    with vector_pool.connection() as conn:
        with conn.cursor() as cur:

            # Enable vector indexing.
            cur.execute(
                """
                SET CLUSTER SETTING
                feature.vector_index.enabled = true
                """
            )

            # Create table if it does not exist.
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

                    namespace STRING NOT NULL,

                    content STRING NOT NULL,

                    metadata JSONB,

                    embedding VECTOR({VECTOR_DIMENSION}) NOT NULL,

                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )

            # Create vector index if it does not exist.
            #
            # namespace is the first column because your current
            # Pinecone implementation isolates data using:
            #
            # namespace = f"shipment-{{shipment_id}}"
            #
            cur.execute(
                f"""
                CREATE VECTOR INDEX IF NOT EXISTS
                {VECTOR_INDEX_NAME}
                ON {TABLE_NAME} (
                    namespace,
                    embedding
                )
                """
            )


def ingest_data_cockroach(
    rag_nodes: list,
    namespace: str | None = None,
) -> int | None:
    """
    Accept pre-chunked LlamaIndex TextNodes.

    Generate embeddings using the existing embedding model
    and store the regulatory chunks in CockroachDB.

    Mirrors the existing Pinecone ingestion behavior.
    """

    if not rag_nodes:
        return None

    if namespace is None:
        namespace = "default"

    embedding_model = get_embedding_model()

    inserted = 0

    with vector_pool.connection() as conn:
        with conn.cursor() as cur:

            for node in rag_nodes:

                content = node.get_content()

                if not content:
                    continue

                metadata = dict(
                    node.metadata or {}
                )

                # Generate embedding using the same
                # embedding model used by the application.
                embedding = (
                    embedding_model.get_text_embedding(
                        content
                    )
                )

                if len(embedding) != VECTOR_DIMENSION:
                    raise ValueError(
                        f"Expected embedding dimension "
                        f"{VECTOR_DIMENSION}, "
                        f"received {len(embedding)}."
                    )

                cur.execute(
                    f"""
                    INSERT INTO {TABLE_NAME} (
                        namespace,
                        content,
                        metadata,
                        embedding
                    )
                    VALUES (
                        %s,
                        %s,
                        %s::JSONB,
                        %s::VECTOR
                    )
                    """,
                    (
                        namespace,
                        content,
                        json.dumps(metadata),
                        str(embedding),
                    ),
                )

                inserted += 1

    return inserted


def fetch_data_from_cockroach(
    query_text: str,
    similarity_top_k: int = 3,
    raw_nodes_only: bool = True,
    namespace: str | None = None,
    metadata_filter: dict | None = None,
):
    """
    Fetch relevant regulatory chunks from CockroachDB
    using vector similarity.

    Supports:

    - shipment namespace
    - metadata filtering
    - cosine similarity
    - LlamaIndex NodeWithScore output
    """

    embedding_model = get_embedding_model()

    # Generate embedding for the search query.
    query_embedding = (
        embedding_model.get_text_embedding(
            query_text
        )
    )

    if len(query_embedding) != VECTOR_DIMENSION:
        raise ValueError(
            f"Expected query embedding dimension "
            f"{VECTOR_DIMENSION}, "
            f"received {len(query_embedding)}."
        )

    conditions: list[str] = []
    params: list[Any] = []

    # --------------------------------------------------------
    # Namespace filtering
    # --------------------------------------------------------

    if namespace is not None:

        conditions.append(
            "namespace = %s"
        )

        params.append(namespace)

    # --------------------------------------------------------
    # Metadata filtering
    # --------------------------------------------------------

    if metadata_filter:

        for key, value in metadata_filter.items():

            if value is None:
                continue

            conditions.append(
                "metadata ->> %s = %s"
            )

            params.extend(
                [
                    key,
                    str(value),
                ]
            )

    # --------------------------------------------------------
    # WHERE clause
    # --------------------------------------------------------

    where_clause = ""

    if conditions:

        where_clause = (
            "WHERE "
            + " AND ".join(conditions)
        )

    vector_string = str(
        query_embedding
    )

    # --------------------------------------------------------
    # Vector similarity search
    # --------------------------------------------------------

    sql = f"""
        SELECT
            id,
            content,
            metadata,

            1 - (
                embedding <=> %s::VECTOR
            ) AS score

        FROM {TABLE_NAME}

        {where_clause}

        ORDER BY
            embedding <=> %s::VECTOR

        LIMIT %s
    """

    query_params = [
        vector_string,
        *params,
        vector_string,
        similarity_top_k,
    ]

    with vector_pool.connection() as conn:
        with conn.cursor() as cur:

            cur.execute(
                sql,
                query_params,
            )

            rows = cur.fetchall()

    if raw_nodes_only:

        return _convert_to_llama_nodes(
            rows
        )

    return rows


def delete_shipment_namespace_cockroach(
    namespace: str | None = None,
) -> None:
    """
    Delete all regulatory chunks belonging to
    a shipment namespace.

    Equivalent to the current Pinecone:

        delete(
            delete_all=True,
            namespace=namespace
        )
    """

    if namespace is None:
        return

    with vector_pool.connection() as conn:
        with conn.cursor() as cur:

            cur.execute(
                f"""
                DELETE FROM {TABLE_NAME}
                WHERE namespace = %s
                """,
                (namespace,),
            )


def _convert_to_llama_nodes(rows):
    """
    Convert CockroachDB search results into
    LlamaIndex NodeWithScore objects.

    This preserves the existing RAG result contract:

        node.text
        node.score
        node.metadata
    """

    from llama_index.core.schema import (
        TextNode,
        NodeWithScore,
    )

    results = []

    for row in rows:

        node_id = str(
            row[0]
        )

        content = row[1]

        metadata = (
            row[2]
            or {}
        )

        score = float(
            row[3]
        )

        node = TextNode(
            id_=node_id,
            text=content,
            metadata=metadata,
        )

        results.append(
            NodeWithScore(
                node=node,
                score=score,
            )
        )

    return results


# ============================================================
# Initialize table + vector index when this module is loaded
# ============================================================

ensure_cockroach_index()