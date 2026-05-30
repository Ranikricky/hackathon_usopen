"""Read-only output adapters for structured simulation state."""

from __future__ import annotations

from typing import Any, Dict, List

from flask import jsonify, request

from . import outputs_bp
from ..models.simulation_state import SimulationStateManager
from ..services.numeric_validation import NumericValidationService
from ..services.output_adapters.report_adapter import StructuredReportAdapter
from ..utils.logger import get_logger

logger = get_logger("horizonxl.api.outputs")


SUPPORTED_OUTPUTS = {
    "report",
    "whitepaper",
    "news_article",
    "numeric_table",
    "charts",
    "visualization",
    "executive_memo",
}


@outputs_bp.route("/generate", methods=["POST"])
def generate_output():
    """Generate a read-only output from structured simulation state."""
    data = request.get_json() or {}
    simulation_id = data.get("simulation_id")
    output_type = str(data.get("output_type") or "report").strip().lower()
    if not simulation_id:
        return jsonify({"success": False, "error": "simulation_id is required."}), 400
    return _render_output_response(simulation_id, output_type)


@outputs_bp.route("/<simulation_id>/<output_type>", methods=["GET"])
def get_output(simulation_id: str, output_type: str):
    """Fetch an output generated on demand from structured simulation state."""
    return _render_output_response(simulation_id, output_type)


def _render_output_response(simulation_id: str, output_type: str):
    output_type = str(output_type or "report").strip().lower()
    if output_type not in SUPPORTED_OUTPUTS:
        return jsonify({
            "success": False,
            "error": f"Unsupported output_type: {output_type}",
            "supported_outputs": sorted(SUPPORTED_OUTPUTS),
        }), 400

    state = SimulationStateManager.load(simulation_id)
    if not state:
        return jsonify({
            "success": False,
            "error": "Structured simulation state not found.",
        }), 404

    state_dict = state.to_dict()
    validation = NumericValidationService().validate(state_dict)
    SimulationStateManager.update_validation(simulation_id, validation)
    state_dict["validation"] = validation

    if not validation.get("passed"):
        return jsonify({
            "success": False,
            "error": "Simulation evidence insufficient",
            "diagnostic": NumericValidationService().diagnostic_message(validation),
        }), 409

    output = _render_output(output_type, state_dict)
    return jsonify({
        "success": True,
        "data": {
            "simulation_id": simulation_id,
            "output_type": output_type,
            "output": output,
        }
    })


def _render_output(output_type: str, state: Dict[str, Any]) -> Any:
    if output_type == "report":
        return StructuredReportAdapter().render(state)
    if output_type == "numeric_table":
        return _numeric_table(state)
    if output_type in {"charts", "visualization"}:
        return _chart_ready_json(state)
    if output_type == "executive_memo":
        return _executive_memo(state)
    if output_type == "whitepaper":
        return _whitepaper(state)
    if output_type == "news_article":
        return _news_article(state)
    return {}


def _numeric_table(state: Dict[str, Any]) -> Dict[str, Any]:
    scenario_rows: List[Dict[str, Any]] = []
    for scenario, targets in (state.get("scenario_outputs") or {}).items():
        for target, points in (targets or {}).items():
            for point in points or []:
                scenario_rows.append({
                    "target_variable": target,
                    "scenario": scenario,
                    "date": point.get("date"),
                    "value": point.get("value"),
                    "agent_count": point.get("agent_count"),
                })

    agent_rows: List[Dict[str, Any]] = []
    for output in state.get("agent_outputs") or []:
        for point in output.get("forecast_path") or []:
            agent_rows.append({
                "agent_id": output.get("agent_id"),
                "agent_name": output.get("agent_name"),
                "target_variable": output.get("target_variable"),
                "date": point.get("date"),
                "scenario": point.get("scenario"),
                "value": point.get("value"),
                "unit": point.get("unit"),
                "confidence": output.get("confidence"),
            })

    return {
        "scenario_rows": scenario_rows,
        "agent_forecast_rows": agent_rows,
        "row_count": len(scenario_rows) + len(agent_rows),
    }


def _chart_ready_json(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    charts: List[Dict[str, Any]] = []
    scenario_outputs = state.get("scenario_outputs") or {}
    for scenario, targets in scenario_outputs.items():
        for target, points in (targets or {}).items():
            charts.append({
                "chart_type": "line_chart",
                "title": f"{target} - {scenario}",
                "x_axis": "date",
                "y_axis": target,
                "series": [{
                    "name": scenario,
                    "data": [
                        {"x": point.get("date"), "y": point.get("value")}
                        for point in points or []
                    ],
                }],
            })
    return charts


def _executive_memo(state: Dict[str, Any]) -> Dict[str, str]:
    plan = state.get("domain_plan") or {}
    final = (state.get("aggregated_outputs") or {}).get("final_outcome") or {}
    lines = [
        f"# Executive Memo: {plan.get('domain', 'Structured Simulation')}",
        "",
        f"Question: {plan.get('user_question', '')}",
        "",
        "## Decision View",
        "",
        "This memo is derived only from the validated structured simulation state.",
        "",
        "```json",
        __import__("json").dumps(final, ensure_ascii=False, indent=2),
        "```",
    ]
    return {"markdown": "\n".join(lines), "final_outcome": final}


def _whitepaper(state: Dict[str, Any]) -> Dict[str, Any]:
    report = StructuredReportAdapter().render(state)
    report["title"] = report["title"].replace("Report", "Whitepaper")
    report["summary"] = "Whitepaper-style synthesis derived from validated structured simulation state. " + report["summary"]
    return report


def _news_article(state: Dict[str, Any]) -> Dict[str, str]:
    plan = state.get("domain_plan") or {}
    final = (state.get("aggregated_outputs") or {}).get("final_outcome") or {}
    headline = f"Simulation Points to {plan.get('domain', 'Future Scenario')} Outcome"
    lede = (
        "Horizon XL generated this article from validated simulation state only; "
        "no new forecasts or claims were invented by the adapter."
    )
    body = "\n\n".join([
        f"# {headline}",
        lede,
        "Key numbers:",
        "```json\n" + __import__("json").dumps(final, ensure_ascii=False, indent=2) + "\n```",
    ])
    return {"headline": headline, "lede": lede, "markdown": body}
