"""Clean forecast ledger construction.

Raw agent logs and dialogue transcripts are audit artifacts. Polished outputs
should consume this ledger, which is derived only from structured simulation
state: agent forecast paths, scenario aggregates, final outcomes, and cleaned
debate effects.
"""

from __future__ import annotations

from typing import Any, Dict, List


class ForecastLedgerBuilder:
    """Build a report-safe ledger from structured simulation state."""

    def build(self, state: Dict[str, Any]) -> Dict[str, Any]:
        plan = state.get("domain_plan") or {}
        aggregated = state.get("aggregated_outputs") or {}
        scenario_outputs = state.get("scenario_outputs") or {}
        agent_outputs = state.get("agent_outputs") or []
        disagreement = aggregated.get("agent_disagreement") or {}
        debate_impact = aggregated.get("debate_impact") or {}

        rows: List[Dict[str, Any]] = []
        for output in agent_outputs:
            agent_id = output.get("agent_id")
            target = output.get("target_variable")
            confidence = output.get("confidence")
            for point in output.get("forecast_path") or []:
                rows.append({
                    "agent_id": agent_id,
                    "target_variable": target,
                    "date": point.get("date"),
                    "scenario": point.get("scenario"),
                    "value": point.get("value"),
                    "unit": point.get("unit"),
                    "confidence": confidence,
                })

        scenario_rows: List[Dict[str, Any]] = []
        for scenario, targets in scenario_outputs.items():
            if not isinstance(targets, dict):
                continue
            for target, points in targets.items():
                for point in points or []:
                    scenario_rows.append({
                        "scenario": scenario,
                        "target_variable": target,
                        "date": point.get("date"),
                        "value": point.get("value"),
                        "unit": point.get("unit"),
                        "agent_count": point.get("agent_count"),
                    })

        return {
            "version": "forecast_ledger_v1",
            "source": "structured_simulation_state",
            "domain": plan.get("domain"),
            "report_template": (plan.get("domain_contract") or {}).get("report_template") or aggregated.get("report_template"),
            "forecast_thesis": state.get("forecast_thesis") or aggregated.get("forecast_thesis") or {},
            "assumption_registry": state.get("assumption_registry") or aggregated.get("assumption_registry") or [],
            "dispute_registry": state.get("dispute_registry") or aggregated.get("dispute_registry") or [],
            "debate_readiness": state.get("debate_readiness") or aggregated.get("debate_readiness") or {},
            "target_variables": plan.get("target_variables") or [],
            "time_pockets": state.get("time_pockets") or [],
            "agent_forecast_rows": rows,
            "scenario_rows": scenario_rows,
            "final_outcome": aggregated.get("final_outcome") or {},
            "agent_disagreement": disagreement,
            "debate_impact": debate_impact,
            "validation": state.get("validation") or {},
            "audit_note": (
                "This ledger is report-safe. Raw agent logs and full transcripts remain audit-only "
                "and should not be used as the primary report source."
            ),
        }
