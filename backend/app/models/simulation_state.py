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
        for idx, archetype in enumerate(domain_plan.get("required_agent_archetypes") or [], start=1):
            name = archetype.get("name") or f"Agent {idx}"
            agents.append({
                "agent_id": f"agent_{idx:02d}_{uuid.uuid5(uuid.NAMESPACE_DNS, name).hex[:8]}",
                "name": name,
                "domain": domain,
                "role": name,
                "institutional_incentives": archetype.get("likely_bias", ""),
                "causal_power": archetype.get("causal_role", ""),
                "information_set": [archetype.get("information_advantage", "")],
                "trusted_data_sources": [],
                "ignored_or_underweighted_data": [],
                "forecasting_method": "Domain-specific judgment constrained by structured numeric outputs.",
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
