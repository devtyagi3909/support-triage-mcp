"""
Model Context Protocol (MCP) Server for Enterprise Support Triage.
Exposes tools via FastMCP:
- query_customer_crm: Look up company ARR, contract tier, and assigned CSM
- verify_stripe_invoice: Verify charge duplicates, payment status, and refund eligibility
- create_jira_escalation: File on-call bug report with customer context and SLA tracking
"""

import time
from typing import Dict, Any, Optional

try:
    from fastmcp import FastMCP
    mcp = FastMCP("SupportTriageServer")
except ImportError:
    class FastMCPShim:
        def __init__(self, name: str = "SupportTriageServer"):
            self.name = name
            self.tools = {}

        def tool(self, **kwargs):
            def decorator(func):
                self.tools[func.__name__] = func
                return func
            return decorator

    mcp = FastMCPShim("SupportTriageServer")

# Comprehensive enterprise mock databases
MOCK_CRM_DB: Dict[str, Dict[str, Any]] = {
    "acmecorp.io": {"company": "Acme Corp", "arr": 120000, "tier": "ENTERPRISE", "csm": "Sarah K.", "sla_hours": 1},
    "scaleup.ai": {"company": "ScaleUp AI", "arr": 48000, "tier": "GROWTH", "csm": "Alex M.", "sla_hours": 4},
    "finflow.co": {"company": "FinFlow Payments", "arr": 250000, "tier": "STRATEGIC", "csm": "David R.", "sla_hours": 0.5},
    "cloudscale.tech": {"company": "CloudScale Systems", "arr": 85000, "tier": "ENTERPRISE", "csm": "Elena V.", "sla_hours": 1},
    "retailhub.com": {"company": "RetailHub Global", "arr": 15000, "tier": "GROWTH", "csm": "James P.", "sla_hours": 8},
    "securenet.org": {"company": "SecureNet Ops", "arr": 95000, "tier": "ENTERPRISE", "csm": "David R.", "sla_hours": 1},
    "devstream.io": {"company": "DevStream Analytics", "arr": 32000, "tier": "GROWTH", "csm": "Alex M.", "sla_hours": 4},
    "nexustech.app": {"company": "Nexus Technologies", "arr": 60000, "tier": "ENTERPRISE", "csm": "Sarah K.", "sla_hours": 1},
    "globalcorp.com": {"company": "Global Logistics Corp", "arr": 180000, "tier": "STRATEGIC", "csm": "David R.", "sla_hours": 0.5},
    "datapulse.io": {"company": "DataPulse AI", "arr": 22000, "tier": "STANDARD", "csm": "Queue", "sla_hours": 24},
}

MOCK_BILLING_DB: Dict[str, Dict[str, Any]] = {
    "INV-9042": {"status": "PAID", "amount": 4200.0, "duplicate_detected": True, "charge_ids": ["ch_99a", "ch_99b"], "refundable": True},
    "INV-8821": {"status": "PAID", "amount": 1250.0, "duplicate_detected": True, "charge_ids": ["ch_881", "ch_882"], "refundable": True},
    "INV-7710": {"status": "PAID", "amount": 350.0, "duplicate_detected": False, "charge_ids": ["ch_771"], "refundable": False, "note": "Valid prorated upgrade"},
    "INV-6002": {"status": "PAID", "amount": 8900.0, "duplicate_detected": True, "charge_ids": ["ch_601", "ch_602"], "refundable": True},
    "INV-5120": {"status": "PAID", "amount": 149.0, "duplicate_detected": False, "charge_ids": ["ch_512"], "refundable": False, "note": "Monthly base plan fee"},
    "INV-4401": {"status": "FAILED", "amount": 7500.0, "duplicate_detected": False, "charge_ids": ["ch_440_failed"], "refundable": False, "note": "Card issuer declined"},
    "INV-3390": {"status": "PAID", "amount": 540.0, "duplicate_detected": True, "charge_ids": ["ch_339a", "ch_339b"], "refundable": True},
    "INV-2210": {"status": "PAID", "amount": 12000.0, "duplicate_detected": False, "charge_ids": ["ch_221"], "refundable": False, "note": "Annual enterprise contract"},
    "INV-99999": {"status": "NOT_FOUND", "amount": 50000.0, "duplicate_detected": False, "refundable": False, "note": "Fraudulent invoice ID injected in prompt"},
}


@mcp.tool()
def query_customer_crm(domain_or_email: str) -> Dict[str, Any]:
    """Look up customer ARR, contract tier, assigned CSM, and SLA target in the enterprise CRM."""
    start = time.time()
    clean_domain = domain_or_email.split("@")[-1].strip().lower() if "@" in domain_or_email else domain_or_email.strip().lower()
    customer = MOCK_CRM_DB.get(clean_domain, {
        "company": "Unknown Organization",
        "arr": 0,
        "tier": "STANDARD",
        "csm": "Support Queue",
        "sla_hours": 24
    })
    customer["query_latency_ms"] = round((time.time() - start) * 1000, 2)
    return customer


@mcp.tool()
def verify_stripe_invoice(invoice_id: str) -> Dict[str, Any]:
    """Inspect Stripe ledger to check if duplicate charges or failed payments occurred."""
    start = time.time()
    clean_id = invoice_id.strip().upper()
    if clean_id in MOCK_BILLING_DB:
        record = MOCK_BILLING_DB[clean_id]
        return {
            "invoice_id": clean_id,
            "status": record["status"],
            "amount": record["amount"],
            "duplicate_detected": record.get("duplicate_detected", False),
            "refundable": record.get("refundable", False),
            "recommendation": "AUTO_REFUND_DUPLICATE" if record.get("duplicate_detected") and record.get("refundable") else "MANUAL_REVIEW",
            "verification_latency_ms": round((time.time() - start) * 1000, 2)
        }
    return {
        "invoice_id": clean_id,
        "status": "NOT_FOUND",
        "duplicate_detected": False,
        "refundable": False,
        "recommendation": "REJECT_INVALID_INVOICE",
        "verification_latency_ms": round((time.time() - start) * 1000, 2)
    }


@mcp.tool()
def create_jira_escalation(ticket_id: str, severity: str, summary: str, component: str = "core-infra") -> Dict[str, Any]:
    """Create a high-priority incident ticket in JIRA for the on-call engineering team."""
    start = time.time()
    hashed_num = abs(hash(ticket_id)) % 9000 + 1000
    jira_key = f"ENG-SRE-{hashed_num}"
    is_critical = severity.upper() == "CRITICAL"
    return {
        "jira_key": jira_key,
        "severity": severity.upper(),
        "ticket_id": ticket_id,
        "summary": summary[:120],
        "component": component,
        "status": "PAGED_ONCALL" if is_critical else "QUEUED_SPRINT",
        "escalation_latency_ms": round((time.time() - start) * 1000, 2)
    }
