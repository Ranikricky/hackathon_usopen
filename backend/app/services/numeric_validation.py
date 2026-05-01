"""
Numeric validation for structured simulations.

Reports and other polished outputs should only run after this validator confirms
that the simulation state contains enough numeric, agent-specific evidence.
"""

from typing import Any, Dict, List, Set, Tuple


SCENARIO_FLAGS = {
    "base_case": "base",
    "upside_case": "upside",
    "downside_case": "downside",
    "tail_case": "tail",
}


class NumericValidationService:
    """Validate whether structured simulation state is output-ready."""

    def validate(self, state: Dict[str, Any]) -> Dict[str, Any]:
        errors: List[str] = []
        warnings: List[str] = []
        missing_agents: List[str] = []
        missing_variables: List[str] = []
        missing_dates: List[str] = []
        missing_scenarios: List[str] = []

        domain_plan = state.get("domain_plan") or {}
        agents = state.get("agents") or []
        agent_outputs = state.get("agent_outputs") or []
        time_pockets = state.get("time_pockets") or []
        scenario_outputs = state.get("scenario_outputs") or {}
        aggregated_outputs = state.get("aggregated_outputs") or {}

        if not domain_plan:
            errors.append("Structured domain plan is missing.")
        if not agents:
            errors.append("No structured agents are available.")
        if not time_pockets:
            warnings.append("No explicit time pockets are available.")

        required_targets = [
            variable for variable in domain_plan.get("target_variables", [])
            if variable.get("required", True)
        ]
        required_target_names = {variable.get("name") for variable in required_targets if variable.get("name")}

        required_agent_ids = {
            agent.get("agent_id")
            for agent in agents
            if (agent.get("numeric_capabilities") or {}).get("must_output_numbers", True)
        }
        required_agent_ids = {agent_id for agent_id in required_agent_ids if agent_id}

        valid_forecast_keys: Set[Tuple[str, str, str, str]] = set()
        agents_with_forecasts: Set[str] = set()
        variables_with_forecasts: Set[str] = set()
        scenarios_with_forecasts: Set[str] = set()
        malformed_count = 0
        confidence_count = 0
        unit_count = 0
        total_points = 0

        for output in agent_outputs:
            agent_id = output.get("agent_id")
            target = output.get("target_variable")
            forecast_path = output.get("forecast_path") or []
            if agent_id and forecast_path:
                agents_with_forecasts.add(agent_id)
            if target and forecast_path:
                variables_with_forecasts.add(target)
            if self._is_valid_confidence(output.get("confidence")):
                confidence_count += 1
            for point in forecast_path:
                total_points += 1
                date = point.get("date")
                scenario = point.get("scenario")
                value = point.get("value")
                unit = point.get("unit")
                if unit:
                    unit_count += 1
                if scenario:
                    scenarios_with_forecasts.add(str(scenario))
                if not date or not target or not scenario or not self._is_valid_number(value) or not unit:
                    malformed_count += 1
                    continue
                valid_forecast_keys.add((agent_id or "", target, str(date), str(scenario)))

        for agent_id in sorted(required_agent_ids - agents_with_forecasts):
            missing_agents.append(agent_id)
        for target_name in sorted(required_target_names - variables_with_forecasts):
            missing_variables.append(target_name)

        required_scenarios = {
            scenario for flag, scenario in SCENARIO_FLAGS.items()
            if (domain_plan.get("scenario_structure") or {}).get(flag, True)
        }
        available_scenarios = {
            scenario for scenario, values in scenario_outputs.items()
            if values
        } | scenarios_with_forecasts
        for scenario in sorted(required_scenarios - available_scenarios):
            missing_scenarios.append(scenario)

        if required_target_names and not variables_with_forecasts:
            errors.append("No required target variables have numeric forecast paths.")
        if required_agent_ids and not agents_with_forecasts:
            errors.append("No required agents produced numeric forecasts.")
        if missing_agents:
            errors.append("Some required agents are missing numeric forecasts.")
        if missing_variables:
            errors.append("Some required target variables are missing numeric forecasts.")
        if missing_scenarios:
            errors.append("Some required scenario paths are missing.")
        if malformed_count:
            errors.append(f"{malformed_count} forecast point(s) are malformed or missing date, value, unit, or scenario.")
        if total_points == 0:
            errors.append("No forecast points are available.")
        if agent_outputs and confidence_count < len(agent_outputs):
            errors.append("Some agent forecasts are missing confidence scores.")
        if total_points and unit_count < total_points:
            errors.append("Some forecast points are missing units.")

        if time_pockets and total_points == 0:
            missing_dates.extend([
                pocket.get("label") or pocket.get("pocket_id") or "unknown_pocket"
                for pocket in time_pockets
            ])

        if not aggregated_outputs:
            warnings.append("Aggregated outputs are not available yet.")

        quality_score = self._quality_score(
            errors=errors,
            warnings=warnings,
            required_agents=len(required_agent_ids),
            agents_with_forecasts=len(agents_with_forecasts),
            required_variables=len(required_target_names),
            variables_with_forecasts=len(variables_with_forecasts),
            required_scenarios=len(required_scenarios),
            available_scenarios=len(required_scenarios - set(missing_scenarios)),
            malformed_count=malformed_count,
            total_points=total_points,
        )

        return {
            "passed": not errors,
            "errors": errors,
            "warnings": warnings,
            "missing_agents": missing_agents,
            "missing_variables": missing_variables,
            "missing_dates": missing_dates,
            "missing_scenarios": missing_scenarios,
            "numeric_quality_score": quality_score,
        }

    def diagnostic_message(self, validation: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "title": "Simulation evidence insufficient",
            "summary": "Report generation is blocked because the structured simulation state did not pass numeric validation.",
            "validation": validation,
            "how_to_fix": [
                "Generate or repair the domain simulation plan.",
                "Generate causal agents with numeric forecast responsibilities.",
                "Run the simulation so each required agent emits forecast paths with date, value, unit, scenario, and confidence.",
                "Ensure base, upside, downside, and tail scenarios are present when required.",
                "Retry report generation only after validation passes.",
            ],
        }

    def _is_valid_number(self, value: Any) -> bool:
        if isinstance(value, bool):
            return False
        if isinstance(value, (int, float)):
            return True
        if isinstance(value, str):
            try:
                float(value)
                return True
            except ValueError:
                return False
        return False

    def _is_valid_confidence(self, value: Any) -> bool:
        if not self._is_valid_number(value):
            return False
        numeric = float(value)
        return 0.0 <= numeric <= 1.0 or 0.0 <= numeric <= 100.0

    def _quality_score(
        self,
        errors: List[str],
        warnings: List[str],
        required_agents: int,
        agents_with_forecasts: int,
        required_variables: int,
        variables_with_forecasts: int,
        required_scenarios: int,
        available_scenarios: int,
        malformed_count: int,
        total_points: int,
    ) -> float:
        if errors and total_points == 0:
            return 0.0

        components = []
        if required_agents:
            components.append(agents_with_forecasts / required_agents)
        if required_variables:
            components.append(variables_with_forecasts / required_variables)
        if required_scenarios:
            components.append(available_scenarios / required_scenarios)
        if total_points:
            components.append(max(0.0, (total_points - malformed_count) / total_points))

        score = sum(components) / len(components) if components else 0.0
        score -= min(0.25, 0.05 * len(warnings))
        score -= min(0.5, 0.1 * len(errors))
        return round(max(0.0, min(1.0, score)), 3)
