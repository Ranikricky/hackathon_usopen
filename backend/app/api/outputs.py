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


@outputs_bp.route("/ask", methods=["POST"])
def ask_state():
    """Answer from saved structured simulation state without inventing new claims."""
    data = request.get_json() or {}
    simulation_id = data.get("simulation_id")
    question = str(data.get("question") or "").strip()
    if not simulation_id:
        return jsonify({"success": False, "error": "simulation_id is required."}), 400
    if not question:
        return jsonify({"success": False, "error": "question is required."}), 400

    state = SimulationStateManager.load(simulation_id)
    if not state:
        return jsonify({
            "success": False,
            "error": "Structured simulation state not found.",
        }), 404

    state_dict = state.to_dict()
    validation = state_dict.get("validation") or NumericValidationService().validate(state_dict)
    state_dict["validation"] = validation
    answer = _answer_from_state(question, state_dict)
    return jsonify({
        "success": True,
        "data": {
            "simulation_id": simulation_id,
            "question": question,
            **answer,
        },
    })


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


def _answer_from_state(question: str, state: Dict[str, Any]) -> Dict[str, Any]:
    """Small deterministic state reader for the frontend Ask State panel.

    This is deliberately not an LLM chat. It only restates objects that already
    exist in simulation_state.json so the UI cannot create new forecasts.
    """
    q = question.lower()
    plan = state.get("domain_plan") or {}
    aggregated = state.get("aggregated_outputs") or {}
    final = aggregated.get("final_outcome") or {}
    ledger = state.get("forecast_ledger") or aggregated.get("forecast_ledger") or {}
    thesis = state.get("forecast_thesis") or aggregated.get("forecast_thesis") or ledger.get("forecast_thesis") or {}
    disputes = state.get("dispute_registry") or aggregated.get("dispute_registry") or ledger.get("dispute_registry") or []
    assumptions = state.get("assumption_registry") or aggregated.get("assumption_registry") or ledger.get("assumption_registry") or []
    validation = state.get("validation") or {}
    transcript = state.get("discussion_transcript") or []
    targets = ledger.get("targets") or ledger.get("agent_forecast_rows") or []

    sources: List[str] = []
    lines: List[str] = []
    facts: List[Dict[str, Any]] = []

    def add_source(name: str):
        if name not in sources:
            sources.append(name)

    if any(term in q for term in ["winner", "won", "outcome", "result", "bottom line", "final"]):
        add_source("aggregated_outputs.final_outcome")
        add_source("forecast_ledger")
        lines.append("Bottom line from the saved simulation state:")
        projected = final.get("projected_winner") or final.get("winner") or final.get("leading_scenario")
        if projected:
            lines.append(f"- Projected outcome: {projected}")
        elif final:
            lines.append("- A final outcome object exists, but no explicit winner/plurality field was saved.")
        else:
            lines.append("- No final outcome has been saved yet.")
        facts.extend(_forecast_fact_rows(final, targets)[:10])
    elif any(term in q for term in ["number", "forecast", "table", "probability", "price", "seat", "vote", "share"]):
        add_source("forecast_ledger")
        add_source("scenario_outputs")
        facts.extend(_forecast_fact_rows(final, targets)[:14])
        if facts:
            lines.append("Here are the saved forecast rows I can inspect:")
        else:
            lines.append("No clean forecast rows are available in the saved ledger yet.")
    elif any(term in q for term in ["why", "evidence", "assumption", "driver", "risk"]):
        add_source("forecast_thesis")
        add_source("assumption_registry")
        add_source("dispute_registry")
        if thesis.get("statement"):
            lines.append(f"Current thesis: {thesis.get('statement')}")
        if thesis.get("core_drivers"):
            lines.append("Core drivers: " + "; ".join(thesis.get("core_drivers", [])[:5]))
        if assumptions:
            lines.append("Important assumptions:")
            for item in assumptions[:5]:
                lines.append(f"- {item.get('statement', 'Unnamed assumption')} [{item.get('status', 'active')}]")
        if disputes:
            lines.append("Main disputes:")
            for item in disputes[:5]:
                lines.append(f"- {item.get('question', 'Unnamed dispute')}")
    elif any(term in q for term in ["agent", "debate", "transcript", "said", "argue", "disagreement"]):
        add_source("discussion_transcript")
        add_source("dispute_registry")
        lines.append(f"The saved debate contains {len(transcript)} turns.")
        for dispute in disputes[:4]:
            lines.append(f"- Dispute: {dispute.get('question', 'Unnamed dispute')}")
        for turn in transcript[:6]:
            speaker = turn.get("speaker_name") or turn.get("speaker_id") or "Agent"
            message = str(turn.get("message") or "").strip()
            if message:
                lines.append(f"- {speaker}: {message[:240]}")
    else:
        add_source("domain_plan")
        add_source("forecast_thesis")
        add_source("forecast_ledger")
        lines.append(f"Domain: {plan.get('domain', 'unknown')}")
        lines.append(f"Question: {plan.get('user_question', '')}")
        if thesis.get("statement"):
            lines.append(f"Thesis: {thesis.get('statement')}")
        lines.append(f"Validation: {'passed' if validation.get('passed') else 'blocked or not checked'}")
        lines.append("Ask about the bottom line, numbers, assumptions, evidence, agents, or debate to inspect a narrower slice.")

    if validation and not validation.get("passed"):
        add_source("validation")
        missing = validation.get("errors") or validation.get("warnings") or []
        if missing:
            lines.append("")
            lines.append("Validation warning:")
            for item in missing[:4]:
                lines.append(f"- {item}")

    return {
        "answer": "\n".join(line for line in lines if line is not None).strip(),
        "facts": facts,
        "sources": sources,
        "validation": validation,
    }


def _forecast_fact_rows(final: Dict[str, Any], targets: Any) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for name, value in (final.get("vote_share_forecast") or {}).items():
        rows.append({"label": f"{name} vote share", "value": value, "unit": "%"})
    for name, value in (final.get("seat_forecast") or {}).items():
        rows.append({"label": f"{name} seats", "value": value, "unit": "seats"})
    for name, point in (final.get("target_forecast") or {}).items():
        rows.append({
            "label": name,
            "value": point.get("value") if isinstance(point, dict) else point,
            "unit": point.get("unit", "") if isinstance(point, dict) else "",
            "date": point.get("date", "") if isinstance(point, dict) else "",
        })
    for target in targets or []:
        rows.append({
            "label": target.get("target_name") or target.get("target_id") or target.get("target_variable"),
            "value": target.get("post_debate_forecast") or target.get("value"),
            "unit": target.get("unit", ""),
            "confidence": target.get("confidence"),
            "status": target.get("validation_status"),
        })
    return [row for row in rows if row.get("label")]


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
