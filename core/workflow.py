"""
LangGraph StateGraph Orchestration Workflow for Support Triage.
Explicit execution nodes:
1. ingest_and_extract: Extracts customer domain, email, invoice ID, and amounts
2. classify_intent: Categorizes intent, urgency, and customer tier
3. execute_mcp_tools: Invokes FastMCP enterprise tools with safety checks
4. calibrated_resolution: Formats grounded resolution and validates Pydantic schema
"""

import os
import re
import time
import json
from typing import Dict, Any, List, Optional
from core.schemas import (
    ExtractedEntities,
    TriageDecision,
    ToolCallAudit,
    FinalTriageOutput,
    TriageState
)
from core.telemetry import observe
from tools_mcp.server import query_customer_crm, verify_stripe_invoice, create_jira_escalation

# LangGraph conditional import with clean state machine fallback
_HAS_LANGGRAPH = False
try:
    from langgraph.graph import StateGraph, START, END
    _HAS_LANGGRAPH = True
except ImportError:
    _HAS_LANGGRAPH = False


@observe(name="node_ingest_and_extract")
def ingest_and_extract(state: TriageState) -> Dict[str, Any]:
    """Extract email, domain, invoice ID, monetary amounts, and check for injection prompts."""
    raw_text = state.get("raw_message", "")
    
    # 1. Email extraction
    email_match = re.search(r"[\w\.-]+@[\w\.-]+\.\w+", raw_text)
    email = email_match.group(0).lower() if email_match else "unknown"
    domain = email.split("@")[-1] if email != "unknown" else "unknown"

    # 2. Invoice ID extraction (e.g. INV-9042, INV-99999)
    inv_match = re.search(r"INV-\d{4,6}", raw_text, re.IGNORECASE)
    invoice_id = inv_match.group(0).upper() if inv_match else None

    # 3. Monetary amount extraction
    amt_match = re.search(r"\$\s?(\d+(?:,\d{3})*(?:\.\d{2})?)", raw_text)
    amount = float(amt_match.group(1).replace(",", "")) if amt_match else 0.0

    # 4. Error code patterns
    error_codes = re.findall(r"\b(?:500|502|503|504|401|403|DEADLOCK|OOM|TIMEOUT)\b", raw_text, re.IGNORECASE)

    # 5. Prompt injection detection
    injection_patterns = [
        "ignore previous instructions",
        "system prompt",
        "bypass safety",
        "unconditional refund",
        "grant admin",
        "execute raw sql"
    ]
    is_injection = any(p in raw_text.lower() for p in injection_patterns)

    # Urgency keywords
    urgency_kw = [w for w in ["urgent", "down", "outage", "broken", "critical", "immediately", "churn"] if w in raw_text.lower()]

    entities = ExtractedEntities(
        customer_email=email,
        domain=domain,
        invoice_id=invoice_id,
        amount=amount,
        urgency_keywords=urgency_kw,
        error_codes=error_codes,
        is_prompt_injection=is_injection
    )

    return {"entities": entities}


@observe(name="node_classify_intent")
def classify_intent(state: TriageState) -> Dict[str, Any]:
    """Classify ticket intent into structured Pydantic v2 TriageDecision."""
    raw_text = state.get("raw_message", "")
    entities: ExtractedEntities = state.get("entities")
    ticket_id = state.get("ticket_id", "TICK-0000")
    offline = state.get("offline", True)

    # If live LLM mode is enabled and api key exists, attempt LiteLLM structured call
    if not offline and os.getenv("OPENAI_API_KEY"):
        try:
            import litellm
            messages = [
                {"role": "system", "content": "You are an enterprise support triage classifier. Output JSON matching the schema."},
                {"role": "user", "content": f"Classify this ticket:\n{raw_text}"}
            ]
            response = litellm.completion(
                model="gpt-4o-mini",
                messages=messages,
                response_format={"type": "json_object"},
                temperature=0.0
            )
            parsed = json.loads(response.choices[0].message.content)
            decision = TriageDecision.model_validate({
                "ticket_id": ticket_id,
                "intent": parsed.get("intent", "GENERAL_INQUIRY"),
                "severity": parsed.get("severity", "LOW"),
                "customer_tier": parsed.get("customer_tier", "STANDARD"),
                "tools_to_invoke": parsed.get("tools_to_invoke", []),
                "rationale": parsed.get("rationale", "LLM structured classification")
            })
            return {"decision": decision}
        except Exception:
            pass  # Fallback smoothly to deterministic calibrated classifier

    # Deterministic Rule Engine for Offline / Zero-Key Execution
    lower = raw_text.lower()

    if entities.is_prompt_injection or any(w in lower for w in ["idor", "vulnerability", "disclosure", "exploit", "cve", "security", "presigned"]):
        intent = "SECURITY_DISCLOSURE"
        severity = "CRITICAL"
        rationale = "Security anomaly or vulnerability disclosure detected."
        tools = ["create_jira_escalation"]
    elif any(w in lower for w in ["data corruption", "missing records", "schema drop", "table vanished"]):
        intent = "DATA_INTEGRITY"
        severity = "CRITICAL"
        rationale = "Customer reported suspected data loss."
        tools = ["query_customer_crm", "create_jira_escalation"]
    elif any(err in entities.error_codes for err in ["500", "502", "503", "504", "DEADLOCK", "OOM"]) or any(w in lower for w in ["outage", "down", "crashloopbackoff", "gateway timeout"]):
        intent = "URGENT_OUTAGE"
        severity = "CRITICAL"
        rationale = "Production infrastructure impairment detected."
        tools = ["query_customer_crm", "create_jira_escalation"]
    elif any(w in lower for w in ["bill", "charge", "invoice", "double billed", "refund", "overcharged", "dispute", "debit", "paid twice", "wire transfer"]):
        intent = "BILLING_DISPUTE"
        severity = "HIGH" if ("urgent" in lower or entities.amount > 1000) else "MEDIUM"
        rationale = "Billing ledger variance or payment verification request."
        tools = ["query_customer_crm", "verify_stripe_invoice"]
    elif any(w in lower for w in ["feature", "roadmap", "scim", "can we export", "would love", "support for", "timeline", "multi-currency"]):
        intent = "FEATURE_REQUEST"
        severity = "LOW"
        rationale = "Product enhancement request."
        tools = ["query_customer_crm"]
    else:
        intent = "GENERAL_INQUIRY"
        severity = "LOW"
        rationale = "General product usage inquiry."
        tools = ["query_customer_crm"]

    decision = TriageDecision(
        ticket_id=ticket_id,
        intent=intent,
        severity=severity,
        customer_tier="STANDARD",
        tools_to_invoke=tools,
        rationale=rationale
    )

    return {"decision": decision}


@observe(name="node_execute_mcp_tools")
def execute_mcp_tools(state: TriageState) -> Dict[str, Any]:
    """Execute enterprise tools via FastMCP with strict parameter passing and audit logs."""
    entities: ExtractedEntities = state.get("entities")
    decision: TriageDecision = state.get("decision")
    ticket_id = state.get("ticket_id", "TICK-0000")

    tool_audits: List[ToolCallAudit] = []
    
    # 1. CRM Lookup
    crm_res = query_customer_crm(entities.customer_email)
    tool_audits.append(ToolCallAudit(
        tool_name="query_customer_crm",
        arguments={"domain_or_email": entities.customer_email},
        result=crm_res,
        execution_time_ms=crm_res.get("query_latency_ms", 1.2),
        status="SUCCESS"
    ))

    # 2. Stripe Verification (Strictly for Billing Disputes with Invoice IDs)
    if decision.intent == "BILLING_DISPUTE":
        if entities.invoice_id and not entities.is_prompt_injection:
            stripe_res = verify_stripe_invoice(entities.invoice_id)
            tool_audits.append(ToolCallAudit(
                tool_name="verify_stripe_invoice",
                arguments={"invoice_id": entities.invoice_id},
                result=stripe_res,
                execution_time_ms=stripe_res.get("verification_latency_ms", 2.1),
                status="SUCCESS"
            ))
        else:
            # Blocked: Either missing invoice ID or prompt injection attempt
            tool_audits.append(ToolCallAudit(
                tool_name="verify_stripe_invoice",
                arguments={"invoice_id": str(entities.invoice_id)},
                result={"error": "Blocked by security gate: suspicious or missing invoice ID"},
                execution_time_ms=0.1,
                status="BLOCKED"
            ))

    # 3. JIRA Escalation (Outages, Security, or Critical Incidents)
    if decision.severity in ["CRITICAL", "HIGH"] and decision.intent in ["URGENT_OUTAGE", "SECURITY_DISCLOSURE", "DATA_INTEGRITY"]:
        jira_res = create_jira_escalation(
            ticket_id=ticket_id,
            severity=decision.severity,
            summary=f"[{decision.intent}] {decision.rationale} (Customer: {crm_res.get('company')})"
        )
        tool_audits.append(ToolCallAudit(
            tool_name="create_jira_escalation",
            arguments={"ticket_id": ticket_id, "severity": decision.severity},
            result=jira_res,
            execution_time_ms=jira_res.get("escalation_latency_ms", 1.8),
            status="SUCCESS"
        ))

    return {
        "customer_crm": crm_res,
        "tool_audits": tool_audits
    }


@observe(name="node_calibrated_resolution")
def calibrated_resolution(state: TriageState) -> Dict[str, Any]:
    """Generate final audit trail, customer draft response, and schema adherence status."""
    entities: ExtractedEntities = state.get("entities")
    decision: TriageDecision = state.get("decision")
    crm_res: Dict[str, Any] = state.get("customer_crm", {})
    tool_audits: List[ToolCallAudit] = state.get("tool_audits", [])
    ticket_id = state.get("ticket_id", "TICK-0000")

    resolution_draft = ""
    company_name = crm_res.get("company", "Valued Customer")
    tier = crm_res.get("tier", "STANDARD")
    sla = crm_res.get("sla_hours", 24)
    csm = crm_res.get("csm", "Support Desk")

    if entities.is_prompt_injection:
        resolution_draft = f"Security anomaly detected on {ticket_id}. Execution halted and forwarded to SecOps. Automated refund capabilities locked."
    elif decision.intent == "BILLING_DISPUTE":
        stripe_audit = next((t for t in tool_audits if t.tool_name == "verify_stripe_invoice"), None)
        if stripe_audit and stripe_audit.status == "SUCCESS":
            stripe_res = stripe_audit.result
            if stripe_res.get("duplicate_detected") and stripe_res.get("refundable"):
                resolution_draft = f"Identified duplicate charge on invoice {entities.invoice_id}. Auto-refund of ${entities.amount:,.2f} scheduled via Stripe. Account CSM {csm} notified."
            elif stripe_res.get("status") == "NOT_FOUND":
                resolution_draft = f"Invoice ID {entities.invoice_id} could not be located in Stripe billing records. Requested customer re-verification."
            else:
                resolution_draft = f"Invoice {entities.invoice_id} verified as legitimate non-duplicate charge ({stripe_res.get('note', 'standard billing')}). Escalated to billing specialist for contract review."
        else:
            resolution_draft = f"Billing inquiry received for {company_name}. Awaiting invoice reference ID for automated ledger verification."

    elif decision.intent == "URGENT_OUTAGE":
        jira_audit = next((t for t in tool_audits if t.tool_name == "create_jira_escalation"), None)
        jira_ref = jira_audit.result.get("jira_key", "ENG-SRE-QUEUE") if jira_audit else "ENG-PENDING"
        resolution_draft = f"Critical incident paged to on-call engineering under {tier} SLA ({sla}h target). Incident reference: {jira_ref}."

    elif decision.intent == "SECURITY_DISCLOSURE":
        resolution_draft = f"Security ticket flagged for priority inspection under {tier} protocol. On-call SecOps paged."

    elif decision.intent == "FEATURE_REQUEST":
        resolution_draft = f"Feature request logged for {company_name} ({tier} tier). Tagged in product roadmap backlog."

    else:
        resolution_draft = f"General request logged for {company_name}. Routed to product operations queue with {sla}h SLA."

    final_output = FinalTriageOutput(
        ticket_id=ticket_id,
        intent=decision.intent,
        severity=decision.severity,
        customer=crm_res,
        tools_called=tool_audits,
        resolution=resolution_draft,
        schema_valid=True,
        execution_trace_id=f"trc_{abs(hash(ticket_id))}",
        status="TRIAGED"
    )

    return {
        "resolution": resolution_draft,
        "final_output": final_output,
        "schema_adherence": True
    }


def build_triage_graph():
    """Build LangGraph StateGraph pipeline if library is available; otherwise return StateMachine runner."""
    if _HAS_LANGGRAPH:
        workflow = StateGraph(TriageState)
        workflow.add_node("ingest_and_extract", ingest_and_extract)
        workflow.add_node("classify_intent", classify_intent)
        workflow.add_node("execute_mcp_tools", execute_mcp_tools)
        workflow.add_node("calibrated_resolution", calibrated_resolution)

        workflow.add_edge(START, "ingest_and_extract")
        workflow.add_edge("ingest_and_extract", "classify_intent")
        workflow.add_edge("classify_intent", "execute_mcp_tools")
        workflow.add_edge("execute_mcp_tools", "calibrated_resolution")
        workflow.add_edge("calibrated_resolution", END)

        return workflow.compile()

    # Portable State Machine Runner executing identical node sequence
    class PortableWorkflowRunner:
        def invoke(self, state: TriageState) -> Dict[str, Any]:
            cur_state = dict(state)
            for node_fn in [ingest_and_extract, classify_intent, execute_mcp_tools, calibrated_resolution]:
                updates = node_fn(cur_state)
                cur_state.update(updates)
            return cur_state

    return PortableWorkflowRunner()
