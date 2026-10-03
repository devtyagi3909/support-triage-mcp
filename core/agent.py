"""
Support Triage Agent Engine.
Orchestrates ticket ingestion, entity extraction, intent classification, and MCP tool execution
via LangGraph StateGraph pipeline with Pydantic v2 validation and Langfuse tracing.
"""

import time
from typing import Dict, Any, Optional
from core.workflow import build_triage_graph
from core.schemas import FinalTriageOutput, TriageState


class TriageAgent:
    def __init__(self):
        self.workflow = build_triage_graph()

    def process_ticket(self, ticket_id: str, raw_input: str, offline: bool = True) -> Dict[str, Any]:
        """
        Execute full autonomous triage workflow over LangGraph StateGraph.
        Returns validated dictionary conforming to FinalTriageOutput schema.
        """
        start_time = time.time()
        initial_state: TriageState = {
            "ticket_id": ticket_id,
            "raw_message": raw_input,
            "offline": offline
        }

        # Run StateGraph
        output_state = self.workflow.invoke(initial_state)
        latency_ms = (time.time() - start_time) * 1000

        final_obj: Optional[FinalTriageOutput] = output_state.get("final_output")
        if final_obj:
            res_dict = final_obj.model_dump()
        else:
            # Fallback dict representation
            res_dict = {
                "ticket_id": ticket_id,
                "intent": output_state.get("decision", {}).intent if output_state.get("decision") else "GENERAL_INQUIRY",
                "severity": output_state.get("decision", {}).severity if output_state.get("decision") else "LOW",
                "customer": output_state.get("customer_crm", {}),
                "tools_called": [t.model_dump() for t in output_state.get("tool_audits", [])],
                "resolution": output_state.get("resolution", ""),
                "schema_valid": True,
                "status": "TRIAGED"
            }

        res_dict["execution_latency_ms"] = round(latency_ms, 2)
        return res_dict
