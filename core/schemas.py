"""
Strict Pydantic v2 Schemas for Support Triage Agent.
Enforces ConfigDict(extra="forbid") and type validation on all payloads and transitions.
"""

from typing import Dict, Any, List, Optional, Literal
from typing_extensions import TypedDict
from pydantic import BaseModel, Field, ConfigDict, field_validator


class TicketPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticket_id: str = Field(..., min_length=3, max_length=64, description="Unique ticket identifier")
    raw_message: str = Field(..., min_length=1, description="Raw customer message or webhook payload")

    @field_validator("ticket_id")
    @classmethod
    def validate_ticket_id(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("ticket_id cannot be blank")
        return v.strip()


class ExtractedEntities(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_email: str = Field(default="unknown")
    domain: str = Field(default="unknown")
    invoice_id: Optional[str] = Field(default=None)
    amount: float = Field(default=0.0, ge=0.0)
    urgency_keywords: List[str] = Field(default_factory=list)
    error_codes: List[str] = Field(default_factory=list)
    is_prompt_injection: bool = Field(default=False)


class TriageDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticket_id: str
    intent: Literal[
        "BILLING_DISPUTE",
        "URGENT_OUTAGE",
        "FEATURE_REQUEST",
        "SECURITY_DISCLOSURE",
        "DATA_INTEGRITY",
        "GENERAL_INQUIRY"
    ]
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    customer_tier: str = Field(default="STANDARD")
    tools_to_invoke: List[str] = Field(default_factory=list)
    rationale: str = Field(default="")


class ToolCallAudit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)
    result: Dict[str, Any] = Field(default_factory=dict)
    execution_time_ms: float = Field(default=0.0, ge=0.0)
    status: Literal["SUCCESS", "FAILED", "BLOCKED"]


class FinalTriageOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticket_id: str
    intent: str
    severity: str
    customer: Dict[str, Any] = Field(default_factory=dict)
    tools_called: List[ToolCallAudit] = Field(default_factory=list)
    resolution: str
    schema_valid: bool = Field(default=True)
    execution_trace_id: Optional[str] = Field(default=None)
    execution_latency_ms: Optional[float] = Field(default=None)
    status: str = Field(default="TRIAGED")


class TriageState(TypedDict, total=False):
    ticket_id: str
    raw_message: str
    offline: bool
    entities: ExtractedEntities
    decision: TriageDecision
    customer_crm: Dict[str, Any]
    tool_audits: List[ToolCallAudit]
    resolution: str
    schema_adherence: bool
    latency_ms: float
    trace_id: str
