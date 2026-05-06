"""
Domain-general causal agent generation.

The engine expands planner archetypes into structured simulation agents with
incentives, information sets, biases, numeric responsibilities, and revision
rules. It deliberately returns schema-first data instead of prose personas.
"""

import uuid
from typing import Any, Dict, List, Optional

from ..utils.llm_client import LLMClient
from ..utils.logger import get_logger
from ..models.simulation_state import SimulationStateManager

logger = get_logger("horizonxl.services.agent_generation_engine")


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
                self.llm_client = LLMClient()
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
                "memory": {
                    "prior_beliefs": self._list((agent.get("memory") or {}).get("prior_beliefs")),
                    "past_revisions": self._list((agent.get("memory") or {}).get("past_revisions")),
                    "important_events_seen": self._list((agent.get("memory") or {}).get("important_events_seen")),
                },
                "numeric_capabilities": {
                    "must_output_numbers": bool(numeric_capabilities.get("must_output_numbers", True)),
                    "allowed_units": self._list(numeric_capabilities.get("allowed_units")) or target_units,
                    "confidence_required": bool(numeric_capabilities.get("confidence_required", True)),
                    "scenario_outputs_required": bool(numeric_capabilities.get("scenario_outputs_required", True)),
                },
                "interaction_style": str(agent.get("interaction_style") or "Evidence-based forecast revision."),
                "revision_rules": self._list(agent.get("revision_rules")) or [
                    "Revise forecasts when new evidence materially changes state variables, causal assumptions, or scenario likelihoods."
                ],
            })
        if len(normalized) < max(1, int(len(fallback) * 0.75)):
            logger.warning(
                "Agent engine LLM returned %s agents but population plan requires about %s; using deterministic expansion.",
                len(normalized),
                len(fallback),
            )
            return fallback
        return normalized or fallback

    def _list(self, value: Any) -> List[Any]:
        if isinstance(value, list):
            return value
        if value is None or value == "":
            return []
        return [value]
