"""Forecast control artifacts for Horizon XL structured runs.

These objects are deliberately domain-general. They turn the Domain Contract
and generated agents into the spine of the simulation:

Forecast Thesis -> Assumptions -> Disputes -> Readiness -> Ledger/Report.

The goal is to stop the system from treating "agents talked" as success. A
run is only debate-ready when the room has a thesis to attack, assumptions to
test, and disputes that map agents to target variables.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, Iterable, List, Tuple


BAD_TARGET_PATTERNS = [
    r"following_numeric_outputs?",
    r"probability_bands_such_as",
    r"required_outputs?",
    r"output_requirements?",
    r"scenario_comparison",
    r"agent_disagreement",
    r"missing_data",
    r"confidence_band",
    r"table|chart|report|appendix",
]


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _slug(value: Any) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", str(value or "").lower()).strip("_")
    return re.sub(r"_+", "_", slug)


def _short(value: Any, limit: int = 180) -> str:
    text = _clean(value)
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _stable_id(prefix: str, *parts: Any) -> str:
    digest = hashlib.sha1("::".join(str(part) for part in parts).encode("utf-8")).hexdigest()[:10]
    return f"{prefix}_{digest}"


def _is_bad_target(name: str) -> bool:
    cleaned = _slug(name)
    if not cleaned or cleaned in {"primary_outcome", "target_variable", "numeric_outputs"}:
        return True
    return any(re.search(pattern, cleaned) for pattern in BAD_TARGET_PATTERNS)


def _target_rows(domain_plan: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = []
    for item in domain_plan.get("target_variables") or []:
        if not isinstance(item, dict):
            continue
        name = _clean(item.get("name"))
        if not name or _is_bad_target(name):
            continue
        rows.append(item)
    return rows


def _evidence_cards(evidence_text: str, graph_brain: Dict[str, Any] | None = None, limit: int = 8) -> List[str]:
    cards: List[str] = []
    graph_brain = graph_brain or {}
    for card in graph_brain.get("evidence_cards") or []:
        if isinstance(card, str) and card.strip():
            cards.append(_short(card, 240))

    for line in str(evidence_text or "").splitlines():
        line = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", line).strip()
        if not line:
            continue
        has_number = re.search(r"\b(?:19|20)\d{2}\b|\d[\d,.]*(?:%| percent| seats?| votes?| usd| dollars?| barrels?| tons?| months?| years?)\b", line, re.I)
        has_context = re.search(r"\b(baseline|poll|survey|price|rate|share|turnout|risk|supply|demand|battle|alliance|evidence|reported)\b", line, re.I)
        is_instruction = re.search(r"\b(do not|must|should|required output|produce|generate report|copy paste)\b", line, re.I)
        if (has_number or has_context) and not is_instruction:
            cards.append(_short(line, 240))

    deduped = list(dict.fromkeys(cards))
    return deduped[:limit]


class ForecastArtifactBuilder:
    """Build thesis, assumptions, disputes, and readiness from structured inputs."""

    def build_all(
        self,
        *,
        domain_plan: Dict[str, Any],
        agents: List[Dict[str, Any]],
        evidence_text: str = "",
        graph_brain: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        thesis = self.build_thesis(domain_plan, evidence_text, graph_brain)
        assumptions = self.build_assumptions(domain_plan, evidence_text, graph_brain)
        disputes = self.build_disputes(domain_plan, agents, assumptions, evidence_text, graph_brain)
        readiness = self.readiness(domain_plan, agents, thesis, assumptions, disputes, graph_brain)
        return {
            "forecast_thesis": thesis,
            "assumption_registry": assumptions,
            "dispute_registry": disputes,
            "debate_readiness": readiness,
        }

    def build_thesis(
        self,
        domain_plan: Dict[str, Any],
        evidence_text: str = "",
        graph_brain: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        domain = _clean(domain_plan.get("domain") or "future simulation")
        targets = _target_rows(domain_plan)
        target_names = [_clean(item.get("name")) for item in targets[:6]]
        horizon = domain_plan.get("forecast_horizon") or {}
        horizon_label = " to ".join(
            part for part in [_clean(horizon.get("start")), _clean(horizon.get("end"))] if part
        ) or _clean(horizon.get("granularity") or "the requested horizon")
        evidence = _evidence_cards(evidence_text, graph_brain, limit=4)
        statement_target = ", ".join(target_names[:3]) or "the requested outcome"
        statement = (
            f"The central forecast for {domain} over {horizon_label} depends on whether "
            f"the strongest evidence-backed drivers move {statement_target} more than the countervailing risks."
        )
        if evidence:
            statement += f" The opening thesis leans on: {evidence[0]}"
        return {
            "thesis_id": _stable_id("thesis", domain, statement_target, horizon_label),
            "statement": statement,
            "domain": domain,
            "horizon": horizon_label,
            "confidence": "medium" if evidence else "low",
            "core_drivers": self._core_drivers(domain_plan, evidence),
            "known_challenges": self._known_challenges(domain_plan, evidence),
            "linked_targets": target_names,
            "status": "draft",
        }

    def build_assumptions(
        self,
        domain_plan: Dict[str, Any],
        evidence_text: str = "",
        graph_brain: Dict[str, Any] | None = None,
    ) -> List[Dict[str, Any]]:
        targets = _target_rows(domain_plan)
        state_vars = [
            item for item in domain_plan.get("state_variables") or []
            if isinstance(item, dict) and _clean(item.get("name"))
        ]
        evidence = _evidence_cards(evidence_text, graph_brain, limit=10)
        rows: List[Dict[str, Any]] = []

        for idx, target in enumerate(targets[:8], start=1):
            target_name = _clean(target.get("name"))
            unit = _clean(target.get("unit") or "unit")
            driver = _clean((state_vars[(idx - 1) % len(state_vars)].get("name") if state_vars else "")) or "the strongest observed driver"
            support = evidence[(idx - 1) % len(evidence)] if evidence else ""
            rows.append({
                "assumption_id": _stable_id("assumption", target_name, driver),
                "statement": f"{target_name} can be moved by {driver} in a way that is visible within the simulation horizon.",
                "importance": "critical" if idx <= 3 else "high",
                "confidence": "medium" if support else "low",
                "supports_targets": [target_name],
                "threatens_targets": [target_name],
                "supporting_evidence": [support] if support else [],
                "challenging_evidence": [],
                "challenged_by_agents": [],
                "status": "active",
                "unit": unit,
            })

        if not rows:
            rows.append({
                "assumption_id": _stable_id("assumption", domain_plan.get("domain"), "fallback"),
                "statement": "The prompt contains enough domain context to define a forecastable outcome.",
                "importance": "critical",
                "confidence": "low",
                "supports_targets": [],
                "threatens_targets": [],
                "supporting_evidence": evidence[:1],
                "challenging_evidence": [],
                "challenged_by_agents": [],
                "status": "active",
            })
        return rows

    def build_disputes(
        self,
        domain_plan: Dict[str, Any],
        agents: List[Dict[str, Any]],
        assumptions: List[Dict[str, Any]],
        evidence_text: str = "",
        graph_brain: Dict[str, Any] | None = None,
    ) -> List[Dict[str, Any]]:
        targets = _target_rows(domain_plan)
        evidence = _evidence_cards(evidence_text, graph_brain, limit=10)
        numeric_agents, qualitative_agents = self._split_agents(agents)
        rows: List[Dict[str, Any]] = []
        for idx, target in enumerate(targets[:8], start=1):
            target_name = _clean(target.get("name"))
            assumption = assumptions[(idx - 1) % len(assumptions)] if assumptions else {}
            side_a_agents = [agent.get("agent_id") for agent in numeric_agents[idx - 1:idx + 1] if agent.get("agent_id")]
            side_b_pool = qualitative_agents or agents
            side_b_agents = [agent.get("agent_id") for agent in side_b_pool[idx - 1:idx + 1] if agent.get("agent_id")]
            if not side_a_agents and agents:
                side_a_agents = [agents[0].get("agent_id")]
            if not side_b_agents and len(agents) > 1:
                side_b_agents = [agents[-1].get("agent_id")]
            rows.append({
                "dispute_id": _stable_id("dispute", target_name, assumption.get("assumption_id"), idx),
                "question": f"What would make the forecast for {target_name} move materially higher or lower?",
                "side_a": {
                    "claim": f"The measurable evidence supports a disciplined central estimate for {target_name}.",
                    "agents": [item for item in side_a_agents if item],
                    "evidence": evidence[idx - 1:idx + 1],
                },
                "side_b": {
                    "claim": f"The lived, strategic, or institutional context may make {target_name} diverge from the central estimate.",
                    "agents": [item for item in side_b_agents if item],
                    "evidence": evidence[idx:idx + 2],
                },
                "linked_targets": [target_name],
                "linked_assumptions": [assumption.get("assumption_id")] if assumption.get("assumption_id") else [],
                "required_for_debate": True,
                "status": "unresolved",
            })

        if not rows:
            rows.append({
                "dispute_id": _stable_id("dispute", domain_plan.get("domain"), "fallback"),
                "question": "Is the prompt sufficiently specified to support a meaningful forecast?",
                "side_a": {"claim": "There is enough context to proceed with low confidence.", "agents": [], "evidence": evidence[:2]},
                "side_b": {"claim": "The run needs clearer targets before debate.", "agents": [], "evidence": []},
                "linked_targets": [],
                "linked_assumptions": [assumptions[0].get("assumption_id")] if assumptions else [],
                "required_for_debate": True,
                "status": "unresolved",
            })
        return rows

    def readiness(
        self,
        domain_plan: Dict[str, Any],
        agents: List[Dict[str, Any]],
        thesis: Dict[str, Any],
        assumptions: List[Dict[str, Any]],
        disputes: List[Dict[str, Any]],
        graph_brain: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        blocking: List[str] = []
        warnings: List[str] = []
        graph_brain = graph_brain or {}
        targets = _target_rows(domain_plan)
        contract = domain_plan.get("domain_contract") or {}
        engine_mode = contract.get("engine_mode") or domain_plan.get("engine_mode") or ""

        if not domain_plan:
            blocking.append("Domain Contract/domain plan is missing.")
        if not targets:
            blocking.append("No meaningful target variables survived target validation.")
        if not thesis or not thesis.get("statement"):
            blocking.append("Forecast Thesis is missing.")
        if not assumptions:
            blocking.append("Assumption Registry is empty.")
        if not disputes:
            blocking.append("Dispute Registry is empty.")
        if agents and not self._has_control_role(agents):
            blocking.append("No control-room role exists: moderator, mediator, auditor, research, or quant.")
        if not agents:
            blocking.append("No agents are available.")
        if engine_mode == "social_discourse":
            warnings.append("Legacy social_discourse engine selected; this should only happen for explicit social discourse prompts.")
        for target in targets:
            if _is_bad_target(target.get("name")):
                blocking.append(f"Instruction fragment survived as target: {target.get('name')}")
        if graph_brain.get("mode") in {"degraded_zep_error", "degraded"}:
            warnings.append("Graph memory is degraded; debate may proceed only with explicit evidence caveats.")

        agent_ids = {agent.get("agent_id") for agent in agents if agent.get("agent_id")}
        unmapped_disputes = []
        for dispute in disputes:
            mapped = set((dispute.get("side_a") or {}).get("agents") or []) | set((dispute.get("side_b") or {}).get("agents") or [])
            if agent_ids and not (mapped & agent_ids):
                unmapped_disputes.append(dispute.get("dispute_id"))
        if unmapped_disputes:
            blocking.append("Disputes are not mapped to active agents: " + ", ".join(unmapped_disputes[:6]))

        score = 100
        score -= len(blocking) * 22
        score -= len(warnings) * 6
        return {
            "ready": not blocking,
            "score": max(0, min(100, score)),
            "blocking_issues": blocking,
            "warnings": warnings,
        }

    def _core_drivers(self, domain_plan: Dict[str, Any], evidence: List[str]) -> List[str]:
        drivers = [
            _clean(item.get("name"))
            for item in domain_plan.get("state_variables") or []
            if isinstance(item, dict) and _clean(item.get("name"))
        ]
        if evidence:
            drivers.extend(_short(card, 100) for card in evidence[:3])
        return list(dict.fromkeys(drivers))[:6]

    def _known_challenges(self, domain_plan: Dict[str, Any], evidence: List[str]) -> List[str]:
        scenarios = domain_plan.get("scenario_structure") or {}
        rows = []
        scenario_items = scenarios.get("scenarios") or [] if isinstance(scenarios, dict) else []
        for item in scenario_items:
            if isinstance(item, dict):
                rows.append(_clean(item.get("name") or item.get("id") or item.get("description")))
        if not rows:
            rows = [
                "Evidence may be incomplete or unevenly distributed across actors.",
                "The central path may hide tail risk or local variation.",
                "Agent incentives may distort public statements or stated preferences.",
            ]
        return [row for row in rows if row][:6]

    def _split_agents(self, agents: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        numeric = []
        qualitative = []
        for agent in agents:
            caps = agent.get("numeric_capabilities") or {}
            role = " ".join([str(agent.get("name") or ""), str(agent.get("role") or "")]).lower()
            if caps.get("must_output_numbers") or re.search(r"\b(quant|data|poll|research|analyst|forecaster|economist|model)\b", role):
                numeric.append(agent)
            else:
                qualitative.append(agent)
        return numeric, qualitative

    def _has_control_role(self, agents: Iterable[Dict[str, Any]]) -> bool:
        for agent in agents:
            text = " ".join([str(agent.get("name") or ""), str(agent.get("role") or ""), str(agent.get("agent_kind") or "")]).lower()
            if re.search(r"\b(moderator|mediator|auditor|research|quant|synthesizer|data retrieval)\b", text):
                return True
        return False
