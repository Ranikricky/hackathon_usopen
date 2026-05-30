"""Structured-state report adapter.

This adapter never invents forecasts. It formats only the existing
simulation_state.json contents into a report that the frontend can display.
"""

from collections import Counter
import json
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ..forecast_ledger import ForecastLedgerBuilder
from .report_template_registry import select_template_id, template_sections


class StructuredReportAdapter:
    """Generate a markdown report from validated structured simulation state."""

    def render(self, state: Dict[str, Any]) -> Dict[str, Any]:
        plan = state.get("domain_plan") or {}
        ledger = ((state.get("aggregated_outputs") or {}).get("forecast_ledger") or ForecastLedgerBuilder().build(state))
        template_id = select_template_id(
            domain=plan.get("domain") or "",
            requested=(plan.get("domain_contract") or {}).get("report_template") or ledger.get("report_template") or "",
        )
        state = {
            **state,
            "forecast_ledger": ledger,
            "selected_report_template": template_id,
            "selected_report_template_sections": template_sections(template_id),
        }
        title = self._title(plan)
        summary = self._summary(plan, state)
        sections = [
            {
                "title": "Template Lens",
                "content": self._template_lens(state),
            },
            {
                "title": "The Human Read",
                "content": self._reader_lead(plan, state),
            },
            {
                "title": "Executive Briefing",
                "content": self._executive_readout(plan, state),
            },
            {
                "title": "The Story Behind the Numbers",
                "content": self._narrative_briefing(plan, state),
            },
            {
                "title": "What Could Happen Next",
                "content": self._final_forecast(state),
            },
            {
                "title": "Scenario Field Guide",
                "content": self._scenario_comparison(state),
            },
            {
                "title": "What To Watch",
                "content": self._what_to_watch(state),
            },
            {
                "title": "Forces Moving The Outcome",
                "content": self._causal_mechanisms(state),
            },
            {
                "title": "Debate Scenes",
                "content": self._debate_findings(state),
            },
            {
                "title": "Numeric Tables For Audit",
                "content": self._numeric_outputs(state),
            },
            {
                "title": "Method Notes",
                "content": self._simulation_setup(plan, state),
            },
            {
                "title": "Agent Appendix",
                "content": self._agent_architecture(state),
            },
            {
                "title": "Time Pockets and Revisions",
                "content": self._time_pockets(state),
            },
            {
                "title": "Evidence, Validation, and Missing Data",
                "content": self._evidence_validation(state),
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
            "brief_cards": self._brief_cards(plan, state),
            "quote_cards": self._quote_cards(state),
            "visuals": self._visuals(state),
            "story_panels": self._story_panels(plan, state),
            "social_cards": self._social_cards(plan, state),
            "image_panels": self._image_panels(plan, state),
            "forecast_ledger": ledger,
            "report_template": template_id,
        }

    def _template_lens(self, state: Dict[str, Any]) -> str:
        template_id = state.get("selected_report_template") or "generic_forecast_memo"
        sections = state.get("selected_report_template_sections") or []
        ledger = state.get("forecast_ledger") or {}
        lines = [
            f"- Selected template: `{template_id}`.",
            "- Source of truth: `forecast_ledger_v1`, derived from structured simulation state, not raw agent logs.",
            f"- Ledger rows: `{len(ledger.get('agent_forecast_rows') or [])}` agent forecast rows and `{len(ledger.get('scenario_rows') or [])}` scenario rows.",
            "- Template sections requested by this lens: " + (", ".join(f"`{section}`" for section in sections) or "`none`"),
        ]
        return "\n".join(lines)

    def _title(self, plan: Dict[str, Any]) -> str:
        domain = self._humanize(plan.get("domain") or "Future Simulation").title()
        question = str(plan.get("user_question") or "").strip()
        if question:
            return f"{domain}: Structured Forecast Report"
        return "Structured Forecast Report"

    def _summary(self, plan: Dict[str, Any], state: Dict[str, Any]) -> str:
        targets = [
            self._humanize(item.get("name"))
            for item in plan.get("target_variables", [])
            if item.get("name")
        ]
        agent_count = len(state.get("agents") or [])
        pocket_count = len(state.get("time_pockets") or [])
        validation = state.get("validation") or {}
        target_phrase = ", ".join(targets[:8]) if targets else "the requested target variables"
        return (
            f"A reader-first briefing from a validated Horizon XL simulation: {agent_count} actors, "
            f"{pocket_count} time pockets, and scenario paths for {target_phrase}. "
            f"Numeric quality score: {validation.get('numeric_quality_score', 'n/a')}."
        )

    def _reader_lead(self, plan: Dict[str, Any], state: Dict[str, Any]) -> str:
        domain = self._humanize(plan.get("domain") or "future simulation")
        question = self._short_text(plan.get("user_question") or "", 260)
        final_rows = self._final_target_rows(state, limit=3)
        spreads = self._largest_disagreements(
            (state.get("aggregated_outputs") or {}).get("agent_disagreement") or {},
            limit=2,
        )
        graph = state.get("graph_context") or {}
        quote_cards = self._quote_cards(state)

        paragraphs = []
        if question:
            paragraphs.append(
                f"Horizon XL treated the question as a `{domain}` simulation: {question}"
            )
        else:
            paragraphs.append(
                f"Horizon XL treated this as a `{domain}` simulation and converted the prompt into actors, scenarios, and target variables."
            )

        if final_rows:
            lead = final_rows[0]
            paragraphs.append(
                f"The top-line base-case read is **{lead[2]} {lead[3]}** for **{lead[0]}** at **{lead[1]}**. "
                "That number is less interesting than the argument around it: which signals were trusted, which actors pushed back, and where the room still had uncertainty."
            )
        else:
            paragraphs.append(
                "The saved state did not contain a clean single headline forecast, so the report reads the scenario paths and debate record rather than pretending there is one neat answer."
            )

        if spreads:
            contested = " and ".join(f"**{row[0]} / {row[1]}**" for row in spreads)
            paragraphs.append(
                f"The most useful tension in the run sits around {contested}. "
                "That is where the report should slow down: disagreement is the signal, not noise."
            )

        if quote_cards:
            paragraphs.append(
                f"A representative room note: “{quote_cards[0]['quote']}” "
                "Treat these pull-quotes as debate evidence from the saved simulation state, not as external source quotations."
            )

        if graph:
            numeric_count = graph.get("numeric_fact_count", 0) or 0
            evidence_count = graph.get("evidence_claim_count", 0) or 0
            if numeric_count < 3:
                paragraphs.append(
                    f"Evidence caution: the signal map had only **{numeric_count} numeric facts** and **{evidence_count} evidence claims**. "
                    "The report can still be useful as a structured scenario read, but it should not sound overconfident."
                )
            else:
                paragraphs.append(
                    f"Evidence base: the signal map carried **{numeric_count} numeric facts** and **{evidence_count} evidence claims**, enough to support a more data-forward read."
                )

        paragraphs.append(
            "Read this like a field memo: headline first, then scenarios, then the arguments that could break the headline."
        )
        return "\n\n".join(paragraphs)

    def _executive_readout(self, plan: Dict[str, Any], state: Dict[str, Any]) -> str:
        validation = state.get("validation") or {}
        scenario_outputs = state.get("scenario_outputs") or {}
        disagreement = (state.get("aggregated_outputs") or {}).get("agent_disagreement") or {}
        debate_impact = (state.get("aggregated_outputs") or {}).get("debate_impact") or {}
        graph = state.get("graph_context") or {}
        targets = self._target_names(plan, state)

        lines = [
            f"- Scope: `{self._humanize(plan.get('domain') or 'other')}` simulation with `{len(targets)}` target variables, `{len(state.get('agents') or [])}` agents, and `{len(state.get('time_pockets') or [])}` time pockets.",
            f"- Validation: `{validation.get('passed', False)}` with numeric quality score `{validation.get('numeric_quality_score', 'n/a')}`.",
            f"- Scenario coverage: `{len(scenario_outputs)}` scenario paths were available: {', '.join(self._humanize(key) for key in scenario_outputs.keys()) or 'none'}.",
        ]
        if graph:
            lines.append(
                f"- Evidence depth: signal map contains `{graph.get('numeric_fact_count', 0)}` numeric facts, "
                f"`{graph.get('evidence_claim_count', 0)}` evidence claims, and `{graph.get('actor_count', graph.get('node_count', 0))}` actor nodes. "
                "Read high validation as numeric-completeness, not proof that external evidence was rich."
            )

        final_rows = self._final_target_rows(state, limit=8)
        if final_rows:
            lines.append("- Base-case endpoint readout is shown below. These are aggregated simulation outputs, not new adapter-created forecasts.")
            lines.append(self._table([["target", "date", "value", "unit", "agent_count"]] + final_rows))
        else:
            lines.append("- No final target endpoint table was available; see the numeric output section for any scenario paths.")

        biggest_spreads = self._largest_disagreements(disagreement, limit=4)
        if biggest_spreads:
            lines.append("\n**Where agents disagreed most**\n")
            rows = [["target", "scenario", "min", "max", "spread"]]
            rows.extend(biggest_spreads)
            lines.append(self._table(rows))

        impact_rows = self._debate_impact_rows(debate_impact, limit=5)
        if impact_rows:
            lines.append("\n**Where the debate moved forecasts**\n")
            lines.append(self._table([["target", "absolute_revision", "revision_count", "agents"]] + impact_rows))

        return "\n\n".join(lines)

    def _narrative_briefing(self, plan: Dict[str, Any], state: Dict[str, Any]) -> str:
        """Write the story arc of the run from saved state only."""
        final_rows = self._final_target_rows(state, limit=5)
        disagreement = (state.get("aggregated_outputs") or {}).get("agent_disagreement") or {}
        debate_impact = (state.get("aggregated_outputs") or {}).get("debate_impact") or {}
        graph = state.get("graph_context") or {}
        targets = [self._humanize(target) for target in self._target_names(plan, state)[:6]]

        lines = [
            (
                f"The simulation is centered on {', '.join(targets) if targets else 'the requested outcome variables'}. "
                "The report should be read as a structured forecast room: agents bring different incentives and evidence lanes, "
                "the moderator forces contested claims back to target variables, and the quant layer turns only numeric-capable outputs into scenario paths."
            )
        ]

        if final_rows:
            headline_target = final_rows[0]
            lines.append(
                f"The headline base-case endpoint is `{headline_target[2]}` `{headline_target[3]}` for "
                f"`{headline_target[0]}` at `{headline_target[1]}`. This is the room's aggregated base-case readout, "
                "not an independently invented adapter conclusion."
            )

        spreads = self._largest_disagreements(disagreement, limit=3)
        if spreads:
            contested = "; ".join(
                f"{row[0]} / {row[1]} spread {row[4]}"
                for row in spreads
            )
            lines.append(
                "The main story is not just the point forecast; it is where the room refused to agree. "
                f"The widest disagreement showed up in {contested}."
            )

        impact_rows = self._debate_impact_rows(debate_impact, limit=3)
        if impact_rows:
            moved = "; ".join(f"{row[0]} revised by {row[1]}" for row in impact_rows)
            lines.append(
                "The debate had visible numerical consequences rather than being pure theater. "
                f"The largest recorded revisions were: {moved}."
            )

        if graph:
            lines.append(
                f"The signal map contributed `{graph.get('actor_count', graph.get('node_count', 0))}` actor nodes and "
                f"`{graph.get('numeric_fact_count', 0)}` numeric facts. If that numeric-fact count is low, the final report should look confident about structure "
                "but cautious about empirical grounding."
            )

        lines.append(
            "The most useful way to read this report is: first inspect the visual briefing, then the scenario comparison, then the debate findings. "
            "The tables are the audit trail; the visual cards are the operating narrative."
        )
        return "\n\n".join(lines)

    def _final_forecast(self, state: Dict[str, Any]) -> str:
        final = (state.get("aggregated_outputs") or {}).get("final_outcome") or {}
        scenario_outputs = state.get("scenario_outputs") or {}
        if not final and not scenario_outputs:
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
        if final.get("target_forecast"):
            rows = [["target_variable", "date", "value", "unit", "agent_count"]]
            for target, point in final.get("target_forecast", {}).items():
                rows.append([
                    self._humanize(target),
                    str(point.get("date", "")),
                    self._format_value(point.get("value", "")),
                    str(point.get("unit", "")),
                    str(point.get("agent_count", "")),
                ])
            lines.append("\n**Final base-case target forecast**\n\n" + self._table(rows))
        if not lines:
            endpoint_rows = self._scenario_endpoint_rows(scenario_outputs)
            if endpoint_rows:
                lines.append(
                    "This simulation is not expressed as a single winner/plurality outcome. "
                    "The final forecast is therefore represented as target-variable endpoints by scenario.\n\n"
                    + self._table(endpoint_rows)
                )
            else:
                lines.append("No final forecast fields were available in the structured state.")
        return "\n".join(lines)

    def _simulation_setup(self, plan: Dict[str, Any], state: Dict[str, Any]) -> str:
        cutoff = plan.get("cutoff_date")
        horizon = plan.get("forecast_horizon") or {}
        scenarios = plan.get("scenario_structure") or {}
        lines = [
            f"- Domain: `{self._humanize(plan.get('domain', 'other'))}`",
            f"- User question: {plan.get('user_question', '')}",
            f"- Horizon: `{horizon.get('start', 'auto')}` to `{horizon.get('end', 'auto')}` at `{horizon.get('granularity', 'auto')}` granularity.",
            f"- Scenario design: {', '.join(self._humanize(key) for key, enabled in scenarios.items() if enabled) or 'not specified'}.",
            f"- Agents: {len(state.get('agents') or [])}",
            f"- Time pockets: {len(state.get('time_pockets') or [])}",
            f"- Agent forecast outputs: {len(state.get('agent_outputs') or [])}",
        ]
        if cutoff:
            lines.append(f"- Future leakage rule: used only information available up to `{cutoff}`.")
        return "\n".join(lines)

    def _numeric_outputs(self, state: Dict[str, Any]) -> str:
        rows = [["target_variable", "scenario", "date", "value", "unit", "agent_count"]]
        for scenario, targets in (state.get("scenario_outputs") or {}).items():
            for target, points in (targets or {}).items():
                for point in (points or [])[:8]:
                    rows.append([
                        self._humanize(target),
                        self._humanize(scenario),
                        str(point.get("date", "")),
                        self._format_value(point.get("value", "")),
                        str(point.get("unit", "")),
                        str(point.get("agent_count", "")),
                    ])
        if len(rows) <= 1:
            return "No aggregated numeric outputs are available."
        return (
            "The table below shows the first available points for each scenario path. "
            "Full chart/table adapters can expose every point.\n\n" + self._table(rows)
        )

    def _scenario_comparison(self, state: Dict[str, Any]) -> str:
        scenario_outputs = state.get("scenario_outputs") or {}
        if not scenario_outputs:
            return "No scenario outputs are available."
        endpoint_rows = self._scenario_endpoint_rows(scenario_outputs)
        if not endpoint_rows:
            return "Scenario paths exist, but no endpoint values were available."
        text = (
            "Scenario comparison uses the last available point in each target path. "
            "It does not create a new forecast; it simply compares saved scenario endpoints.\n\n"
            + self._table(endpoint_rows)
        )
        scenario_counts = [
            f"- `{self._humanize(scenario)}` contains `{sum(len(points or []) for points in (targets or {}).values())}` saved forecast points."
            for scenario, targets in scenario_outputs.items()
        ]
        return text + "\n\n" + "\n".join(scenario_counts)

    def _what_to_watch(self, state: Dict[str, Any]) -> str:
        outputs = state.get("agent_outputs") or []
        disagreement = (state.get("aggregated_outputs") or {}).get("agent_disagreement") or {}
        debate_impact = (state.get("aggregated_outputs") or {}).get("debate_impact") or {}

        risks = Counter()
        drivers = Counter()
        falsifiers = Counter()
        for output in outputs:
            for item in output.get("risks") or []:
                cleaned = self._clean_signal(item)
                if cleaned:
                    risks[cleaned] += 1
            for item in output.get("drivers") or []:
                cleaned = self._clean_signal(item)
                if cleaned:
                    drivers[cleaned] += 1
            for item in output.get("what_would_change_my_forecast") or []:
                cleaned = self._clean_signal(item)
                if cleaned:
                    falsifiers[cleaned] += 1

        lines = [
            "This is the short watchlist: the variables, claims, or mechanisms most likely to move the forecast if new information arrives."
        ]
        spreads = self._largest_disagreements(disagreement, limit=4)
        if spreads:
            lines.append(
                "**Largest live uncertainties**\n\n"
                + self._table([["question", "scenario", "low", "high", "spread"]] + spreads)
            )
        impact_rows = self._debate_impact_rows(debate_impact, limit=4)
        if impact_rows:
            lines.append(
                "**Forecasts that moved during debate**\n\n"
                + self._table([["target", "revision", "count", "agents"]] + impact_rows)
            )
        if drivers:
            lines.append("**Signals pushing the forecast**\n\n" + self._count_table(drivers, "signal", limit=5))
        if risks:
            lines.append("**Risks that could break the base case**\n\n" + self._count_table(risks, "risk", limit=5))
        if falsifiers:
            lines.append("**What would change the room's mind**\n\n" + self._count_table(falsifiers, "falsifier", limit=5))
        return "\n\n".join(lines)

    def _causal_mechanisms(self, state: Dict[str, Any]) -> str:
        outputs = state.get("agent_outputs") or []
        if not outputs:
            return "No agent forecast metadata is available to derive causal mechanisms."

        driver_counts = Counter()
        risk_counts = Counter()
        blind_spot_counts = Counter()
        agent_target_map: Dict[str, Counter] = {}
        for output in outputs:
            target = self._humanize(output.get("target_variable"))
            agent_name = output.get("agent_name") or output.get("agent_id") or "Unknown agent"
            if target:
                agent_target_map.setdefault(target, Counter())[agent_name] += 1
            for item in output.get("drivers") or []:
                cleaned = self._clean_signal(item)
                if cleaned:
                    driver_counts[cleaned] += 1
            for item in output.get("risks") or []:
                cleaned = self._clean_signal(item)
                if cleaned:
                    risk_counts[cleaned] += 1
            for item in output.get("blind_spots") or []:
                cleaned = self._clean_signal(item)
                if cleaned:
                    blind_spot_counts[cleaned] += 1

        sections = []
        if driver_counts:
            sections.append("**Most repeated forecast drivers**\n\n" + self._count_table(driver_counts, "driver"))
        if risk_counts:
            sections.append("**Most repeated risk channels**\n\n" + self._count_table(risk_counts, "risk"))
        if blind_spot_counts:
            sections.append("**Known blind spots retained in the state**\n\n" + self._count_table(blind_spot_counts, "blind_spot"))

        if agent_target_map:
            rows = [["target", "numeric agents contributing"]]
            for target, agents in list(agent_target_map.items())[:12]:
                rows.append([target, ", ".join(name for name, _ in agents.most_common(4))])
            sections.append("**Which agents informed which targets**\n\n" + self._table(rows))

        return "\n\n".join(sections) if sections else "The structured state did not include usable driver/risk metadata."

    def _debate_findings(self, state: Dict[str, Any]) -> str:
        transcript = state.get("discussion_transcript") or []
        pockets = state.get("time_pockets") or []
        if not transcript and not pockets:
            return "No debate transcript or time-pocket revision record is available."

        turn_counts = Counter(turn.get("turn_type") or "unknown" for turn in transcript)
        lines = [
            "The debate record is summarized from saved transcript turns and time-pocket revisions. "
            "This section is intentionally not a full transcript dump.",
            self._table([
                ["turn_type", "count"],
                *[
                    [self._humanize(turn_type), str(count)]
                    for turn_type, count in turn_counts.most_common()
                ],
            ]) if turn_counts else "No transcript turn counts were available.",
        ]

        revision_rows = [["pocket", "revision"]]
        for pocket in pockets:
            for revision in pocket.get("triggered_revisions") or []:
                revision_rows.append([
                    pocket.get("label") or pocket.get("pocket_id") or "",
                    self._short_text(revision, 180),
                ])
        if len(revision_rows) > 1:
            lines.append("**Recorded forecast revisions**\n\n" + self._table(revision_rows[:12]))

        selected_turns = self._selected_debate_turns(transcript)
        if selected_turns:
            rows = [["pocket", "speaker", "type", "what was said"]]
            rows.extend(selected_turns)
            lines.append("**Representative debate moments**\n\n" + self._table(rows))

        return "\n\n".join(lines)

    def _agent_architecture(self, state: Dict[str, Any]) -> str:
        agents = state.get("agents") or []
        rows = [["agent", "role", "skin/stake", "must_output_numbers"]]
        for agent in agents[:40]:
            rows.append([
                agent.get("name", ""),
                self._humanize(agent.get("role", "")),
                self._short_text(
                    agent.get("skin_in_the_game")
                    or agent.get("institutional_incentives")
                    or agent.get("causal_power")
                    or "",
                    120,
                ),
                str((agent.get("numeric_capabilities") or {}).get("must_output_numbers", True)),
            ])
        disagreement = state.get("aggregated_outputs", {}).get("agent_disagreement") or {}
        text = self._table(rows) if len(rows) > 1 else "No agents are available."
        if disagreement:
            sample = self._largest_disagreements(disagreement, limit=10)
            text += "\n\n**Disagreement sample**\n\n"
            text += self._table([["target", "scenario", "min", "max", "spread"]] + sample)
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

    def _evidence_validation(self, state: Dict[str, Any]) -> str:
        validation = state.get("validation") or {}
        graph = state.get("graph_context") or {}
        lines = [
            "**Validation result**",
            "",
            "```json\n" + json.dumps(validation, ensure_ascii=False, indent=2) + "\n```",
        ]
        if graph:
            graph_rows = [
                ["graph_id", str(graph.get("graph_id", ""))],
                ["mode", str(graph.get("mode", ""))],
                ["actor_count", str(graph.get("actor_count", graph.get("node_count", "")))],
                ["target_count", str(graph.get("target_count", ""))],
                ["numeric_fact_count", str(graph.get("numeric_fact_count", ""))],
                ["evidence_claim_count", str(graph.get("evidence_claim_count", ""))],
                ["blocking", str(graph.get("blocking", ""))],
            ]
            lines.append("**Signal map context**\n\n" + self._key_value_table(graph_rows))
            warnings = graph.get("warnings") or []
            if warnings:
                lines.append("**Graph warnings**\n\n" + "\n".join(f"- {warning}" for warning in warnings))
            if graph.get("intervention_plan"):
                lines.append(
                    "**Graph intervention contract**\n\n"
                    + "\n".join(f"- {item}" for item in graph.get("intervention_plan")[:8])
                )
        return "\n\n".join(lines)

    def _appendix_tables(self, state: Dict[str, Any]) -> str:
        rows = [["agent", "target_variable", "confidence", "forecast_points"]]
        for output in (state.get("agent_outputs") or [])[:80]:
            rows.append([
                output.get("agent_name") or output.get("agent_id", ""),
                self._humanize(output.get("target_variable", "")),
                self._format_value(output.get("confidence", "")),
                str(len(output.get("forecast_path") or [])),
            ])
        return self._table(rows) if len(rows) > 1 else "No agent forecast appendix is available."

    def _markdown(self, title: str, summary: str, sections: List[Dict[str, str]]) -> str:
        parts = [f"# {title}", "", f"> {summary}", ""]
        for section in sections:
            parts.extend([f"## {section['title']}", "", section["content"], ""])
        return "\n".join(parts)

    def _brief_cards(self, plan: Dict[str, Any], state: Dict[str, Any]) -> List[Dict[str, Any]]:
        cards: List[Dict[str, Any]] = []
        validation = state.get("validation") or {}
        graph = state.get("graph_context") or {}
        cards.append({
            "label": "Simulation shape",
            "value": f"{len(state.get('agents') or [])} agents",
            "detail": f"{len(state.get('time_pockets') or [])} time pockets · {len(self._target_names(plan, state))} targets",
            "tone": "neutral",
        })
        cards.append({
            "label": "Numeric quality",
            "value": str(validation.get("numeric_quality_score", "n/a")),
            "detail": "Completeness score, not source richness",
            "tone": "positive" if validation.get("passed") else "warning",
        })
        if graph:
            cards.append({
                "label": "Evidence depth",
                "value": f"{graph.get('numeric_fact_count', 0)} numeric facts",
                "detail": f"{graph.get('evidence_claim_count', 0)} claims · {graph.get('actor_count', graph.get('node_count', 0))} actor nodes",
                "tone": "warning" if (graph.get("numeric_fact_count", 0) or 0) < 3 else "positive",
            })
        for row in self._final_target_rows(state, limit=5):
            cards.append({
                "label": row[0],
                "value": row[2],
                "detail": f"{row[3]} · {row[1]} · {row[4]} agents",
                "tone": "neutral",
            })
        return cards[:8]

    def _quote_cards(self, state: Dict[str, Any]) -> List[Dict[str, str]]:
        transcript = state.get("discussion_transcript") or []
        preferred_types = [
            "moderator_cross_question",
            "research_check",
            "quant_check",
            "evidence_audit",
            "numeric_synthesis",
            "mediated_revision",
            "challenge",
            "rebuttal",
        ]
        cards: List[Dict[str, str]] = []
        seen_speakers = Counter()
        for turn_type in preferred_types:
            for turn in transcript:
                if turn.get("turn_type") != turn_type:
                    continue
                speaker = str(turn.get("speaker_name") or turn.get("speaker_id") or "Agent")
                if seen_speakers[speaker] >= 2:
                    continue
                quote = self._quote_text(turn.get("message") or "")
                if not quote:
                    continue
                cards.append({
                    "speaker": speaker,
                    "role": self._humanize(turn_type),
                    "pocket": str(turn.get("pocket_label") or ""),
                    "quote": quote,
                })
                seen_speakers[speaker] += 1
                break
            if len(cards) >= 8:
                break
        return cards[:8]

    def _visuals(self, state: Dict[str, Any]) -> List[Dict[str, Any]]:
        visuals: List[Dict[str, Any]] = []
        scenario_outputs = state.get("scenario_outputs") or {}
        scenario_order = [key for key in ["base_case", "upside_case", "downside_case", "tail_case"] if key in scenario_outputs]
        scenario_order.extend(key for key in scenario_outputs.keys() if key not in scenario_order)

        target_order = []
        for scenario in scenario_order:
            for target in (scenario_outputs.get(scenario) or {}).keys():
                if target not in target_order:
                    target_order.append(target)

        for target in target_order[:4]:
            bars = []
            unit = ""
            for scenario in scenario_order:
                point = self._last_point((scenario_outputs.get(scenario) or {}).get(target) or [])
                if not point:
                    continue
                numeric = self._numeric_value(point.get("value"))
                if numeric is None:
                    continue
                unit = unit or str(point.get("unit") or "")
                bars.append({
                    "label": self._humanize(scenario),
                    "value": numeric,
                    "display": self._format_value(numeric),
                })
            if bars:
                visuals.append({
                    "type": "scenario_bars",
                    "title": self._humanize(target),
                    "subtitle": unit,
                    "bars": bars,
                })

        disagreement = (state.get("aggregated_outputs") or {}).get("agent_disagreement") or {}
        spread_rows = self._largest_disagreements(disagreement, limit=6)
        if spread_rows:
            visuals.append({
                "type": "disagreement_bars",
                "title": "Where the room disagreed most",
                "subtitle": "Agent spread by target and scenario",
                "bars": [
                    {
                        "label": f"{row[0]} · {row[1]}",
                        "value": self._numeric_value(row[4]) or 0,
                        "display": row[4],
                    }
                    for row in spread_rows
                ],
            })

        transcript = state.get("discussion_transcript") or []
        turn_counts = Counter(turn.get("turn_type") or "unknown" for turn in transcript)
        if turn_counts:
            visuals.append({
                "type": "debate_flow",
                "title": "Debate flow",
                "subtitle": "Saved transcript turn mix",
                "bars": [
                    {
                        "label": self._humanize(name),
                        "value": count,
                        "display": str(count),
                    }
                    for name, count in turn_counts.most_common(7)
                ],
            })
        return visuals[:7]

    def _story_panels(self, plan: Dict[str, Any], state: Dict[str, Any]) -> List[Dict[str, str]]:
        panels: List[Dict[str, str]] = []
        domain = self._humanize(plan.get("domain") or "future simulation")
        final_rows = self._final_target_rows(state, limit=2)
        graph = state.get("graph_context") or {}
        spreads = self._largest_disagreements(
            (state.get("aggregated_outputs") or {}).get("agent_disagreement") or {},
            limit=2,
        )
        quote_cards = self._quote_cards(state)

        opening = (
            f"The room was asked to reason about {domain}. Instead of producing one polished answer immediately, "
            "Horizon XL first turned the prompt into actors, evidence lanes, scenarios, and numeric responsibilities."
        )
        panels.append({
            "kicker": "Opening scene",
            "title": "The question becomes a room",
            "text": opening,
            "tone": "paper",
        })

        if final_rows:
            row = final_rows[0]
            target_note = ""
            if self._is_generic_target(row[0]):
                target_note = " Target parsing is weak here: the saved state bundled multiple requested outputs into one generic target."
            panels.append({
                "kicker": "Headline",
                "title": f"{row[0]}: {row[2]} {row[3]}",
                "text": f"The base-case endpoint lands at {row[2]} {row[3]} for {row[0]} at {row[1]}.{target_note}",
                "tone": "headline",
            })

        if spreads:
            row = spreads[0]
            panels.append({
                "kicker": "Tension",
                "title": f"The room split on {row[0]}",
                "text": f"In the {row[1]} lane, agent estimates ran from {row[2]} to {row[3]}, a spread of {row[4]}. That disagreement is the story to inspect.",
                "tone": "warning",
            })

        if quote_cards:
            panels.append({
                "kicker": "Room note",
                "title": quote_cards[0]["speaker"],
                "text": quote_cards[0]["quote"],
                "tone": "quote",
            })

        panels.append({
            "kicker": "Evidence health",
            "title": f"{graph.get('numeric_fact_count', 0) if graph else 0} numeric facts in the map",
            "text": (
                f"The graph recorded {graph.get('evidence_claim_count', 0) if graph else 0} evidence claims and "
                f"{graph.get('actor_count', graph.get('node_count', 0)) if graph else 0} actor nodes. "
                "If that evidence base is thin, treat the output as a scenario exercise, not a sourced forecast memo."
            ),
            "tone": "caveat",
        })
        return panels[:6]

    def _social_cards(self, plan: Dict[str, Any], state: Dict[str, Any]) -> List[Dict[str, str]]:
        cards: List[Dict[str, str]] = []
        final_rows = self._final_target_rows(state, limit=3)
        spreads = self._largest_disagreements(
            (state.get("aggregated_outputs") or {}).get("agent_disagreement") or {},
            limit=3,
        )
        quote_cards = self._quote_cards(state)
        domain = self._humanize(plan.get("domain") or "simulation")

        if final_rows:
            row = final_rows[0]
            cards.append({
                "label": "Post card",
                "handle": "@HorizonXL",
                "text": f"Base case for {domain}: {row[0]} ends at {row[2]} {row[3]}. The number matters, but the disagreement around it matters more.",
            })
        if spreads:
            row = spreads[0]
            cards.append({
                "label": "Tension post",
                "handle": "@SignalDesk",
                "text": f"Watch {row[0]} in {row[1]}: the simulated room is split by {row[4]}. Wide spread means the base case is fragile.",
            })
        if quote_cards:
            cards.append({
                "label": "Quote post",
                "handle": f"@{self._slug(quote_cards[0]['speaker'])}",
                "text": quote_cards[0]["quote"],
            })
        graph = state.get("graph_context") or {}
        cards.append({
            "label": "Evidence note",
            "handle": "@EvidenceDesk",
            "text": f"Evidence check: {graph.get('numeric_fact_count', 0)} numeric facts, {graph.get('evidence_claim_count', 0)} evidence claims. Strong narrative still needs source depth.",
        })
        return cards[:4]

    def _image_panels(self, plan: Dict[str, Any], state: Dict[str, Any]) -> List[Dict[str, str]]:
        """Return art-direction cards the frontend can render as editorial image blocks."""
        domain = self._humanize(plan.get("domain") or "simulation")
        targets = [self._humanize(target) for target in self._target_names(plan, state)[:3]]
        graph = state.get("graph_context") or {}
        panels = [
            {
                "kicker": "Illustration brief",
                "title": "The forecast room",
                "caption": f"An editorial scene for {domain}: actors around a table, evidence pinned to the wall, and the base case under pressure.",
                "motif": "room",
            },
            {
                "kicker": "Chart-image",
                "title": "Scenario fork",
                "caption": "Base, upside, downside, and tail paths split from the same state rather than from separate essays.",
                "motif": "fork",
            },
            {
                "kicker": "Signal map",
                "title": "Evidence under glass",
                "caption": f"{graph.get('actor_count', graph.get('node_count', 0)) if graph else 0} actors, {graph.get('numeric_fact_count', 0) if graph else 0} numeric facts, and {len(targets)} visible targets.",
                "motif": "map",
            },
        ]
        if targets:
            panels.append({
                "kicker": "Target wall",
                "title": ", ".join(targets),
                "caption": "These are the outcomes the room was supposed to explain, not just talk around.",
                "motif": "targets",
            })
        return panels[:4]

    def _slug(self, value: Any) -> str:
        text = re.sub(r"[^A-Za-z0-9]+", "", str(value or "Agent"))
        return text[:22] or "Agent"

    def _is_generic_target(self, value: Any) -> bool:
        normalized = re.sub(r"[^a-z0-9]+", "", str(value or "").lower())
        generic = {
            "thefollowingnumericoutputs",
            "requestednumericoutputs",
            "targetvariables",
            "primaryoutcome",
            "forecastoutputs",
        }
        return normalized in generic or normalized.endswith("numericoutputs")

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

    def _target_names(self, plan: Dict[str, Any], state: Dict[str, Any]) -> List[str]:
        names = [item.get("name") for item in plan.get("target_variables", []) if item.get("name")]
        if names:
            return names
        scenario_outputs = state.get("scenario_outputs") or {}
        seen = []
        for targets in scenario_outputs.values():
            for target in (targets or {}).keys():
                if target not in seen:
                    seen.append(target)
        return seen

    def _final_target_rows(self, state: Dict[str, Any], limit: int = 12) -> List[List[str]]:
        final = (state.get("aggregated_outputs") or {}).get("final_outcome") or {}
        rows = []
        target_forecast = final.get("target_forecast") or {}
        if target_forecast:
            for target, point in list(target_forecast.items())[:limit]:
                rows.append([
                    self._humanize(target),
                    str(point.get("date", "")),
                    self._format_value(point.get("value", "")),
                    str(point.get("unit", "")),
                    str(point.get("agent_count", "")),
                ])
            return rows

        base_targets = (state.get("scenario_outputs") or {}).get("base_case") or {}
        for target, points in list(base_targets.items())[:limit]:
            point = self._last_point(points)
            if point:
                rows.append([
                    self._humanize(target),
                    str(point.get("date", "")),
                    self._format_value(point.get("value", "")),
                    str(point.get("unit", "")),
                    str(point.get("agent_count", "")),
                ])
        return rows

    def _scenario_endpoint_rows(self, scenario_outputs: Dict[str, Any]) -> List[List[str]]:
        scenario_order = [key for key in ["base_case", "upside_case", "downside_case", "tail_case"] if key in scenario_outputs]
        scenario_order.extend(key for key in scenario_outputs.keys() if key not in scenario_order)
        target_order = []
        units: Dict[str, str] = {}
        values: Dict[Tuple[str, str], str] = {}
        for scenario in scenario_order:
            for target, points in (scenario_outputs.get(scenario) or {}).items():
                if target not in target_order:
                    target_order.append(target)
                point = self._last_point(points)
                if point:
                    values[(target, scenario)] = self._format_value(point.get("value", ""))
                    if point.get("unit"):
                        units[target] = str(point.get("unit"))

        if not target_order or not scenario_order:
            return []

        rows = [["target", "unit"] + [self._humanize(scenario) for scenario in scenario_order]]
        for target in target_order[:16]:
            rows.append([
                self._humanize(target),
                units.get(target, ""),
                *[values.get((target, scenario), "") for scenario in scenario_order],
            ])
        return rows

    def _largest_disagreements(self, disagreement: Dict[str, Any], limit: int = 5) -> List[List[str]]:
        parsed = []
        for key, value in disagreement.items():
            if not isinstance(value, dict):
                continue
            target, scenario = self._split_disagreement_key(key)
            spread = value.get("spread")
            try:
                sort_value = float(spread)
            except (TypeError, ValueError):
                sort_value = 0.0
            parsed.append((sort_value, [
                self._humanize(target),
                self._humanize(scenario),
                self._format_value(value.get("min", "")),
                self._format_value(value.get("max", "")),
                self._format_value(spread),
            ]))
        parsed.sort(key=lambda item: item[0], reverse=True)
        return [row for _, row in parsed[:limit]]

    def _debate_impact_rows(self, debate_impact: Dict[str, Any], limit: int = 5) -> List[List[str]]:
        by_target = debate_impact.get("by_target") if isinstance(debate_impact, dict) else {}
        if not isinstance(by_target, dict):
            return []
        rows = []
        sortable = []
        for target, item in by_target.items():
            if not isinstance(item, dict):
                continue
            try:
                delta = float(item.get("absolute_delta") or 0)
            except (TypeError, ValueError):
                delta = 0.0
            sortable.append((delta, target, item))
        sortable.sort(key=lambda item: item[0], reverse=True)
        for _, target, item in sortable[:limit]:
            rows.append([
                self._humanize(target),
                self._format_value(item.get("absolute_delta", "")),
                str(item.get("count", "")),
                ", ".join(item.get("agents") or [])[:140],
            ])
        return rows

    def _selected_debate_turns(self, transcript: List[Dict[str, Any]]) -> List[List[str]]:
        desired = [
            "challenge",
            "rebuttal",
            "moderator_evaluation",
            "evidence_audit",
            "numeric_synthesis",
            "mediated_revision",
            "research_check",
            "quant_check",
        ]
        rows = []
        seen_types = Counter()
        for turn in transcript:
            turn_type = turn.get("turn_type") or ""
            if turn_type not in desired or seen_types[turn_type] >= 2:
                continue
            message = self._short_text(turn.get("message") or "", 220)
            if not message:
                continue
            rows.append([
                str(turn.get("pocket_label") or turn.get("pocket_id") or ""),
                str(turn.get("speaker_name") or turn.get("speaker_id") or ""),
                self._humanize(turn_type),
                message,
            ])
            seen_types[turn_type] += 1
            if len(rows) >= 10:
                break
        return rows

    def _count_table(self, counter: Counter, label: str, limit: int = 8) -> str:
        rows = [[label, "mentions"]]
        rows.extend([[item, str(count)] for item, count in counter.most_common(limit)])
        return self._table(rows)

    def _key_value_table(self, rows: List[List[str]]) -> str:
        return self._table([["field", "value"]] + rows)

    def _last_point(self, points: Optional[Iterable[Dict[str, Any]]]) -> Optional[Dict[str, Any]]:
        if not points:
            return None
        point_list = list(points)
        return point_list[-1] if point_list else None

    def _split_disagreement_key(self, key: str) -> Tuple[str, str]:
        text = str(key)
        if ":" in text:
            target, scenario = text.rsplit(":", 1)
            return target, scenario
        return text, ""

    def _clean_signal(self, value: Any) -> str:
        text = self._short_text(value, 180)
        if not text:
            return ""
        text = re.sub(r"\s+", " ", text).strip()
        low_value_patterns = [
            r"^evidence_strength$",
            r"^actor_alignment$",
            r"^uncertainty_pressure$",
            r"^context-derived knowledge associated with",
            r"^represents or moves part of the simulation outcome through",
            r"^does not represent a causal stakeholder unless explicitly assigned",
        ]
        lowered = text.lower()
        if any(re.search(pattern, lowered) for pattern in low_value_patterns):
            return ""
        return text

    def _humanize(self, value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
        text = text.replace("_", " ").replace("-", " ")
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    def _short_text(self, value: Any, limit: int = 160) -> str:
        text = str(value or "").replace("\n", " ").strip()
        text = re.sub(r"\s+", " ", text)
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 3)].rstrip() + "..."

    def _quote_text(self, value: Any, limit: int = 240) -> str:
        text = str(value or "").replace("\n", " ").strip()
        text = re.sub(r"\s+", " ", text)
        text = re.sub(r"`([^`]+)`", r"\1", text)
        if not text:
            return ""
        # Prefer a natural sentence over a full templated turn.
        sentences = re.split(r"(?<=[.!?])\s+", text)
        skip_phrases = [
            "brings role-specific",
            "the part i know best",
            "the part i am attacking",
            "i may be wrong where",
            "the map gives",
            "my stake is",
            "i’m coming in",
            "i'm coming in",
            "fact basis:",
            "opportunistic",
            "risk-warning",
            "target =",
        ]
        useful = [
            sentence for sentence in sentences
            if (
                len(sentence) > 28
                and not sentence.lower().startswith(("graph conscience", "source packet"))
                and not any(phrase in sentence.lower() for phrase in skip_phrases)
            )
        ]
        quote = " ".join(useful[:2]) if useful else text
        return self._short_text(quote, limit)

    def _format_value(self, value: Any) -> str:
        if isinstance(value, bool):
            return str(value)
        if isinstance(value, (int, float)):
            return f"{value:,.2f}".rstrip("0").rstrip(".")
        try:
            if isinstance(value, str) and value.strip():
                number = float(value)
                return f"{number:,.2f}".rstrip("0").rstrip(".")
        except ValueError:
            pass
        return str(value)

    def _numeric_value(self, value: Any) -> Optional[float]:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        try:
            return float(str(value).replace(",", "").strip())
        except (TypeError, ValueError):
            return None
