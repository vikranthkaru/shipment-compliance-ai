#Standard Librayr
import os, html, re, hashlib
from datetime import datetime, timezone
from typing import  Mapping
import logging
import json
logger = logging.getLogger(__name__)

#Third Party
from tavily import TavilyClient
from langchain_text_splitters import RecursiveCharacterTextSplitter
from llama_index.core.schema import TextNode
from rank_bm25 import BM25Okapi

#Local
from llm.factory import ( get_structured_chat_model )
# from services.vector_service import (ingest_data_pinecone,delete_shipment_namespace)
from services.cockroach_vector_service import (ingest_data_cockroach,delete_shipment_namespace_cockroach)
from agents.compliance_agent.schemas import (
    RouteComplianceDecision,
    SourceRerankResponse
)
from agents.compliance_agent.prompts import (
    SOURCE_RERANKER_PROMPT
)

from config.loader import load_yaml

def tokenize(text: str) -> list[str]:
    return re.findall(r"\b\w+\b", text.lower())

_LARGE_DOCUMENT_SPLITTER = RecursiveCharacterTextSplitter(
    chunk_size=50_000,
    chunk_overlap=1_000,
    separators=[
        "\n\n",
        "\n",
        ". ",
        " ",
        "",
    ],
)

_REGULATORY_CHUNK_SPLITTER = RecursiveCharacterTextSplitter(
    chunk_size=1200,
    chunk_overlap=150,
    separators=[
        "\n\n",
        "\n",
        ". ",
        "; ",
        ": ",
        " ",
        "",
    ],
)


_DOMAIN_CONFIG: dict | None = None

def load_regulatory_domains() -> dict:
    global _DOMAIN_CONFIG

    if _DOMAIN_CONFIG is None:
        _DOMAIN_CONFIG = load_yaml("regulatory_domains.yaml")

    return _DOMAIN_CONFIG

_RANKING_CONFIG: dict | None = None

def load_source_ranking() -> dict:
    global _RANKING_CONFIG

    if _RANKING_CONFIG is None:
        _RANKING_CONFIG = load_yaml("source_ranking.yaml")

    return _RANKING_CONFIG

def get_domain_rules(country: str) -> tuple[list[str], list[str]]:
    config = load_regulatory_domains()

    global_rules = config.get("global", {})
    countries = config.get("countries", {})
    country_rules = config.get("countries", {}).get(country, {})

    include_domains = list(
        dict.fromkeys(
            global_rules.get("trusted", [])
            + country_rules.get("trusted", [])
        )
    )

    exclude_domains = list(
        dict.fromkeys(
            global_rules.get("excluded", [])
            + country_rules.get("excluded", [])
        )
    )

    return include_domains, exclude_domains

def calculate_source_rank(
    result: dict,
    route_type: str,
    regulation_topics: list[str],
) -> float:
    config = load_source_ranking()

    weights = config["weights"]
    route_keywords = config.get("route_keywords", {})
    topic_keywords = config.get("topic_keywords", {})
    specificity_terms = config.get(
        "document_specificity_terms",
        [],
    )

    searchable_text = " ".join(
        [
            result.get("title", ""),
            result.get("content", ""),
            result.get("url", ""),
        ]
    ).lower()

    tavily_score = float(result.get("score") or 0.0)

    route_match = 0.0

    route_match = float(
        any(
            keyword.lower() in searchable_text
            for keyword in route_keywords.get(route_type, [])
        )
    )
    matched_topics = 0

    for topic in regulation_topics:
        keywords = topic_keywords.get(
            topic,
            [topic.lower()],
        )

        if any(
            keyword.lower() in searchable_text
            for keyword in keywords
        ):
            matched_topics += 1

    topic_coverage = (
        matched_topics / len(regulation_topics)
        if regulation_topics
        else 0.0
    )

    document_specificity = float(
        any(
            term.lower() in searchable_text
            for term in specificity_terms
        )
    )

    final_score = (
        tavily_score * weights["tavily_score"]
        + route_match * weights["route_match"]
        + topic_coverage * weights["topic_coverage"]
        + document_specificity
        * weights["document_specificity"]
    )

    return round(final_score, 4)

def rank_regulatory_sources(
    results: list[dict],
    route_type: str,
    regulation_topics: list[str],
) -> list[dict]:

    ranked_results = [
        {
            **result,
            "rank_score": calculate_source_rank(
                result=result,
                route_type=route_type,
                regulation_topics=regulation_topics,
            ),
        }
        for result in results
    ]

    return sorted(
        ranked_results,
        key=lambda item: item["rank_score"],
        reverse=True,
    )

def helper_build_regulation_search_query(
    country: str,
    route_type: str,
    regulation_topics: list[str],
    shipment_context: dict,
) -> str:
    product = shipment_context["product"]

    route_action = {
        "Origin": "export",
        "Transit": "transit",
        "Destination": "import",
    }.get(route_type, route_type.lower())

    parts = [
        "official government",
        country,
        route_action,
        product["productName"],
        product["drugCategory"],
        *regulation_topics,
    ]

    return " ".join(
        str(part).strip()
        for part in parts
        if part
    )

def helper_rerank_regulatory_sources(
    ranked_results: list[dict],
    country: str,
    route_type: str,
    regulation_topics: list[str],
    why_this_applies: str | list[str],
    top_k: int = 3,
    minimum_score: float = 0.60,
) -> list[dict]:
    """
    Uses an LLM to semantically rerank already filtered and ranked
    Tavily results.

    The LLM evaluates only the supplied snippets. It does not crawl URLs.
    """

    if not ranked_results:
        return []

    candidate_sources = []

    for source_index, result in enumerate(ranked_results):
        candidate_sources.append(
            {
                "source_index": source_index,
                "title": result.get("title", ""),
                "url": result.get("url", ""),
                "content": result.get("content", ""),
                "rank_score": result.get("rank_score", 0.0),
            }
        )

    llm = get_structured_chat_model(SourceRerankResponse)

    response = llm.invoke(
        SOURCE_RERANKER_PROMPT.format(
            country=country,
            route_type=route_type,
            regulation_topics=regulation_topics,
            why_this_applies=why_this_applies,
            candidate_sources=candidate_sources,
            top_k=top_k,
        )
    )

    rerank_response = response.model_dump(mode="json")

    evaluation_by_index = {
        item["source_index"]: item
        for item in rerank_response.get("results", [])
    }

    reranked_sources = []

    for source_index, source in enumerate(ranked_results):
        evaluation = evaluation_by_index.get(source_index)

        if not evaluation:
            continue

        enriched_source = {
            **source,
            "rerank_score": evaluation["relevance_score"],
            "country_match": evaluation["country_match"],
            "route_match": evaluation["route_match"],
            "topic_match": evaluation["topic_match"],
            "rerank_selected": evaluation["selected"],
            "rerank_reason": evaluation["reason"],
        }

        if (
            evaluation["selected"]
            and evaluation["relevance_score"] >= minimum_score
        ):
            reranked_sources.append(enriched_source)

    reranked_sources.sort(
        key=lambda item: item["rerank_score"],
        reverse=True,
    )

    return reranked_sources[:top_k]

def helper_search_regulatory_sources(
    search_query: str,
    search_country: str,
    route_type: str,
    regulation_topics: list[str],
) -> list[dict]:
    client = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))
    include_domains, exclude_domains = get_domain_rules(search_country)

    response = client.search(
        query=search_query,
        search_depth="advanced",
        max_results=10,
        chunks_per_source=5,
        include_domains=include_domains,
        exclude_domains=exclude_domains,
        country=search_country.lower(),
    )

    filtered_results = []
    seen_urls = set()
    for result in response.get("results", []):
        url = result.get("url")

        if not url:
            continue

        normalized_url = url.rstrip("/").lower()

        if normalized_url in seen_urls:
            continue

        seen_urls.add(normalized_url)

        filtered_results.append(result)
    
    
    ranked_results = rank_regulatory_sources(
        results=filtered_results,
        route_type=route_type,
        regulation_topics=regulation_topics,
    )
    return ranked_results[:5]


def clean_extracted_content(raw_text: str) -> str:
    """
    Deterministically cleans extracted webpage or PDF content before
    chunking and embedding.
    """

    if not raw_text:
        return ""

    cleaned_text = html.unescape(raw_text)

    # Remove script and style sections.
    cleaned_text = re.sub(
        r"<script\b[^>]*>.*?</script>",
        " ",
        cleaned_text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    cleaned_text = re.sub(
        r"<style\b[^>]*>.*?</style>",
        " ",
        cleaned_text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    # Remove HTML comments.
    cleaned_text = re.sub(
        r"<!--.*?-->",
        " ",
        cleaned_text,
        flags=re.DOTALL,
    )

    # Remove remaining HTML tags.
    cleaned_text = re.sub(
        r"<[^>]+>",
        " ",
        cleaned_text,
    )

    boilerplate_patterns = [
        r"accept all cookies",
        r"manage cookie preferences",
        r"cookie policy",
        r"privacy policy",
        r"terms and conditions",
        r"skip to main content",
        r"subscribe to our newsletter",
        r"all rights reserved",
    ]

    for pattern in boilerplate_patterns:
        cleaned_text = re.sub(
            pattern,
            " ",
            cleaned_text,
            flags=re.IGNORECASE,
        )

    # Normalize line endings.
    cleaned_text = cleaned_text.replace("\r\n", "\n")
    cleaned_text = cleaned_text.replace("\r", "\n")

    # Collapse repeated spaces but preserve useful line breaks.
    cleaned_text = re.sub(r"[ \t]+", " ", cleaned_text)
    cleaned_text = re.sub(r"\n\s*\n\s*\n+", "\n\n", cleaned_text)

    return cleaned_text.strip()


def remove_duplicate_chunks(
    chunks: list[dict],
) -> list[dict]:
    """
    Removes exact duplicates after normalizing case and whitespace.

    A deterministic hash is attached to each retained chunk.
    """

    unique_chunks = []
    seen_hashes: set[str] = set()

    for chunk in chunks:
        chunk_text = chunk.get("chunk_text", "").strip()

        if not chunk_text:
            continue

        normalized_text = " ".join(
            chunk_text.lower().split()
        )

        chunk_hash = hashlib.sha256(
            normalized_text.encode("utf-8")
        ).hexdigest()

        if chunk_hash in seen_hashes:
            continue

        seen_hashes.add(chunk_hash)

        unique_chunks.append(
            {
                **chunk,
                "chunk_text": chunk_text,
                "chunk_hash": chunk_hash,
            }
        )

    return unique_chunks

def build_bm25_query(
    country: str,
    role: str,
    regulation_topics: list[str],
    why_this_applies: str | list[str],
) -> str:

    if isinstance(why_this_applies, list):
        why_this_applies = " ".join(why_this_applies)

    return " ".join(
        [
            country,
            role,
            *regulation_topics,
            why_this_applies,
        ]
    )

def filter_chunks_with_bm25(
    chunks: list[dict],
    query: str,
    top_k: int = 20,
) -> list[dict]:

    if not chunks:
        return []

    corpus = [
        tokenize(chunk["chunk_text"])
        for chunk in chunks
    ]

    bm25 = BM25Okapi(corpus)

    query_tokens = tokenize(query)

    scores = bm25.get_scores(query_tokens)

    ranked = sorted(
        zip(chunks, scores),
        key=lambda item: item[1],
        reverse=True,
    )

    filtered = []

    for chunk, score in ranked[:top_k]:
            filtered.append(
                {
                    **chunk,
                    "bm25_score": round(float(score), 4),
                }
            )

    # Fallback: keep the best chunk(s) if nothing met the threshold
    if not filtered and ranked:
        for chunk, score in ranked[:3]:
            filtered.append(
                {
                    **chunk,
                    "bm25_score": round(float(score), 4),
                }
            )

    return filtered

def validate_chunks(
    chunks: list[dict],
    min_characters: int = 200,
    max_characters: int = 4000,
) -> list[dict]:
    """
    Removes chunks that are empty, too short, too large, or contain
    insufficient readable text.
    """
    valid_chunks = []

    for chunk in chunks:
        chunk_text = chunk.get("chunk_text", "").strip()

        if not chunk_text:
            continue

        if len(chunk_text) < min_characters:
            continue

        if len(chunk_text) > max_characters:
            continue
        
        # Reject chunks containing almost no alphabetic content.
        alphabetic_count = sum(
            character.isalpha()
            for character in chunk_text
        )

        if alphabetic_count < 100:
            continue

        valid_chunks.append(chunk)

    return valid_chunks

def split_large_document(
    cleaned_text: str,
    max_section_size: int = 50_000,
) -> list[str]:
    """
    Splits a large extracted document into manageable sections before
    LLM semantic cleaning.
    """

    if not cleaned_text:
        return []
    
    if len(cleaned_text) <= max_section_size:
        return [cleaned_text]

    return _LARGE_DOCUMENT_SPLITTER.split_text(
        cleaned_text
    )

def helper_extract_url_content_and_ingest(
    sources: list[dict],
    country: str,
    role: str,
    regulation_topics: list[str],
    why_this_applies: str | list[str],
    shipment_id: str,
    namespace: str,
) -> None:
    """
    Extracts, cleans, chunks, validates, enriches, and embeds regulatory
    content selected by the source reranker.
    """

    client = TavilyClient(
        api_key=os.getenv("TAVILY_API_KEY")
    )

    for source in sources:
        url = source.get("url")

        if not url:
            continue

        source_title = source.get("title", "")
        tavily_score = source.get("score")
        rank_score = source.get("rank_score")
        rerank_score = source.get("rerank_score")
        rerank_reason = source.get("rerank_reason", "")

        try:
            extraction = client.extract(
                urls=[url],
            )

            extraction_results = extraction.get(
                "results",
                [],
            )

            if not extraction_results:
                logger.error(
                    f"No content extracted from source: {url}"
                )
                continue

            raw_text = extraction_results[0].get(
                "raw_content",
                "",
            )

            if not raw_text:
                logger.error(
                    f"Extracted content was empty: {url}"
                )
                continue

            cleaned_text = clean_extracted_content(
                raw_text
            )

            if not cleaned_text:
                logger.error(
                    f"No usable content after cleaning: {url}"
                )
                continue

            logger.info(
                f"Content cleaning for {url}: "
                f"{len(raw_text)} → "
                f"{len(cleaned_text)} characters"
            )

            generated_chunks = create_regulatory_chunks(
                cleaned_text=cleaned_text,
                country=country,
                role=role,
                regulation_topics=regulation_topics,
            )

            logger.info(
                f"Semantic chunking for {url}: "
                f"{len(generated_chunks)} chunks created"
            )

            unique_chunks = remove_duplicate_chunks(
                generated_chunks
            )

            logger.info(
                f"Duplicate removal for {url}: "
                f"{len(generated_chunks)} → "
                f"{len(unique_chunks)} chunks"
            )

            bm25_query = build_bm25_query(
                country=country,
                role=role,
                regulation_topics=regulation_topics,
                why_this_applies=why_this_applies,
            )
            
            filtered_chunks = filter_chunks_with_bm25(
                chunks=unique_chunks,
                query=bm25_query,
                top_k=20
            )
            logger.info(
                f"BM25 filtering: "
                f"{len(unique_chunks)} → "
                f"{len(filtered_chunks)} chunks"
            )
            validated_chunks = validate_chunks(
                chunks=filtered_chunks,
                min_characters=200,
                max_characters=4000,
            )

            logger.info(
                f"Chunk validation for {url}: "
                f"{len(unique_chunks)} → "
                f"{len(validated_chunks)} chunks"
            )

            if not validated_chunks:
                logger.error(
                    f"No valid chunks available for ingestion: {url}"
                )
                continue

            ingested_at = datetime.now(
                timezone.utc
            ).isoformat()

            rag_nodes = []

            for chunk_index, chunk in enumerate(
                validated_chunks,
                start=1,
            ):
                node = TextNode(
                    id_=chunk["chunk_hash"],
                    text=chunk["chunk_text"],
                )

                node.metadata = {
                    # Execution context
                    "shipment_id": shipment_id,
                    "namespace": namespace,

                    # Route context
                    "country": country,
                    "role": role,

                    # Regulatory context
                    "regulation_topics": regulation_topics,
                    "why_this_applies": (
                                            "\n".join(why_this_applies)
                                            if isinstance(why_this_applies, list)
                                            else why_this_applies
                                        ),

                    # Source traceability
                    "source_type": "government_regulation",
                    "source_url": url,
                    "document_title": source_title,

                    # Retrieval scoring
                    "tavily_score": tavily_score,
                    "rank_score": rank_score,
                    "rerank_score": rerank_score,
                    "rerank_reason": rerank_reason,

                    # Chunk traceability
                    "chunk_hash": chunk["chunk_hash"],
                    "chunk_index": chunk_index,
                    "chunk_character_count": len(
                        chunk["chunk_text"]
                    ),

                    # Audit metadata
                    "ingested_at": ingested_at,
                }

                rag_nodes.append(node)

            ingest_data_cockroach(
                rag_nodes=rag_nodes,
                namespace=namespace,
            )

            logger.info(
                f"Ingested {len(rag_nodes)} chunks "
                f"for {country} / {role} from {url}"
            )

        except Exception as exc:
            logger.error(
                f"Error processing URL {url}: {exc}"
            )
            continue

def create_regulatory_chunks(
    cleaned_text: str,
    country: str,
    role: str,
    regulation_topics: list[str],
) -> list[dict]:

    if not cleaned_text:
        return []

    document_sections = split_large_document(
        cleaned_text
    )

    topic_text = ", ".join(regulation_topics)

    context_header = (
        f"Country: {country}\n"
        f"Route role: {role}\n"
        f"Regulation topics: {topic_text}"
    )

    generated_chunks = []

    for section_number, section_text in enumerate(
        document_sections,
        start=1,
    ):

        retrieval_chunks = (
            _REGULATORY_CHUNK_SPLITTER.split_text(
                section_text
            )
        )

        for chunk_index, chunk in enumerate(
            retrieval_chunks,
            start=1,
        ):

            generated_chunks.append(
                {
                    "chunk_text": (
                        f"{context_header}\n\n{chunk}"
                    ),
                    "source_section": section_number,
                    "chunk_index": chunk_index,
                }
            )

    return generated_chunks

def helper_stringify_list(values: list | None) -> str:
    return "\n".join(str(value) for value in (values or []))

from collections.abc import Mapping
def helper_get_route_check_status(
    decision: RouteComplianceDecision,
) -> str:
    logger.info(f"Evaluating route check status for decision: {decision}")
    if isinstance(decision, Mapping):
        human_review = decision.get("human_intervention_required", False)
        status = decision.get("compliance_status")
    else:
        human_review = decision.human_intervention_required
        status = decision.compliance_status

    # Handle Enum or string
    if hasattr(status, "value"):
        status = status.value

    if human_review:
        return "Review Required"

    if status == "COMPLIANT":
        return "Passed"

    if status in ("NON_COMPLIANT", "BLOCKED"):
        return "Blocked"

    return "Failed"


def helper_delete_namespace_pinecone(namespace: str | None = None):
    """
    Deletes all regulatory content for a specific shipment namespace
    from the Pinecone vector store.
    """

    delete_shipment_namespace_cockroach(namespace=namespace)


def helper_extract_cockroach_rows(
    mcp_result,
) -> list[dict]:
    """
    Extracts rows from the CockroachDB MCP response.

    Expected MCP response format:

    [
        {
            "type": "text",
            "text": "{\"rows\": [...]}"
        }
    ]
    """

    if not mcp_result:
        return []

    rows = []

    for item in mcp_result:
        if not isinstance(item, dict):
            continue

        text = item.get("text")

        if not text:
            continue

        try:
            parsed = json.loads(text)

            result_rows = parsed.get(
                "rows",
                [],
            )

            if isinstance(result_rows, list):
                rows.extend(result_rows)

        except json.JSONDecodeError:
            logger.warning(
                "Unable to parse Cockroach MCP response: %s",
                text,
            )

    return rows