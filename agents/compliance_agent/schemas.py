from enum import Enum
from typing import List,Literal
from pydantic import BaseModel, Field


class RegulationRequirement(BaseModel):
    country: str = Field(description="Country for which regulations must be checked")
    route_type: str = Field(description="Origin, Transit or Destination")
    regulation_topics: List[str] = Field(description="Specific regulatory topics that must be searched, such as Export, Import, Transit, Prescription Medicine, Cold Chain, Controlled Substance, Hazardous Material, GDP, GMP, or Dangerous Goods")
    why_this_applies: list[str] = Field(description="Reason these regulatory requirements apply based on the shipment, product, transport mode, and route")


class RegulationSearchPlan(BaseModel):
    regulation_requirements: List[RegulationRequirement]

class RouteComplianceStatus(str, Enum):
    COMPLIANT = "COMPLIANT"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    NON_COMPLIANT = "NON_COMPLIANT"
    BLOCKED = "BLOCKED"

class ConfidenceLevel(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"

class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

class RouteComplianceDecision(BaseModel):
    country: str
    route_type: str

    compliance_status: RouteComplianceStatus
    confidence_level: ConfidenceLevel
    confidence_score: float

    human_intervention_required: bool

    risk_level: RiskLevel

    summary: str
    reason: str

    missing_documents: List[str]
    policy_conflicts: List[str]
    regulatory_concerns: List[str]
    recommended_action: str
    evidence_sources: List[str]


class ShipmentComplianceDecision(BaseModel):
    shipment_id: str
    shipment_number: str

    overall_status: str = Field(
        description=(
            "COMPLIANT, REVIEW_REQUIRED, "
            "NON_COMPLIANT, or BLOCKED"
        )
    )

    overall_risk_level: str = Field(
        description="LOW, MEDIUM, HIGH, or CRITICAL"
    )

    confidence_score: float

    human_review_required: bool

    summary: str

    ai_reasoning: str

    route_summary: List[dict]

    blocking_issues: List[str]

    missing_documents: List[str]

    evidence_summary: List[str]

    recommended_next_action: str

    # --------------------------------------------------
    # COMPLIANCE MEMORY
    # --------------------------------------------------

    memory_summary: str = Field(
        description=(
            "Compact natural-language memory for future "
            "shipment compliance executions. Include the "
            "shipment's overall outcome and, for every route, "
            "the shipment_route_id, country, route_type, "
            "previous compliance outcome, and the important "
            "conditions or reasons needed to determine whether "
            "that route can be SKIPPED or must be ANALYZED "
            "during the next execution."
        )
    )


class SourceRerankResult(BaseModel):
    source_index: int

    relevance_score: float = Field(
        ge=0.0,
        le=1.0,
        description="Overall relevance score between 0 and 1",
    )

    country_match: bool
    route_match: bool
    topic_match: bool

    selected: bool
    reason: str

class SourceRerankResponse(BaseModel):
    results: list[SourceRerankResult]



class RouteMemoryDecision(BaseModel):
    shipment_route_id: str = Field(
        description="The Salesforce shipment route ID."
    )

    action: Literal["SKIP", "ANALYZE"] = Field(
        description=(
            "SKIP only when the current route is unchanged "
            "and the latest relevant previous result was PASSED. "
            "Otherwise ANALYZE."
        )
    )

    reason: str = Field(
        description=(
            "Explanation based on current shipment context "
            "and previous compliance memory."
        )
    )

    previous_status: str | None = Field(
        default=None,
        description=(
            "Latest relevant previous compliance status "
            "for this route, if available."
        )
    )

    route_changed: bool = Field(
        description=(
            "Whether the current route differs from the "
            "previous route snapshot."
        )
    )


class ShipmentMemoryAnalysis(BaseModel):
    route_decisions: list[RouteMemoryDecision]

    overall_reasoning: str = Field(
        description=(
            "Summary of how previous shipment compliance "
            "memory was used."
        )
    )