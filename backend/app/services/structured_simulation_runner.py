"""
Structured simulation runner.

This runner is the backend bridge between planning/agents and report generation:
it produces simulation_state.json with agent-specific numeric forecast paths,
scenario outputs, aggregated outputs, and validation. It is intentionally
domain-general; domain meaning comes from the prompt-derived plan and agents.
"""

import hashlib
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Tuple

from ..models.simulation_state import SimulationStateManager, StructuredSimulationState
from .numeric_validation import NumericValidationService
from .forecast_ledger import ForecastLedgerBuilder
from .forecast_artifacts import ForecastArtifactBuilder
from ..utils.logger import get_logger


logger = get_logger("horizonxl.services.structured_simulation_runner")


FALLBACK_SCENARIO_ORDER = ["base", "upside", "downside", "tail"]


def _clean_name(value: Any) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_]+", "_", str(value or "").lower())
    return re.sub(r"_+", "_", cleaned).strip("_")


class StructuredSimulationRunner:
    """Create a validated structured simulation state from a plan and agents."""

    def run(
        self,
        simulation_id: str,
        project_id: str,
        domain_plan: Dict[str, Any],
        agents: List[Dict[str, Any]],
        evidence_text: str = "",
        graph_context: Dict[str, Any] | None = None,
    ) -> StructuredSimulationState:
        state = SimulationStateManager.initialize(
            simulation_id=simulation_id,
            project_id=project_id,
            domain_plan=domain_plan,
            agents=agents,
        )
        graph_brain = self._build_graph_brain(graph_context or {}, domain_plan, evidence_text)
        state.graph_context = graph_brain
        artifacts = ForecastArtifactBuilder().build_all(
            domain_plan=domain_plan,
            agents=agents,
            evidence_text=evidence_text,
            graph_brain=graph_brain,
        )
        state.forecast_thesis = artifacts["forecast_thesis"]
        state.assumption_registry = artifacts["assumption_registry"]
        state.dispute_registry = artifacts["dispute_registry"]
        state.debate_readiness = artifacts["debate_readiness"]

        state.agent_outputs = []
        state.scenario_outputs = {scenario: {} for scenario in self._required_scenarios(domain_plan)}
        state.aggregated_outputs = {
            "method": "structured_time_pocket_synthesis",
            "provenance": "Generated from prompt-derived agents, target variables, time pockets, and scenario rules.",
            "warning": (
                "These numeric paths are structured simulation outputs. They should be "
                "treated as model judgments unless replaced by a live agent engine or "
                "external data-backed forecasts."
            ),
            "by_target": {},
            "agent_disagreement": {},
            "graph_brain": graph_brain,
            "forecast_thesis": state.forecast_thesis,
            "assumption_registry": state.assumption_registry,
            "dispute_registry": state.dispute_registry,
            "debate_readiness": state.debate_readiness,
        }

        targets = [
            variable for variable in domain_plan.get("target_variables", [])
            if variable.get("required", True)
        ] or [{"name": "primary_outcome", "unit": "index", "required": True}]

        if not state.debate_readiness.get("ready", False):
            state.validation = {
                "passed": False,
                "errors": [
                    "Debate readiness failed: " + issue
                    for issue in state.debate_readiness.get("blocking_issues", [])
                ] or ["Debate readiness failed."],
                "warnings": state.debate_readiness.get("warnings", []),
                "missing_agents": [],
                "missing_variables": [
                    target.get("name")
                    for target in targets
                    if isinstance(target, dict) and target.get("name")
                ],
                "missing_dates": [],
                "missing_scenarios": [],
                "numeric_quality_score": 0.0,
            }
            state.forecast_ledger = ForecastLedgerBuilder().build(state.to_dict())
            state.aggregated_outputs["forecast_ledger"] = state.forecast_ledger
            return SimulationStateManager.save(state)

        forecast_periods = self._forecast_periods(domain_plan, state.time_pockets)
        state.aggregated_outputs["forecast_horizon"] = {
            "granularity": (domain_plan.get("forecast_horizon") or {}).get("granularity") or "event_triggered",
            "start": (domain_plan.get("forecast_horizon") or {}).get("start"),
            "end": (domain_plan.get("forecast_horizon") or {}).get("end"),
            "point_count": len(forecast_periods),
            "dates": [period.get("date") for period in forecast_periods],
        }
        numeric_agents = [
            agent for agent in agents
            if (agent.get("numeric_capabilities") or {}).get("must_output_numbers", True)
        ] or list(agents)

        for pocket_idx, pocket in enumerate(state.time_pockets):
            state_before = self._state_snapshot(targets, pocket_idx, prior=True)
            state_after = self._state_snapshot(targets, pocket_idx, prior=False)
            pocket["state_before"] = state_before
            pocket["state_after"] = state_after
            pocket["graph_brain"] = self._graph_pocket_directive(graph_brain, pocket, targets)
            pocket["agent_actions"] = [
                self._agent_action(agent, pocket, domain_plan)
                for agent in agents[: min(len(agents), 40)]
            ]
            pocket["cross_agent_interactions"] = self._cross_agent_interactions(agents, pocket)
            pocket["triggered_revisions"] = self._triggered_revisions(targets, pocket_idx)

        for agent_idx, agent in enumerate(numeric_agents):
            for target_idx, target in enumerate(targets):
                output = self._agent_forecast_output(
                    agent=agent,
                    agent_idx=agent_idx,
                    target=target,
                    target_idx=target_idx,
                    time_pockets=state.time_pockets,
                    forecast_periods=forecast_periods,
                    domain_plan=domain_plan,
                    evidence_text=evidence_text,
                )
                state.agent_outputs.append(output)

        state.scenario_outputs = self._aggregate_scenarios(state.agent_outputs, targets, evidence_text)
        state.aggregated_outputs["relationship_topology"] = self._relationship_topology(state.agents)
        state.discussion_transcript = self._build_discussion_transcript(
            state=state,
            targets=targets,
            evidence_text=evidence_text,
            graph_brain=graph_brain,
        )
        debate_revisions = self._extract_debate_revisions(state.discussion_transcript)
        state.agent_outputs = self._apply_debate_revisions(state.agent_outputs, debate_revisions, state.time_pockets)
        state.scenario_outputs = self._aggregate_scenarios(state.agent_outputs, targets, evidence_text)
        state.aggregated_outputs["by_target"] = self._aggregate_targets(state.agent_outputs)
        state.aggregated_outputs["agent_disagreement"] = self._agent_disagreement(state.agent_outputs)
        state.aggregated_outputs["final_outcome"] = self._final_outcome_summary(state.scenario_outputs, targets, evidence_text)
        state.aggregated_outputs["debate_revisions"] = debate_revisions
        state.aggregated_outputs["debate_impact"] = self._debate_impact_summary(debate_revisions)
        state.aggregated_outputs["report_template"] = (
            (domain_plan.get("domain_contract") or {}).get("report_template")
            or self._report_template_for_plan(domain_plan)
        )
        state.forecast_ledger = ForecastLedgerBuilder().build(state.to_dict())
        state.aggregated_outputs["forecast_ledger"] = state.forecast_ledger
        # Rebuild once so the visible transcript and quant summaries reflect the debated state.
        state.discussion_transcript = self._build_discussion_transcript(
            state=state,
            targets=targets,
            evidence_text=evidence_text,
            graph_brain=graph_brain,
        )
        turns_by_pocket = {}
        for turn in state.discussion_transcript:
            turns_by_pocket.setdefault(turn.get("pocket_id"), []).append(turn)
        for pocket in state.time_pockets:
            pocket["discussion_turns"] = turns_by_pocket.get(pocket.get("pocket_id"), [])

        validation = NumericValidationService().validate(state.to_dict())
        state.validation = validation
        state.forecast_ledger = ForecastLedgerBuilder().build(state.to_dict())
        state.aggregated_outputs["forecast_ledger"] = state.forecast_ledger
        saved = SimulationStateManager.save(state)
        logger.info(
            "Structured simulation saved: %s agents=%s targets=%s outputs=%s validation=%s",
            simulation_id,
            len(agents),
            len(targets),
            len(state.agent_outputs),
            validation.get("passed"),
        )
        return saved

    def _report_template_for_plan(self, domain_plan: Dict[str, Any]) -> str:
        domain_l = str((domain_plan or {}).get("domain") or "").lower()
        question_l = str((domain_plan or {}).get("user_question") or "").lower()
        text = f"{domain_l} {question_l}"
        if "election" in text or any(term in text for term in ["vote", "seat", "turnout"]):
            return "election_forecast"
        if any(term in text for term in ["oil", "commodity", "price", "shipping", "lng", "energy"]):
            return "commodity_market_note"
        if "ai" in text or "adoption" in text:
            return "ai_adoption_whitepaper"
        if any(term in text for term in ["geopolitic", "military", "conflict", "naval", "war", "escalation"]):
            return "geopolitical_risk_memo"
        if any(term in text for term in ["story", "fiction", "canon", "character", "throne", "battle"]):
            return "narrative_fiction_forecast"
        if any(term in text for term in ["business", "strategy", "market entry", "consumer trend"]):
            return "business_strategy_memo"
        return "generic_forecast_memo"

    def _build_graph_brain(
        self,
        graph_context: Dict[str, Any],
        domain_plan: Dict[str, Any],
        evidence_text: str,
    ) -> Dict[str, Any]:
        """Turn the signal graph into an always-on run supervisor.

        The graph brain is not another simulated stakeholder. It is the run's
        evidence conscience: actor roster, requested variables, evidence cards,
        missing-data warnings, and intervention instructions.
        """
        nodes = graph_context.get("nodes") if isinstance(graph_context, dict) else []
        edges = graph_context.get("edges") if isinstance(graph_context, dict) else []
        nodes = nodes if isinstance(nodes, list) else []
        edges = edges if isinstance(edges, list) else []

        def has_label(node: Dict[str, Any], label: str) -> bool:
            return label in (node.get("labels") or [])

        actor_nodes = [node for node in nodes if has_label(node, "Entity")]
        target_nodes = [node for node in nodes if has_label(node, "TargetVariable")]
        evidence_sources = [node for node in nodes if has_label(node, "EvidenceSource")]
        evidence_claims = [node for node in nodes if has_label(node, "EvidenceClaim")]
        numeric_facts = [node for node in nodes if has_label(node, "NumericFact")]
        time_pockets = [node for node in nodes if has_label(node, "TimePocket")]

        actor_names = [str(node.get("name") or "") for node in actor_nodes if node.get("name")]
        target_names = [
            str((node.get("attributes") or {}).get("target_variable") or node.get("name") or "")
            for node in target_nodes
        ]
        if not target_names:
            target_names = [str(target.get("name") or "") for target in domain_plan.get("target_variables", [])]

        evidence_cards = self._graph_evidence_cards(evidence_claims, numeric_facts, evidence_text)
        prompt_evidence_notes = [
            note for note in self._extract_evidence_notes(evidence_text)
            if not self._is_bad_evidence_card(note)
            and not note.startswith("The prompt provides the active evidence set")
        ]
        missing_target_evidence = self._graph_missing_target_evidence(target_names, evidence_cards)
        invalid_actor_nodes = [
            name for name in actor_names
            if re.search(r"\b(?:usd|kwh|mwh|gwh|twh|pocket|baseline|snapshot|synthesis|target|variable|forecast table|scenario path)\b", name, re.IGNORECASE)
        ]
        warnings = []
        if not evidence_sources and not evidence_claims and not numeric_facts:
            warnings.append("Graph has no explicit evidence-source/claim/fact lane; debate must rely on prompt evidence only.")
        if missing_target_evidence:
            warnings.append(f"Targets with thin numeric support: {', '.join(missing_target_evidence[:8])}.")
        if invalid_actor_nodes:
            warnings.append(f"Non-actor artifacts appeared in actor lane: {', '.join(invalid_actor_nodes[:8])}.")

        interventions = [
            "Moderator must stop drift whenever agent claims do not map to a target variable or evidence card.",
            "Evidence Auditor must flag unsupported, post-cutoff, or source-less claims before quant synthesis.",
            "Research Scout must add source pointers for thin targets instead of letting agents improvise facts.",
            "Data Retrieval Analyst must extract units, dates, denominators, ranges, and source caveats before numbers are used.",
            "Quantitative Synthesizer may aggregate only numeric-capable agents and must preserve non-numeric agents as pressure signals.",
        ]
        if missing_target_evidence:
            interventions.insert(0, f"Immediate follow-up: retrieve or label missing evidence for {', '.join(missing_target_evidence[:6])}.")
        reexecution_advice = []
        if invalid_actor_nodes:
            reexecution_advice.append("Re-run ontology/graph repair for actor-lane cleanup; do not stop the simulation while repaired graph data is pending.")
        if missing_target_evidence:
            reexecution_advice.append("Re-run or enrich research/data extraction for thin target variables before treating confidence as high.")
        if not evidence_cards:
            reexecution_advice.append("Proceed in prompt-only degraded mode and ask the user or research scout for more evidence before polished claims.")

        return {
            "mode": "active_graph_conscience",
            "control_contract": {
                "allowed_dynamic_controls": [
                    "research_queries",
                    "source_priorities",
                    "debate_agenda",
                    "agent_context",
                    "evidence_cards",
                    "validation_warnings",
                    "rerun_recommendations",
                ],
                "forbidden_controls": [
                    "runtime_source_code_mutation",
                    "blocking_step_completion_without_diagnostic",
                    "inventing_forecasts_or_sources",
                    "overriding_numeric_validation",
                ],
                "failure_policy": (
                    "Graph guidance is advisory and corrective. It may request re-execution or enrichment, "
                    "but it must never dead-end the run. If graph data is weak, continue with explicit caveats "
                    "and let numeric validation decide whether polished output is safe."
                ),
            },
            "graph_id": graph_context.get("graph_id") if isinstance(graph_context, dict) else None,
            "node_count": len(nodes),
            "edge_count": len(edges),
            "actor_count": len(actor_nodes),
            "target_count": len(target_names),
            "evidence_source_count": len(evidence_sources),
            "evidence_claim_count": len(evidence_claims),
            "numeric_fact_count": len(numeric_facts),
            "prompt_evidence_count": len(prompt_evidence_notes),
            "clean_evidence_count": len(evidence_cards) + len(prompt_evidence_notes),
            "time_pocket_count": len(time_pockets),
            "actor_roster_preview": actor_names[:30],
            "target_variables": target_names[:40],
            "evidence_cards": evidence_cards[:80],
            "prompt_evidence_preview": prompt_evidence_notes[:20],
            "missing_target_evidence": missing_target_evidence,
            "invalid_actor_nodes": invalid_actor_nodes,
            "warnings": warnings,
            "intervention_plan": interventions,
            "reexecution_advice": reexecution_advice,
            "blocking": False,
        }

    def _graph_evidence_cards(
        self,
        evidence_claims: List[Dict[str, Any]],
        numeric_facts: List[Dict[str, Any]],
        evidence_text: str,
    ) -> List[str]:
        cards: List[str] = []
        for node in numeric_facts:
            summary = re.sub(r"\s+", " ", str(node.get("summary") or node.get("name") or "")).strip()
            if summary:
                cards.append(f"Graph numeric fact :: {summary[:300]}")
        for node in evidence_claims:
            summary = re.sub(r"\s+", " ", str(node.get("summary") or node.get("name") or "")).strip()
            if summary:
                cards.append(f"Graph evidence claim :: {summary[:300]}")
        for note in self._numeric_anchor_notes(evidence_text)[:30]:
            cards.append(f"Prompt numeric anchor :: {note[:300]}")
        return [
            card for card in list(dict.fromkeys(cards))
            if not self._is_bad_evidence_card(card)
        ]

    def _is_bad_evidence_card(self, value: Any) -> bool:
        """Reject workflow scaffolding, role lists, and prompt instructions as evidence."""
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        if not text:
            return True
        # Remove common card prefixes before judging the actual content.
        bare = re.sub(
            r"^(?:graph numeric fact|graph evidence claim|prompt numeric anchor|[^:]{1,90})\s*::\s*",
            "",
            text,
            flags=re.IGNORECASE,
        ).strip()
        lowered = bare.lower().strip(" .")
        if not lowered:
            return True
        if self._is_meta_instruction_note(bare) or self._looks_like_metric_request_line(bare):
            return True
        bad_fragments = [
            "pocket_id",
            "'start':",
            '"start":',
            "'end':",
            '"end":',
            "'events':",
            '"events":',
            "auto', 'end'",
            "targets contain placeholder",
            "time pockets include",
            "style requirements",
            "forecast the following",
            "source of truth",
            "semantic validation",
            "weak debate quality",
            "report-template mismatch",
            "agents must",
            "agent must",
            "for every agent",
            "each agent",
            "debate moderator",
            "separate fact, inference",
            "avoid leakage",
            "do not use future",
            "do not generate",
            "do not produce",
            "numeric forecasts first",
            "clearly separate facts",
            "required output",
            "required outputs",
            "target variables",
            "time-pocket simulation",
            "time pocket simulation",
            "create ",
            "generate ",
            "produce ",
            "output ",
            "run four scenarios",
            "run the simulation",
            "forecast these",
            "simulate sequentially",
            "what are the most likely paths",
            "next 90 days, divided into",
            "forecast horizon",
            "what would change their view",
            "challenge another agent",
            "cite evidence",
            "make a concrete claim",
        ]
        if any(fragment in lowered for fragment in bad_fragments):
            return True
        if lowered.startswith(("{", "}", "[", "]")) or re.search(r"[{}]{1,}.*:", lowered):
            return True
        if re.match(r"^(?:days?\s+\d+|days?\s+\d+\s*[–-]\s*\d+|scenario synthesis|forecast ledger|current state)\b", lowered):
            return True
        # A numbered list item like "2. Bolton regime defender" is an actor hint,
        # not a dated fact or numeric anchor. Keep numbered evidence only when it
        # has an observable metric/event/state term.
        if re.fullmatch(r"\d{1,2}\.\s+[a-z0-9 /'&().,-]{2,90}", lowered):
            has_observable_context = bool(re.search(
                r"%|percent|rate|price|seat|vote|turnout|poll|won|lost|died|survived|"
                r"betray|battle|alliance|claim|throne|army|fleet|dragon|wall|"
                r"supply|demand|capacity|growth|risk|index|election|baseline|"
                r"\b(?:19|20)\d{2}\b",
                lowered,
            ))
            if not has_observable_context:
                return True
        return False

    def _has_real_numeric_measure(self, cleaned: str) -> bool:
        """True only for real measurements, dates, money, ranges, or unit-bearing values."""
        text = str(cleaned or "")
        without_ordinal = re.sub(r"^\s*\d{1,2}\.\s+", "", text)
        return bool(re.search(
            r"[$€£₹]\s?\d|\b(?:19|20)\d{2}\b|\d[\d,]*(?:\.\d+)?\s*(?:%|percent|bps|basis points|"
            r"million|billion|trillion|m\b|bn\b|tn\b|kwh|mwh|gwh|twh|tons?|tonnes?|metric tons?|"
            r"barrels?|bpd|mb/d|usd|dollars?|seats?|votes?|months?|years?|days?|vessels?|ships?|seafarers?|"
            r"routes?|premiums?|rates?)|"
            r"\d[\d,]*(?:\.\d+)?\s*[-–]\s*\d[\d,]*(?:\.\d+)?",
            without_ordinal,
            flags=re.IGNORECASE,
        ))

    def _graph_missing_target_evidence(self, target_names: List[str], evidence_cards: List[str]) -> List[str]:
        missing: List[str] = []
        joined_cards = "\n".join(evidence_cards).lower()
        for target in target_names:
            cleaned = _clean_name(target)
            tokens = [
                token for token in re.split(r"[_\s/]+", cleaned)
                if len(token) >= 4 and token not in {"target", "variable", "probability", "price", "rate", "cost", "index", "share"}
            ]
            if not tokens:
                continue
            overlap = sum(1 for token in tokens if token in joined_cards)
            if overlap == 0:
                missing.append(cleaned)
        return missing[:16]

    def _graph_pocket_directive(
        self,
        graph_brain: Dict[str, Any],
        pocket: Dict[str, Any],
        targets: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        label = str(pocket.get("label") or pocket.get("pocket_id") or "")
        target_names = [str(target.get("name") or "") for target in targets[:8]]
        return {
            "directive": (
                f"Before advancing `{label}`, compare agent claims against graph evidence, "
                f"missing targets, and requested outputs: {', '.join(target_names)}."
            ),
            "blocking": False,
            "failure_policy": "Proceed with caveats; request targeted re-execution/enrichment instead of stopping the flow.",
            "interventions": graph_brain.get("intervention_plan", [])[:5],
            "reexecution_advice": graph_brain.get("reexecution_advice", [])[:5],
            "warnings": graph_brain.get("warnings", [])[:5],
            "missing_target_evidence": graph_brain.get("missing_target_evidence", [])[:8],
        }

    def _required_scenarios(self, domain_plan: Dict[str, Any]) -> List[str]:
        scenario_flags = domain_plan.get("scenario_structure") or {}
        scenario_paths = scenario_flags.get("scenarios") if isinstance(scenario_flags, dict) else []
        if isinstance(scenario_paths, list) and scenario_paths:
            scenarios = [
                str(item.get("id") or item.get("name") or "").strip()
                for item in scenario_paths
                if isinstance(item, dict) and item.get("required", True)
            ]
            scenarios = [scenario for scenario in scenarios if scenario]
            if scenarios:
                return list(dict.fromkeys(scenarios))
        mapping = {
            "base_case": "base",
            "upside_case": "upside",
            "downside_case": "downside",
            "tail_case": "tail",
        }
        return [
            scenario
            for flag, scenario in mapping.items()
            if scenario_flags.get(flag, True)
        ] or ["base"]

    def _agent_forecast_output(
        self,
        agent: Dict[str, Any],
        agent_idx: int,
        target: Dict[str, Any],
        target_idx: int,
        time_pockets: List[Dict[str, Any]],
        forecast_periods: List[Dict[str, Any]],
        domain_plan: Dict[str, Any],
        evidence_text: str,
    ) -> Dict[str, Any]:
        target_name = target.get("name") or "primary_outcome"
        unit = target.get("unit") or "index"
        forecast_path = []
        periods = forecast_periods or self._pocket_periods(time_pockets)
        for period_idx, period in enumerate(periods):
            date = period.get("date") or period.get("label") or period.get("period_id")
            for scenario in self._required_scenarios(domain_plan):
                period_label = str(period.get("label") or date or period.get("period_id") or "")
                forecast_path.append({
                    "date": date,
                    "value": self._forecast_value(
                        target_name=target_name,
                        unit=unit,
                        agent=agent,
                        agent_idx=agent_idx,
                        target_idx=target_idx,
                        pocket_idx=period_idx,
                        pocket_label=period_label,
                        scenario=scenario,
                        evidence_text=evidence_text,
                    ),
                    "unit": unit,
                    "scenario": scenario,
                    "period_index": period_idx,
                    "period_label": period_label,
                    "period_source": period.get("source") or "forecast_horizon",
                })
        confidence = self._confidence(agent, target_name, evidence_text)
        return {
            "agent_id": agent.get("agent_id"),
            "agent_name": agent.get("name"),
            "pocket_id": time_pockets[-1].get("pocket_id") if time_pockets else "pocket_001",
            "target_variable": target_name,
            "forecast_path": forecast_path,
            "confidence": confidence,
            "reasoning_summary": (
                f"{agent.get('name', 'Agent')} updates {target_name} across time pockets "
                "using its role, information set, scenario assumptions, and prompt evidence."
            ),
            "drivers": self._drivers(agent, target_name, domain_plan),
            "risks": [
                "Evidence may be incomplete or uneven across regions/groups/time pockets.",
                "Scenario spread should be treated as uncertainty, not ground truth.",
            ],
            "what_would_change_my_forecast": [
                "New validated evidence that changes a state variable.",
                "Large disagreement from high-information agents in later pockets.",
                "Evidence auditor flags leakage, contradiction, or missing numeric support.",
            ],
            "blind_spots": agent.get("blind_spots") or ["May overweight its own information advantage."],
        }

    def _forecast_periods(
        self,
        domain_plan: Dict[str, Any],
        time_pockets: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Generate forecast output dates separately from debate time pockets."""
        horizon = domain_plan.get("forecast_horizon") or {}
        granularity = str(horizon.get("granularity") or "event_triggered").lower()
        start_raw = horizon.get("start")
        end_raw = horizon.get("end")
        if granularity in {"event_triggered", "event", "auto"}:
            return self._pocket_periods(time_pockets)

        if granularity == "yearly":
            start_year = self._parse_year_bound(start_raw, default=None)
            end_year = self._parse_year_bound(end_raw, default=start_year)
            if start_year and end_year:
                if end_year < start_year:
                    start_year, end_year = end_year, start_year
                return [
                    {
                        "period_id": f"period_{year}",
                        "date": str(year),
                        "label": str(year),
                        "source": "forecast_horizon",
                    }
                    for year in range(start_year, min(end_year, start_year + 30) + 1)
                ]

        start = self._parse_date_bound(start_raw, prefer_end=False)
        end = self._parse_date_bound(end_raw, prefer_end=True)
        if not start or not end:
            return self._pocket_periods(time_pockets)
        if end < start:
            start, end = end, start

        if granularity == "monthly":
            periods = []
            current = datetime(start.year, start.month, 1)
            final = datetime(end.year, end.month, 1)
            idx = 0
            while current <= final and idx < 120:
                periods.append({
                    "period_id": f"period_{idx + 1:03d}",
                    "date": current.strftime("%Y-%m"),
                    "label": current.strftime("%b %Y"),
                    "source": "forecast_horizon",
                })
                current = self._add_months(current, 1)
                idx += 1
            return periods or self._pocket_periods(time_pockets)

        if granularity == "quarterly":
            periods = []
            current = datetime(start.year, ((start.month - 1) // 3) * 3 + 1, 1)
            final = datetime(end.year, ((end.month - 1) // 3) * 3 + 1, 1)
            idx = 0
            while current <= final and idx < 80:
                quarter = ((current.month - 1) // 3) + 1
                periods.append({
                    "period_id": f"period_{idx + 1:03d}",
                    "date": f"{current.year}-Q{quarter}",
                    "label": f"Q{quarter} {current.year}",
                    "source": "forecast_horizon",
                })
                current = self._add_months(current, 3)
                idx += 1
            return periods or self._pocket_periods(time_pockets)

        if granularity == "weekly":
            return self._fixed_day_periods(start, end, step_days=7, max_points=156, date_format="%Y-%m-%d")
        if granularity == "daily":
            return self._fixed_day_periods(start, end, step_days=1, max_points=180, date_format="%Y-%m-%d")
        return self._pocket_periods(time_pockets)

    def _pocket_periods(self, time_pockets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        periods = []
        for idx, pocket in enumerate(time_pockets or []):
            date = pocket.get("end") if pocket.get("end") != "auto" else pocket.get("label") or pocket.get("pocket_id")
            periods.append({
                "period_id": f"period_{idx + 1:03d}",
                "date": date,
                "label": pocket.get("label") or date,
                "source": "time_pocket",
            })
        return periods or [{
            "period_id": "period_001",
            "date": "event_triggered simulation pocket",
            "label": "event_triggered simulation pocket",
            "source": "fallback",
        }]

    def _fixed_day_periods(
        self,
        start: datetime,
        end: datetime,
        step_days: int,
        max_points: int,
        date_format: str,
    ) -> List[Dict[str, Any]]:
        periods = []
        current = start
        idx = 0
        while current <= end and idx < max_points:
            periods.append({
                "period_id": f"period_{idx + 1:03d}",
                "date": current.strftime(date_format),
                "label": current.strftime(date_format),
                "source": "forecast_horizon",
            })
            current += timedelta(days=step_days)
            idx += 1
        return periods

    def _add_months(self, value: datetime, months: int) -> datetime:
        month = value.month - 1 + months
        year = value.year + month // 12
        month = month % 12 + 1
        return datetime(year, month, 1)

    def _parse_year_bound(self, value: Any, default: int | None = None) -> int | None:
        match = re.search(r"\b(20\d{2}|19\d{2})\b", str(value or ""))
        return int(match.group(1)) if match else default

    def _parse_date_bound(self, value: Any, prefer_end: bool = False) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        month_pattern = (
            r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
            r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
        )
        match = re.search(month_pattern + r"[\s,/-]+(20\d{2}|19\d{2})", text, flags=re.IGNORECASE)
        if match:
            month = self._month_number(match.group(1))
            return datetime(int(match.group(2)), month, 1)
        match = re.search(r"\b(20\d{2}|19\d{2})[-/](\d{1,2})(?:[-/](\d{1,2}))?\b", text)
        if match:
            year = int(match.group(1))
            month = max(1, min(12, int(match.group(2))))
            day = max(1, min(28, int(match.group(3) or 1)))
            return datetime(year, month, day)
        match = re.search(r"\b(20\d{2}|19\d{2})\b", text)
        if match:
            year = int(match.group(1))
            return datetime(year, 12 if prefer_end else 1, 1)
        return None

    def _month_number(self, value: str) -> int:
        lookup = {
            "jan": 1, "january": 1,
            "feb": 2, "february": 2,
            "mar": 3, "march": 3,
            "apr": 4, "april": 4,
            "may": 5,
            "jun": 6, "june": 6,
            "jul": 7, "july": 7,
            "aug": 8, "august": 8,
            "sep": 9, "sept": 9, "september": 9,
            "oct": 10, "october": 10,
            "nov": 11, "november": 11,
            "dec": 12, "december": 12,
        }
        return lookup.get(str(value or "").lower()[:3], 1)

    def _forecast_value(
        self,
        target_name: str,
        unit: str,
        agent: Dict[str, Any],
        agent_idx: int,
        target_idx: int,
        pocket_idx: int,
        pocket_label: str,
        scenario: str,
        evidence_text: str,
    ) -> float:
        lowered = f"{target_name} {unit}".lower()
        anchored = self._anchored_forecast_value(
            target_name=target_name,
            unit=unit,
            agent=agent,
            agent_idx=agent_idx,
            target_idx=target_idx,
            pocket_idx=pocket_idx,
            pocket_label=pocket_label,
            scenario=scenario,
            evidence_text=evidence_text,
        )
        if anchored is not None:
            return anchored
        jitter = self._stable_jitter(agent.get("agent_id"), target_name, scenario, evidence_text, span=8.0)
        scenario_shift = self._scenario_shift(scenario, target_name, agent)
        if any(term in lowered for term in ["probability", "chance"]):
            base = 52.0 + scenario_shift + jitter + pocket_idx * 1.2
            return round(max(1.0, min(99.0, base)), 2)
        if any(term in lowered for term in ["share", "rate", "turnout", "percent", "index"]):
            base = 50.0 + scenario_shift * 0.6 + jitter + pocket_idx * 0.8
            return round(max(0.0, min(100.0, base)), 2)
        if any(term in lowered for term in ["seat", "count", "number"]):
            assembly_size = self._extract_total_count(evidence_text) or 100.0
            base = assembly_size * (0.34 + (scenario_shift / 300.0)) + jitter * 2.2 + pocket_idx * 1.5
            return round(max(0.0, min(assembly_size, base)), 0)
        if "currency" in unit or "price" in lowered or "cost" in lowered:
            base = 100.0 + scenario_shift * 2.0 + jitter * 2.5 + pocket_idx * 3.0
            return round(max(0.0, base), 2)
        base = 50.0 + scenario_shift * 0.7 + jitter + pocket_idx
        return round(max(0.0, min(100.0, base)), 2)

    def _anchored_forecast_value(
        self,
        target_name: str,
        unit: str,
        agent: Dict[str, Any],
        agent_idx: int,
        target_idx: int,
        pocket_idx: int,
        pocket_label: str,
        scenario: str,
        evidence_text: str,
    ) -> float | None:
        """Use prompt-supplied numeric anchors when target names expose a metric.

        This is not domain-specific. It activates for reusable constrained
        output types such as shares, counts, probabilities, prices, costs,
        growth rates, inventory cover, and indices.
        """
        label, metric = self._target_label_and_metric(target_name)
        if not metric:
            return None
        evidence_anchor = self._extract_metric_anchor(label, metric, evidence_text, unit, target_name, pocket_label)
        scenario_shift = self._scenario_shift(scenario, target_name, agent)
        agent_bias = self._agent_numeric_bias(agent, label, target_name)
        pocket_drift = pocket_idx * self._metric_drift(metric, scenario)
        jitter = self._stable_jitter(agent.get("agent_id"), target_name, scenario, evidence_text, span=2.4)

        if metric in {"vote_share", "seat_share", "share", "rate", "turnout", "index", "growth_rate", "growth"}:
            base = evidence_anchor if evidence_anchor is not None else self._default_rate_anchor(label, metric)
            value = base + scenario_shift * 0.35 + agent_bias + pocket_drift + jitter
            return round(max(0.0, min(100.0, value)), 2)
        if metric in {"price", "cost", "value", "premium", "revenue", "capex"}:
            label_terms = self._meaningful_label_terms(label)
            base = evidence_anchor
            if base is None and not label_terms:
                base = self._generic_currency_anchor(evidence_text, unit, label, metric)
            if base is None:
                base = self._default_currency_anchor(label, unit, metric)
            if base is None:
                return None
            value = base * (1 + (scenario_shift * 0.006) + (agent_bias * 0.004) + (pocket_drift * 0.003))
            value += jitter * max(1.0, abs(base) * 0.012)
            return round(max(0.0, value), 2)
        if metric == "probability":
            base = evidence_anchor if evidence_anchor is not None else self._probability_anchor_from_target(label, scenario)
            value = base + scenario_shift * 0.45 + agent_bias + pocket_drift + jitter
            return round(max(1.0, min(99.0, value)), 2)
        if metric == "inventory_cover":
            base = evidence_anchor if evidence_anchor is not None else 3.5
            value = base + scenario_shift * 0.04 + agent_bias * 0.03 + pocket_drift * 0.08 + jitter * 0.12
            return round(max(0.0, value), 2)
        if metric in {"seats", "count"}:
            total = self._extract_total_count(evidence_text) or 100.0
            if evidence_anchor is None:
                if label in {"other", "others", "misc", "miscellaneous", "independent", "independents"}:
                    share_anchor = (
                        self._extract_metric_anchor(label, "vote_share", evidence_text, unit, target_name, pocket_label)
                        or self._extract_metric_anchor(label, "share", evidence_text, unit, target_name, pocket_label)
                    )
                    evidence_anchor = total * (share_anchor / 100.0) if share_anchor is not None else total * 0.08
                else:
                    evidence_anchor = total * 0.34
            value = evidence_anchor + scenario_shift * (total / 220.0) + agent_bias * (total / 140.0) + pocket_drift + jitter * (total / 100.0)
            return round(max(0.0, min(total, value)), 0)
        return None

    def _target_label_and_metric(self, target_name: str) -> Tuple[str, str]:
        name = _clean_name(target_name)
        if "inventory_cover" in name:
            return name.replace("inventory_cover", "").strip("_"), "inventory_cover"
        if name.startswith("probability_"):
            return name[len("probability_"):], "probability"
        if "probability_of_" in name:
            return name.replace("probability_of_", ""), "probability"
        suffixes = [
            "vote_share", "seat_share", "growth_rate", "probability", "turnout", "seats",
            "share", "rate", "growth", "index", "count",
        ]
        for suffix in suffixes:
            if name == suffix:
                return "", suffix
            if name.endswith(f"_{suffix}"):
                return name[: -(len(suffix) + 1)], suffix
        for metric in ["price", "cost", "value", "premium", "revenue", "capex"]:
            marker = f"_{metric}"
            if marker in name:
                return name.split(marker, 1)[0], metric
            if name.endswith(metric):
                return name[: -len(metric)].strip("_"), metric
        return "", ""

    def _extract_metric_anchor(
        self,
        label: str,
        metric: str,
        evidence_text: str,
        unit: str = "",
        target_name: str = "",
        pocket_label: str = "",
    ) -> float | None:
        text = evidence_text or ""
        if not text:
            return None
        label_terms = self._meaningful_label_terms(label)
        if not label_terms and metric not in {"turnout"}:
            return None
        label_pattern = r"(?:%s)" % r"|".join(re.escape(term) for term in label_terms) if label_terms else ""
        values: List[float] = []

        if metric in {"vote_share", "seat_share", "share", "rate", "turnout", "index", "probability", "growth_rate", "growth"}:
            if metric == "index" and "index" not in str(target_name or label).lower():
                return None
            if label_pattern:
                for line in text.splitlines():
                    line_l = line.lower()
                    if not self._line_matches_label(line_l, label_terms, min_matches=2 if len(label_terms) >= 2 else 1):
                        continue
                    if metric == "index" and "index" not in line_l:
                        continue
                    if metric in {"growth_rate", "growth", "rate"} and not re.search(r"\b(growth|rate|increase|decline|fell|rose|yoy|year[- ]on[- ]year)\b", line_l):
                        continue
                    labeled_values = self._labeled_percent_values(line, label_terms)
                    if labeled_values:
                        values.extend(labeled_values)
                        continue
                    for match in re.finditer(r"(\d{1,3}(?:\.\d+)?)\s*%", line, flags=re.IGNORECASE):
                        values.append(float(match.group(1)))
            if metric == "turnout" and not values:
                for match in re.finditer(r"turnout[^%\n]{0,60}?(\d{1,3}(?:\.\d+)?)\s*%", text, flags=re.IGNORECASE):
                    values.append(float(match.group(1)))
            return values[-1] if values else None

        if metric in {"price", "cost", "value", "premium", "revenue", "capex"}:
            label_token_set = set(label_terms)
            scored_values: List[Tuple[float, int, float]] = []
            lines = text.splitlines()
            for line_no, line in enumerate(lines):
                previous = lines[line_no - 1] if line_no > 0 else ""
                match_l = f"{previous} {line}".strip().lower()
                line_l = line.lower()
                min_matches = 2 if len(label_token_set) >= 2 else 1
                if label_token_set and not self._line_matches_label(match_l, label_terms, min_matches=min_matches):
                    continue
                if metric == "cost" and "kwh" in str(unit).lower() and not re.search(r"\b(kwh|pack|battery\s+cost|cell\s+cost)\b", match_l):
                    continue
                if not re.search(r"\b(price|cost|value|premium|revenue|capex|usd|dollars?|\$|eur|gbp|inr|kwh|kg|metric ton|tonne|barrel)\b", match_l):
                    continue
                number_items = self._numbers_with_context(line)
                long_mixed_source_line = len(line) > 700 and len(number_items) > 6
                if long_mixed_source_line:
                    continue
                for value, context in number_items:
                    if 1900 <= value <= 2100:
                        continue
                    if value < 0 or context.get("is_percent"):
                        continue
                    if context.get("is_list_marker"):
                        continue
                    if label_token_set and metric in {"price", "cost", "value", "premium", "revenue", "capex"}:
                        prefix_len = (len(previous) + 1 if previous else 0) + int(context.get("start") or 0)
                        prefix_l = match_l[:prefix_len]
                        if not self._line_matches_label(prefix_l, label_terms, min_matches=min_matches):
                            continue
                    if self._is_unit_scale_mismatch(value, line_l, unit):
                        continue
                    value = self._normalize_currency_value(value, line_l, unit, target_name)
                    score = 1.0
                    if label_token_set:
                        score += len(label_token_set & set(re.findall(r"[a-zA-Z0-9]{3,}", match_l))) * 2.0
                    if pocket_label and self._line_matches_label(line_l, self._meaningful_label_terms(pocket_label), min_matches=1):
                        score += 2.5
                    if re.search(r"\b(base|current|latest|today|as of|now|spot|benchmark)\b", match_l):
                        score += 2.0
                    if re.search(r"\b(may\s+2026|march\s+2026|2026)\b", match_l) and not re.search(r"\b(2021|2022|2023|2024|2025)\b", str(pocket_label).lower()):
                        score += 2.0
                    if re.search(r"\b(crash|correction|marginal cost|below cost|trough|2023|2024|2025)\b", str(pocket_label).lower()) and re.search(r"\b(crash|correction|marginal cost|below cost|trough|2023|2024|2025)\b", line_l):
                        score += 4.0
                    if re.search(r"\b(peak|previous|historical|past|last cycle)\b", line_l):
                        score -= 1.0
                    if re.search(r"\b(peak|late[- ]?2022|2021[- ]?2022|boom)\b", line_l):
                        if re.search(r"\b(boom|2021|2022|baseline)\b", str(pocket_label).lower()):
                            score += 3.0
                        else:
                            score -= 8.0
                    scored_values.append((score, line_no, value))
            if scored_values:
                scored_values.sort(key=lambda item: (item[0], item[1]))
                return scored_values[-1][2]

        if metric == "inventory_cover":
            for line in text.splitlines():
                line_l = line.lower()
                if not self._line_matches_label(line_l, label_terms, min_matches=1):
                    continue
                if not re.search(r"\b(inventory|cover)\b", line_l):
                    continue
                for value, context in self._numbers_with_context(line):
                    if context.get("is_list_marker"):
                        continue
                    if re.search(r"\b(next|following|forecast horizon|horizon)\b", line_l) and value in {12, 18, 24, 36, 48, 60}:
                        continue
                    if not context.get("is_percent") and 0 <= value <= 60:
                        values.append(value)
            return values[-1] if values else None

        if metric in {"seats", "count"} and label_pattern:
            scored_values: List[Tuple[float, int, float]] = []
            assembly_like_prompt = bool(re.search(r"\b(assembly|legislative|state election)\b", text, flags=re.IGNORECASE))
            for line_no, line in enumerate(text.splitlines()):
                line_l = line.lower()
                context_score = 0.0
                if assembly_like_prompt:
                    if "assembly-segment" in line_l:
                        context_score += 8.0
                    if re.search(r"\b(assembly|legislative|state election)\b", line_l):
                        context_score += 5.0
                    if re.search(r"\b(lok sabha|parliament|ls)\b", line_l):
                        context_score -= 5.0
                for match in re.finditer(label_pattern + r"[^%\n]{0,35}?(\d{1,4})\s*(?:seats?|,|\.|\)|$)", line, flags=re.IGNORECASE):
                    value = float(match.group(1))
                    if 0 <= value <= 10000 and value not in {1900, 2000, 2011, 2014, 2016, 2019, 2021, 2024, 2026}:
                        scored_values.append((context_score, line_no, value))
            if scored_values:
                scored_values.sort(key=lambda item: (item[0], item[1]))
                return scored_values[-1][2]
        return None

    def _labeled_percent_values(self, line: str, label_terms: List[str]) -> List[float]:
        """Extract percentages attached to the requested label on mixed lines.

        Prompts often contain compact rows such as
        "Actor A 46.2%, Actor B 39.1%, Actor C 10%". A plain line-level percent
        scan would return the last value for every actor. This helper chooses
        the nearest following percent after the matching label phrase.
        """
        if not line or not label_terms:
            return []
        line_l = line.lower()
        label_variants = self._label_variants(label_terms)
        candidates: List[Tuple[int, float]] = []
        for variant in label_variants:
            for label_match in re.finditer(variant, line_l, flags=re.IGNORECASE):
                after = line[label_match.end(): label_match.end() + 90]
                percent_match = re.search(r"(\d{1,3}(?:\.\d+)?)\s*%", after)
                if not percent_match:
                    percent_match = re.search(r"(?:around|approx(?:imately)?|about|near|roughly)?\s*(\d{1,3}(?:\.\d+)?)\s*percent", after, flags=re.IGNORECASE)
                if percent_match:
                    distance = percent_match.start()
                    value = float(percent_match.group(1))
                    if 0 <= value <= 100:
                        candidates.append((distance, value))
        if not candidates:
            return []
        candidates.sort(key=lambda item: item[0])
        return [candidates[0][1]]

    def _label_variants(self, label_terms: List[str]) -> List[str]:
        escaped = [re.escape(term) for term in label_terms if term]
        if not escaped:
            return []
        variants = []
        if len(escaped) == 1:
            variants.append(r"\b" + escaped[0] + r"\b")
        else:
            variants.append(r"\b" + r"[\s/_&+\-]*".join(escaped) + r"\b")
            variants.append(r"\b" + r".{0,20}".join(escaped) + r"\b")
        return variants

    def _numbers_from_text(self, text: str) -> List[float]:
        return [value for value, _ in self._numbers_with_context(text)]

    def _numbers_with_context(self, text: str) -> List[Tuple[float, Dict[str, bool]]]:
        values: List[float] = []
        contextual: List[Tuple[float, Dict[str, bool]]] = []
        for match in re.finditer(r"(?:[$€£₹]\s*|\b(?:usd|eur|gbp|inr|dollars?)\s*)?(\d[\d,]*(?:\.\d+)?)", text or "", flags=re.IGNORECASE):
            try:
                value = float(match.group(1).replace(",", ""))
            except ValueError:
                continue
            after = (text or "")[match.end(): match.end() + 8].lower()
            before = (text or "")[max(0, match.start() - 12): match.start()].lower()
            contextual.append((value, {
                "is_percent": bool(re.match(r"\s*%", after)) or "percent" in after[:8],
                "has_currency_marker": bool(re.search(r"[$€£₹]|usd|eur|gbp|inr|dollars?", before + after, flags=re.IGNORECASE)),
                "is_list_marker": bool(re.match(r"^\s*\d+[\.)]", (text or "")[: match.end()])),
                "start": match.start(),
                "end": match.end(),
            }))
        return contextual

    def _generic_currency_anchor(self, evidence_text: str, unit: str = "", label: str = "", metric: str = "") -> float | None:
        scored_values: List[Tuple[float, int, float]] = []
        for line_no, line in enumerate((evidence_text or "").splitlines()):
            line_l = line.lower()
            if not re.search(r"\b(price|cost|value|usd|dollars?|\$|eur|gbp|inr|kwh|metric ton|tonne|barrel)\b", line_l):
                continue
            if metric == "cost" and "kwh" in str(unit).lower() and not re.search(r"\b(kwh|pack|battery\s+cost|cell\s+cost)\b", line_l):
                continue
            for value, context in self._numbers_with_context(line):
                if value <= 0 or 1900 <= value <= 2100 or context.get("is_percent"):
                    continue
                value = self._normalize_currency_value(value, line_l, unit, label)
                score = 1.0 + (2.0 if re.search(r"\b(current|latest|spot|benchmark|as of|today)\b", line_l) else 0.0)
                scored_values.append((score, line_no, value))
        if not scored_values:
            return None
        scored_values.sort(key=lambda item: (item[0], item[1]))
        return scored_values[-1][2]

    def _meaningful_label_terms(self, value: str) -> List[str]:
        stopwords = {
            "the", "and", "for", "from", "with", "into", "onto", "rate", "share",
            "price", "cost", "value", "index", "count", "probability", "global",
            "statewide", "overall", "numeric", "output", "outputs", "forecast",
            "scenario", "case", "simulation", "pocket", "current", "baseline",
        }
        terms = []
        for term in re.split(r"[_\s/()\-]+", str(value or "").lower()):
            if len(term) < 3 or term in stopwords:
                continue
            terms.append(term)
        return terms

    def _line_matches_label(self, line_l: str, terms: List[str], min_matches: int = 1) -> bool:
        if not terms:
            return False
        tokens = set(re.findall(r"[a-zA-Z0-9]{3,}", line_l))
        matches = sum(1 for term in terms if term in tokens or term in line_l)
        return matches >= max(1, min_matches)

    def _normalize_currency_value(self, value: float, line_l: str, unit: str, target_name: str) -> float:
        unit_l = f"{unit} {target_name}".lower()
        if "metric ton" in unit_l or "tonne" in unit_l or "per_ton" in unit_l or "per_metric_ton" in unit_l:
            if re.search(r"\busd\s*/?\s*kg\b|\bper\s+kg\b|\bkg\b", line_l) and value < 1000:
                return value * 1000.0
        if "kwh" in unit_l and value > 1000 and not re.search(r"\bkwh\b", line_l):
            return value / 1000.0
        return value

    def _is_unit_scale_mismatch(self, value: float, line_l: str, unit: str) -> bool:
        unit_l = str(unit or "").lower()
        if "metric ton" in unit_l or "tonne" in unit_l or "per_metric_ton" in unit_l:
            if value < 100 and not re.search(r"\b(usd\s*/?\s*kg|per\s+kg|/kg|kg)\b", line_l):
                return True
        if "kwh" in unit_l and value > 1000 and not re.search(r"\bkwh\b", line_l):
            return True
        return False

    def _default_rate_anchor(self, label: str, metric: str) -> float:
        label_l = str(label or "").lower()
        if metric in {"vote_share", "seat_share", "share"}:
            return 35.0
        if metric == "turnout":
            return 60.0
        if metric in {"growth_rate", "growth", "rate"}:
            if any(term in label_l for term in ["storage", "adoption", "demand"]):
                return 18.0
            if any(term in label_l for term in ["supply", "mine", "production"]):
                return 8.0
            return 10.0
        if metric == "index":
            return 50.0
        return 50.0

    def _default_currency_anchor(self, label: str, unit: str, metric: str) -> float | None:
        unit_l = str(unit or "").lower()
        label_l = str(label or "").lower()
        if "kwh" in unit_l:
            return 100.0
        if "barrel" in unit_l:
            return 75.0
        if "metric ton" in unit_l or "tonne" in unit_l or "per_ton" in unit_l:
            if any(term in label_l for term in ["concentrate", "feedstock", "ore", "raw"]):
                return 1200.0
            return 10000.0
        if "kg" in unit_l:
            return 10.0
        if metric in {"revenue", "capex"}:
            return 100.0
        return 100.0

    def _agent_numeric_bias(self, agent: Dict[str, Any], label: str, target_name: str) -> float:
        text = " ".join([
            str(agent.get("name") or ""),
            str(agent.get("role") or ""),
            str(agent.get("institutional_incentives") or ""),
            str(agent.get("likely_bias") or ""),
        ]).lower()
        label_tokens = [token for token in re.split(r"[_\s/]+", label or "") if len(token) >= 3]
        if label_tokens and any(token in text for token in label_tokens):
            return 2.0
        if any(word in text for word in ["auditor", "watchdog", "skeptic", "risk"]):
            return -1.2
        if any(word in text for word in ["quant", "data", "pollster", "scientist", "model"]):
            return 0.0
        return self._stable_jitter(agent.get("agent_id"), target_name, "agent_bias", span=1.5)

    def _metric_drift(self, metric: str, scenario: str) -> float:
        scenario_l = str(scenario or "").lower()
        if metric in {"seats", "count"}:
            return 0.8 if any(term in scenario_l for term in ["surge", "resilience", "upside"]) else -0.2
        if metric in {"price", "cost", "value", "premium", "revenue", "capex"}:
            return 1.0 if any(term in scenario_l for term in ["surge", "upside", "bull", "high", "deficit"]) else -0.25
        return 0.25 if any(term in scenario_l for term in ["surge", "resilience", "upside"]) else -0.05

    def _probability_anchor_from_target(self, label: str, scenario: str) -> float:
        label_l = str(label or "").lower()
        label_text = label_l.replace("_", " ").replace("-", " ")
        scenario_l = str(scenario or "").lower()
        if label_l and any(token in scenario_l for token in re.split(r"[_\s/]+", label_l) if len(token) >= 3):
            return 62.0
        # Generic conflict/geopolitical probability priors. These are keyed to
        # reusable target semantics, not to any specific country or prompt.
        if any(term in label_text for term in ["truce", "ceasefire", "deal", "negotiated", "de escalation", "deescalation", "settlement"]):
            return 44.0
        if any(term in label_text for term in ["partial disruption", "gray zone", "grey zone", "limited disruption", "continued disruption"]):
            return 48.0
        if any(term in label_text for term in ["cyber", "proxy", "militia", "houthi", "hezbollah", "red sea"]):
            return 34.0
        if any(term in label_text for term in ["renewed strike", "strikes", "direct retaliation", "unilateral escalation", "naval incident"]):
            return 30.0
        if any(term in label_text for term in ["severe disruption", "near closure", "closure", "wider war", "broader regional war", "regional war"]):
            return 18.0
        if any(term in label_l for term in ["hung", "fragment", "gridlock"]) and any(term in scenario_l for term in ["hung", "fragment", "gridlock"]):
            return 68.0
        return 28.0

    def _stable_jitter(self, *parts: Any, span: float) -> float:
        digest = hashlib.sha256("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()
        raw = int(digest[:8], 16) / 0xFFFFFFFF
        return (raw - 0.5) * span

    def _extract_total_count(self, evidence_text: str) -> float:
        match = None
        for pattern in [
            r"(?:assembly size|legislative assembly size|total seats|seat total|seats total)\s*[:=]?\s*(\d{2,4})",
            r"\bassembly\b[^\n]{0,30}?\(?(\d{2,4})\s+seats",
            r"(?:majority mark|majority threshold)\s*[:=]?\s*(\d{2,4})",
        ]:
            match = match or re.search(pattern, evidence_text or "", flags=re.IGNORECASE)
        if not match:
            inferred = self._infer_total_count_from_historical_rows(evidence_text)
            return inferred if inferred else 0.0
        value = float(match.group(1))
        matched_text = match.group(0).lower()
        if "majority" in matched_text:
            value = (value - 1) * 2
        return value if 1 <= value <= 10000 else 0.0

    def _infer_total_count_from_historical_rows(self, evidence_text: str) -> float:
        """Infer a contest-size denominator from comparable historical rows.

        This is a generic fallback for prompts that list party seat counts but
        omit a total. It avoids treating seat forecasts as a 0-100 index.
        """
        row_totals: List[float] = []
        for line in (evidence_text or "").splitlines():
            line_l = line.lower()
            if not re.search(r"\b(assembly|legislative|state election|seat)\b", line_l):
                continue
            if re.search(r"\b(lok sabha|parliament|ls)\b", line_l) and not re.search(r"assembly[- ]segment", line_l):
                continue
            seat_part = re.split(
                r"\b(?:approx(?:imate)? vote share|vote share|turnout|main meaning|issues?)\b",
                line,
                maxsplit=1,
                flags=re.IGNORECASE,
            )[0]
            values = []
            for match in re.finditer(r"\b[A-Za-z][A-Za-z()/&+.' -]{1,40}\s+(\d{1,4})(?=\s*(?:seats?|,|\.|$|\)))", seat_part):
                value = float(match.group(1))
                if value in {1900, 2000, 2011, 2014, 2016, 2019, 2021, 2024, 2026}:
                    continue
                if 0 < value < 1000:
                    values.append(value)
            if len(values) >= 2:
                total = sum(values)
                if 20 <= total <= 10000:
                    row_totals.append(total)
        return max(row_totals) if row_totals else 0.0

    def _confidence(self, agent: Dict[str, Any], target_name: str, evidence_text: str) -> float:
        base = 0.62 + self._stable_jitter(agent.get("agent_id"), target_name, "confidence", evidence_text, span=0.2)
        return round(max(0.35, min(0.88, base)), 2)

    def _drivers(self, agent: Dict[str, Any], target_name: str, domain_plan: Dict[str, Any]) -> List[str]:
        drivers = [
            agent.get("causal_power") or f"Agent-specific pressure on {target_name}.",
            agent.get("information_set", [""])[0] if agent.get("information_set") else "",
        ]
        drivers.extend([
            variable.get("name")
            for variable in domain_plan.get("state_variables", [])[:3]
            if variable.get("name")
        ])
        return [driver for driver in drivers if driver][:6]

    def _scenario_shift(self, scenario: str, target_name: str, agent: Dict[str, Any]) -> float:
        scenario_l = str(scenario or "").lower()
        target_l = str(target_name or "").lower()
        agent_l = f"{agent.get('name', '')} {agent.get('role', '')} {agent.get('archetype', '')}".lower()
        if "base" in scenario_l or "central" in scenario_l or "most_likely" in scenario_l:
            return 0.0

        target_tokens = {token for token in re.findall(r"[a-zA-Z][a-zA-Z0-9_]{2,}", target_l)}
        scenario_tokens = {token for token in re.findall(r"[a-zA-Z][a-zA-Z0-9_]{2,}", scenario_l)}
        aligns_with_target = bool(target_tokens & scenario_tokens)
        aligns_with_agent = any(token in agent_l for token in scenario_tokens if len(token) >= 4)

        if any(term in scenario_l for term in ["tail", "shock", "crisis", "collapse", "failure", "bear", "stress"]):
            return -14.0
        if any(term in scenario_l for term in ["downside", "adverse", "weak", "low", "fragment", "hung", "gridlock"]):
            return -8.0 if not aligns_with_target else 6.0
        if any(term in scenario_l for term in ["upside", "resilience", "surge", "breakthrough", "bull", "high", "optimistic"]):
            return 8.0 if (aligns_with_target or aligns_with_agent) else -4.0
        return self._stable_jitter(scenario, target_name, agent.get("agent_id"), "scenario_shift", span=10.0)

    def _aggregate_scenarios(
        self,
        agent_outputs: List[Dict[str, Any]],
        targets: List[Dict[str, Any]],
        evidence_text: str = "",
    ) -> Dict[str, Any]:
        buckets: Dict[Tuple[str, str, str], List[float]] = {}
        for output in agent_outputs:
            target = output.get("target_variable")
            for point in output.get("forecast_path") or []:
                key = (point.get("scenario"), target, str(point.get("date")))
                if point.get("scenario") and target:
                    buckets.setdefault(key, []).append(float(point.get("value")))
        scenarios: Dict[str, Any] = {}
        for (scenario, target, date), values in buckets.items():
            scenarios.setdefault(scenario, {}).setdefault(target, []).append({
                "date": date,
                "value": round(sum(values) / len(values), 2),
                "agent_count": len(values),
            })
        return self._normalize_constrained_outputs(
            {scenario: value for scenario, value in scenarios.items() if value},
            targets,
            evidence_text,
        )

    def _normalize_constrained_outputs(
        self,
        scenarios: Dict[str, Any],
        targets: List[Dict[str, Any]],
        evidence_text: str = "",
    ) -> Dict[str, Any]:
        target_names = [str(target.get("name") or "") for target in targets]
        vote_targets = [name for name in target_names if self._is_composition_target(name, "_vote_share")]
        seat_targets = [name for name in target_names if self._is_composition_target(name, "_seats")]
        total_seats = self._extract_total_count(evidence_text) or None
        exclusive_probability_targets = self._exclusive_probability_targets(target_names)

        for scenario_values in scenarios.values():
            self._normalize_points_to_total(scenario_values, vote_targets, 100.0)
            if total_seats:
                self._normalize_points_to_total(scenario_values, seat_targets, total_seats, integer=True, use_residual=False)
            self._normalize_points_to_total(scenario_values, exclusive_probability_targets, 100.0, use_residual=False)
            self._align_probability_outputs_with_counts(scenario_values, target_names, seat_targets)
        return scenarios

    def _exclusive_probability_targets(self, target_names: List[str]) -> List[str]:
        """Return probability targets that describe mutually exclusive outcomes."""
        selected = []
        for name in target_names:
            lowered = name.lower()
            if not lowered.startswith("probability_"):
                continue
            if any(term in lowered for term in ["cross", "above", "below", "over", "under", "threshold"]):
                continue
            if any(term in lowered for term in ["majority", "win", "winner", "hung", "draw", "no_majority", "plurality"]):
                selected.append(name)
        return selected if len(selected) >= 2 else []

    def _align_probability_outputs_with_counts(
        self,
        scenario_values: Dict[str, Any],
        target_names: List[str],
        count_targets: List[str],
    ) -> None:
        """Keep probability outputs consistent with related count/seat paths."""
        if not count_targets:
            return
        probability_targets = [name for name in target_names if name.lower().startswith("probability_")]
        if not probability_targets:
            return
        dates = sorted({
            str(point.get("date"))
            for target in count_targets
            for point in scenario_values.get(target, []) or []
            if point.get("date") is not None
        })
        for date in dates:
            counts = {}
            for target in count_targets:
                point = next((p for p in scenario_values.get(target, []) or [] if str(p.get("date")) == date), None)
                if point is not None:
                    counts[target[: -len("_seats")]] = float(point.get("value") or 0)
            if len(counts) < 2:
                continue
            total = sum(counts.values())
            if total <= 0:
                continue
            majority = total / 2.0 + 0.5
            winner, winner_count = max(counts.items(), key=lambda item: item[1])
            margin = winner_count - majority

            exclusive_targets = self._exclusive_probability_targets(probability_targets)
            if exclusive_targets:
                assigned = {}
                hung_target = next((target for target in exclusive_targets if "hung" in target.lower() or "no_majority" in target.lower()), None)
                actor_targets = [
                    target for target in exclusive_targets
                    if target != hung_target
                ]
                if margin >= 0:
                    winner_probability = min(82.0, max(55.0, 58.0 + margin / max(total, 1.0) * 220.0))
                    hung_probability = max(8.0, min(32.0, 28.0 - margin / max(total, 1.0) * 90.0))
                    remaining = max(0.0, 100.0 - winner_probability - hung_probability)
                    other_actor_targets = [
                        target for target in actor_targets
                        if not self._probability_target_matches_actor(target, winner)
                    ]
                    for target in actor_targets:
                        assigned[target] = winner_probability if self._probability_target_matches_actor(target, winner) else (
                            remaining / max(1, len(other_actor_targets))
                        )
                    if hung_target:
                        assigned[hung_target] = hung_probability
                else:
                    hung_probability = min(78.0, max(45.0, 52.0 + abs(margin) / max(total, 1.0) * 120.0))
                    remaining = max(0.0, 100.0 - hung_probability)
                    for target in actor_targets:
                        actor = self._actor_from_probability_target(target)
                        count = counts.get(actor, 0.0)
                        assigned[target] = remaining * count / max(1.0, sum(counts.get(self._actor_from_probability_target(item), 0.0) for item in actor_targets))
                    if hung_target:
                        assigned[hung_target] = hung_probability
                self._write_probability_points(scenario_values, assigned, date)

            threshold_assignments = {}
            for target in probability_targets:
                match = re.search(r"^probability_(.+?)_cross(?:es)?_(\d{1,5})(?:_[a-z][a-z0-9_]*)?$", target.lower())
                if not match:
                    continue
                actor = match.group(1)
                threshold = float(match.group(2))
                count = counts.get(actor)
                if count is None:
                    continue
                gap = count - threshold
                probability = 50.0 + gap / max(total, 1.0) * 180.0
                threshold_assignments[target] = max(5.0, min(95.0, probability))
            self._write_probability_points(scenario_values, threshold_assignments, date)

    def _actor_from_probability_target(self, target: str) -> str:
        label = target.lower().replace("probability_", "", 1)
        label = re.sub(r"_cross(?:es)?_\d{1,5}(?:_[a-z][a-z0-9_]*)?$", "", label)
        label = re.sub(r"_(?:majority|win|winner|plurality)$", "", label)
        return label

    def _probability_target_matches_actor(self, target: str, actor: str) -> bool:
        return self._actor_from_probability_target(target) == actor

    def _write_probability_points(self, scenario_values: Dict[str, Any], assignments: Dict[str, float], date: str) -> None:
        for target, value in assignments.items():
            for point in scenario_values.get(target, []) or []:
                if str(point.get("date")) == date:
                    point["value"] = round(float(value), 2)
                    point["probability_aligned_to_counts"] = True
                    break

    def _normalize_points_to_total(
        self,
        scenario_values: Dict[str, Any],
        target_names: List[str],
        total: float,
        integer: bool = False,
        use_residual: bool = True,
    ) -> None:
        if len(target_names) < 2:
            return
        dates = sorted({
            str(point.get("date"))
            for target in target_names
            for point in scenario_values.get(target, []) or []
            if point.get("date") is not None
        })
        for date in dates:
            points = []
            point_targets = []
            for target in target_names:
                for point in scenario_values.get(target, []) or []:
                    if str(point.get("date")) == date:
                        points.append(point)
                        point_targets.append(target)
                        break
            residual_idx = next(
                (
                    idx for idx, target in enumerate(point_targets)
                    if target[: -len("_vote_share")] in {"other", "others"} or target[: -len("_seats")] in {"other", "others"}
                ),
                None,
            )
            if use_residual and residual_idx is not None and len(points) == len(target_names):
                subtotal_without_residual = sum(
                    float(point.get("value") or 0)
                    for idx, point in enumerate(points)
                    if idx != residual_idx
                )
                if 0 <= subtotal_without_residual <= total:
                    if integer:
                        rounded_non_residual = 0
                        for idx, point in enumerate(points):
                            if idx == residual_idx:
                                continue
                            rounded_value = int(round(float(point.get("value") or 0)))
                            point["value"] = max(0, rounded_value)
                            rounded_non_residual += rounded_value
                        points[residual_idx]["value"] = max(0, int(round(total)) - rounded_non_residual)
                    else:
                        residual_value = total - subtotal_without_residual
                        points[residual_idx]["value"] = round(residual_value, 2)
                    continue
            subtotal = sum(float(point.get("value") or 0) for point in points)
            if subtotal <= 0:
                continue
            scaled = [float(point.get("value") or 0) * total / subtotal for point in points]
            if integer:
                rounded = [int(round(value)) for value in scaled]
                delta = int(round(total)) - sum(rounded)
                if rounded:
                    rounded[0] += delta
                for point, value in zip(points, rounded):
                    point["value"] = max(0, value)
            else:
                for point, value in zip(points, scaled):
                    point["value"] = round(max(0.0, value), 2)

    def _final_outcome_summary(
        self,
        scenario_outputs: Dict[str, Any],
        targets: List[Dict[str, Any]],
        evidence_text: str,
    ) -> Dict[str, Any]:
        base_key = next((key for key in scenario_outputs if "base" in key.lower()), next(iter(scenario_outputs), ""))
        base = scenario_outputs.get(base_key) or {}
        seat_targets = [str(target.get("name") or "") for target in targets if self._is_composition_target(str(target.get("name") or ""), "_seats")]
        vote_targets = [str(target.get("name") or "") for target in targets if self._is_composition_target(str(target.get("name") or ""), "_vote_share")]
        final: Dict[str, Any] = {
            "scenario": base_key,
            "target_forecast": {},
        }
        for target in [str(item.get("name") or "") for item in targets if item.get("name")]:
            points = base.get(target) or []
            if points:
                final["target_forecast"][target] = {
                    "date": points[-1].get("date"),
                    "value": points[-1].get("value"),
                    "agent_count": points[-1].get("agent_count"),
                    "unit": next((item.get("unit") for item in targets if item.get("name") == target), ""),
                }
        for target in vote_targets:
            points = base.get(target) or []
            if points:
                label = target[: -len("_vote_share")]
                final.setdefault("vote_share_forecast", {})[label] = points[-1].get("value")
        for target in seat_targets:
            points = base.get(target) or []
            if points:
                label = target[: -len("_seats")]
                final.setdefault("seat_forecast", {})[label] = points[-1].get("value")
        if final.get("seat_forecast"):
            winner, seats = max(final["seat_forecast"].items(), key=lambda item: float(item[1] or 0))
            total = self._extract_total_count(evidence_text) or sum(float(value or 0) for value in final["seat_forecast"].values())
            majority = int(total // 2 + 1) if total else None
            final["projected_winner"] = winner
            final["projected_winner_seats"] = seats
            final["majority_mark"] = majority
            final["majority_status"] = "majority" if majority and float(seats or 0) >= majority else "plurality_or_hung"
        return final

    def _is_composition_target(self, name: str, suffix: str) -> bool:
        if not name.endswith(suffix):
            return False
        label = name[: -len(suffix)]
        blocked = ["statewide", "overall", "regional", "crosses", "threshold", "probability", "scenario"]
        return bool(label) and not any(term in label for term in blocked)

    def _aggregate_targets(self, agent_outputs: List[Dict[str, Any]]) -> Dict[str, Any]:
        output: Dict[str, Any] = {}
        for agent_output in agent_outputs:
            target = agent_output.get("target_variable")
            output.setdefault(target, {"points": 0, "agents": set()})
            output[target]["points"] += len(agent_output.get("forecast_path") or [])
            if agent_output.get("agent_id"):
                output[target]["agents"].add(agent_output["agent_id"])
        return {
            target: {"points": value["points"], "agent_count": len(value["agents"])}
            for target, value in output.items()
        }

    def _agent_disagreement(self, agent_outputs: List[Dict[str, Any]]) -> Dict[str, Any]:
        buckets: Dict[Tuple[str, str], List[float]] = {}
        for output in agent_outputs:
            target = output.get("target_variable")
            for point in output.get("forecast_path") or []:
                buckets.setdefault((target, point.get("scenario")), []).append(float(point.get("value")))
        return {
            f"{target}:{scenario}": {
                "min": round(min(values), 2),
                "max": round(max(values), 2),
                "spread": round(max(values) - min(values), 2),
            }
            for (target, scenario), values in buckets.items()
            if values
        }

    def _extract_debate_revisions(self, transcript: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        revisions = []
        for turn in transcript:
            if turn.get("turn_type") != "mediated_revision":
                continue
            metadata = turn.get("metadata") or {}
            provisional = metadata.get("provisional_value")
            if provisional in (None, "unresolved"):
                continue
            revisions.append({
                "pocket_id": turn.get("pocket_id"),
                "pocket_label": metadata.get("pocket_label") or turn.get("pocket_label"),
                "agent_id": metadata.get("claimant_agent_id"),
                "agent_name": metadata.get("claimant_agent_name"),
                "challenger_agent_id": metadata.get("challenger_agent_id"),
                "challenger_agent_name": metadata.get("challenger_agent_name"),
                "target_variable": metadata.get("contested_target"),
                "original_base": metadata.get("original_base"),
                "challenger_base": metadata.get("challenger_base"),
                "revised_base": provisional,
                "spread": metadata.get("spread"),
                "source_turn_id": turn.get("turn_id"),
                "reason": "mediated debate revision",
            })
        return revisions

    def _apply_debate_revisions(
        self,
        agent_outputs: List[Dict[str, Any]],
        revisions: List[Dict[str, Any]],
        time_pockets: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        if not revisions:
            return agent_outputs
        pocket_order = {
            (pocket.get("end") if pocket.get("end") != "auto" else pocket.get("label") or pocket.get("pocket_id")): idx
            for idx, pocket in enumerate(time_pockets)
        }
        revisions_by_key: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
        for revision in revisions:
            agent_id = revision.get("agent_id")
            target = revision.get("target_variable")
            if agent_id and target:
                revisions_by_key.setdefault((agent_id, target), []).append(revision)

        final_pocket_idx = max(0, len(time_pockets) - 1)
        updated_outputs = []
        for output in agent_outputs:
            output = dict(output)
            key = (output.get("agent_id"), output.get("target_variable"))
            relevant = revisions_by_key.get(key, [])
            if not relevant:
                updated_outputs.append(output)
                continue
            path = [dict(point) for point in output.get("forecast_path") or []]
            adjustments = []
            for revision in relevant:
                original = revision.get("original_base")
                revised = revision.get("revised_base")
                try:
                    delta = float(revised) - float(original)
                except (TypeError, ValueError):
                    continue
                if abs(delta) < 0.01:
                    continue
                adjustments.append({
                    "pocket_label": revision.get("pocket_label"),
                    "pocket_idx": pocket_order.get(revision.get("pocket_label"), 0),
                    "delta": delta,
                    "revision": revision,
                })
            if not adjustments:
                updated_outputs.append(output)
                continue
            max_period_idx = max(
                [int(point.get("period_index") or 0) for point in path] or [final_pocket_idx]
            )
            for point in path:
                # Forecast dates often represent the requested future horizon,
                # not a debate-pocket label. Unknown dates should still receive
                # accumulated debate revisions rather than silently staying
                # unchanged.
                try:
                    point_idx = int(point.get("period_index"))
                except (TypeError, ValueError):
                    point_idx = pocket_order.get(point.get("date"), final_pocket_idx)
                total_delta = 0.0
                for adjustment in adjustments:
                    debate_threshold = self._map_pocket_index_to_period_index(
                        adjustment["pocket_idx"],
                        final_pocket_idx,
                        max_period_idx,
                    )
                    if point_idx < debate_threshold:
                        continue
                    distance = point_idx - debate_threshold
                    carry = max(0.25, 1.0 - distance * 0.12)
                    scenario_weight = self._scenario_revision_weight(point.get("scenario"))
                    total_delta += adjustment["delta"] * carry * scenario_weight
                if total_delta:
                    point["pre_debate_value"] = point.get("value")
                    point["value"] = self._bounded_value(point.get("value"), total_delta, point.get("unit"))
                    point["debate_adjusted"] = True
            output["forecast_path"] = path
            output["debate_revisions_applied"] = relevant
            updated_outputs.append(output)
        return updated_outputs

    def _map_pocket_index_to_period_index(self, pocket_idx: int, final_pocket_idx: int, max_period_idx: int) -> int:
        if final_pocket_idx <= 0 or max_period_idx <= 0:
            return 0
        ratio = max(0.0, min(1.0, float(pocket_idx) / float(final_pocket_idx)))
        return int(round(ratio * max_period_idx))

    def _scenario_revision_weight(self, scenario: str) -> float:
        scenario_l = str(scenario or "").lower()
        if "base" in scenario_l or "central" in scenario_l or "most_likely" in scenario_l:
            return 1.0
        if any(term in scenario_l for term in ["tail", "shock", "crisis", "collapse"]):
            return 0.5
        if any(term in scenario_l for term in ["downside", "adverse", "weak", "low", "fragment", "hung"]):
            return 0.75
        return 0.65

    def _bounded_value(self, current_value: Any, delta: float, unit: str) -> float:
        try:
            current = float(current_value)
        except (TypeError, ValueError):
            current = 0.0
        lowered = str(unit or "").lower()
        capped_delta = delta
        if any(term in lowered for term in ["percent", "probability", "index", "share", "rate"]):
            capped_delta = max(-8.0, min(8.0, delta))
        elif any(term in lowered for term in ["usd", "currency", "price", "cost", "revenue", "capex"]):
            cap = max(1.0, abs(current) * 0.18)
            capped_delta = max(-cap, min(cap, delta))
        elif any(term in lowered for term in ["month", "count", "seat", "number"]):
            cap = max(1.0, abs(current) * 0.20)
            capped_delta = max(-cap, min(cap, delta))
        value = current + capped_delta
        if any(term in lowered for term in ["percent", "probability", "index", "share", "rate"]):
            return round(max(0.0, min(100.0, value)), 2)
        return round(max(0.0, value), 2)

    def _debate_impact_summary(self, revisions: List[Dict[str, Any]]) -> Dict[str, Any]:
        by_target: Dict[str, Dict[str, Any]] = {}
        for revision in revisions:
            target = revision.get("target_variable") or "unknown"
            bucket = by_target.setdefault(target, {"count": 0, "absolute_delta": 0.0, "agents": set()})
            bucket["count"] += 1
            try:
                bucket["absolute_delta"] += abs(float(revision.get("revised_base")) - float(revision.get("original_base")))
            except (TypeError, ValueError):
                pass
            if revision.get("agent_name"):
                bucket["agents"].add(revision.get("agent_name"))
        return {
            "revision_count": len(revisions),
            "by_target": {
                target: {
                    "count": value["count"],
                    "absolute_delta": round(value["absolute_delta"], 2),
                    "agents": sorted(value["agents"]),
                }
                for target, value in by_target.items()
            },
        }

    def _state_snapshot(self, targets: List[Dict[str, Any]], pocket_idx: int, prior: bool) -> Dict[str, Any]:
        offset = pocket_idx * 5 + (0 if prior else 3)
        return {
            "evidence_strength": min(100, 45 + offset),
            "actor_alignment": max(0, 55 - offset / 2),
            "uncertainty_pressure": max(0, 65 - offset / 3),
            "tracked_targets": [target.get("name") for target in targets[:10]],
            "created_at": datetime.now().isoformat(),
        }

    def _agent_action(self, agent: Dict[str, Any], pocket: Dict[str, Any], domain_plan: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "agent_id": agent.get("agent_id"),
            "agent_name": agent.get("name"),
            "pocket_id": pocket.get("pocket_id"),
            "action_type": "interpret_and_update",
            "summary": self._agent_argument(agent, pocket, domain_plan, [], {}),
        }

    def _cross_agent_interactions(self, agents: List[Dict[str, Any]], pocket: Dict[str, Any]) -> List[Dict[str, Any]]:
        if len(agents) < 2:
            return []
        return [{
            "pocket_id": pocket.get("pocket_id"),
            "source_agent_id": agents[0].get("agent_id"),
            "target_agent_id": agents[-1].get("agent_id"),
            "interaction_type": "moderated_challenge",
            "summary": "Moderator/mediator prompts agents to explain disagreement and evidence needs.",
        }]

    def _triggered_revisions(self, targets: List[Dict[str, Any]], pocket_idx: int) -> List[Dict[str, Any]]:
        return [
            {
                "target_variable": target.get("name"),
                "revision_reason": f"Sequential pocket {pocket_idx + 1} changed the evidence/state snapshot.",
            }
            for target in targets[:5]
        ]

    def _build_discussion_transcript(
        self,
        state: StructuredSimulationState,
        targets: List[Dict[str, Any]],
        evidence_text: str,
        graph_brain: Dict[str, Any] | None = None,
    ) -> List[Dict[str, Any]]:
        transcript: List[Dict[str, Any]] = []
        output_index = self._index_agent_outputs(state.agent_outputs)
        graph_brain = graph_brain or {}
        evidence_notes = self._extract_evidence_notes(evidence_text)
        evidence_notes = [
            note for note in list(dict.fromkeys((graph_brain.get("evidence_cards") or []) + evidence_notes))
            if not self._is_bad_evidence_card(note)
        ]
        if not evidence_notes:
            evidence_notes = [
                "No clean factual evidence cards were extracted; treat this run as prompt-only and mark factual confidence low."
            ]
        key_targets = [target.get("name") for target in targets[:6] if target.get("name")]
        relationship_topology = state.aggregated_outputs.get("relationship_topology") or []

        for pocket_idx, pocket in enumerate(state.time_pockets):
            pocket_id = pocket.get("pocket_id")
            label = pocket.get("label")
            transcript.append(self._turn(
                pocket_id,
                label,
                "moderator",
                "Simulation Moderator",
                (
                    f"We are entering `{label}`. Stay inside the user's cutoff and argue against "
                    f"the target variables: {', '.join(key_targets)}. Each causal agent should "
                    "state what evidence moves its forecast, where it disagrees, and what number "
                    "it would revise next."
                ),
                turn_type="frame",
            ))
            transcript.append(self._turn(
                pocket_id,
                label,
                "thesis",
                "Forecast Thesis",
                self._thesis_presentation_turn(state.forecast_thesis, state.assumption_registry, state.dispute_registry),
                turn_type="thesis_presentation",
                metadata={
                    "thesis_id": state.forecast_thesis.get("thesis_id"),
                    "linked_targets": state.forecast_thesis.get("linked_targets", []),
                },
            ))
            transcript.append(self._turn(
                pocket_id,
                label,
                "graph_brain",
                "Signal Map Conscience",
                self._graph_conscience_turn(graph_brain, label, key_targets),
                turn_type="graph_intervention",
                metadata={
                    "graph_id": graph_brain.get("graph_id"),
                    "actor_count": graph_brain.get("actor_count"),
                    "target_count": graph_brain.get("target_count"),
                    "numeric_fact_count": graph_brain.get("numeric_fact_count"),
                    "missing_target_evidence": graph_brain.get("missing_target_evidence", []),
                },
            ))
            transcript.append(self._turn(
                pocket_id,
                label,
                "research",
                "External Research Scout",
                self._research_turn(evidence_notes, pocket_idx, label),
                turn_type="evidence_injection",
            ))
            transcript.append(self._turn(
                pocket_id,
                label,
                "data",
                "Data Retrieval Analyst",
                self._data_turn(evidence_text, targets, pocket_idx, label),
                turn_type="data_extraction",
            ))

            causal_agents = [
                agent for agent in state.agents
                if agent.get("agent_kind") not in {"orchestrator", "research"}
            ]
            causal_context = []
            for agent in causal_agents:
                forecast_sample = self._forecast_sample(
                    output_index.get(agent.get("agent_id"), []),
                    label,
                    agent,
                )
                rag_adjustment = self._agent_rag_adjustment(
                    agent=agent,
                    graph_brain=graph_brain,
                    evidence_notes=evidence_notes,
                    target=forecast_sample.get("target_variable") or "",
                    pocket_label=label,
                )
                causal_context.append({
                    "agent": agent,
                    "forecast_sample": forecast_sample,
                    "rag_adjustment": rag_adjustment,
                })
                transcript.append(self._turn(
                    pocket_id,
                    label,
                    agent.get("agent_id"),
                    agent.get("name"),
                    self._agent_argument(agent, pocket, state.domain_plan, evidence_notes, forecast_sample, rag_adjustment),
                    turn_type="agent_argument",
                    metadata={
                        "role": agent.get("role"),
                        "target_variable": forecast_sample.get("target_variable"),
                        "base_value": forecast_sample.get("base"),
                        "downside_value": forecast_sample.get("downside"),
                        "confidence": forecast_sample.get("confidence"),
                        "rag_adjustment": rag_adjustment,
                    },
                ))

            for round_turn in self._debate_rounds(
                pocket=pocket,
                causal_context=causal_context,
                evidence_notes=evidence_notes,
                relationship_topology=relationship_topology,
                disputes=state.dispute_registry,
            ):
                transcript.append(round_turn)

            transcript.append(self._turn(
                pocket_id,
                label,
                "moderator",
                "Simulation Moderator",
                self._moderator_evaluation_turn(causal_context, evidence_notes, label),
                turn_type="moderator_evaluation",
            ))
            transcript.append(self._turn(
                pocket_id,
                label,
                "mediator",
                "Negotiation Mediator",
                self._mediator_turn(causal_agents, label),
                turn_type="challenge_summary",
            ))
            transcript.append(self._turn(
                pocket_id,
                label,
                "auditor",
                "Evidence Auditor",
                self._auditor_turn(state.domain_plan, evidence_notes, causal_context, label),
                turn_type="evidence_audit",
            ))
            transcript.append(self._turn(
                pocket_id,
                label,
                "quant",
                "Quantitative Synthesizer",
                self._quant_turn(state.scenario_outputs, label, key_targets),
                turn_type="numeric_synthesis",
            ))
        return transcript

    def _graph_conscience_turn(self, graph_brain: Dict[str, Any], pocket_label: str, key_targets: List[str]) -> str:
        if not graph_brain:
            return (
                f"Graph conscience for `{pocket_label}`: no graph context was passed into this run. "
                "Treat this as degraded mode; auditor and research scout must explicitly label missing evidence."
            )
        actors = ", ".join((graph_brain.get("actor_roster_preview") or [])[:10]) or "no actor roster"
        warnings = " | ".join(graph_brain.get("warnings") or []) or "no graph warnings"
        interventions = " | ".join((graph_brain.get("intervention_plan") or [])[:4])
        evidence_preview = " | ".join((graph_brain.get("evidence_cards") or [])[:3]) or "no evidence cards extracted"
        return (
            f"Graph conscience for `{pocket_label}`: I see `{graph_brain.get('actor_count', 0)}` actor nodes, "
            f"`{graph_brain.get('target_count', 0)}` target variables, `{graph_brain.get('numeric_fact_count', 0)}` numeric facts, "
            f"and `{graph_brain.get('evidence_claim_count', 0)}` evidence claims. Actor lane preview: {actors}. "
            f"Target lane preview: {', '.join(key_targets)}. Evidence preview: {evidence_preview}. "
            f"Warnings: {warnings}. Intervention order: {interventions}. "
            "If a speaker drifts away from these lanes, I will force a correction before the next pocket."
        )

    def _thesis_presentation_turn(
        self,
        thesis: Dict[str, Any],
        assumptions: List[Dict[str, Any]],
        disputes: List[Dict[str, Any]],
    ) -> str:
        statement = thesis.get("statement") or "No forecast thesis was created."
        drivers = ", ".join(str(item) for item in (thesis.get("core_drivers") or [])[:5]) or "no core drivers identified"
        assumption_line = "; ".join(
            str(item.get("statement") or "")
            for item in (assumptions or [])[:3]
            if item.get("statement")
        ) or "no assumptions identified"
        dispute_line = "; ".join(
            str(item.get("question") or "")
            for item in (disputes or [])[:3]
            if item.get("question")
        ) or "no disputes identified"
        return (
            f"Opening thesis: {statement} Drivers on the table: {drivers}. "
            f"Assumptions to test: {assumption_line}. "
            f"Disputes that must create cross-questioning, concession, or revision: {dispute_line}."
        )

    def _debate_rounds(
        self,
        pocket: Dict[str, Any],
        causal_context: List[Dict[str, Any]],
        evidence_notes: List[str],
        relationship_topology: List[Dict[str, Any]],
        disputes: List[Dict[str, Any]] | None = None,
    ) -> List[Dict[str, Any]]:
        turns: List[Dict[str, Any]] = []
        pocket_id = pocket.get("pocket_id")
        label = pocket.get("label")
        pairs = self._select_debate_pairs(causal_context, relationship_topology)
        disputes = disputes or []

        for round_idx, (claimant_ctx, challenger_ctx) in enumerate(pairs, start=1):
            dispute = disputes[(round_idx - 1) % len(disputes)] if disputes else {}
            claimant = claimant_ctx.get("agent") or {}
            challenger = challenger_ctx.get("agent") or {}
            claimant_sample = claimant_ctx.get("forecast_sample") or {}
            challenger_sample = challenger_ctx.get("forecast_sample") or {}
            if claimant_sample.get("base") in (None, "") and challenger_sample.get("base") not in (None, ""):
                claimant, challenger = challenger, claimant
                claimant_sample, challenger_sample = challenger_sample, claimant_sample
            if claimant_sample.get("base") not in (None, "") and challenger_sample.get("base") in (None, ""):
                challenger_sample = dict(challenger_sample)
                challenger_sample["target_variable"] = claimant_sample.get("target_variable")
                challenger_sample["base"] = self._qualitative_challenge_value(
                    claimant_sample.get("base"),
                    challenger,
                    round_idx,
                )
                challenger_sample["confidence"] = self._qualitative_challenge_confidence(challenger)
            contested_target = (
                ((dispute.get("linked_targets") or [None])[0] if dispute else None)
                or claimant_sample.get("target_variable")
                or challenger_sample.get("target_variable")
                or "primary_outcome"
            )
            turns.append(self._turn(
                pocket_id,
                label,
                challenger.get("agent_id"),
                challenger.get("name"),
                self._challenge_message(
                    claimant=claimant,
                    challenger=challenger,
                    claimant_sample=claimant_sample,
                    challenger_sample=challenger_sample,
                    contested_target=contested_target,
                    evidence_notes=evidence_notes,
                    relationship=self._relationship_between(claimant, challenger, relationship_topology),
                    round_idx=round_idx,
                ),
                turn_type="challenge",
                metadata={
                    "round": round_idx,
                    "dispute_id": dispute.get("dispute_id"),
                    "dispute_question": dispute.get("question"),
                    "linked_assumptions": dispute.get("linked_assumptions") or [],
                    "target_agent_id": claimant.get("agent_id"),
                    "contested_target": contested_target,
                    "claimant_base": claimant_sample.get("base"),
                    "challenger_base": challenger_sample.get("base"),
                },
            ))
            turns.append(self._turn(
                pocket_id,
                label,
                "moderator",
                "Simulation Moderator",
                self._moderator_cross_question(
                    claimant=claimant,
                    challenger=challenger,
                    claimant_sample=claimant_sample,
                    challenger_sample=challenger_sample,
                    contested_target=contested_target,
                    round_idx=round_idx,
                ),
                turn_type="moderator_cross_question",
                metadata={
                    "round": round_idx,
                    "dispute_id": dispute.get("dispute_id"),
                    "contested_target": contested_target,
                },
            ))
            turns.append(self._turn(
                pocket_id,
                label,
                "research",
                "External Research Scout",
                self._research_check_turn(
                    challenger=challenger,
                    contested_target=contested_target,
                    evidence_notes=evidence_notes,
                    pocket_label=label,
                ),
                turn_type="research_check",
                metadata={
                    "round": round_idx,
                    "dispute_id": dispute.get("dispute_id"),
                    "contested_target": contested_target,
                },
            ))
            turns.append(self._turn(
                pocket_id,
                label,
                claimant.get("agent_id"),
                claimant.get("name"),
                self._rebuttal_message(
                    claimant=claimant,
                    challenger=challenger,
                    claimant_sample=claimant_sample,
                    challenger_sample=challenger_sample,
                    contested_target=contested_target,
                    evidence_notes=evidence_notes,
                    relationship=self._relationship_between(challenger, claimant, relationship_topology),
                    round_idx=round_idx,
                ),
                turn_type="rebuttal",
                metadata={
                    "round": round_idx,
                    "dispute_id": dispute.get("dispute_id"),
                    "challenger_agent_id": challenger.get("agent_id"),
                    "contested_target": contested_target,
                },
            ))
            turns.append(self._turn(
                pocket_id,
                label,
                claimant.get("agent_id"),
                claimant.get("name"),
                self._concession_message(
                    claimant=claimant,
                    challenger=challenger,
                    claimant_sample=claimant_sample,
                    challenger_sample=challenger_sample,
                    contested_target=contested_target,
                    round_idx=round_idx,
                ),
                turn_type="concession",
                metadata={
                    "round": round_idx,
                    "dispute_id": dispute.get("dispute_id"),
                    "contested_target": contested_target,
                    "linked_assumptions": dispute.get("linked_assumptions") or [],
                },
            ))
            turns.append(self._turn(
                pocket_id,
                label,
                "quant",
                "Quantitative Synthesizer",
                self._quant_check_turn(
                    claimant=claimant,
                    challenger=challenger,
                    claimant_sample=claimant_sample,
                    challenger_sample=challenger_sample,
                    contested_target=contested_target,
                    round_idx=round_idx,
                ),
                turn_type="quant_check",
                metadata={
                    "round": round_idx,
                    "dispute_id": dispute.get("dispute_id"),
                    "contested_target": contested_target,
                },
            ))
            turns.append(self._turn(
                pocket_id,
                label,
                "mediator",
                "Negotiation Mediator",
                self._revision_note(
                    claimant=claimant,
                    challenger=challenger,
                    claimant_sample=claimant_sample,
                    challenger_sample=challenger_sample,
                    contested_target=contested_target,
                    round_idx=round_idx,
                ),
                turn_type="mediated_revision",
                metadata={
                    "round": round_idx,
                    "dispute_id": dispute.get("dispute_id"),
                    "claimant_agent_id": claimant.get("agent_id"),
                    "claimant_agent_name": claimant.get("name"),
                    "challenger_agent_id": challenger.get("agent_id"),
                    "challenger_agent_name": challenger.get("name"),
                    "contested_target": contested_target,
                    "pocket_label": label,
                    "original_base": claimant_sample.get("base"),
                    "challenger_base": challenger_sample.get("base"),
                    "provisional_value": self._provisional_revision(
                        claimant_sample.get("base"),
                        challenger_sample.get("base"),
                        round_idx,
                    ),
                    "spread": self._numeric_spread(
                        claimant_sample.get("base"),
                        challenger_sample.get("base"),
                    ),
                },
            ))
        return turns

    def _select_debate_pairs(
        self,
        causal_context: List[Dict[str, Any]],
        relationship_topology: List[Dict[str, Any]],
    ) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
        pairs = []
        seen = set()
        by_id = {
            (ctx.get("agent") or {}).get("agent_id"): ctx
            for ctx in causal_context
            if (ctx.get("agent") or {}).get("agent_id")
        }
        numeric_contexts = [
            ctx for ctx in causal_context
            if (ctx.get("forecast_sample") or {}).get("base") not in (None, "")
        ]
        non_numeric_contexts = [
            ctx for ctx in causal_context
            if (ctx.get("forecast_sample") or {}).get("base") in (None, "")
        ]
        for edge in relationship_topology:
            if edge.get("relationship_type") not in {"opposes", "challenges_assumption", "audits", "information_gap"}:
                continue
            claimant = by_id.get(edge.get("target_agent_id"))
            challenger = by_id.get(edge.get("source_agent_id"))
            if not claimant or not challenger:
                continue
            claimant_has_number = (claimant.get("forecast_sample") or {}).get("base") not in (None, "")
            challenger_has_number = (challenger.get("forecast_sample") or {}).get("base") not in (None, "")
            if not claimant_has_number and challenger_has_number:
                claimant, challenger = challenger, claimant
            elif not claimant_has_number and not challenger_has_number:
                continue
            key = (edge.get("target_agent_id"), edge.get("source_agent_id"))
            if key in seen:
                continue
            seen.add(key)
            pairs.append((claimant, challenger))
            if len(pairs) >= 4:
                break

        for claimant in numeric_contexts:
            challenger = self._best_generic_challenger(claimant, non_numeric_contexts or causal_context, seen)
            if not challenger:
                continue
            claimant_id = (claimant.get("agent") or {}).get("agent_id")
            challenger_id = (challenger.get("agent") or {}).get("agent_id")
            key = (claimant_id, challenger_id)
            if claimant_id and challenger_id and claimant_id != challenger_id and key not in seen:
                pairs.append((claimant, challenger))
                seen.add(key)
            if len(pairs) >= 4:
                break

        if len(pairs) < 3:
            ranked = sorted(
                causal_context,
                key=lambda ctx: self._debate_strength((ctx.get("agent") or {})),
                reverse=True,
            )
            for idx, claimant in enumerate(ranked):
                challenger = self._best_generic_challenger(claimant, ranked, seen)
                if not challenger:
                    continue
                claimant_id = (claimant.get("agent") or {}).get("agent_id")
                challenger_id = (challenger.get("agent") or {}).get("agent_id")
                key = (claimant_id, challenger_id)
                if claimant_id and challenger_id and claimant_id != challenger_id and key not in seen:
                    pairs.append((claimant, challenger))
                    seen.add(key)
                if len(pairs) >= 6:
                    break
        return pairs

    def _qualitative_challenge_value(self, claimant_base: Any, challenger: Dict[str, Any], round_idx: int) -> float:
        try:
            base = float(claimant_base)
        except (TypeError, ValueError):
            base = 50.0
        text = " ".join([
            str(challenger.get("name") or ""),
            str(challenger.get("role") or ""),
            str(challenger.get("causal_power") or ""),
            str(challenger.get("institutional_incentives") or ""),
        ]).lower()
        pressure = 0.0
        if any(term in text for term in ["voter", "beneficiary", "worker", "household", "rural", "urban", "minority", "youth"]):
            pressure += 3.0
        if any(term in text for term in ["watchdog", "auditor", "corruption", "risk", "skeptic"]):
            pressure -= 4.0
        if any(term in text for term in ["strategist", "campaign", "opposition", "surge", "contest"]):
            pressure += 2.0 if round_idx % 2 else -2.0
        pressure += self._stable_jitter(challenger.get("agent_id"), "qualitative_challenge", round_idx, span=2.0)
        return round(max(0.0, min(100.0, base + pressure)), 2)

    def _qualitative_challenge_confidence(self, challenger: Dict[str, Any]) -> float:
        cognitive = challenger.get("cognitive_profile") if isinstance(challenger.get("cognitive_profile"), dict) else {}
        local = float(cognitive.get("local_knowledge_score") or 50)
        eq = float(cognitive.get("eq_score") or 50)
        return round(max(0.35, min(0.78, (local * 0.006 + eq * 0.004))), 2)

    def _debate_strength(self, agent: Dict[str, Any]) -> float:
        cognitive = agent.get("cognitive_profile") if isinstance(agent.get("cognitive_profile"), dict) else {}
        stakes = agent.get("stakes_profile") if isinstance(agent.get("stakes_profile"), dict) else {}
        return (
            float(cognitive.get("game_theory_score") or 50) * 0.35
            + float(cognitive.get("domain_expertise_score") or 50) * 0.25
            + float(cognitive.get("local_knowledge_score") or 50) * 0.2
            + float(cognitive.get("eq_score") or 50) * 0.1
            + (10 if str(stakes.get("public_position_pressure", "")).lower() in {"high", "very high"} else 0)
        )

    def _moderator_evaluation_turn(
        self,
        causal_context: List[Dict[str, Any]],
        evidence_notes: List[str],
        label: str,
    ) -> str:
        scored = []
        for ctx in causal_context:
            agent = ctx.get("agent") or {}
            sample = ctx.get("forecast_sample") or {}
            role_l = f"{agent.get('name', '')} {agent.get('role', '')}".lower()
            evidence = self._select_fact_cards(
                role_l=role_l,
                evidence_notes=evidence_notes,
                axis="moderator evidence audit",
                target=sample.get("target_variable") or "",
                pocket_label=label,
                limit=2,
            )
            cognitive = agent.get("cognitive_profile") if isinstance(agent.get("cognitive_profile"), dict) else {}
            stakes = agent.get("stakes_profile") if isinstance(agent.get("stakes_profile"), dict) else {}
            has_numeric = sample.get("base") is not None
            evidence_score = min(100, 35 + len(evidence) * 15 + (15 if any(re.search(r"\d", fact) for fact in evidence) else 0))
            contribution_type = self._agent_contribution_type(agent)
            rag_adjustment = ctx.get("rag_adjustment") or {}
            scored.append({
                "name": agent.get("name"),
                "contribution_type": contribution_type,
                "target": sample.get("target_variable") or "non-numeric influence",
                "has_numeric": has_numeric,
                "evidence_score": evidence_score,
                "rag_confidence_delta": rag_adjustment.get("confidence_delta", 0),
                "graph_support": rag_adjustment.get("support_level", "unknown"),
                "posture": stakes.get("strategic_posture", "n/a"),
            })

        strongest = sorted(scored, key=lambda item: item["evidence_score"], reverse=True)[:4]
        weak = [item for item in scored if item["evidence_score"] < 55][:4]
        non_numeric = [item for item in scored if not item["has_numeric"]][:5]
        return (
            f"Moderator evaluation for `{label}`: I am not adopting the last speaker's view. "
            f"Strongest evidence-backed contributions: {self._compact_eval_items(strongest)}. "
            f"Weak or under-supported lanes needing follow-up: {self._compact_eval_items(weak) if weak else 'none above threshold'}. "
            f"Non-quant stakeholder signals to carry forward without forcing fake numbers: {self._compact_eval_items(non_numeric) if non_numeric else 'none'}. "
            "RAG adjustment rule: agents with direct graph support keep more confidence; agents with thin evidence are carried as hypotheses. "
            "Direction for next stage: Research Scout must find or expose missing source pointers for weak lanes; "
            "Data Retrieval Analyst must extract denominators/units; Quantitative Synthesizer may only adjust numeric paths from numeric-capable agents; "
            "common/ground agents should update behavioral pressure, trust, participation, demand, or refusal signals rather than pretend to be forecasters."
        )

    def _compact_eval_items(self, items: List[Dict[str, Any]]) -> str:
        return "; ".join(
            f"{item.get('name')} ({item.get('contribution_type')}; target={item.get('target')}; "
            f"evidence={item.get('evidence_score')}; graph support={item.get('graph_support')}; posture={item.get('posture')})"
            for item in items
        )

    def _agent_contribution_type(self, agent: Dict[str, Any]) -> str:
        numeric = (agent.get("numeric_capabilities") or {}).get("must_output_numbers", True)
        role_l = f"{agent.get('name', '')} {agent.get('role', '')}".lower()
        if numeric:
            return "numeric forecast owner"
        if any(term in role_l for term in ["voter", "consumer", "worker", "household", "community", "cohort", "beneficiary", "public"]):
            return "behavioral ground signal"
        if any(term in role_l for term in ["strategist", "campaign", "party", "executive", "operator", "trader"]):
            return "strategic incentive signal"
        if any(term in role_l for term in ["media", "journalist", "narrative"]):
            return "narrative salience signal"
        return "causal pressure signal"

    def _best_generic_challenger(
        self,
        claimant: Dict[str, Any],
        contexts: List[Dict[str, Any]],
        seen: set,
    ) -> Dict[str, Any]:
        claimant_agent = claimant.get("agent") or {}
        claimant_id = claimant_agent.get("agent_id")
        best = None
        best_score = -1.0
        for ctx in contexts:
            challenger = ctx.get("agent") or {}
            challenger_id = challenger.get("agent_id")
            if not challenger_id or challenger_id == claimant_id or (claimant_id, challenger_id) in seen:
                continue
            score = self._agent_tension_score(claimant_agent, challenger)
            if score > best_score:
                best = ctx
                best_score = score
        return best

    def _relationship_topology(self, agents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        causal_agents = [
            agent for agent in agents
            if agent.get("agent_kind") not in {"orchestrator", "research"}
        ]
        edges: List[Dict[str, Any]] = []

        def add(source: Dict[str, Any], target: Dict[str, Any], relationship_type: str, rationale: str, strength: float):
            if not source or not target or source.get("agent_id") == target.get("agent_id"):
                return
            edges.append({
                "source_agent_id": source.get("agent_id"),
                "source_agent_name": source.get("name"),
                "target_agent_id": target.get("agent_id"),
                "target_agent_name": target.get("name"),
                "relationship_type": relationship_type,
                "strength": round(max(0.1, min(1.0, strength)), 2),
                "rationale": rationale,
            })
        ranked_pairs = []
        for source in causal_agents:
            for target in causal_agents:
                if source.get("agent_id") == target.get("agent_id"):
                    continue
                score = self._agent_tension_score(target, source)
                if score >= 0.45:
                    ranked_pairs.append((score, source, target))
        ranked_pairs.sort(key=lambda item: item[0], reverse=True)
        for score, source, target in ranked_pairs[: max(8, min(24, len(causal_agents) * 2))]:
            rel_type = self._relationship_type_for_pair(source, target)
            add(source, target, rel_type, self._relationship_rationale(source, target, rel_type), score)

        seen = set()
        unique = []
        for edge in edges:
            key = (edge["source_agent_id"], edge["target_agent_id"], edge["relationship_type"])
            if key in seen:
                continue
            seen.add(key)
            unique.append(edge)
        return unique

    def _agent_tension_score(self, claimant: Dict[str, Any], challenger: Dict[str, Any]) -> float:
        claimant_tokens = set(self._role_tokens(claimant))
        challenger_tokens = set(self._role_tokens(challenger))
        overlap_penalty = min(0.25, len(claimant_tokens & challenger_tokens) * 0.04)
        c_cog = claimant.get("cognitive_profile") if isinstance(claimant.get("cognitive_profile"), dict) else {}
        h_cog = challenger.get("cognitive_profile") if isinstance(challenger.get("cognitive_profile"), dict) else {}
        c_stakes = claimant.get("stakes_profile") if isinstance(claimant.get("stakes_profile"), dict) else {}
        h_stakes = challenger.get("stakes_profile") if isinstance(challenger.get("stakes_profile"), dict) else {}
        skill_gap = abs(float(h_cog.get("game_theory_score") or 50) - float(c_cog.get("game_theory_score") or 50)) / 100
        knowledge_complement = abs(float(h_cog.get("local_knowledge_score") or 50) - float(c_cog.get("numeracy_score") or 50)) / 120
        posture_gap = 0.25 if h_stakes.get("strategic_posture") != c_stakes.get("strategic_posture") else 0.05
        pressure_gap = 0.18 if h_stakes.get("public_position_pressure") != c_stakes.get("public_position_pressure") else 0.04
        return round(max(0.0, min(1.0, 0.35 + skill_gap + knowledge_complement + posture_gap + pressure_gap - overlap_penalty)), 2)

    def _relationship_type_for_pair(self, source: Dict[str, Any], target: Dict[str, Any]) -> str:
        source_text = f"{source.get('name', '')} {source.get('role', '')}".lower()
        if any(term in source_text for term in ["audit", "watchdog", "data", "quant", "scientist", "research"]):
            return "audits"
        source_stakes = source.get("stakes_profile") if isinstance(source.get("stakes_profile"), dict) else {}
        target_stakes = target.get("stakes_profile") if isinstance(target.get("stakes_profile"), dict) else {}
        if source_stakes.get("strategic_posture") != target_stakes.get("strategic_posture"):
            return "challenges_assumption"
        return "information_gap"

    def _relationship_rationale(self, source: Dict[str, Any], target: Dict[str, Any], relationship_type: str) -> str:
        source_stakes = source.get("stakes_profile") if isinstance(source.get("stakes_profile"), dict) else {}
        target_stakes = target.get("stakes_profile") if isinstance(target.get("stakes_profile"), dict) else {}
        return (
            f"{source.get('name')} is likely to test {target.get('name')} because their incentives differ: "
            f"{source.get('name')} is exposed to `{source_stakes.get('skin_in_the_game', 'not specified')}`, "
            f"while {target.get('name')} is exposed to `{target_stakes.get('skin_in_the_game', 'not specified')}`."
        )

    def _relationship_between(
        self,
        source: Dict[str, Any],
        target: Dict[str, Any],
        relationship_topology: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        source_id = source.get("agent_id")
        target_id = target.get("agent_id")
        return next(
            (
                edge for edge in relationship_topology
                if edge.get("source_agent_id") == source_id and edge.get("target_agent_id") == target_id
            ),
            {},
        )

    def _challenge_message(
        self,
        claimant: Dict[str, Any],
        challenger: Dict[str, Any],
        claimant_sample: Dict[str, Any],
        challenger_sample: Dict[str, Any],
        contested_target: str,
        evidence_notes: List[str],
        relationship: Dict[str, Any],
        round_idx: int,
    ) -> str:
        claimant_name = claimant.get("name") or "the previous speaker"
        challenger_name = challenger.get("name") or "challenger"
        claimant_base = claimant_sample.get("base")
        claimant_downside = claimant_sample.get("downside")
        challenger_base = challenger_sample.get("base")
        axis = self._challenge_axis(claimant, challenger)
        evidence = self._select_role_evidence(
            f"{challenger.get('name', '')} {challenger.get('role', '')}".lower(),
            evidence_notes,
            target=contested_target,
        )
        fact_basis = self._fact_basis(
            f"{challenger.get('name', '')} {challenger.get('role', '')}".lower(),
            evidence_notes,
            axis,
            target=contested_target,
        )
        background = self._background_claim(challenger)
        relation = self._relationship_clause(relationship)
        pressure = self._numeric_pressure(claimant_base, challenger_base)
        evidence_status = self._evidence_status_line(evidence_notes, contested_target, challenger)
        challenger_claim = self._agent_claim_from_fields(challenger, contested_target)
        return (
            f"{challenger_name}: {claimant_name}, I do not buy your mark on `{contested_target}` as stated. "
            f"You are sitting at `{claimant_base}`; my lane pulls it toward `{challenger_base}`. {evidence_status} "
            f"My concrete objection is: {challenger_claim}. "
            f"The weak link is {axis}. {fact_basis} "
            f"Your adverse case `{claimant_downside}` still does not explain the mechanism; {pressure}. "
            "Answer directly: name the first assumption you would cut if my lane turns out to be right."
        )

    def _rebuttal_message(
        self,
        claimant: Dict[str, Any],
        challenger: Dict[str, Any],
        claimant_sample: Dict[str, Any],
        challenger_sample: Dict[str, Any],
        contested_target: str,
        evidence_notes: List[str],
        relationship: Dict[str, Any],
        round_idx: int,
    ) -> str:
        claimant_name = claimant.get("name") or "claimant"
        challenger_name = challenger.get("name") or "challenger"
        claimant_base = claimant_sample.get("base")
        challenger_base = challenger_sample.get("base")
        revised = self._provisional_revision(claimant_base, challenger_base, round_idx)
        axis = self._challenge_axis(challenger, claimant)
        defense = self._select_role_evidence(
            f"{claimant.get('name', '')} {claimant.get('role', '')}".lower(),
            evidence_notes,
            target=contested_target,
        )
        fact_basis = self._fact_basis(
            f"{claimant.get('name', '')} {claimant.get('role', '')}".lower(),
            evidence_notes,
            axis,
            target=contested_target,
        )
        background = self._background_claim(claimant)
        relation = self._relationship_clause(relationship)
        evidence_status = self._evidence_status_line(evidence_notes, contested_target, claimant)
        claimant_claim = self._agent_claim_from_fields(claimant, contested_target)
        openers = [
            f"{claimant_name}: {challenger_name}, I’ll give you part of that, but not the whole conclusion.",
            f"{claimant_name}: That objection changes my confidence, not my entire direction yet.",
            f"{claimant_name}: I hear the mechanism you are pointing to; here is where I still disagree.",
        ]
        opener = openers[round_idx % len(openers)]
        return (
            f"{opener} "
            f"{evidence_status} The claim I still defend is: {claimant_claim}. "
            f"The piece I am protecting is {axis}. {fact_basis} "
            f"My best counter-card is: {self._short_card(defense, max_len=240)}. "
            f"So I revise from `{claimant_base}` toward `{revised}` for now, but only because that mechanism survived this cross-question."
        )

    def _concession_message(
        self,
        claimant: Dict[str, Any],
        challenger: Dict[str, Any],
        claimant_sample: Dict[str, Any],
        challenger_sample: Dict[str, Any],
        contested_target: str,
        round_idx: int,
    ) -> str:
        claimant_name = claimant.get("name") or "Claimant"
        challenger_name = challenger.get("name") or "challenger"
        claimant_base = claimant_sample.get("base")
        challenger_base = challenger_sample.get("base")
        spread = self._numeric_spread(claimant_base, challenger_base)
        concession = self._concession_line(claimant, challenger, contested_target)
        revision = self._provisional_revision(claimant_base, challenger_base, round_idx)
        openers = [
            "Fair. I will concede the narrow point",
            "I am not persuaded on the endpoint, but I concede this mechanism",
            "That is a real weakness in my first answer",
        ]
        opener = openers[round_idx % len(openers)]
        return (
            f"{claimant_name}: {opener} to {challenger_name}: {concession}. "
            f"The gap is still `{spread}` on `{contested_target}`, so I am not abandoning my view, "
            f"but I accept a provisional move from `{claimant_base}` toward `{revision}` if the evidence check holds."
        )

    def _concession_line(self, claimant: Dict[str, Any], challenger: Dict[str, Any], target: str) -> str:
        challenger_role = f"{challenger.get('name', '')} {challenger.get('role', '')}".lower()
        claimant_role = f"{claimant.get('name', '')} {claimant.get('role', '')}".lower()
        if any(term in challenger_role for term in ["auditor", "watchdog", "research", "data"]):
            return "my claim needs a cleaner source chain, date, unit, or denominator before it deserves full weight"
        if any(term in challenger_role for term in ["quant", "poll", "model", "scientist"]):
            return "the uncertainty band is wider than my first point estimate implied"
        if any(term in challenger_role for term in ["voter", "worker", "consumer", "civilian", "household", "community"]):
            return "the lived-response channel may change behavior before it shows up in aggregate data"
        if any(term in challenger_role for term in ["strategist", "military", "security", "diplomat", "trader", "market"]):
            return "incentives, signaling, and second-order reactions may matter more than the headline evidence"
        if "same" in claimant_role:
            return f"my lane may be overweighting its own view of {self._target_label(target)}"
        return "there is a plausible mechanism in your lane that my first answer underweighted"

    def _background_claim(self, agent: Dict[str, Any]) -> str:
        persona = agent.get("persona") if isinstance(agent.get("persona"), dict) else {}
        background = persona.get("background")
        boundary = persona.get("knowledge_boundary")
        strong_topics = persona.get("speaks_strongly_about") or []
        pieces = []
        if background:
            pieces.append(self._short_card(background, max_len=170))
        if strong_topics:
            pieces.append(f"The part I know best is {', '.join(str(topic) for topic in strong_topics[:4])}.")
        if boundary:
            pieces.append(f"I may be wrong where {boundary}")
        return " ".join(pieces) + (" " if pieces else "")

    def _relationship_clause(self, relationship: Dict[str, Any]) -> str:
        if not relationship:
            return ""
        return (
            "I’m pushing back because our incentives do not line up. "
            f"{self._short_card(relationship.get('rationale'), max_len=150)}. "
        )

    def _moderator_cross_question(
        self,
        claimant: Dict[str, Any],
        challenger: Dict[str, Any],
        claimant_sample: Dict[str, Any],
        challenger_sample: Dict[str, Any],
        contested_target: str,
        round_idx: int,
    ) -> str:
        spread = self._numeric_spread(claimant_sample.get("base"), challenger_sample.get("base"))
        return (
            f"Moderator: pause. We are arguing over `{contested_target}`, and the visible gap is `{spread}`. "
            f"{claimant.get('name')}, name the input that changes first. "
            f"{challenger.get('name')}, name the fact that would make you back off. "
            "No falsifier, no full weight."
        )

    def _research_check_turn(
        self,
        challenger: Dict[str, Any],
        contested_target: str,
        evidence_notes: List[str],
        pocket_label: str,
    ) -> str:
        role_l = f"{challenger.get('name', '')} {challenger.get('role', '')}".lower()
        facts = self._select_fact_cards(
            role_l=role_l,
            evidence_notes=evidence_notes,
            axis="live cross-check",
            target=contested_target,
            pocket_label=pocket_label,
            limit=2,
        )
        if facts:
            return (
                f"Quick source check: I can support part of {challenger.get('name')}'s challenge with "
                + " | ".join(facts)
                + ". I am not saying it settles the argument; I am saying this is the evidence the room should test next."
            )
        return (
            f"Quick source check: I cannot find a clean support card for {challenger.get('name')}'s challenge on `{contested_target}`. "
            "Treat that claim as a hypothesis until the next research pass finds a source, date, unit, or denominator."
        )

    def _quant_check_turn(
        self,
        claimant: Dict[str, Any],
        challenger: Dict[str, Any],
        claimant_sample: Dict[str, Any],
        challenger_sample: Dict[str, Any],
        contested_target: str,
        round_idx: int,
    ) -> str:
        original = claimant_sample.get("base")
        challenger_value = challenger_sample.get("base")
        revised = self._provisional_revision(original, challenger_value, round_idx)
        spread = self._numeric_spread(original, challenger_value)
        return (
            f"Quant note: no blind averaging. `{claimant.get('name')}` starts at `{original}`; "
            f"`{challenger.get('name')}` pulls toward `{challenger_value}`; spread `{spread}`. "
            f"I carry only part of the challenge into `{contested_target}`, so the provisional number is `{revised}`. "
            "Confirmed mechanisms get more weight later; unsupported pressure fades."
        )

    def _revision_note(
        self,
        claimant: Dict[str, Any],
        challenger: Dict[str, Any],
        claimant_sample: Dict[str, Any],
        challenger_sample: Dict[str, Any],
        contested_target: str,
        round_idx: int,
    ) -> str:
        claimant_base = claimant_sample.get("base")
        challenger_base = challenger_sample.get("base")
        revised = self._provisional_revision(claimant_base, challenger_base, round_idx)
        spread = self._numeric_spread(claimant_base, challenger_base)
        return (
            f"Mediator ruling, round {round_idx}: this is a live disagreement, not a winner-take-all vote. "
            f"`{claimant.get('name')}` and `{challenger.get('name')}` are `{spread}` apart on `{contested_target}`. "
            f"The room carries forward `{revised}` as the provisional value. "
            "Next pocket, this either earns more weight through evidence or gets pulled back."
        )

    def _challenge_axis(self, claimant: Dict[str, Any], challenger: Dict[str, Any]) -> str:
        c_cog = claimant.get("cognitive_profile") if isinstance(claimant.get("cognitive_profile"), dict) else {}
        h_cog = challenger.get("cognitive_profile") if isinstance(challenger.get("cognitive_profile"), dict) else {}
        c_stakes = claimant.get("stakes_profile") if isinstance(claimant.get("stakes_profile"), dict) else {}
        h_stakes = challenger.get("stakes_profile") if isinstance(challenger.get("stakes_profile"), dict) else {}
        if (h_cog.get("numeracy_score") or 0) > (c_cog.get("numeracy_score") or 0) + 15:
            return "whether the claimant's causal story has enough numeric support, denominators, and uncertainty discipline"
        if (h_cog.get("local_knowledge_score") or 0) > (c_cog.get("local_knowledge_score") or 0) + 15:
            return "whether the claimant is flattening ground-level variation into a clean aggregate story"
        if h_stakes.get("strategic_posture") != c_stakes.get("strategic_posture"):
            return (
                f"whether `{c_stakes.get('strategic_posture', 'one posture')}` is overweighting the outcome compared with "
                f"`{h_stakes.get('strategic_posture', 'another posture')}`"
            )
        if h_stakes.get("public_position_pressure") in {"high", "very high"}:
            return "whether public-position pressure is causing strategic overstatement, understatement, or selective attention"
        return "whether one causal channel is being overweighted while another is being treated as noise"

    def _fact_basis(
        self,
        role_l: str,
        evidence_notes: List[str],
        axis: str,
        target: str = "",
        pocket_label: str = "",
    ) -> str:
        facts = self._select_fact_cards(role_l, evidence_notes, axis, target=target, pocket_label=pocket_label, limit=3)
        if not facts:
            return "Fact basis: the prompt does not provide enough role-specific evidence, so this claim should be treated as weak."
        formatted = " ".join(f"({idx}) {fact}" for idx, fact in enumerate(facts, start=1))
        inference = self._fact_inference(role_l, axis, facts)
        return f"Fact basis: {formatted} Inference: {inference}"

    def _select_fact_cards(
        self,
        role_l: str,
        evidence_notes: List[str],
        axis: str,
        target: str = "",
        pocket_label: str = "",
        limit: int = 3,
    ) -> List[str]:
        clean_notes = [
            note for note in evidence_notes
            if not self._is_bad_evidence_card(note)
        ]
        if not clean_notes:
            return []
        search_text = f"{role_l} {axis} {target} {pocket_label}".lower()
        wanted = {
            token for token in re.findall(r"[a-zA-Z][a-zA-Z0-9_/-]{3,}", search_text)
            if token not in {"whether", "claimant", "causal", "story", "enough", "another", "compared", "public", "pressure"}
        }
        wanted.update(["seat", "vote", "share", "turnout", "price", "rate", "index", "probability", "risk", "region", "scenario"])

        scored = []
        offset = self._stable_index(role_l, axis, target, pocket_label, modulo=max(1, len(clean_notes)))
        ordered_notes = clean_notes[offset:] + clean_notes[:offset]
        for note_idx, note in enumerate(ordered_notes):
            note_l = note.lower()
            score = sum(1 for term in wanted if term in note_l)
            score += self._section_score(note_l, search_text)
            if re.search(r"\d", note):
                score += 2
            if any(term in note_l for term in ["seat", "vote", "turnout", "share", "risk", "advantage", "region"]):
                score += 1
            if pocket_label and self._loose_overlap(pocket_label, note):
                score += 4
            if target and self._loose_overlap(target, note):
                score += 3
            if score > 0:
                scored.append((score, -note_idx, note))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)

        facts = []
        seen = set()
        used_sections = set()
        for _, _, note in scored:
            normalized = note.lower()
            if normalized in seen:
                continue
            section = self._note_section(note)
            if section in used_sections and len(facts) < max(1, limit - 1):
                continue
            seen.add(normalized)
            used_sections.add(section)
            facts.append(note)
            if len(facts) >= limit:
                break
        if len(facts) < limit:
            for note in ordered_notes:
                normalized = note.lower()
                if normalized not in seen and re.search(r"\d", note):
                    facts.append(note)
                    seen.add(normalized)
                if len(facts) >= limit:
                    break
        return facts[:limit]

    def _stable_index(self, *parts: Any, modulo: int) -> int:
        if modulo <= 1:
            return 0
        digest = hashlib.sha256("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()
        return int(digest[:8], 16) % modulo

    def _loose_overlap(self, left: str, right: str) -> bool:
        left = str(left or "").replace("_", " ").replace("-", " ")
        left_tokens = {
            token for token in re.findall(r"[a-zA-Z][a-zA-Z0-9]{3,}", left.lower())
            if token not in {"phase", "case", "scenario", "forecast", "share", "rate"}
        }
        right_l = str(right or "").replace("_", " ").replace("-", " ").lower()
        return any(token in right_l for token in left_tokens)

    def _note_section(self, note: str) -> str:
        if "::" in note:
            return note.split("::", 1)[0].strip().lower()
        return "general"

    def _section_score(self, note_l: str, search_text: str) -> int:
        section = self._note_section(note_l)
        score = 0
        if "historical" in section and any(term in search_text for term in ["baseline", "history", "past"]):
            score += 3
        if "region" in section and any(term in search_text for term in ["region", "local", "belt", "urban", "rural", "north", "south"]):
            score += 4
        if "scenario" in section and "scenario" in search_text:
            score += 4
        if "target" in section and any(term in search_text for term in ["target", "variable", "probability", "index"]):
            score += 3
        if "current" in section and any(term in search_text for term in ["current", "campaign", "phase", "voting"]):
            score += 3
        return score

    def _fact_inference(self, role_l: str, axis: str, facts: List[str]) -> str:
        joined = " ".join(facts).lower()
        if any(term in role_l for term in ["pollster", "data", "quant", "scientist", "research"]):
            return "the numbers justify uncertainty bands and regional modeling; a single point estimate would hide the actual disagreement."
        if any(term in role_l for term in ["voter", "consumer", "worker", "household", "community", "beneficiary", "cohort"]):
            return "ground-level behavior should be modeled as an active driver, not a passive average inside an expert forecast."
        if any(term in role_l for term in ["strategist", "campaign", "executive", "operator", "party", "trader"]):
            return "strategic actors can convert the same evidence into different outcomes because incentives, execution, and counter-moves matter."
        if any(term in role_l for term in ["media", "journalist", "narrative", "influencer"]):
            return "salience can change behavior, but it must be separated from evidence quality and actual conversion."
        if any(term in role_l for term in ["watchdog", "auditor", "governance"]):
            return "claims need dated evidence, denominators, and leakage checks before they can move the simulated state."
        if any(term in joined for term in ["region", "local", "belt", "urban", "rural", "state", "district"]):
            return "place-specific variation can break a uniform aggregate forecast, so the causal channel should be tested locally."
        return "the cited facts support a specific causal channel, but the channel should remain provisional until tested against other agents and later pockets."

    def _numeric_pressure(self, claimant_base: Any, challenger_base: Any) -> str:
        spread = self._numeric_spread(claimant_base, challenger_base)
        if spread == "unknown":
            return "the numerical disagreement cannot be audited until both sides expose comparable numbers"
        if spread >= 6:
            return f"the gap between our base values is `{spread}` points, which is too large to bury in narrative"
        if spread >= 3:
            return f"the gap is `{spread}` points, enough to change a close scenario"
        return f"the gap is only `{spread}` points, so the real argument is confidence and mechanism, not headline value"

    def _numeric_spread(self, left: Any, right: Any) -> Any:
        try:
            return round(abs(float(left) - float(right)), 2)
        except (TypeError, ValueError):
            return "unknown"

    def _provisional_revision(self, claimant_base: Any, challenger_base: Any, round_idx: int) -> Any:
        try:
            claimant_value = float(claimant_base)
            challenger_value = float(challenger_base)
        except (TypeError, ValueError):
            return claimant_base if claimant_base is not None else "unresolved"
        weight = 0.22 + (round_idx % 3) * 0.06
        return round(claimant_value * (1 - weight) + challenger_value * weight, 2)

    def _turn(
        self,
        pocket_id: str,
        pocket_label: str,
        speaker_id: str,
        speaker_name: str,
        message: str,
        turn_type: str,
        metadata: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        return {
            "turn_id": f"{pocket_id}_{len(message)}_{hashlib.sha1((speaker_name + message).encode('utf-8')).hexdigest()[:8]}",
            "pocket_id": pocket_id,
            "pocket_label": pocket_label,
            "speaker_id": speaker_id,
            "speaker_name": speaker_name,
            "turn_type": turn_type,
            "message": message,
            "metadata": metadata or {},
        }

    def _index_agent_outputs(self, agent_outputs: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        indexed: Dict[str, List[Dict[str, Any]]] = {}
        for output in agent_outputs:
            indexed.setdefault(output.get("agent_id"), []).append(output)
        return indexed

    def _forecast_sample(self, outputs: List[Dict[str, Any]], pocket_label: str, agent: Dict[str, Any] = None) -> Dict[str, Any]:
        if not outputs:
            return {}
        preferred = self._preferred_output_for_agent(outputs, agent or {}, pocket_label)
        points = preferred.get("forecast_path") or []
        sample = {
            "target_variable": preferred.get("target_variable"),
            "confidence": preferred.get("confidence"),
        }
        pocket_points = [point for point in points if point.get("date") == pocket_label]
        if not pocket_points:
            # Time-pocket labels and forecast-horizon dates are intentionally
            # different concepts. Debate should still see the available numeric
            # path when no pocket-specific date exists.
            pocket_points = list(points)
        for point in pocket_points:
            scenario = str(point.get("scenario") or "")
            if scenario:
                sample.setdefault("scenarios", {})[scenario] = point.get("value")
        primary = self._scenario_point_by_role(pocket_points, "primary")
        stress = self._scenario_point_by_role(pocket_points, "stress")
        if primary:
            sample["base"] = primary.get("value")
            sample["primary_scenario"] = primary.get("scenario")
            sample["unit"] = primary.get("unit")
        if stress:
            sample["downside"] = stress.get("value")
            sample["stress_scenario"] = stress.get("scenario")
            sample.setdefault("unit", stress.get("unit"))
        return sample

    def _preferred_output_for_agent(self, outputs: List[Dict[str, Any]], agent: Dict[str, Any], pocket_label: str = "") -> Dict[str, Any]:
        role_tokens = self._role_tokens(agent)
        scored: List[Tuple[int, int, Dict[str, Any]]] = []
        for output in outputs:
            target_l = str(output.get("target_variable") or "").lower()
            role_score = sum(1 for token in role_tokens if token in target_l)
            score = role_score
            if any(term in target_l for term in ["probability", "share", "rate", "seat", "price", "index", "turnout"]):
                score += 1
            scored.append((score, role_score, output))
        scored.sort(key=lambda item: item[0], reverse=True)
        if scored and scored[0][1] > 0:
            return scored[0][2]
        return outputs[self._stable_index(agent.get("agent_id"), pocket_label, "target_rotation", modulo=len(outputs))]

    def _role_tokens(self, agent: Dict[str, Any]) -> List[str]:
        text = f"{agent.get('name', '')} {agent.get('role', '')} {agent.get('archetype', '')}".lower()
        blocked = {"agent", "analyst", "observer", "strategist", "voter", "worker", "data", "the", "and", "for"}
        return [
            token for token in re.findall(r"[a-zA-Z][a-zA-Z0-9_]{2,}", text)
            if token not in blocked
        ][:16]

    def _scenario_point_by_role(self, points: List[Dict[str, Any]], role: str) -> Dict[str, Any]:
        if not points:
            return {}
        if role == "primary":
            preferred_terms = ["base", "central", "most_likely", "status_quo"]
            fallback_index = 0
        else:
            preferred_terms = ["downside", "tail", "risk", "shock", "crisis", "fragment", "hung", "adverse", "stress"]
            fallback_index = -1
        for term in preferred_terms:
            match = next(
                (
                    point for point in points
                    if term in str(point.get("scenario") or "").lower()
                ),
                None,
            )
            if match:
                return match
        return points[fallback_index]

    def _extract_evidence_notes(self, evidence_text: str) -> List[str]:
        notes = []
        current_section = "General Evidence"
        for line in (evidence_text or "").splitlines():
            cleaned = re.sub(r"\s+", " ", line).strip(" -•\t")
            if not cleaned:
                continue
            if self._is_meta_instruction_note(cleaned):
                continue
            if self._looks_like_metric_request_line(cleaned):
                continue
            if self._is_bad_evidence_card(cleaned):
                continue
            if self._looks_like_section_heading(cleaned):
                current_section = cleaned[:80]
                continue
            if self._is_instruction_section(current_section):
                continue
            has_signal = bool(re.search(
                r"\d|turnout|vote|seat|risk|advantage|poll|scenario|baseline|current|region|price|rate|share|probability|"
                r"agent|voter|consumer|worker|household|campaign|alliance|policy|jobs|corruption|welfare|identity|supply|demand|"
                r"canon|character|death|survive|betray|battle|throne|claim|prophecy|dragon|wall|winterfell|king|queen|lord|"
                r"magic|reveal|secret|faction|house|army|fleet|city|court|regime|book|chapter",
                cleaned,
                re.IGNORECASE,
            ))
            if has_signal and 16 <= len(cleaned) <= 360:
                notes.append(f"{current_section} :: {cleaned}")
            if len(notes) >= 180:
                break
        return notes or ["The prompt provides the active evidence set; no external research packet was attached to this structured run."]

    def _is_instruction_section(self, section: str) -> bool:
        lowered = str(section or "").lower()
        return bool(re.search(
            r"\b(core question|time[- ]?pocket|simulation plan|required output|final prompt|rules?|target variables?|agent architecture)\b",
            lowered,
        ))

    def _is_meta_instruction_note(self, cleaned: str) -> bool:
        """Filter workflow instructions out of factual evidence cards."""
        lowered = cleaned.lower()
        if re.match(r"^(run|build|generate|create|produce|output|act as)\b", lowered) and re.search(
            r"\b(simulation|structured|agent|report|transcript|numeric forecasts?|explain|required output)\b",
            lowered,
        ):
            return True
        if re.match(r"^(?:time[- ]pocket\s*)?pocket\s+\d+\s*:", lowered):
            return True
        if re.match(r"^\d{1,2}\.\s*(?:days?\s+\d|current state|scenario synthesis|baseline|pre[- ]campaign|candidate|alliance|manifesto|final campaign|voting)\b", lowered):
            return True
        if re.match(r"^\d{1,2}\.\s+.+\?$", lowered):
            return True
        if re.search(r"\bit is not a\b.*\bsimulation\b", lowered):
            return True
        if re.match(r"^this is a .+\bsimulation\b", lowered):
            return True
        if re.search(r"\bdo not use\b.*\b(?:twitter|reddit|platform|logic|future|actual|invented)\b", lowered):
            return True
        if re.match(r"^(rules?|required outputs?|final prompt|task|your task|for every agent|each agent|agents must|agents should)\b", lowered):
            return True
        if re.search(
            r"\b(?:do not use future|do not invent|do not produce|produce numeric|clearly separate facts|"
            r"challenge another agent|what would change their view|cite evidence|make a concrete claim)\b",
            lowered,
        ):
            return True
        return False

    def _looks_like_metric_request_line(self, cleaned: str) -> bool:
        """Skip bare requested-output bullets so they are not treated as facts."""
        lowered = cleaned.lower().strip(" .")
        if ":" in lowered or len(lowered.split()) > 8:
            return False
        has_metric_words = bool(re.search(
            r"\b(vote share|seat share|seats?|turnout|probability|confidence band|uncertainty band|index|forecast table)\b",
            lowered,
        ))
        has_observed_language = bool(re.search(
            r"\b(won|lost|got|received|around|approx|approximately|baseline|poll|survey|turnout around|actual)\b",
            lowered,
        ))
        return has_metric_words and not has_observed_language

    def _looks_like_section_heading(self, cleaned: str) -> bool:
        if len(cleaned) > 90:
            return False
        if re.match(r"^\d{1,2}\.\s+", cleaned):
            title = re.sub(r"^\d{1,2}\.\s+", "", cleaned).strip()
            title_l = title.lower().strip(": ")
            return bool(re.search(
                r"\b(baseline|context|setup|region|signals?|scenario|target|variables?|required|outputs?|rules?|"
                r"question|summary|overview|history|historical|data|table|forecast|plan|pockets?|timeline|story|canon|evidence)\b",
                title_l,
            ))
        if cleaned.isupper() and len(cleaned.split()) <= 12:
            return True
        return bool(re.search(r"(baseline|context|region|signals|scenario|target variables|required output|rules)$", cleaned, re.IGNORECASE))

    def _research_turn(self, evidence_notes: List[str], pocket_idx: int, pocket_label: str = "") -> str:
        selected = self._select_fact_cards(
            role_l="external research scout source packet",
            evidence_notes=evidence_notes,
            axis="pocket evidence injection",
            target="",
            pocket_label=pocket_label,
            limit=4,
        )
        if not selected:
            return (
                "Research desk: I do not have a clean source packet for this pocket yet. "
                "That is a warning, not a free pass. The room should keep claims provisional "
                "until a source, date, unit, or comparable precedent is available."
            )
        return (
            "Research desk: usable source cards for this pocket are "
            + " | ".join(selected)
            + ". I am not adding unapproved post-cutoff facts; I am handing the room only the evidence it is allowed to react to."
        )

    def _data_turn(self, evidence_text: str, targets: List[Dict[str, Any]], pocket_idx: int, pocket_label: str = "") -> str:
        anchors = self._numeric_anchor_notes(evidence_text)
        target_names = ", ".join(target.get("name") for target in targets[:5])
        selected_anchors = self._select_fact_cards(
            role_l="data retrieval analyst",
            evidence_notes=anchors,
            axis="numeric anchors denominators units",
            target=target_names,
            pocket_label=pocket_label,
            limit=4,
        )
        if not selected_anchors:
            return (
                f"Data desk: for `{target_names}`, I do not see clean numeric anchors in this pocket. "
                "That means analysts can still argue directionally, but the quant layer should not pretend the input is well measured."
            )
        return (
            f"Data desk: for `{target_names}`, the numeric anchors I can actually point to are "
            f"{' | '.join(selected_anchors)}. "
            "Numbers must be used as anchors, not as future actuals."
        )

    def _numeric_anchor_notes(self, evidence_text: str) -> List[str]:
        anchors = []
        for line in (evidence_text or "").splitlines():
            cleaned = re.sub(r"\s+", " ", line).strip(" -•\t")
            if not cleaned or len(cleaned) < 12:
                continue
            if self._is_meta_instruction_note(cleaned):
                continue
            if self._looks_like_metric_request_line(cleaned):
                continue
            if self._is_bad_evidence_card(cleaned):
                continue
            has_number = bool(re.search(r"\d", cleaned))
            has_real_number = self._has_real_numeric_measure(cleaned)
            has_context = bool(re.search(
                r"seat|vote|share|turnout|probability|index|poll|assembly|lok sabha|majority|phase|scenario|range|percent|%|"
                r"price|pricing|cost|usd|dollar|kwh|mwh|gwh|twh|ton|tonne|metric|lithium|spodumene|carbonate|"
                r"supply|demand|inventory|growth|margin|deficit|oversupply|sales|"
                r"book|chapter|battle|army|fleet|death|survive|survival|throne|claim|dragon|house|king|queen|probability",
                cleaned,
                re.IGNORECASE,
            ))
            if has_number and has_real_number and has_context:
                anchors.append(cleaned[:260])
            if len(anchors) >= 120:
                break
        return anchors

    def _agent_rag_adjustment(
        self,
        agent: Dict[str, Any],
        graph_brain: Dict[str, Any],
        evidence_notes: List[str],
        target: str,
        pocket_label: str,
    ) -> Dict[str, Any]:
        """Give each agent a graph/RAG steering note without turning it into a hard blocker."""
        role_l = f"{agent.get('name', '')} {agent.get('role', '')}".lower()
        cards = self._select_fact_cards(
            role_l=role_l,
            evidence_notes=(graph_brain.get("evidence_cards") or []) + evidence_notes,
            axis="agent rag adjustment",
            target=target,
            pocket_label=pocket_label,
            limit=3,
        )
        clean_target = _clean_name(target)
        missing_targets = set(graph_brain.get("missing_target_evidence") or [])
        target_is_thin = bool(clean_target and clean_target in missing_targets)
        has_numeric_card = any(re.search(r"\d", card) for card in cards)
        support_points = len(cards) + (1 if has_numeric_card else 0) - (2 if target_is_thin else 0)
        if support_points >= 4:
            support_level = "strong"
            confidence_delta = 0.05
            directive = "lean on the mapped evidence, but still name the falsifier"
        elif support_points >= 2:
            support_level = "moderate"
            confidence_delta = 0.02
            directive = "use the evidence as a live input, not as settled truth"
        else:
            support_level = "thin"
            confidence_delta = -0.04
            directive = "treat the claim as provisional and ask research/data roles for source detail"

        if target_is_thin:
            directive = (
                "treat this target as under-evidenced; speak only within your lane and ask for source/date/unit support"
            )

        return {
            "support_level": support_level,
            "confidence_delta": confidence_delta,
            "directive": directive,
            "target_is_thin": target_is_thin,
            "evidence_cards": cards[:3],
            "warnings": graph_brain.get("warnings", [])[:3],
        }

    def _rag_adjustment_line(self, rag_adjustment: Dict[str, Any]) -> str:
        if not rag_adjustment:
            return "The map does not give me a clean support card here, so I’m treating this as a provisional read."
        cards = rag_adjustment.get("evidence_cards") or []
        if cards:
            evidence = " | ".join(self._short_card(card) for card in cards[:2])
        else:
            evidence = "no clean support card for this exact lane"
        return (
            f"The map gives this {rag_adjustment.get('support_level', 'thin')} support: {evidence}. "
            f"So I’ll {rag_adjustment.get('directive', 'keep the claim provisional')}."
        )

    def _agent_argument(
        self,
        agent: Dict[str, Any],
        pocket: Dict[str, Any],
        domain_plan: Dict[str, Any],
        evidence_notes: List[str],
        forecast_sample: Dict[str, Any],
        rag_adjustment: Dict[str, Any] | None = None,
    ) -> str:
        role = str(agent.get("role") or agent.get("name") or "Agent")
        role_l = role.lower()
        target = forecast_sample.get("target_variable") or (domain_plan.get("target_variables") or [{"name": "primary_outcome"}])[0].get("name")
        target_label = self._target_label(target)
        base = forecast_sample.get("base")
        downside = forecast_sample.get("downside")
        confidence = forecast_sample.get("confidence")
        unit = forecast_sample.get("unit")
        owns_numbers = (agent.get("numeric_capabilities") or {}).get("must_output_numbers", True)
        persona = agent.get("persona") if isinstance(agent.get("persona"), dict) else {}
        stakes = agent.get("stakes_profile") if isinstance(agent.get("stakes_profile"), dict) else {}
        disagreement = self._disagreement_claim(role_l)
        claim = self._agent_claim_from_fields(agent, target)
        signal = self._agent_observable_signal(agent)
        falsifier = self._agent_falsifier(agent, target)
        concern = (
            self._clean_agent_field(persona.get("private_concern"))
            or self._clean_agent_field((agent.get("blind_spots") or [""])[0])
            or "the room may be overconfident where the evidence is thin"
        )
        tension = (
            self._clean_agent_field(persona.get("default_tension"))
            or self._clean_agent_field((agent.get("biases") or [""])[0])
            or "a clean aggregate story may be hiding a messy local mechanism"
        )
        stakes_line = self._human_stakes_line(stakes)
        evidence_status = self._evidence_status_line(evidence_notes, target, agent)
        character = persona.get("character_name") or agent.get("name")
        pointer = self._human_fact_basis(
            role_l,
            evidence_notes,
            disagreement,
            target=target,
            pocket_label=pocket.get("label") or "",
        ) or "I do not have a clean source card for this lane yet"
        lens = self._role_lens(role_l, target_label)
        numeric_sentence = self._forecast_sentence(owns_numbers, target_label, base, downside, confidence, unit)
        pushback = self._plain_disagreement(disagreement)
        claim = self._trim_mechanical_claim(claim, target_label)
        signal = self._trim_mechanical_claim(signal, target_label)
        return (
            f"{character}: {lens} My claim on {target_label} is: {claim}. "
            f"The strongest card in my lane is {pointer}. {evidence_status} "
            f"What I can actually see from my seat is {signal}. {numeric_sentence} "
            f"I am pushing back on {pushback}. Change my mind with {falsifier}. "
            f"{stakes_line} My private worry is {concern}; the tension I am not letting the room smooth over is {tension}."
        )

    def _target_label(self, target: Any) -> str:
        text = str(target or "the outcome").replace("_", " ").replace("-", " ")
        text = re.sub(r"\s+", " ", text).strip()
        return text or "the outcome"

    def _forecast_sentence(self, owns_numbers: bool, target_label: str, base: Any, downside: Any, confidence: Any, unit: Any = None) -> str:
        if owns_numbers and base is not None:
            unit_label = f" {unit}" if unit else ""
            return (
                f"My current mark is {target_label}: base {base}{unit_label}, stress {downside}{unit_label}, confidence {confidence}. "
                "I am treating that as a revisable mark, not a prophecy."
            )
        return (
            f"I am not issuing a precise number for {target_label}; I am moving the assumptions around behavior, "
            "constraints, incentives, timing, and second-order reactions."
        )

    def _plain_disagreement(self, disagreement: str) -> str:
        text = re.sub(r"^I disagree with\s+", "", str(disagreement or ""), flags=re.IGNORECASE)
        return text[:1].lower() + text[1:] if text else "the single-cause explanation"

    def _trim_mechanical_claim(self, claim: str, target_label: str) -> str:
        cleaned = self._clean_agent_field(claim, max_len=260)
        if cleaned:
            return cleaned
        return (
            f"my lane can move {target_label}, but this profile did not provide enough concrete detail, "
            "so my claim should carry low weight until challenged"
        )

    def _human_style_line(self, cognitive: Dict[str, Any]) -> str:
        fragments = []
        text = " ".join(str(value).lower() for value in (cognitive or {}).values())
        if "case" in text or "pattern" in text:
            fragments.append("I’m comparing this with similar cases, not just the headline.")
        if "skeptical" in text or "falsifier" in text:
            fragments.append("I’m looking for the thing that would break the claim.")
        if "incentive" in text or "principal" in text or "game" in text:
            fragments.append("I’m watching incentives, not only public statements.")
        if "ground" in text or "local" in text:
            fragments.append("I’m giving local or user-level behavior real weight.")
        if "slow" in text:
            fragments.append("I won’t move quickly without a mechanism.")
        elif "fast" in text or "quick" in text:
            fragments.append("If the evidence changes, I’ll update quickly.")
        return " ".join(fragments[:2]) or "I’ll stay inside what this role can actually know."

    def _human_stakes_line(self, stakes: Dict[str, Any]) -> str:
        if not stakes:
            return "I have a stake here because bad assumptions would distort the whole room."
        skin = str(stakes.get("skin_in_the_game") or "credibility and decision quality")
        loss = str(stakes.get("what_they_lose_if_wrong") or "credibility")
        if skin.lower().startswith("represents or moves part of the simulation outcome through"):
            skin = "the forecast treating my lane as real, not decorative"
        if loss.lower().startswith("may misread the wider system outside its information lane"):
            loss = "overclaiming beyond what my lane can honestly see"
        return f"My stake is {skin}; if I’m wrong, the cost is {loss}."

    def _clean_agent_field(self, value: Any, max_len: int = 220) -> str:
        """Return only useful agent/profile text, dropping scaffolding."""
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        if not text:
            return ""
        lowered = text.lower()
        weak_patterns = [
            "represents or moves part of the simulation outcome through",
            "context-derived knowledge associated with",
            "make the room account for what",
            "may overweight its own information access",
            "may misread the wider system outside its information lane",
            "update forecasts only when new evidence changes",
            "role-specific incentives, information gaps",
            "role specific incentives, information gaps",
            "brings role-specific exposure",
            "brings role specific exposure",
            "causal channel assigned to my role",
            "whether it changes the forecast",
            "forecast treating my lane as real",
            "specialized vantage point created from the prompt",
            "from the operating lane closest",
            "stress-test the simulation from this role",
            "assigned causal channel",
            "keep the forecast numerically coherent",
            "turn a messy debate",
            "expand the evidence frontier",
            "track which claims become salient",
            "route to victory",
            "campaign room where turnout",
            "booth reports",
            "seat math",
            "the practical world of",
            "evidence_strength",
            "actor_alignment",
            "uncertainty_pressure",
        ]
        if any(pattern in lowered for pattern in weak_patterns):
            return ""
        if self._is_meta_instruction_note(text):
            return ""
        return self._short_card(text, max_len=max_len)

    def _agent_claim_from_fields(self, agent: Dict[str, Any], target: str) -> str:
        """Build a claim from actual agent fields without inventing facts."""
        persona = agent.get("persona") if isinstance(agent.get("persona"), dict) else {}
        role_l = f"{agent.get('name', '')} {agent.get('role', '')} {agent.get('archetype', '')}".lower()
        role_specific = self._role_specific_claim(role_l, target)
        candidates: List[Any] = [
            persona.get("opening_position"),
            persona.get("objective"),
            persona.get("background"),
            persona.get("vantage_point"),
            agent.get("causal_power"),
            agent.get("forecasting_method"),
            *self._listish(agent.get("heuristics")),
            *self._listish(agent.get("information_set")),
            agent.get("institutional_incentives"),
        ]
        for candidate in candidates:
            cleaned = self._clean_agent_field(candidate)
            if cleaned:
                if role_specific and self._is_generic_role_claim(cleaned, role_l):
                    return role_specific
                return cleaned
        if role_specific:
            return role_specific
        return (
            f"No concrete role-specific claim was supplied for `{target}`; "
            "treat this speaker as a weak pressure lane until better evidence/profile detail is provided."
        )

    def _is_generic_role_claim(self, claim: str, role_l: str) -> bool:
        lowered = str(claim or "").lower()
        role_tokens = {
            token for token in re.findall(r"[a-z][a-z0-9]{3,}", role_l)
            if token not in {"agent", "analyst", "observer", "strategist", "model", "public", "facing"}
        }
        return (
            "state what" in lowered
            or "can observe, what it can influence" in lowered
            or "separate visible signaling from actual escalation incentives" in lowered
            or "role-specific incentives" in lowered
            or (len(role_tokens) > 0 and sum(1 for token in role_tokens if token in lowered) == 0 and len(lowered) < 120)
        )

    def _role_specific_claim(self, role_l: str, target: str) -> str:
        target_label = self._target_label(target)
        if "nuclear" in role_l or "non-proliferation" in role_l or "nonproliferation" in role_l:
            return (
                f"{target_label} hinges on whether inspection, enrichment limits, and breakout-risk language can be made credible "
                "enough for both sides to accept without looking like they surrendered."
            )
        if "domestic political" in role_l or ("political adviser" in role_l and "domestic" in role_l):
            return (
                f"{target_label} is constrained by audience cost: leaders may prefer a tense pause over escalation if oil prices, casualties, "
                "or war-fatigue become politically expensive at home."
            )
        if "pentagon" in role_l or "centcom" in role_l or "force-protection" in role_l or "force protection" in role_l:
            return (
                f"{target_label} moves when force-protection posture changes: evacuations, naval spacing, air-defense alerts, "
                "or rules-of-engagement shifts matter more than public rhetoric."
            )
        if "irgc" in role_l or "iranian security hardliner" in role_l:
            return (
                f"{target_label} depends on the regime's credibility calculus: it can use proxies, maritime pressure, or calibrated retaliation "
                "to show pain without necessarily choosing open war."
            )
        if "israeli security" in role_l:
            return (
                f"{target_label} depends on whether Israel believes the pause lets Iran rebuild military or nuclear leverage; "
                "that perception can lower tolerance for waiting."
            )
        if "diplomat" in role_l or "negotiator" in role_l or "mediator" in role_l:
            return (
                f"{target_label} improves only if the parties can sell a face-saving off-ramp to domestic and allied audiences, "
                "not merely if a technical bargain exists."
            )
        if any(term in role_l for term in ["shipping", "insur", "tanker", "port", "logistics", "lng", "oil", "energy"]):
            return (
                f"{target_label} should be read through commercial behavior: insurance premia, AIS usage, waiting times, rerouting, "
                "and cargo nominations reveal stress before official statements do."
            )
        if any(term in role_l for term in ["consumer", "household", "buyer", "end-user", "end user", "demand"]):
            if any(term in target_label.lower() for term in ["price", "supply", "demand", "inventory", "grid", "power", "capacity", "premium", "market"]):
                return (
                    f"{target_label} depends on whether end users absorb higher costs, delay orders, substitute inputs, "
                    "or pull demand forward before the supply chain can respond."
                )
            return (
                f"{target_label} moves when ordinary participants change behavior before institutions notice it in aggregate data."
            )
        if "cyber" in role_l:
            return (
                f"{target_label} can rise through deniable disruption: ports, banks, energy systems, and communications are attractive pressure points "
                "when open retaliation is too costly."
            )
        if any(term in role_l for term in ["civilian", "humanitarian", "medicine", "diaspora", "resident", "household"]):
            return (
                f"{target_label} is not only a state decision; shortages, fear, payment friction, medicine access, and family-level adaptation "
                "change the political room leaders have."
            )
        return ""

    def _agent_observable_signal(self, agent: Dict[str, Any]) -> str:
        """State what the agent can observe, based only on its profile fields."""
        candidates: List[str] = []
        persona = agent.get("persona") if isinstance(agent.get("persona"), dict) else {}
        for item in self._listish(persona.get("speaks_strongly_about")):
            cleaned = self._clean_agent_field(item, max_len=140)
            if cleaned:
                candidates.append(cleaned)
        for key in ("trusted_data_sources", "information_set", "ignored_or_underweighted_data"):
            for item in self._listish(agent.get(key)):
                cleaned = self._clean_agent_field(item, max_len=180)
                if cleaned:
                    candidates.append(cleaned)
        if candidates:
            return " / ".join(candidates[:2])
        return "No concrete observable signal was supplied in this agent profile."

    def _claim_label(self, evidence_notes: List[str], target: str) -> str:
        """Label evidence quality; do not imply more support than exists."""
        relevant = [
            note for note in evidence_notes
            if target and self._loose_overlap(target, note)
        ]
        if any(re.search(r"\d", note or "") for note in relevant):
            return "evidence-linked model judgment"
        if relevant:
            return "prompt/graph-linked model judgment"
        return "unsupported model judgment"

    def _evidence_status_line(self, evidence_notes: List[str], target: str, agent: Dict[str, Any]) -> str:
        role_l = f"{agent.get('name', '')} {agent.get('role', '')}".lower()
        facts = self._select_fact_cards(role_l, evidence_notes, "evidence status", target=target, limit=2)
        label = self._claim_label(evidence_notes, target)
        if not facts:
            return f"Evidence check: `{label}`; I do not have a clean source card for this exact speaker-target lane."
        cards = " | ".join(self._short_card(fact, max_len=170) for fact in facts[:2])
        prompt_only = all("using only information available" in (fact or "").lower() for fact in facts[:2])
        caveat = " This is prompt-derived support, not independent external research." if prompt_only else ""
        return f"Evidence check: `{label}`. Source cards I am leaning on: {cards}.{caveat}"

    def _listish(self, value: Any) -> List[Any]:
        if isinstance(value, list):
            return value
        if value in (None, ""):
            return []
        return [value]

    def _agent_falsifier(self, agent: Dict[str, Any], target: str) -> str:
        role_l = f"{agent.get('name', '')} {agent.get('role', '')}".lower()
        target_label = str(target or "the target")
        if any(term in role_l for term in ["poll", "data", "quant", "scientist", "research"]):
            return f"a dated source, denominator, or sensitivity check that moves `{target_label}` outside my current band"
        if any(term in role_l for term in ["consumer", "household", "voter", "worker", "community", "beneficiary"]):
            return "proof that affected people are absorbing the shock without changing behavior"
        if any(term in role_l for term in ["operator", "utility", "grid", "miner", "producer", "supplier", "developer"]):
            return "a credible operational workaround that changes capacity, timing, or bottleneck severity"
        if any(term in role_l for term in ["security", "military", "pentagon", "centcom", "irgc", "hardliner", "diplomat", "negotiator"]):
            return "a visible change in red lines, force posture, back-channel terms, or third-party pressure"
        if any(term in role_l for term in ["oil", "lng", "shipping", "insurance", "port", "logistics"]):
            return "a change in cargo flows, insurance pricing, tanker routing, port throughput, or spare capacity"
        if any(term in role_l for term in ["trader", "strategist", "executive", "party", "campaign"]):
            return "evidence that the payoff structure changed, not just the public narrative"
        if any(term in role_l for term in ["auditor", "watchdog", "moderator", "mediator"]):
            return "a cleaner evidence chain that survives challenge from the other side"
        return f"a concrete mechanism that changes `{target_label}`, not a louder version of the same claim"

    def _short_card(self, value: Any, max_len: int = 180) -> str:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        text = re.sub(r"^[A-Za-z ]+::\s*", "", text)
        if len(text) <= max_len:
            return text
        return text[: max_len - 1].rstrip() + "…"

    def _human_fact_basis(
        self,
        role_l: str,
        evidence_notes: List[str],
        axis: str,
        target: str = "",
        pocket_label: str = "",
    ) -> str:
        facts = self._select_fact_cards(role_l, evidence_notes, axis, target=target, pocket_label=pocket_label, limit=2)
        if not facts:
            return "the prompt is thin for my lane, so this should stay low-confidence"
        return " / ".join(self._short_card(fact, max_len=150) for fact in facts)

    def _role_lens(self, role_l: str, target_label: str = "the outcome") -> str:
        if any(term in role_l for term in ["security", "military", "pentagon", "centcom", "irgc", "hardliner", "planner", "strategist"]):
            return (
                "I am treating this as a decision problem, not a press release: who needs to look strong, "
                "who can absorb pain, and where a small move can become escalation."
            )
        if any(term in role_l for term in ["campaign", "party"]):
            return "I care about organization, field execution, message control, counter-moves, and conversion into the final outcome."
        if any(term in role_l for term in ["diplomat", "negotiator", "mediator", "alliance"]):
            return "I am watching face-saving exits, veto players, bargaining leverage, and where public positions differ from private room for a deal."
        if any(term in role_l for term in ["oil", "lng", "shipping", "port", "logistics", "insurance", "underwriter", "trader", "market"]):
            return "I am watching bottlenecks, premiums, cargo timing, hedging behavior, and whether fear becomes a real price or supply shock."
        if any(term in role_l for term in ["voter", "beneficiary", "rural", "urban", "worker", "youth", "poor", "middle"]):
            return "I care about lived incentives, turnout motivation, local trust, and whether promises feel credible."
        if any(term in role_l for term in ["pollster", "scientist", "quant", "data"]):
            return "I care about denominators, base rates, missing data, sensitivity, and whether the forecast is pretending to be cleaner than it is."
        if any(term in role_l for term in ["journalist", "media", "narrative"]):
            return "I care about which story gets repeated, what people ignore, and whether attention changes behavior before facts fully settle."
        if any(term in role_l for term in ["watchdog", "auditor", "governance"]):
            return "I care about evidence quality, institutional credibility, and claims that may be overstated."
        if any(term in role_l for term in ["business", "industry"]):
            return "I care about jobs, investment, business sentiment, and whether economic promises are believable."
        if any(term in role_l for term in ["witness", "civilian", "humanitarian", "patient", "resident", "expatriate"]):
            return "I am closest to the human consequences: shortages, fear, workarounds, trust, fatigue, and what people do when institutions lag."
        return f"I am testing one concrete path into {target_label}, and I will not let the room treat it as decorative context."

    def _select_role_evidence(self, role_l: str, evidence_notes: List[str], target: str = "", pocket_label: str = "") -> str:
        wanted = [
            token for token in re.findall(r"[a-zA-Z][a-zA-Z0-9_/-]{3,}", role_l)
            if token not in {"agent", "analyst", "observer", "strategist", "representative"}
        ]
        matches = [
            note for note in evidence_notes
            if any(term.lower() in note.lower() for term in wanted)
        ]
        selected = matches[:2] or self._select_fact_cards(
            role_l,
            evidence_notes,
            role_l,
            target=target or role_l,
            pocket_label=pocket_label,
            limit=2,
        ) or evidence_notes[:2]
        return " | ".join(selected) if selected else "the provided prompt evidence"

    def _disagreement_claim(self, role_l: str) -> str:
        if any(term in role_l for term in ["security", "military", "pentagon", "centcom", "irgc", "hardliner", "diplomat", "negotiator"]):
            return "I disagree with treating public signaling as sincere probability; actors may posture, bluff, or search for off-ramps at the same time."
        if any(term in role_l for term in ["oil", "lng", "shipping", "insurance", "port", "logistics", "trader", "market"]):
            return "I disagree with treating geopolitical risk as just a headline; it becomes real only through cargo flows, premiums, routing, inventory, and timing."
        if any(term in role_l for term in ["pollster", "scientist", "quant", "data"]):
            return "I disagree with collapsing uncertainty into one clean number; the forecast needs distributions and sensitivity."
        if any(term in role_l for term in ["watchdog", "auditor", "governance"]):
            return "I disagree with any confident claim that lacks a dated source, numeric anchor, or leakage check."
        if any(term in role_l for term in ["voter", "consumer", "worker", "household", "community", "beneficiary", "cohort"]):
            return "I disagree with treating affected people as a passive output; their response is one of the engines of the simulation."
        if any(term in role_l for term in ["strategist", "campaign", "executive", "operator", "party", "trader"]):
            return "I disagree with assuming actors simply reveal beliefs; many signals are strategic and payoff-driven."
        if any(term in role_l for term in ["region", "local", "urban", "rural", "belt", "border", "coastal"]):
            return "I disagree with a uniform aggregate frame; local variation can dominate conversion into final outcomes."
        return "My disagreement is with any single-cause explanation; the outcome needs multiple channels."

    def _mediator_turn(self, causal_agents: List[Dict[str, Any]], label: str) -> str:
        names = [agent.get("name") for agent in causal_agents[:6]]
        return (
            f"In `{label}`, the unresolved conflict is not just the headline forecast. It is which causal channel, "
            "stakeholder incentive, information gap, local variation, strategic move, and numeric anchor deserves more weight. "
            f"I am asking {', '.join(names)} to name the evidence that would make them change their number."
        )

    def _auditor_turn(
        self,
        domain_plan: Dict[str, Any],
        evidence_notes: List[str],
        causal_context: List[Dict[str, Any]] = None,
        label: str = "",
    ) -> str:
        cutoff = domain_plan.get("cutoff_date")
        contexts = causal_context or []
        numeric_owners = [
            (ctx.get("agent") or {}).get("name")
            for ctx in contexts
            if ((ctx.get("agent") or {}).get("numeric_capabilities") or {}).get("must_output_numbers", True)
        ]
        non_numeric = [
            (ctx.get("agent") or {}).get("name")
            for ctx in contexts
            if not (((ctx.get("agent") or {}).get("numeric_capabilities") or {}).get("must_output_numbers", True))
        ]
        return (
            f"Audit status: cutoff is `{cutoff or 'not specified'}`. I found {len(evidence_notes)} prompt evidence notes. "
            f"Numeric forecast owners in `{label}`: {', '.join(numeric_owners[:8]) or 'none declared'}. "
            f"Non-numeric behavioral/strategic signal agents: {', '.join(non_numeric[:8]) or 'none declared'}. "
            "Any claim about results, post-cutoff behavior, or unprovided polling/source data must be blocked or labeled as missing data. "
            "Non-numeric agents may move assumptions and pressure variables, but should not be treated as independent quantitative forecasters."
        )

    def _quant_turn(self, scenario_outputs: Dict[str, Any], pocket_label: str, key_targets: List[str]) -> str:
        fragments = []
        for target in key_targets[:4]:
            scenario_values = {}
            for scenario, targets in scenario_outputs.items():
                points = targets.get(target) or []
                selected = next((point for point in points if point.get("date") == pocket_label), None)
                if selected is None and points:
                    selected = points[-1]
                if selected is not None:
                    scenario_values[scenario] = selected.get("value")
            if scenario_values:
                ordered = ", ".join(
                    f"{scenario}={value}"
                    for scenario, value in list(scenario_values.items())[:6]
                )
                fragments.append(
                    f"{target}: {ordered}"
                )
        return (
            "I aggregated the agent paths for this pocket. "
            + ("; ".join(fragments) if fragments else "No scenario values were available for the selected targets.")
            + ". These values move forward as state inputs for the next pocket."
        )
