"""
Structured simulation runner.

This runner is the backend bridge between planning/agents and report generation:
it produces simulation_state.json with agent-specific numeric forecast paths,
scenario outputs, aggregated outputs, and validation. It is intentionally
domain-general; domain meaning comes from the prompt-derived plan and agents.
"""

import hashlib
import re
from datetime import datetime
from typing import Any, Dict, List, Tuple

from ..models.simulation_state import SimulationStateManager, StructuredSimulationState
from .numeric_validation import NumericValidationService
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
    ) -> StructuredSimulationState:
        state = SimulationStateManager.initialize(
            simulation_id=simulation_id,
            project_id=project_id,
            domain_plan=domain_plan,
            agents=agents,
        )

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
        }

        targets = [
            variable for variable in domain_plan.get("target_variables", [])
            if variable.get("required", True)
        ] or [{"name": "primary_outcome", "unit": "index", "required": True}]
        numeric_agents = [
            agent for agent in agents
            if (agent.get("numeric_capabilities") or {}).get("must_output_numbers", True)
        ] or list(agents)

        for pocket_idx, pocket in enumerate(state.time_pockets):
            state_before = self._state_snapshot(targets, pocket_idx, prior=True)
            state_after = self._state_snapshot(targets, pocket_idx, prior=False)
            pocket["state_before"] = state_before
            pocket["state_after"] = state_after
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
        )
        debate_revisions = self._extract_debate_revisions(state.discussion_transcript)
        state.agent_outputs = self._apply_debate_revisions(state.agent_outputs, debate_revisions, state.time_pockets)
        state.scenario_outputs = self._aggregate_scenarios(state.agent_outputs, targets, evidence_text)
        state.aggregated_outputs["by_target"] = self._aggregate_targets(state.agent_outputs)
        state.aggregated_outputs["agent_disagreement"] = self._agent_disagreement(state.agent_outputs)
        state.aggregated_outputs["final_outcome"] = self._final_outcome_summary(state.scenario_outputs, targets, evidence_text)
        state.aggregated_outputs["debate_revisions"] = debate_revisions
        state.aggregated_outputs["debate_impact"] = self._debate_impact_summary(debate_revisions)
        # Rebuild once so the visible transcript and quant summaries reflect the debated state.
        state.discussion_transcript = self._build_discussion_transcript(
            state=state,
            targets=targets,
            evidence_text=evidence_text,
        )
        turns_by_pocket = {}
        for turn in state.discussion_transcript:
            turns_by_pocket.setdefault(turn.get("pocket_id"), []).append(turn)
        for pocket in state.time_pockets:
            pocket["discussion_turns"] = turns_by_pocket.get(pocket.get("pocket_id"), [])

        validation = NumericValidationService().validate(state.to_dict())
        state.validation = validation
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
        domain_plan: Dict[str, Any],
        evidence_text: str,
    ) -> Dict[str, Any]:
        target_name = target.get("name") or "primary_outcome"
        unit = target.get("unit") or "index"
        forecast_path = []
        for pocket_idx, pocket in enumerate(time_pockets):
            date = pocket.get("end") if pocket.get("end") != "auto" else pocket.get("label") or pocket.get("pocket_id")
            for scenario in self._required_scenarios(domain_plan):
                forecast_path.append({
                    "date": date,
                    "value": self._forecast_value(
                        target_name=target_name,
                        unit=unit,
                        agent=agent,
                        agent_idx=agent_idx,
                        target_idx=target_idx,
                        pocket_idx=pocket_idx,
                        scenario=scenario,
                        evidence_text=evidence_text,
                    ),
                    "unit": unit,
                    "scenario": scenario,
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

    def _forecast_value(
        self,
        target_name: str,
        unit: str,
        agent: Dict[str, Any],
        agent_idx: int,
        target_idx: int,
        pocket_idx: int,
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
        scenario: str,
        evidence_text: str,
    ) -> float | None:
        """Use prompt-supplied numeric anchors when target names expose a metric.

        This is not domain-specific. It activates for reusable constrained
        output types such as vote share, seats, turnout, probability, and index.
        """
        label, metric = self._target_label_and_metric(target_name)
        if not metric:
            return None
        evidence_anchor = self._extract_metric_anchor(label, metric, evidence_text)
        scenario_shift = self._scenario_shift(scenario, target_name, agent)
        agent_bias = self._agent_numeric_bias(agent, label, target_name)
        pocket_drift = pocket_idx * self._metric_drift(metric, scenario)
        jitter = self._stable_jitter(agent.get("agent_id"), target_name, scenario, evidence_text, span=2.4)

        if metric in {"vote_share", "seat_share", "share", "rate", "turnout", "index"}:
            base = evidence_anchor if evidence_anchor is not None else 50.0
            value = base + scenario_shift * 0.35 + agent_bias + pocket_drift + jitter
            return round(max(0.0, min(100.0, value)), 2)
        if metric == "probability":
            base = evidence_anchor if evidence_anchor is not None else self._probability_anchor_from_target(label, scenario)
            value = base + scenario_shift * 0.45 + agent_bias + pocket_drift + jitter
            return round(max(1.0, min(99.0, value)), 2)
        if metric in {"seats", "count"}:
            total = self._extract_total_count(evidence_text) or 100.0
            if evidence_anchor is None:
                evidence_anchor = total * 0.34
            value = evidence_anchor + scenario_shift * (total / 220.0) + agent_bias * (total / 140.0) + pocket_drift + jitter * (total / 100.0)
            return round(max(0.0, min(total, value)), 0)
        return None

    def _target_label_and_metric(self, target_name: str) -> Tuple[str, str]:
        name = _clean_name(target_name)
        suffixes = [
            "vote_share", "seat_share", "probability", "turnout", "seats",
            "share", "rate", "index", "count",
        ]
        for suffix in suffixes:
            if name == suffix:
                return "", suffix
            if name.endswith(f"_{suffix}"):
                return name[: -(len(suffix) + 1)], suffix
        if "probability_of_" in name:
            return name.replace("probability_of_", ""), "probability"
        return "", ""

    def _extract_metric_anchor(self, label: str, metric: str, evidence_text: str) -> float | None:
        text = evidence_text or ""
        if not text:
            return None
        label_terms = [term for term in re.split(r"[_\s/]+", label or "") if len(term) >= 2]
        if not label_terms and metric not in {"turnout"}:
            return None
        label_pattern = r"(?:%s)" % r"|".join(re.escape(term) for term in label_terms) if label_terms else ""
        values: List[float] = []

        if metric in {"vote_share", "seat_share", "share", "rate", "turnout", "index", "probability"}:
            if label_pattern:
                for match in re.finditer(label_pattern + r"[^%\n]{0,60}?(\d{1,3}(?:\.\d+)?)\s*%", text, flags=re.IGNORECASE):
                    values.append(float(match.group(1)))
            elif metric == "turnout":
                for match in re.finditer(r"turnout[^%\n]{0,50}?(\d{1,3}(?:\.\d+)?)\s*%", text, flags=re.IGNORECASE):
                    values.append(float(match.group(1)))
            return values[-1] if values else None

        if metric in {"seats", "count"} and label_pattern:
            for match in re.finditer(label_pattern + r"[^%\n]{0,35}?(\d{1,4})\s*(?:seats?|,|\.|\))", text, flags=re.IGNORECASE):
                value = float(match.group(1))
                if value <= 10000:
                    values.append(value)
            return values[-1] if values else None
        return None

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
        return 0.25 if any(term in scenario_l for term in ["surge", "resilience", "upside"]) else -0.05

    def _probability_anchor_from_target(self, label: str, scenario: str) -> float:
        label_l = str(label or "").lower()
        scenario_l = str(scenario or "").lower()
        if label_l and any(token in scenario_l for token in re.split(r"[_\s/]+", label_l) if len(token) >= 3):
            return 62.0
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
            r"(?:assembly size|total seats|seats)\s*[:=]?\s*(\d{2,4})",
            r"(\d{2,4})\s+seats",
        ]:
            match = match or re.search(pattern, evidence_text or "", flags=re.IGNORECASE)
        if not match:
            return 0.0
        value = float(match.group(1))
        return value if 1 <= value <= 10000 else 0.0

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

        for scenario_values in scenarios.values():
            self._normalize_points_to_total(scenario_values, vote_targets, 100.0)
            if total_seats:
                self._normalize_points_to_total(scenario_values, seat_targets, total_seats, integer=True)
        return scenarios

    def _normalize_points_to_total(
        self,
        scenario_values: Dict[str, Any],
        target_names: List[str],
        total: float,
        integer: bool = False,
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
            if residual_idx is not None and len(points) == len(target_names):
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
        final: Dict[str, Any] = {"scenario": base_key, "seat_forecast": {}, "vote_share_forecast": {}}
        for target in seat_targets:
            points = base.get(target) or []
            if points:
                label = target[: -len("_seats")]
                final["seat_forecast"][label] = points[-1].get("value")
        for target in vote_targets:
            points = base.get(target) or []
            if points:
                label = target[: -len("_vote_share")]
                final["vote_share_forecast"][label] = points[-1].get("value")
        if final["seat_forecast"]:
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
            for point in path:
                point_idx = pocket_order.get(point.get("date"), 0)
                total_delta = 0.0
                for adjustment in adjustments:
                    if point_idx < adjustment["pocket_idx"]:
                        continue
                    distance = point_idx - adjustment["pocket_idx"]
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
            value = float(current_value) + delta
        except (TypeError, ValueError):
            value = delta
        lowered = str(unit or "").lower()
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
    ) -> List[Dict[str, Any]]:
        transcript: List[Dict[str, Any]] = []
        output_index = self._index_agent_outputs(state.agent_outputs)
        evidence_notes = self._extract_evidence_notes(evidence_text)
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
                causal_context.append({
                    "agent": agent,
                    "forecast_sample": forecast_sample,
                })
                transcript.append(self._turn(
                    pocket_id,
                    label,
                    agent.get("agent_id"),
                    agent.get("name"),
                    self._agent_argument(agent, pocket, state.domain_plan, evidence_notes, forecast_sample),
                    turn_type="agent_argument",
                    metadata={
                        "role": agent.get("role"),
                        "target_variable": forecast_sample.get("target_variable"),
                        "base_value": forecast_sample.get("base"),
                        "downside_value": forecast_sample.get("downside"),
                        "confidence": forecast_sample.get("confidence"),
                    },
                ))

            for round_turn in self._debate_rounds(
                pocket=pocket,
                causal_context=causal_context,
                evidence_notes=evidence_notes,
                relationship_topology=relationship_topology,
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

    def _debate_rounds(
        self,
        pocket: Dict[str, Any],
        causal_context: List[Dict[str, Any]],
        evidence_notes: List[str],
        relationship_topology: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        turns: List[Dict[str, Any]] = []
        pocket_id = pocket.get("pocket_id")
        label = pocket.get("label")
        pairs = self._select_debate_pairs(causal_context, relationship_topology)

        for round_idx, (claimant_ctx, challenger_ctx) in enumerate(pairs, start=1):
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
                claimant_sample.get("target_variable")
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
                    "target_agent_id": claimant.get("agent_id"),
                    "contested_target": contested_target,
                    "claimant_base": claimant_sample.get("base"),
                    "challenger_base": challenger_sample.get("base"),
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
                    "challenger_agent_id": challenger.get("agent_id"),
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
        for edge in relationship_topology:
            if edge.get("relationship_type") not in {"opposes", "challenges_assumption", "audits", "information_gap"}:
                continue
            claimant = by_id.get(edge.get("target_agent_id"))
            challenger = by_id.get(edge.get("source_agent_id"))
            if not claimant or not challenger:
                continue
            key = (edge.get("target_agent_id"), edge.get("source_agent_id"))
            if key in seen:
                continue
            seen.add(key)
            pairs.append((claimant, challenger))
            if len(pairs) >= 4:
                break

        numeric_contexts = [
            ctx for ctx in causal_context
            if (ctx.get("forecast_sample") or {}).get("base") not in (None, "")
        ]
        non_numeric_contexts = [
            ctx for ctx in causal_context
            if (ctx.get("forecast_sample") or {}).get("base") in (None, "")
        ]
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
            scored.append({
                "name": agent.get("name"),
                "contribution_type": contribution_type,
                "target": sample.get("target_variable") or "non-numeric influence",
                "has_numeric": has_numeric,
                "evidence_score": evidence_score,
                "numeracy": cognitive.get("numeracy_score", "n/a"),
                "local": cognitive.get("local_knowledge_score", "n/a"),
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
            "Direction for next stage: Research Scout must find or expose missing source pointers for weak lanes; "
            "Data Retrieval Analyst must extract denominators/units; Quantitative Synthesizer may only adjust numeric paths from numeric-capable agents; "
            "common/ground agents should update behavioral pressure, trust, participation, demand, or refusal signals rather than pretend to be forecasters."
        )

    def _compact_eval_items(self, items: List[Dict[str, Any]]) -> str:
        return "; ".join(
            f"{item.get('name')}[{item.get('contribution_type')}, target={item.get('target')}, evidence={item.get('evidence_score')}, posture={item.get('posture')}]"
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
        source_cog = source.get("cognitive_profile") if isinstance(source.get("cognitive_profile"), dict) else {}
        target_cog = target.get("cognitive_profile") if isinstance(target.get("cognitive_profile"), dict) else {}
        source_stakes = source.get("stakes_profile") if isinstance(source.get("stakes_profile"), dict) else {}
        target_stakes = target.get("stakes_profile") if isinstance(target.get("stakes_profile"), dict) else {}
        return (
            f"{relationship_type} because {source.get('name')} has "
            f"{source_cog.get('game_theory_style', 'a different strategic lens')} and skin in the game "
            f"`{source_stakes.get('skin_in_the_game', 'not specified')}`, while {target.get('name')} has "
            f"`{target_stakes.get('skin_in_the_game', 'not specified')}` and a different evidence/skill profile "
            f"(local={target_cog.get('local_knowledge_score', 'n/a')}, numeracy={target_cog.get('numeracy_score', 'n/a')})."
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
        return (
            f"{claimant_name}, I am challenging your `{contested_target}` base `{claimant_base}`. "
            f"{relation}{background} The weak assumption is: {axis}. {fact_basis} "
            f"My narrower evidence lane is {evidence}. "
            f"Your downside `{claimant_downside}` is not enough if this channel is real; {pressure}. "
            f"In this round, do not restate your position. Tell us which assumption you will actually cut, defend, or reweight."
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
        return (
            f"{challenger_name}, I accept the challenge on `{contested_target}` but not your full conclusion. "
            f"{relation}{background} I am defending this part: {axis}. {fact_basis} "
            f"My counter-evidence lane is {defense}. "
            f"I will provisionally move my base from `{claimant_base}` to `{revised}` for this debate round, "
            "but only if the next pocket confirms the mechanism rather than just the headline signal."
        )

    def _background_claim(self, agent: Dict[str, Any]) -> str:
        persona = agent.get("persona") if isinstance(agent.get("persona"), dict) else {}
        background = persona.get("background")
        boundary = persona.get("knowledge_boundary")
        strong_topics = persona.get("speaks_strongly_about") or []
        pieces = []
        if background:
            pieces.append(f"My background matters here: {background}")
        if strong_topics:
            pieces.append(f"I can speak strongly about {', '.join(str(topic) for topic in strong_topics[:4])}.")
        if boundary:
            pieces.append(f"My boundary: {boundary}")
        return " ".join(pieces) + (" " if pieces else "")

    def _relationship_clause(self, relationship: Dict[str, Any]) -> str:
        if not relationship:
            return ""
        return (
            f"Our relationship is `{relationship.get('relationship_type')}` "
            f"(strength `{relationship.get('strength')}`): {relationship.get('rationale')}. "
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
            f"Round {round_idx} revision record: `{claimant.get('name')}` and `{challenger.get('name')}` "
            f"disagree on `{contested_target}` by approximately `{spread}` points. "
            f"The provisional mediated value is `{revised}`. This is not final output yet; it is a debated state input "
            "that should either harden or reverse in the next pocket."
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
        search_text = f"{role_l} {axis} {target} {pocket_label}".lower()
        wanted = {
            token for token in re.findall(r"[a-zA-Z][a-zA-Z0-9_/-]{3,}", search_text)
            if token not in {"whether", "claimant", "causal", "story", "enough", "another", "compared", "public", "pressure"}
        }
        wanted.update(["seat", "vote", "share", "turnout", "price", "rate", "index", "probability", "risk", "region", "scenario"])

        scored = []
        offset = self._stable_index(role_l, axis, target, pocket_label, modulo=max(1, len(evidence_notes)))
        ordered_notes = evidence_notes[offset:] + evidence_notes[:offset]
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
        left_tokens = {
            token for token in re.findall(r"[a-zA-Z][a-zA-Z0-9_]{3,}", str(left).lower())
            if token not in {"phase", "case", "scenario", "forecast", "share", "rate"}
        }
        right_l = str(right).lower()
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
        preferred = self._preferred_output_for_agent(outputs, agent or {})
        points = preferred.get("forecast_path") or []
        sample = {
            "target_variable": preferred.get("target_variable"),
            "confidence": preferred.get("confidence"),
        }
        pocket_points = [point for point in points if point.get("date") == pocket_label]
        for point in pocket_points:
            scenario = str(point.get("scenario") or "")
            if scenario:
                sample.setdefault("scenarios", {})[scenario] = point.get("value")
        primary = self._scenario_point_by_role(pocket_points, "primary")
        stress = self._scenario_point_by_role(pocket_points, "stress")
        if primary:
            sample["base"] = primary.get("value")
            sample["primary_scenario"] = primary.get("scenario")
        if stress:
            sample["downside"] = stress.get("value")
            sample["stress_scenario"] = stress.get("scenario")
        return sample

    def _preferred_output_for_agent(self, outputs: List[Dict[str, Any]], agent: Dict[str, Any]) -> Dict[str, Any]:
        role_tokens = self._role_tokens(agent)
        scored: List[Tuple[int, Dict[str, Any]]] = []
        for output in outputs:
            target_l = str(output.get("target_variable") or "").lower()
            score = sum(1 for token in role_tokens if token in target_l)
            if any(term in target_l for term in ["probability", "share", "rate", "seat", "price", "index", "turnout"]):
                score += 1
            scored.append((score, output))
        scored.sort(key=lambda item: item[0], reverse=True)
        if scored and scored[0][0] > 0:
            return scored[0][1]
        return outputs[0]

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
            if self._looks_like_section_heading(cleaned):
                current_section = cleaned[:80]
                continue
            has_signal = bool(re.search(
                r"\d|turnout|vote|seat|risk|advantage|poll|scenario|baseline|current|region|price|rate|share|probability|"
                r"agent|voter|consumer|worker|household|campaign|alliance|policy|jobs|corruption|welfare|identity|supply|demand",
                cleaned,
                re.IGNORECASE,
            ))
            if has_signal and 16 <= len(cleaned) <= 360:
                notes.append(f"{current_section} :: {cleaned}")
            if len(notes) >= 180:
                break
        return notes or ["The prompt provides the active evidence set; no external research packet was attached to this structured run."]

    def _looks_like_section_heading(self, cleaned: str) -> bool:
        if len(cleaned) > 90:
            return False
        if re.match(r"^\d{1,2}\.\s+", cleaned):
            return True
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
        return (
            "Source packet for this pocket: "
            + " | ".join(selected)
            + ". I am not adding unapproved post-cutoff facts; these are the evidence pointers agents may react to."
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
        return (
            f"I extracted numeric anchors for `{target_names}`. Relevant evidence rows for this pocket: "
            f"{' | '.join(selected_anchors) if selected_anchors else 'none detected'}. "
            "Numbers must be used as anchors, not as future actuals."
        )

    def _numeric_anchor_notes(self, evidence_text: str) -> List[str]:
        anchors = []
        for line in (evidence_text or "").splitlines():
            cleaned = re.sub(r"\s+", " ", line).strip(" -•\t")
            if not cleaned or len(cleaned) < 12:
                continue
            has_number = bool(re.search(r"\d", cleaned))
            has_context = bool(re.search(
                r"seat|vote|share|turnout|probability|index|poll|assembly|lok sabha|majority|phase|scenario|range|percent|%",
                cleaned,
                re.IGNORECASE,
            ))
            if has_number and has_context:
                anchors.append(cleaned[:260])
            if len(anchors) >= 120:
                break
        return anchors

    def _agent_argument(
        self,
        agent: Dict[str, Any],
        pocket: Dict[str, Any],
        domain_plan: Dict[str, Any],
        evidence_notes: List[str],
        forecast_sample: Dict[str, Any],
    ) -> str:
        role = str(agent.get("role") or agent.get("name") or "Agent")
        role_l = role.lower()
        target = forecast_sample.get("target_variable") or (domain_plan.get("target_variables") or [{"name": "primary_outcome"}])[0].get("name")
        base = forecast_sample.get("base")
        downside = forecast_sample.get("downside")
        confidence = forecast_sample.get("confidence")
        owns_numbers = (agent.get("numeric_capabilities") or {}).get("must_output_numbers", True)
        persona = agent.get("persona") if isinstance(agent.get("persona"), dict) else {}
        cognitive = agent.get("cognitive_profile") if isinstance(agent.get("cognitive_profile"), dict) else {}
        stakes = agent.get("stakes_profile") if isinstance(agent.get("stakes_profile"), dict) else {}
        evidence = self._select_role_evidence(role_l, evidence_notes, target=target, pocket_label=pocket.get("label") or "")
        disagreement = self._disagreement_claim(role_l)
        fact_basis = self._fact_basis(role_l, evidence_notes, disagreement, target=target, pocket_label=pocket.get("label") or "")
        numeric = (
            f"My number on `{target}` is base `{base}`, downside `{downside}`, confidence `{confidence}`."
            if owns_numbers and base is not None else
            f"I am not a numeric forecast owner for `{target}`. I contribute `{self._agent_contribution_type(agent)}`: what changes behavior, incentives, trust, attention, or constraints."
        )
        opening = persona.get("opening_position") or agent.get("causal_power") or "I am here to test one causal channel."
        vantage = persona.get("vantage_point") or self._role_lens(role_l)
        concern = persona.get("private_concern") or "the model is smoothing over a real disagreement"
        tension = persona.get("default_tension") or "one clean story may hide the actual mechanism"
        voice = persona.get("voice") or "direct and evidence-focused"
        skill_line = (
            f"My reasoning profile is IQ `{cognitive.get('iq_score', 'n/a')}`/{cognitive.get('iq_style', 'n/a')}, "
            f"EQ `{cognitive.get('eq_score', 'n/a')}`/{cognitive.get('eq_style', 'n/a')}, "
            f"game-theory `{cognitive.get('game_theory_score', 'n/a')}`/{cognitive.get('game_theory_style', 'n/a')}; "
            f"risk posture `{cognitive.get('risk_tolerance', 'n/a')}`."
        )
        stakes_line = (
            f"My skin in the game: {stakes.get('skin_in_the_game', 'not specified')}. "
            f"If wrong, I lose: {stakes.get('what_they_lose_if_wrong', 'credibility or decision quality')}. "
            f"My strategic posture is `{stakes.get('strategic_posture', 'truth-seeking')}`."
        )
        return (
            f"I am speaking as `{persona.get('character_name') or agent.get('name')}`, {voice}, from {vantage}. "
            f"My stake in this pocket is simple: {opening}. {skill_line} {stakes_line} In `{pocket.get('label')}`, {fact_basis} "
            f"My evidence lane is {evidence}. {disagreement} {numeric} My private worry is {concern}; the tension I want the room to confront is that "
            f"{tension}. If the next pocket changes the evidence quality, actor behavior, local participation, "
            "or scenario math, I will move my number rather than defend my first instinct."
        )

    def _role_lens(self, role_l: str) -> str:
        if any(term in role_l for term in ["strategist", "campaign", "party"]):
            return "I care about how organization, message discipline, and opponent mistakes convert into outcomes."
        if any(term in role_l for term in ["voter", "beneficiary", "rural", "urban", "worker", "youth", "poor", "middle"]):
            return "I care about lived incentives, turnout motivation, local trust, and whether promises feel credible."
        if any(term in role_l for term in ["pollster", "scientist", "quant", "data"]):
            return "I care about sample bias, historical baselines, regional heterogeneity, and uncertainty bands."
        if any(term in role_l for term in ["journalist", "media", "narrative"]):
            return "I care about which story voters hear repeatedly and which scandal or promise becomes salient."
        if any(term in role_l for term in ["watchdog", "auditor", "governance"]):
            return "I care about evidence quality, institutional credibility, and claims that may be overstated."
        if any(term in role_l for term in ["business", "industry"]):
            return "I care about jobs, investment, business sentiment, and whether economic promises are believable."
        if any(term in role_l for term in ["mediator", "negotiator", "alliance"]):
            return "I care about bargaining power, vote transfer, fragmentation, and whether allies actually coordinate."
        return "I care about the causal channel assigned to my role and whether it changes the forecast."

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
                for point in (targets.get(target) or []):
                    if point.get("date") == pocket_label:
                        scenario_values[scenario] = point.get("value")
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
