"""
Domain-general causal agent generation.

The engine expands planner archetypes into structured simulation agents with
incentives, information sets, biases, numeric responsibilities, and revision
rules. It deliberately returns schema-first data instead of prose personas.
"""

import uuid
from typing import Any, Dict, List, Optional

from ..config import Config
from ..utils.llm_client import LLMClient
from ..utils.logger import get_logger
from ..models.simulation_state import SimulationStateManager

logger = get_logger("horizonxl.services.agent_generation_engine")


_NON_NUMERIC_ROLE_HINTS = (
    "voter",
    "consumer",
    "worker",
    "household",
    "beneficiary",
    "community",
    "cohort",
    "common",
    "public",
    "people",
    "rural",
    "urban",
    "youth",
    "student",
    "citizen",
    "resident",
    "booth",
    "field",
    "observer",
    "witness",
    "diplomat",
    "planner",
    "adviser",
    "advisor",
    "hardliner",
    "coordinator",
    "representative",
    "strategist",
    "negotiator",
    "campaign",
    "party",
    "executive",
    "operator",
)

_NUMERIC_ROLE_HINTS = (
    "quant",
    "data",
    "pollster",
    "scientist",
    "economist",
    "researcher",
    "analyst",
    "forecaster",
    "model",
    "statistic",
    "expert",
    "auditor",
    "synthesizer",
    "retrieval",
)


def _infer_numeric_ownership(name: str, role: str) -> bool:
    text = f"{name} {role}".lower()
    if any(hint in text for hint in _NUMERIC_ROLE_HINTS):
        return True
    if any(hint in text for hint in _NON_NUMERIC_ROLE_HINTS):
        return False
    return True


class AgentGenerationEngine:
    """Generate causally meaningful structured agents from a domain plan."""

    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm_client = llm_client

    def generate_agents(
        self,
        domain_plan: Dict[str, Any],
        evidence_summary: str = "",
        use_llm: bool = True,
    ) -> List[Dict[str, Any]]:
        if not domain_plan:
            raise ValueError("domain_plan is required to generate agents.")

        if not use_llm:
            return SimulationStateManager.agents_from_plan(domain_plan)

        try:
            if self.llm_client is None:
                self.llm_client = LLMClient(timeout=Config.AGENT_LLM_TIMEOUT_SECONDS)
            agents = self._generate_with_llm(domain_plan, evidence_summary)
            return self._normalize_agents(agents, domain_plan)
        except Exception as exc:
            logger.warning(f"Agent engine LLM failed, using deterministic agents: {exc}")
            return SimulationStateManager.agents_from_plan(domain_plan)

    def _generate_with_llm(self, domain_plan: Dict[str, Any], evidence_summary: str) -> List[Dict[str, Any]]:
        generation_seed = domain_plan.get("generation_seed") or domain_plan.get("_generation_seed") or uuid.uuid4().hex[:12]
        system = (
            "You are Horizon XL's agent generation engine. Return only valid JSON. "
            "Generate structured causal agents, not shallow personas. Every agent "
            "must be useful for simulation, numeric forecasting, disagreement, and "
            "state revision. Keep all content in English. Treat each request as a "
            "fresh simulation run and do not reuse prior agent rosters."
        )
        prompt = f"""
Generate agents for this simulation plan.

Return JSON with this shape:
{{
  "agents": [
    {{
      "agent_id": "...",
      "name": "...",
      "domain": "...",
      "role": "...",
      "institutional_incentives": "...",
      "causal_power": "...",
      "information_set": ["..."],
      "trusted_data_sources": ["..."],
      "ignored_or_underweighted_data": ["..."],
      "forecasting_method": "...",
      "heuristics": ["..."],
      "biases": ["..."],
      "blind_spots": ["..."],
      "allowed_claims": ["..."],
      "forbidden_claims": ["..."],
      "evidence_scope": ["..."],
      "must_not_know": ["..."],
      "ground_truth_mode": "direct_lived_experience | expert_model | institutional_signal | source_audit | synthetic_process_control",
      "memory": {{
        "prior_beliefs": [],
        "past_revisions": [],
        "important_events_seen": []
      }},
      "numeric_capabilities": {{
        "must_output_numbers": true,
        "allowed_units": [],
        "confidence_required": true,
        "scenario_outputs_required": true
      }},
      "cognitive_profile": {{
        "iq_style": "...",
        "iq_score": 0,
        "eq_style": "...",
        "eq_score": 0,
        "game_theory_style": "...",
        "game_theory_score": 0,
        "numeracy_score": 0,
        "domain_expertise_score": 0,
        "local_knowledge_score": 0,
        "risk_tolerance": "...",
        "incentive_susceptibility": "...",
        "belief_update_temperament": "...",
        "debate_behavior": "..."
      }},
      "stakes_profile": {{
        "skin_in_the_game": "...",
        "what_they_gain_if_right": "...",
        "what_they_lose_if_wrong": "...",
        "payoff_horizon": "...",
        "public_position_pressure": "...",
        "private_information_pressure": "...",
        "strategic_posture": "..."
      }},
      "interaction_style": "...",
      "revision_rules": ["..."]
    }}
  ]
}}

The agents must answer:
- Who can causally move the target variable?
- Who has privileged information?
- Who reacts to whom?
- Who is biased?
- Who has incentives to understate or overstate risk?
- Who produces meaningful numerical forecasts?
- Who represents lived experience or ground truth?
- Who has high analytical skill, social/emotional reading, local knowledge, numeracy, or game-theory skill?
- Who can detect bluffing, coalition pressure, strategic silence, credible threats, coordination failures, or principal-agent problems?
- What does each agent personally or institutionally gain or lose from being right or wrong?
- What is each agent allowed to claim from its own information lane?
- What must each agent refuse to claim without evidence?
- What information must remain unknown because of cutoff, role, or ground-truth limits?

Simulation plan:
{domain_plan}

Uploaded/context/research evidence summary:
{evidence_summary[:8000]}

Fresh generation seed:
{generation_seed}

Use the seed only to vary secondary character framing, information access,
and interaction style. Preserve the core causal actors required by the plan.
Follow agent_population.allocations exactly when it is present: expand an
archetype into multiple agents when instance_count is greater than 1, use its
subtypes for variation, and include the moderator, mediator, evidence, quant,
external research, and data retrieval agents. Do not collapse the roster back to
a fixed set of 10 generic agents.

Domain-specific names are allowed only if they are present in the plan or
evidence summary. They must not come from hidden templates.
"""
        result = self.llm_client.chat_json(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=6000,
        )
        return result.get("agents") or []

    def _normalize_agents(self, agents: List[Dict[str, Any]], domain_plan: Dict[str, Any]) -> List[Dict[str, Any]]:
        fallback = SimulationStateManager.agents_from_plan(domain_plan)
        if not isinstance(agents, list) or not agents:
            return fallback

        target_units = [
            variable.get("unit", "unit")
            for variable in domain_plan.get("target_variables", [])
        ]
        domain = domain_plan.get("domain", "other")
        generation_seed = domain_plan.get("generation_seed") or domain_plan.get("_generation_seed") or uuid.uuid4().hex[:12]
        normalized = []
        for idx, agent in enumerate(agents, start=1):
            if not isinstance(agent, dict):
                continue
            name = str(agent.get("name") or agent.get("role") or f"Agent {idx}")
            agent_id = str(agent.get("agent_id") or f"agent_{idx:02d}_{uuid.uuid5(uuid.NAMESPACE_DNS, f'{generation_seed}:{idx}:{name}').hex[:8]}")
            numeric_capabilities = agent.get("numeric_capabilities") if isinstance(agent.get("numeric_capabilities"), dict) else {}
            numeric_owner = numeric_capabilities.get("must_output_numbers")
            if numeric_owner is None:
                numeric_owner = agent.get("numeric_output_required")
            if numeric_owner is None:
                numeric_owner = _infer_numeric_ownership(name, str(agent.get("role") or name))
            cognitive_profile = agent.get("cognitive_profile") if isinstance(agent.get("cognitive_profile"), dict) else {}
            stakes_profile = agent.get("stakes_profile") if isinstance(agent.get("stakes_profile"), dict) else {}
            normalized.append({
                "agent_id": agent_id,
                "name": name,
                "domain": str(agent.get("domain") or domain),
                "generation_seed": generation_seed,
                "role": str(agent.get("role") or name),
                "institutional_incentives": str(agent.get("institutional_incentives") or ""),
                "causal_power": str(agent.get("causal_power") or ""),
                "information_set": self._list(agent.get("information_set")),
                "trusted_data_sources": self._list(agent.get("trusted_data_sources")),
                "ignored_or_underweighted_data": self._list(agent.get("ignored_or_underweighted_data")),
                "forecasting_method": str(agent.get("forecasting_method") or "Structured causal forecast update."),
                "heuristics": self._list(agent.get("heuristics")),
                "biases": self._list(agent.get("biases")),
                "blind_spots": self._list(agent.get("blind_spots")),
                "allowed_claims": self._list(agent.get("allowed_claims")) or self._default_allowed_claims(name, agent),
                "forbidden_claims": self._list(agent.get("forbidden_claims")) or self._default_forbidden_claims(name, agent),
                "evidence_scope": self._list(agent.get("evidence_scope")) or self._default_evidence_scope(name, agent),
                "must_not_know": self._list(agent.get("must_not_know")) or [
                    "Post-cutoff outcomes or actual future results unless explicitly provided by the user as allowed evidence.",
                    "Private information outside this role's information lane.",
                ],
                "ground_truth_mode": str(agent.get("ground_truth_mode") or self._infer_ground_truth_mode(name, str(agent.get("role") or name))),
                "memory": {
                    "prior_beliefs": self._list((agent.get("memory") or {}).get("prior_beliefs")),
                    "past_revisions": self._list((agent.get("memory") or {}).get("past_revisions")),
                    "important_events_seen": self._list((agent.get("memory") or {}).get("important_events_seen")),
                },
                "numeric_capabilities": {
                    "must_output_numbers": bool(numeric_owner),
                    "allowed_units": self._list(numeric_capabilities.get("allowed_units")) or target_units,
                    "confidence_required": bool(numeric_capabilities.get("confidence_required", True)),
                    "scenario_outputs_required": bool(numeric_capabilities.get("scenario_outputs_required", True)),
                },
                "cognitive_profile": self._normalize_cognitive_profile(cognitive_profile, name, idx),
                "stakes_profile": self._normalize_stakes_profile(stakes_profile, name, idx),
                "interaction_style": str(agent.get("interaction_style") or "Evidence-based forecast revision."),
                "revision_rules": self._list(agent.get("revision_rules")) or [
                    "Revise forecasts when new evidence materially changes state variables, causal assumptions, or scenario likelihoods."
                ],
                "persona": agent.get("persona") if isinstance(agent.get("persona"), dict) else {},
            })
        if len(normalized) < max(1, int(len(fallback) * 0.75)):
            logger.warning(
                "Agent engine LLM returned %s agents but population plan requires about %s; using deterministic expansion.",
                len(normalized),
                len(fallback),
            )
            return fallback
        return normalized or fallback

    def _infer_ground_truth_mode(self, name: str, role: str) -> str:
        text = f"{name} {role}".lower()
        if any(term in text for term in ["voter", "consumer", "worker", "household", "patient", "student", "beneficiary", "community"]):
            return "direct_lived_experience"
        if any(term in text for term in ["pollster", "quant", "scientist", "economist", "analyst", "research"]):
            return "expert_model"
        if any(term in text for term in ["auditor", "watchdog", "data retrieval"]):
            return "source_audit"
        if any(term in text for term in ["moderator", "mediator", "synthesizer"]):
            return "synthetic_process_control"
        return "institutional_signal"

    def _default_allowed_claims(self, name: str, agent: Dict[str, Any]) -> List[str]:
        return [
            f"{name} may make claims grounded in its role, evidence scope, incentives, and stated information set.",
            "May describe uncertainty, local observations, incentives, and conditional assumptions.",
        ]

    def _default_forbidden_claims(self, name: str, agent: Dict[str, Any]) -> List[str]:
        return [
            "Must not claim private facts, future actuals, hidden polling, proprietary data, or exact outcomes unless present in approved evidence.",
            "Must not convert personal/lived experience into a full numeric forecast unless assigned numeric capability.",
        ]

    def _default_evidence_scope(self, name: str, agent: Dict[str, Any]) -> List[str]:
        scope = self._list(agent.get("trusted_data_sources"))
        if scope:
            return scope
        return [
            "approved_domain_contract",
            "graph_evidence",
            "approved_external_research",
            "role_specific_lived_or_institutional_context",
        ]

    def _normalize_cognitive_profile(self, value: Dict[str, Any], name: str, idx: int) -> Dict[str, Any]:
        def score(key: str, fallback: int) -> int:
            try:
                return max(0, min(100, int(value.get(key, fallback))))
            except (TypeError, ValueError):
                return fallback

        return {
            "iq_style": str(value.get("iq_style") or "evidence-weighted causal reasoning"),
            "iq_score": score("iq_score", 70 + (idx % 17)),
            "eq_style": str(value.get("eq_style") or "reads incentives, trust, fear, pride, and fatigue in other agents"),
            "eq_score": score("eq_score", 66 + (idx % 19)),
            "game_theory_style": str(value.get("game_theory_style") or "tracks strategic incentives, signaling, coordination failure, and credible commitments"),
            "game_theory_score": score("game_theory_score", 64 + (idx % 23)),
            "numeracy_score": score("numeracy_score", 58 + (idx % 31)),
            "domain_expertise_score": score("domain_expertise_score", 62 + (idx % 29)),
            "local_knowledge_score": score("local_knowledge_score", 55 + (idx % 37)),
            "risk_tolerance": str(value.get("risk_tolerance") or ["risk-averse", "risk-balanced", "risk-seeking", "tail-risk-sensitive"][idx % 4]),
            "incentive_susceptibility": str(value.get("incentive_susceptibility") or "medium; reacts to reputation and institutional pressure"),
            "belief_update_temperament": str(value.get("belief_update_temperament") or "revises when counter-evidence changes incentives or numeric anchors"),
            "debate_behavior": str(value.get("debate_behavior") or f"{name} must challenge weak assumptions instead of agreeing politely"),
        }

    def _normalize_stakes_profile(self, value: Dict[str, Any], name: str, idx: int) -> Dict[str, Any]:
        return {
            "skin_in_the_game": str(value.get("skin_in_the_game") or f"{name} has reputational or material exposure to the simulated outcome."),
            "what_they_gain_if_right": str(value.get("what_they_gain_if_right") or ["credibility", "strategic advantage", "material protection", "coalition leverage"][idx % 4]),
            "what_they_lose_if_wrong": str(value.get("what_they_lose_if_wrong") or "trust, resources, influence, or decision quality"),
            "payoff_horizon": str(value.get("payoff_horizon") or ["immediate", "short-term", "medium-term", "long-term"][idx % 4]),
            "public_position_pressure": str(value.get("public_position_pressure") or ["low", "medium", "high", "very high"][idx % 4]),
            "private_information_pressure": str(value.get("private_information_pressure") or ["low", "medium", "high", "conflicted"][(idx + 1) % 4]),
            "strategic_posture": str(value.get("strategic_posture") or ["truth-seeking", "defensive", "opportunistic", "coalition-building", "risk-warning"][idx % 5]),
        }

    def _list(self, value: Any) -> List[Any]:
        if isinstance(value, list):
            return value
        if value is None or value == "":
            return []
        return [value]
