"""
Structured simulation state persistence.

This file-backed model is the bridge from the current graph/report pipeline to
the new architecture where every output must be derived from a single validated
simulation state.
"""

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..config import Config


STRUCTURED_STATE_FILENAME = "structured_state.json"


@dataclass
class StructuredSimulationState:
    simulation_id: str
    project_id: str
    domain_plan: Dict[str, Any] = field(default_factory=dict)
    agents: List[Dict[str, Any]] = field(default_factory=list)
    time_pockets: List[Dict[str, Any]] = field(default_factory=list)
    state_variables: List[Dict[str, Any]] = field(default_factory=list)
    agent_outputs: List[Dict[str, Any]] = field(default_factory=list)
    scenario_outputs: Dict[str, Any] = field(default_factory=dict)
    aggregated_outputs: Dict[str, Any] = field(default_factory=dict)
    validation: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StructuredSimulationState":
        return cls(
            simulation_id=data["simulation_id"],
            project_id=data["project_id"],
            domain_plan=data.get("domain_plan") or {},
            agents=data.get("agents") or [],
            time_pockets=data.get("time_pockets") or [],
            state_variables=data.get("state_variables") or [],
            agent_outputs=data.get("agent_outputs") or [],
            scenario_outputs=data.get("scenario_outputs") or {},
            aggregated_outputs=data.get("aggregated_outputs") or {},
            validation=data.get("validation") or {},
            created_at=data.get("created_at") or datetime.now().isoformat(),
            updated_at=data.get("updated_at") or datetime.now().isoformat(),
        )


class SimulationStateManager:
    """Read/write structured simulation state JSON for a simulation."""

    @classmethod
    def _simulation_dir(cls, simulation_id: str) -> str:
        return os.path.join(Config.OASIS_SIMULATION_DATA_DIR, simulation_id)

    @classmethod
    def _state_path(cls, simulation_id: str) -> str:
        return os.path.join(cls._simulation_dir(simulation_id), STRUCTURED_STATE_FILENAME)

    @classmethod
    def exists(cls, simulation_id: str) -> bool:
        return os.path.exists(cls._state_path(simulation_id))

    @classmethod
    def initialize(
        cls,
        simulation_id: str,
        project_id: str,
        domain_plan: Optional[Dict[str, Any]] = None,
        agents: Optional[List[Dict[str, Any]]] = None,
    ) -> StructuredSimulationState:
        domain_plan = domain_plan or {}
        state = StructuredSimulationState(
            simulation_id=simulation_id,
            project_id=project_id,
            domain_plan=domain_plan,
            agents=agents or cls.agents_from_plan(domain_plan),
            time_pockets=cls.time_pockets_from_plan(domain_plan),
            state_variables=domain_plan.get("state_variables") or [],
            scenario_outputs=cls.empty_scenario_outputs(domain_plan),
        )
        return cls.save(state)

    @classmethod
    def load(cls, simulation_id: str) -> Optional[StructuredSimulationState]:
        path = cls._state_path(simulation_id)
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as handle:
            return StructuredSimulationState.from_dict(json.load(handle))

    @classmethod
    def save(cls, state: StructuredSimulationState) -> StructuredSimulationState:
        os.makedirs(cls._simulation_dir(state.simulation_id), exist_ok=True)
        state.updated_at = datetime.now().isoformat()
        with open(cls._state_path(state.simulation_id), "w", encoding="utf-8") as handle:
            json.dump(state.to_dict(), handle, ensure_ascii=False, indent=2)
        return state

    @classmethod
    def update_validation(cls, simulation_id: str, validation: Dict[str, Any]) -> Optional[StructuredSimulationState]:
        state = cls.load(simulation_id)
        if not state:
            return None
        state.validation = validation or {}
        return cls.save(state)

    @classmethod
    def append_agent_output(cls, simulation_id: str, output: Dict[str, Any]) -> Optional[StructuredSimulationState]:
        state = cls.load(simulation_id)
        if not state:
            return None
        state.agent_outputs.append(output)
        return cls.save(state)

    @classmethod
    def agents_from_plan(cls, domain_plan: Dict[str, Any]) -> List[Dict[str, Any]]:
        agents = []
        domain = domain_plan.get("domain", "other")
        generation_seed = domain_plan.get("generation_seed") or domain_plan.get("_generation_seed") or uuid.uuid4().hex[:12]
        population = domain_plan.get("agent_population") or {}
        allocations = {
            item.get("archetype_name"): item
            for item in population.get("allocations", [])
            if isinstance(item, dict)
        }

        idx = 0
        for archetype_index, archetype in enumerate(domain_plan.get("required_agent_archetypes") or [], start=1):
            name = archetype.get("name") or f"Agent {idx}"
            allocation = allocations.get(name) or {}
            instance_count = int(
                allocation.get("instance_count")
                or archetype.get("instance_count")
                or 1
            )
            instance_count = max(1, min(25, instance_count))
            subtypes = allocation.get("subtypes") or archetype.get("subtypes") or []
            for copy_index in range(1, instance_count + 1):
                idx += 1
                subtype = subtypes[(copy_index - 1) % len(subtypes)] if subtypes else ""
                display_name = name if instance_count == 1 else f"{name} {copy_index}"
                if subtype:
                    display_name = f"{display_name}: {subtype}"
                agents.append({
                    "agent_id": f"agent_{idx:02d}_{uuid.uuid5(uuid.NAMESPACE_DNS, f'{generation_seed}:{idx}:{name}').hex[:8]}",
                    "name": display_name,
                    "domain": domain,
                    "generation_seed": generation_seed,
                    "role": name,
                    "archetype": name,
                    "population_copy_index": copy_index,
                    "population_instance_count": instance_count,
                    "population_share": allocation.get("population_share") or archetype.get("population_share"),
                    "subtype": subtype,
                    "institutional_incentives": archetype.get("likely_bias", ""),
                    "causal_power": archetype.get("causal_role", ""),
                    "information_set": [archetype.get("information_advantage", "")],
                    "trusted_data_sources": [],
                    "ignored_or_underweighted_data": [],
                    "forecasting_method": "Context-specific judgment constrained by structured numeric outputs.",
                    "heuristics": [],
                    "biases": [archetype.get("likely_bias", "")] if archetype.get("likely_bias") else [],
                    "blind_spots": [],
                    "memory": {
                        "prior_beliefs": [],
                        "past_revisions": [],
                        "important_events_seen": [],
                    },
                    "numeric_capabilities": {
                        "must_output_numbers": bool(archetype.get("numeric_output_required", True)),
                        "allowed_units": [
                            variable.get("unit", "unit")
                            for variable in domain_plan.get("target_variables", [])
                        ],
                        "confidence_required": True,
                        "scenario_outputs_required": True,
                    },
                    "interaction_style": "Structured forecast update with concise reasoning.",
                    "revision_rules": [
                        "Update forecasts only when new evidence changes the state variables or scenario probabilities."
                    ],
                })
        for special_group, agent_kind in [
            ("orchestration_agents", "orchestrator"),
            ("research_agents", "research"),
        ]:
            for template in population.get(special_group, []) or []:
                for copy_index in range(1, int(template.get("count", 1)) + 1):
                    idx += 1
                    name = template.get("name") or f"{agent_kind.title()} Agent"
                    agents.append({
                        "agent_id": f"agent_{idx:02d}_{uuid.uuid5(uuid.NAMESPACE_DNS, f'{generation_seed}:{idx}:{name}:{agent_kind}').hex[:8]}",
                        "name": name if copy_index == 1 else f"{name} {copy_index}",
                        "domain": domain,
                        "generation_seed": generation_seed,
                        "role": name,
                        "archetype": name,
                        "agent_kind": agent_kind,
                        "institutional_incentives": "Maintain simulation quality, focus, and evidence discipline.",
                        "causal_power": template.get("role", ""),
                        "information_set": [template.get("role", "")],
                        "trusted_data_sources": ["simulation_state", "graph_evidence", "approved_external_research"],
                        "ignored_or_underweighted_data": [],
                        "forecasting_method": "Process-control and evidence-validation role.",
                        "heuristics": ["Keep discussion grounded in target variables and validated evidence."],
                        "biases": [],
                        "blind_spots": ["Does not represent a causal stakeholder unless explicitly assigned."],
                        "memory": {
                            "prior_beliefs": [],
                            "past_revisions": [],
                            "important_events_seen": [],
                        },
                        "numeric_capabilities": {
                            "must_output_numbers": bool(template.get("numeric_output_required", False)),
                            "allowed_units": [
                                variable.get("unit", "unit")
                                for variable in domain_plan.get("target_variables", [])
                            ],
                            "confidence_required": bool(template.get("numeric_output_required", False)),
                            "scenario_outputs_required": bool(template.get("numeric_output_required", False)),
                        },
                        "interaction_style": "Moderate, audit, research, or synthesize without inventing unsupported facts.",
                        "revision_rules": [
                            "Escalate missing evidence, numerical gaps, or off-topic drift before the next simulation pocket."
                        ],
                    })
        return agents

    @classmethod
    def empty_scenario_outputs(cls, domain_plan: Dict[str, Any]) -> Dict[str, Any]:
        scenarios = domain_plan.get("scenario_structure") or {}
        scenario_names = {
            "base_case": "base",
            "upside_case": "upside",
            "downside_case": "downside",
            "tail_case": "tail",
        }
        return {
            scenario_name: {}
            for flag, scenario_name in scenario_names.items()
            if scenarios.get(flag, True)
        }

    @classmethod
    def time_pockets_from_plan(cls, domain_plan: Dict[str, Any]) -> List[Dict[str, Any]]:
        horizon = domain_plan.get("forecast_horizon") or {}
        granularity = horizon.get("granularity") or "event_triggered"
        start = horizon.get("start") or "auto"
        end = horizon.get("end") or "auto"
        label = f"{granularity} simulation pocket"
        if start == "auto" or end == "auto":
            return [{
                "pocket_id": "pocket_001",
                "label": label,
                "start": start,
                "end": end,
                "events": [],
                "state_before": {},
                "agent_actions": [],
                "agent_forecasts": [],
                "cross_agent_interactions": [],
                "state_after": {},
                "triggered_revisions": [],
            }]

        return [{
            "pocket_id": "pocket_001",
            "label": label,
            "start": start,
            "end": end,
            "events": [],
            "state_before": {},
            "agent_actions": [],
            "agent_forecasts": [],
            "cross_agent_interactions": [],
            "state_after": {},
            "triggered_revisions": [],
        }]
