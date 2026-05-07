"""
Numeric validation for structured simulations.

Reports and other polished outputs should only run after this validator confirms
that the simulation state contains enough numeric, agent-specific evidence.
"""

import re
from typing import Any, Dict, List, Set, Tuple


FALLBACK_SCENARIO_FLAGS = {
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

        required_scenarios = self._required_scenarios(domain_plan)
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

        invalid_numeric_roles = self._invalid_numeric_role_agents(agents)
        if invalid_numeric_roles:
            errors.append(
                "Non-numeric participant roles were assigned forecast ownership: "
                + ", ".join(invalid_numeric_roles[:10])
            )

        constrained_errors, constrained_warnings = self._validate_constrained_outputs(state)
        errors.extend(constrained_errors)
        warnings.extend(constrained_warnings)

        discussion = state.get("discussion_transcript") or []
        debate_impact = (aggregated_outputs.get("debate_impact") or {}) if isinstance(aggregated_outputs, dict) else {}
        if discussion and int(debate_impact.get("revision_count") or 0) == 0:
            errors.append("Debate transcript exists, but no mediated revision changed the structured forecast state.")

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

    def _invalid_numeric_role_agents(self, agents: List[Dict[str, Any]]) -> List[str]:
        invalid_terms = [
            "voter", "beneficiary", "consumer", "worker", "household", "community",
            "cohort", "citizen", "resident", "observer", "journalist", "media",
            "watchdog", "governance", "strategist", "campaign", "negotiator",
            "rural", "urban", "youth", "minority", "women", "booth",
        ]
        allowed_terms = [
            "quant", "data", "pollster", "scientist", "economist", "research",
            "forecaster", "model", "statistic", "auditor", "synthesizer", "retrieval",
        ]
        invalid = []
        for agent in agents:
            caps = agent.get("numeric_capabilities") or {}
            if not caps.get("must_output_numbers", True):
                continue
            role_text = " ".join([
                str(agent.get("name") or ""),
                str(agent.get("role") or ""),
                str(agent.get("causal_role") or ""),
            ]).lower()
            if any(term in role_text for term in allowed_terms):
                continue
            if any(term in role_text for term in invalid_terms):
                invalid.append(str(agent.get("name") or agent.get("agent_id") or "unknown_agent"))
        return invalid

    def _validate_constrained_outputs(self, state: Dict[str, Any]) -> Tuple[List[str], List[str]]:
        errors: List[str] = []
        warnings: List[str] = []
        domain_plan = state.get("domain_plan") or {}
        scenario_outputs = state.get("scenario_outputs") or {}
        aggregated = state.get("aggregated_outputs") or {}
        target_names = [
            str(target.get("name") or "")
            for target in domain_plan.get("target_variables", []) or []
            if isinstance(target, dict)
        ]
        vote_targets = [target for target in target_names if self._is_composition_target(target, "_vote_share")]
        seat_targets = [target for target in target_names if self._is_composition_target(target, "_seats")]
        if len(vote_targets) >= 2:
            vote_error = self._validate_total_by_date(scenario_outputs, vote_targets, 100.0, tolerance=2.0, label="vote share")
            if vote_error:
                errors.append(vote_error)
        if len(seat_targets) >= 2:
            total = self._extract_total_count("\n".join([
                str(domain_plan.get("user_question") or ""),
                str(domain_plan.get("source_summary") or ""),
            ]))
            final_outcome = aggregated.get("final_outcome") if isinstance(aggregated, dict) else {}
            if total:
                seat_error = self._validate_total_by_date(scenario_outputs, seat_targets, total, tolerance=1.0, label="seat")
                if seat_error:
                    errors.append(seat_error)
            if not final_outcome or not final_outcome.get("projected_winner"):
                errors.append("Seat forecast exists, but final projected winner/majority status is missing.")
        if len(vote_targets) == 1:
            warnings.append("Only one vote-share target is present; composition cannot be checked against 100%.")
        return errors, warnings

    def _is_composition_target(self, name: str, suffix: str) -> bool:
        if not name.endswith(suffix):
            return False
        label = name[: -len(suffix)]
        blocked = ["statewide", "overall", "regional", "crosses", "threshold", "probability", "scenario"]
        return bool(label) and not any(term in label for term in blocked)

    def _validate_total_by_date(
        self,
        scenario_outputs: Dict[str, Any],
        target_names: List[str],
        expected: float,
        tolerance: float,
        label: str,
    ) -> str:
        for scenario, values in (scenario_outputs or {}).items():
            if not isinstance(values, dict):
                continue
            dates = sorted({
                str(point.get("date"))
                for target in target_names
                for point in values.get(target, []) or []
                if isinstance(point, dict) and point.get("date") is not None
            })
            for date in dates:
                total = 0.0
                present = 0
                for target in target_names:
                    point = next((p for p in values.get(target, []) or [] if str(p.get("date")) == date), None)
                    if point is None:
                        continue
                    present += 1
                    total += float(point.get("value") or 0)
                if present == len(target_names) and abs(total - expected) > tolerance:
                    return f"{label.title()} constrained outputs do not sum to {expected:g} for scenario {scenario} at {date}."
        return ""

    def _extract_total_count(self, text: str) -> float:
        for pattern in [
            r"(?:assembly size|total seats|seats|total count|total)\s*[:=]?\s*(\d{2,5})",
            r"(\d{2,5})\s+seats",
        ]:
            match = re.search(pattern, text or "", flags=re.IGNORECASE)
            if match:
                value = float(match.group(1))
                return value if 1 <= value <= 100000 else 0.0
        return 0.0

    def diagnostic_message(self, validation: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "title": "Simulation evidence insufficient",
            "summary": "Report generation is blocked because the structured simulation state did not pass numeric validation.",
            "validation": validation,
            "how_to_fix": [
                "Generate or repair the domain simulation plan.",
                "Generate causal agents with numeric forecast responsibilities.",
                "Run the simulation so each required agent emits forecast paths with date, value, unit, scenario, and confidence.",
                "Ensure every prompt-required scenario path has complete forecast points.",
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

    def _required_scenarios(self, domain_plan: Dict[str, Any]) -> Set[str]:
        scenario_structure = domain_plan.get("scenario_structure") or {}
        scenario_paths = scenario_structure.get("scenarios") if isinstance(scenario_structure, dict) else []
        if isinstance(scenario_paths, list) and scenario_paths:
            scenarios = {
                str(item.get("id") or item.get("name") or "").strip()
                for item in scenario_paths
                if isinstance(item, dict) and item.get("required", True)
            }
            scenarios = {scenario for scenario in scenarios if scenario}
            if scenarios:
                return scenarios
        return {
            scenario for flag, scenario in FALLBACK_SCENARIO_FLAGS.items()
            if scenario_structure.get(flag, True)
        }

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
