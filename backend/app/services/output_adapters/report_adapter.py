"""Structured-state report adapter.

This adapter never invents forecasts. It formats only the existing
simulation_state.json contents into a report that the frontend can display.
"""

from typing import Any, Dict, List


class StructuredReportAdapter:
    """Generate a markdown report from validated structured simulation state."""

    def render(self, state: Dict[str, Any]) -> Dict[str, Any]:
        plan = state.get("domain_plan") or {}
        title = self._title(plan)
        summary = self._summary(plan, state)
        sections = [
            {
                "title": "Final Forecast",
                "content": self._final_forecast(state),
            },
            {
                "title": "Simulation Setup",
                "content": self._simulation_setup(plan, state),
            },
            {
                "title": "Numeric Forecast Outputs",
                "content": self._numeric_outputs(state),
            },
            {
                "title": "Scenario Comparison",
                "content": self._scenario_comparison(state),
            },
            {
                "title": "Agent Architecture and Disagreement",
                "content": self._agent_architecture(state),
            },
            {
                "title": "Time Pockets and Revisions",
                "content": self._time_pockets(state),
            },
            {
                "title": "Validation and Missing Data",
                "content": self._validation(state),
            },
            {
                "title": "Appendix Tables",
                "content": self._appendix_tables(state),
            },
        ]
        markdown = self._markdown(title, summary, sections)
        return {
            "title": title,
            "summary": summary,
            "sections": sections,
            "markdown": markdown,
        }

    def _title(self, plan: Dict[str, Any]) -> str:
        domain = str(plan.get("domain") or "Future Simulation").title()
        question = str(plan.get("user_question") or "").strip()
        if question:
            return f"{domain}: Structured Forecast Report"
        return "Structured Forecast Report"

    def _summary(self, plan: Dict[str, Any], state: Dict[str, Any]) -> str:
        targets = [item.get("name") for item in plan.get("target_variables", []) if item.get("name")]
        agent_count = len(state.get("agents") or [])
        pocket_count = len(state.get("time_pockets") or [])
        validation = state.get("validation") or {}
        return (
            f"Generated from one validated structured simulation state with {agent_count} agents, "
            f"{pocket_count} time pockets, and target variables: {', '.join(targets[:12])}. "
            f"Numeric quality score: {validation.get('numeric_quality_score', 'n/a')}."
        )

    def _final_forecast(self, state: Dict[str, Any]) -> str:
        final = (state.get("aggregated_outputs") or {}).get("final_outcome") or {}
        if not final:
            return "No final outcome summary is available in the structured state."
        lines = []
        if final.get("projected_winner"):
            lines.append(
                f"- Projected winner/plurality: `{final.get('projected_winner')}` "
                f"with `{final.get('projected_winner_seats')}` seats."
            )
            lines.append(f"- Majority mark: `{final.get('majority_mark')}`; status: `{final.get('majority_status')}`.")
        if final.get("vote_share_forecast"):
            rows = [["actor", "vote_share"]]
            for actor, value in final.get("vote_share_forecast", {}).items():
                rows.append([actor, str(value)])
            lines.append("\n**Final vote-share forecast**\n\n" + self._table(rows))
        if final.get("seat_forecast"):
            rows = [["actor", "seats"]]
            for actor, value in final.get("seat_forecast", {}).items():
                rows.append([actor, str(value)])
            lines.append("\n**Final seat forecast**\n\n" + self._table(rows))
        return "\n".join(lines) if lines else "Final outcome exists, but no winner/seat/vote fields were available."

    def _simulation_setup(self, plan: Dict[str, Any], state: Dict[str, Any]) -> str:
        cutoff = plan.get("cutoff_date")
        lines = [
            f"- Domain: `{plan.get('domain', 'other')}`",
            f"- User question: {plan.get('user_question', '')}",
            f"- Agents: {len(state.get('agents') or [])}",
            f"- Time pockets: {len(state.get('time_pockets') or [])}",
            f"- Agent forecast outputs: {len(state.get('agent_outputs') or [])}",
        ]
        if cutoff:
            lines.append(f"- Future leakage rule: used only information available up to `{cutoff}`.")
        return "\n".join(lines)

    def _numeric_outputs(self, state: Dict[str, Any]) -> str:
        rows = [["target_variable", "scenario", "date", "value", "agent_count"]]
        for scenario, targets in (state.get("scenario_outputs") or {}).items():
            for target, points in (targets or {}).items():
                for point in points[:12]:
                    rows.append([
                        target,
                        scenario,
                        str(point.get("date", "")),
                        str(point.get("value", "")),
                        str(point.get("agent_count", "")),
                    ])
        return self._table(rows) if len(rows) > 1 else "No aggregated numeric outputs are available."

    def _scenario_comparison(self, state: Dict[str, Any]) -> str:
        scenario_outputs = state.get("scenario_outputs") or {}
        if not scenario_outputs:
            return "No scenario outputs are available."
        lines = []
        for scenario, targets in scenario_outputs.items():
            target_names = ", ".join((targets or {}).keys())
            lines.append(f"- `{scenario}` includes numeric paths for: {target_names}.")
        return "\n".join(lines)

    def _agent_architecture(self, state: Dict[str, Any]) -> str:
        agents = state.get("agents") or []
        rows = [["agent_id", "name", "role", "must_output_numbers"]]
        for agent in agents[:40]:
            rows.append([
                agent.get("agent_id", ""),
                agent.get("name", ""),
                agent.get("role", ""),
                str((agent.get("numeric_capabilities") or {}).get("must_output_numbers", True)),
            ])
        disagreement = state.get("aggregated_outputs", {}).get("agent_disagreement") or {}
        text = self._table(rows) if len(rows) > 1 else "No agents are available."
        if disagreement:
            sample = list(disagreement.items())[:10]
            text += "\n\n**Disagreement sample**\n\n"
            text += "\n".join(
                f"- `{key}` spread: {value.get('spread')} ({value.get('min')} to {value.get('max')})"
                for key, value in sample
            )
        return text

    def _time_pockets(self, state: Dict[str, Any]) -> str:
        pockets = state.get("time_pockets") or []
        rows = [["pocket_id", "label", "start", "end", "actions", "revisions"]]
        for pocket in pockets:
            rows.append([
                pocket.get("pocket_id", ""),
                pocket.get("label", ""),
                str(pocket.get("start", "")),
                str(pocket.get("end", "")),
                str(len(pocket.get("agent_actions") or [])),
                str(len(pocket.get("triggered_revisions") or [])),
            ])
        return self._table(rows) if len(rows) > 1 else "No time pockets are available."

    def _validation(self, state: Dict[str, Any]) -> str:
        validation = state.get("validation") or {}
        return "```json\n" + __import__("json").dumps(validation, ensure_ascii=False, indent=2) + "\n```"

    def _appendix_tables(self, state: Dict[str, Any]) -> str:
        rows = [["agent", "target_variable", "confidence", "forecast_points"]]
        for output in (state.get("agent_outputs") or [])[:80]:
            rows.append([
                output.get("agent_name") or output.get("agent_id", ""),
                output.get("target_variable", ""),
                str(output.get("confidence", "")),
                str(len(output.get("forecast_path") or [])),
            ])
        return self._table(rows) if len(rows) > 1 else "No agent forecast appendix is available."

    def _markdown(self, title: str, summary: str, sections: List[Dict[str, str]]) -> str:
        parts = [f"# {title}", "", f"> {summary}", ""]
        for section in sections:
            parts.extend([f"## {section['title']}", "", section["content"], ""])
        return "\n".join(parts)

    def _table(self, rows: List[List[str]]) -> str:
        if not rows:
            return ""
        header = "| " + " | ".join(rows[0]) + " |"
        divider = "| " + " | ".join("---" for _ in rows[0]) + " |"
        body = [
            "| " + " | ".join(self._cell(cell) for cell in row) + " |"
            for row in rows[1:]
        ]
        return "\n".join([header, divider] + body)

    def _cell(self, value: Any) -> str:
        return str(value).replace("|", "\\|").replace("\n", " ").strip()
