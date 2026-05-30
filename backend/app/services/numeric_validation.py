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

        horizon = domain_plan.get("forecast_horizon") if isinstance(domain_plan, dict) else {}
        horizon_errors = self._horizon_quality_errors(horizon if isinstance(horizon, dict) else {})
        errors.extend(horizon_errors)

        required_targets = [
            variable for variable in domain_plan.get("target_variables", [])
            if variable.get("required", True)
        ]
        required_target_names = {variable.get("name") for variable in required_targets if variable.get("name")}
        placeholder_targets = sorted(
            name for name in required_target_names
            if self._is_placeholder_target_name(str(name))
        )
        prompt_text = "\n".join([
            str(domain_plan.get("user_question") or ""),
            str(domain_plan.get("source_summary") or ""),
            str(domain_plan.get("domain") or ""),
        ])
        requested_terms = self._requested_numeric_terms(prompt_text)

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

        expected_horizon = aggregated_outputs.get("forecast_horizon") if isinstance(aggregated_outputs, dict) else {}
        expected_dates = [
            str(date)
            for date in (expected_horizon or {}).get("dates", [])
            if date
        ]
        horizon_granularity = str((expected_horizon or {}).get("granularity") or "").lower()
        if expected_dates and horizon_granularity not in {"event_triggered", "event", "auto"}:
            horizon_gaps = self._forecast_horizon_gaps(
                scenario_outputs=scenario_outputs,
                required_target_names=required_target_names,
                required_scenarios=required_scenarios,
                expected_dates=expected_dates,
            )
            if horizon_gaps:
                missing_dates.extend(horizon_gaps[:30])
                errors.append(
                    "Forecast horizon is incomplete for required targets/scenarios. "
                    "Missing examples: " + ", ".join(horizon_gaps[:8])
                )

        if required_target_names and not variables_with_forecasts:
            errors.append("No required target variables have numeric forecast paths.")
        if placeholder_targets:
            errors.append(
                "Planner produced placeholder target variable(s) instead of concrete requested metrics: "
                + ", ".join(placeholder_targets)
            )
        if requested_terms:
            missing_requested_terms = [
                term for term in sorted(requested_terms)
                if not self._target_satisfies_requested_term(required_target_names, term)
            ]
            if missing_requested_terms:
                warnings.append(
                    "Prompt requested concrete numeric metric families that are missing from the plan: "
                    + ", ".join(missing_requested_terms)
                )
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
            warnings.append(
                "Non-numeric participant roles were assigned forecast ownership: "
                + ", ".join(invalid_numeric_roles[:10])
            )

        constrained_errors, constrained_warnings = self._validate_constrained_outputs(state)
        errors.extend(constrained_errors)
        warnings.extend(constrained_warnings)

        semantic_errors, semantic_warnings = self._semantic_contract_validation(state)
        errors.extend(semantic_errors)
        warnings.extend(semantic_warnings)

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

    def _forecast_horizon_gaps(
        self,
        scenario_outputs: Dict[str, Any],
        required_target_names: Set[str],
        required_scenarios: Set[str],
        expected_dates: List[str],
    ) -> List[str]:
        gaps: List[str] = []
        expected = set(expected_dates)
        for scenario in sorted(required_scenarios):
            scenario_targets = scenario_outputs.get(scenario) or {}
            for target in sorted(required_target_names):
                points = scenario_targets.get(target) or []
                actual = {str(point.get("date")) for point in points if point.get("date")}
                missing = sorted(expected - actual)
                if missing:
                    gaps.append(f"{scenario}:{target}:{missing[0]}")
        return gaps

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
            else:
                warnings.append(
                    "Seat composition targets are present, but no reliable total-seat denominator was detected. "
                    "Provide an explicit total such as `Assembly size: 294 seats` for strict seat validation."
                )
            if not final_outcome or not final_outcome.get("projected_winner"):
                errors.append("Seat forecast exists, but final projected winner/majority status is missing.")
        if len(vote_targets) == 1:
            warnings.append("Only one vote-share target is present; composition cannot be checked against 100%.")
        probability_error = self._validate_probability_ranges(scenario_outputs, target_names)
        if probability_error:
            errors.append(probability_error)
        return errors, warnings

    def _semantic_contract_validation(self, state: Dict[str, Any]) -> Tuple[List[str], List[str]]:
        """Validate cross-layer semantics, not only numeric shape.

        This remains domain-general. It checks whether the approved Domain
        Contract, target list, agent roster, debate record, and report template
        are internally coherent.
        """
        errors: List[str] = []
        warnings: List[str] = []
        plan = state.get("domain_plan") or {}
        contract_ref = plan.get("domain_contract") if isinstance(plan.get("domain_contract"), dict) else {}
        aggregated = state.get("aggregated_outputs") or {}
        agents = state.get("agents") or []
        targets = [
            str(item.get("name") or "")
            for item in plan.get("target_variables", []) or []
            if isinstance(item, dict)
        ]
        time_pockets = state.get("time_pockets") or []
        discussion = state.get("discussion_transcript") or []

        if contract_ref and contract_ref.get("approved") is False:
            errors.append("Domain Contract is present but not approved.")

        if contract_ref:
            expected_template = contract_ref.get("report_template")
            actual_template = aggregated.get("report_template") or (aggregated.get("forecast_ledger") or {}).get("report_template")
            if expected_template and actual_template and expected_template != actual_template:
                errors.append(
                    f"Report-template mismatch: Domain Contract requires `{expected_template}` but state has `{actual_template}`."
                )

        bad_targets = [
            target for target in targets
            if self._is_semantic_bad_target(target)
        ]
        if bad_targets:
            errors.append(
                "Target extraction leaked instructions, placeholders, or output-format phrases: "
                + ", ".join(sorted(set(bad_targets))[:12])
            )

        generic_agents = self._generic_agent_names(agents)
        if len(agents) >= 4 and len(generic_agents) / max(len(agents), 1) > 0.35:
            errors.append(
                "Agent roster is too generic for a structured simulation: "
                + ", ".join(generic_agents[:10])
            )
        elif generic_agents:
            warnings.append("Some agents have generic names: " + ", ".join(generic_agents[:10]))

        leaked_instruction_agents = [
            str(agent.get("name") or agent.get("role") or agent.get("agent_id"))
            for agent in agents
            if self._looks_like_instruction_fragment(str(agent.get("name") or ""))
            or self._looks_like_instruction_fragment(str(agent.get("role") or ""))
        ]
        if leaked_instruction_agents:
            errors.append(
                "Instruction leakage appeared in agent roster: "
                + ", ".join(leaked_instruction_agents[:8])
            )

        if time_pockets:
            pocket_labels = " ".join(str(p.get("label") or p.get("start") or p.get("end") or "") for p in time_pockets)
            if len(time_pockets) == 1 and re.search(r"\b(monthly|weekly|quarterly|yearly|sequential|pockets?)\b", str(plan), flags=re.IGNORECASE):
                errors.append("Wrong time-pocket structure: plan implies multiple sequential pockets but only one pocket was saved.")
            if self._looks_like_instruction_fragment(pocket_labels):
                errors.append("Time-pocket labels contain instruction/output-format fragments rather than temporal or event boundaries.")

        if discussion:
            turn_types = {str(turn.get("turn_type") or "") for turn in discussion if isinstance(turn, dict)}
            if not ({"challenge", "rebuttal", "moderator_cross_question"} & turn_types):
                errors.append("Weak debate quality: transcript has no challenge, rebuttal, or moderator cross-question turns.")
            if len(discussion) < max(6, min(18, len(agents))):
                warnings.append("Debate transcript is short relative to the agent roster.")

        ledger = aggregated.get("forecast_ledger") if isinstance(aggregated, dict) else {}
        if not isinstance(ledger, dict) or not ledger.get("agent_forecast_rows"):
            errors.append("Clean Forecast Ledger is missing or empty; reports must not consume raw agent logs.")

        return errors, warnings

    def _is_semantic_bad_target(self, target: str) -> bool:
        cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", str(target or "").lower()).strip("_")
        if self._is_placeholder_target_name(cleaned):
            return True
        bad_terms = [
            "following_numeric_outputs", "required_output", "output_requirements",
            "table", "chart", "report", "appendix", "explain_agent_disagreement",
            "scenario_comparison", "missing_data_warning", "confidence_band",
            "then_explain", "rules", "do_not", "produce_numeric_forecasts_first",
        ]
        return any(term in cleaned for term in bad_terms)

    def _looks_like_instruction_fragment(self, text: str) -> bool:
        lowered = str(text or "").lower()
        return bool(re.search(
            r"\b(do not|must|should|required output|then explain|produce numeric|generate report|copy paste|rules?)\b",
            lowered,
        ))

    def _generic_agent_names(self, agents: List[Dict[str, Any]]) -> List[str]:
        generic = []
        for agent in agents:
            name = str(agent.get("name") or agent.get("role") or "")
            cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", name.lower()).strip("_")
            if cleaned in {"agent", "actor", "participant", "person", "organization", "dynamic_agents", "stakeholder"}:
                generic.append(name or str(agent.get("agent_id") or "unknown"))
            elif re.fullmatch(r"(?:dynamic|generic|simulation|scenario|forecast|target).{0,20}(?:agent|actor|participant)s?", cleaned):
                generic.append(name)
        return generic

    def _validate_probability_ranges(self, scenario_outputs: Dict[str, Any], target_names: List[str]) -> str:
        probability_targets = [
            target for target in target_names
            if re.search(r"\bprobability\b|^probability_", target, flags=re.IGNORECASE)
        ]
        for scenario, values in (scenario_outputs or {}).items():
            if not isinstance(values, dict):
                continue
            for target in probability_targets:
                for point in values.get(target, []) or []:
                    try:
                        value = float(point.get("value"))
                    except (TypeError, ValueError):
                        continue
                    if value < 0 or value > 100:
                        return (
                            f"Probability target `{target}` has out-of-range value {value:g} "
                            f"for scenario {scenario} at {point.get('date')}."
                        )
        return ""

    def _is_placeholder_target_name(self, name: str) -> bool:
        cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", str(name or "").lower()).strip("_")
        if not cleaned:
            return True
        exact = {
            "primary_outcome",
            "target_variable",
            "target_variables",
            "numeric_outputs",
            "following_numeric_outputs",
            "the_following_numeric_outputs",
            "forecast_the_following_numeric_outputs",
            "light_probability_bands_such_as",
            "probability_bands_such_as",
        }
        if cleaned in exact:
            return True
        patterns = [
            r"^(?:the_)?following_(?:numeric_)?outputs?$",
            r".*probability_bands_such_as.*",
            r".*(?:^|_)such_as(?:_|$).*",
            r"^(?:requested_)?(?:target|metric|numeric|output)_variables?$",
            r"^(?:primary|main|overall)_outcome$",
        ]
        return any(re.fullmatch(pattern, cleaned) for pattern in patterns)

    def _horizon_quality_errors(self, horizon: Dict[str, Any]) -> List[str]:
        errors: List[str] = []
        for field in ["start", "end"]:
            value = str(horizon.get(field) or "").strip()
            if not value:
                continue
            word_count = len(re.findall(r"[A-Za-z0-9]+", value))
            looks_temporal = bool(re.search(
                r"\b(?:19|20)\d{2}\b|\bq[1-4]\b|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\b|"
                r"\b(?:today|current|cutoff|future|next|coming|month|quarter|year|week|day|phase|pocket|book|chapter|auto|requested)\b",
                value.lower(),
                flags=re.IGNORECASE,
            ))
            if word_count > 8 and not looks_temporal:
                errors.append(
                    f"Forecast horizon `{field}` looks like a prose fragment rather than a time/event boundary: {value[:120]}"
                )
            if "," in value and word_count > 5 and not looks_temporal:
                errors.append(
                    f"Forecast horizon `{field}` contains non-temporal comma-separated prose: {value[:120]}"
                )
        return errors

    def _requested_numeric_terms(self, text: str) -> Set[str]:
        """Detect metric families explicitly requested in prose/headings.

        This remains domain-general. It does not know parties, countries, or
        sectors; it only checks reusable numeric output concepts.
        """
        lowered = (text or "").lower()
        if not re.search(r"\b(?:forecast|predict|simulate|estimate|numeric outputs?|target variables?)\b", lowered):
            return set()
        requested: Set[str] = set()
        term_patterns = {
            "vote_share": r"\bvote\s+share\b",
            "seats": r"\bseats?\b",
            "turnout": r"\bturnout\b",
            "probability": r"\bprobability\b|\bchance\b|\bwin\s+probability\b",
            "price": r"\bprice\b",
            "rate": r"\brate\b",
            "index": r"\bindex\b",
            "share": r"\bshare\b",
        }
        for term, pattern in term_patterns.items():
            if re.search(pattern, lowered):
                requested.add(term)
        if "vote_share" in requested:
            requested.discard("share")
        return requested

    def _target_satisfies_requested_term(self, target_names: Set[str], term: str) -> bool:
        """Match prompt metric-family words to concrete target names.

        This intentionally understands reusable metric synonyms only. It does
        not know any domain-specific party, commodity, country, or company.
        """
        normalized = {
            re.sub(r"[^a-zA-Z0-9]+", "_", str(name or "").lower()).strip("_")
            for name in target_names
        }
        if not normalized:
            return False

        term_aliases = {
            "seats": ["seat", "seats", "seat_share", "seat_count"],
            "vote_share": ["vote_share", "vote", "party_share"],
            "turnout": ["turnout", "participation"],
            "probability": ["probability", "chance", "risk", "odds"],
            "price": ["price", "pricing", "cost", "premium"],
            "rate": ["rate", "growth", "adoption", "change"],
            "index": ["index", "score", "pressure"],
            "share": ["share", "mix", "portion"],
        }
        aliases = term_aliases.get(term, [term])
        return any(any(alias in name for alias in aliases) for name in normalized)

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
        """Extract a true composition denominator, not an arbitrary threshold.

        This intentionally avoids broad patterns like ``100 seats`` because
        threshold targets such as "probability party X crosses 100 seats" are
        not total-seat denominators. If no explicit total is present, infer from
        comparable historical seat rows in a domain-general way.
        """
        cleaned = text or ""
        for pattern in [
            r"(?:assembly size|legislative assembly size|total seats|seat total|seats total|total count)\s*[:=]?\s*(\d{2,5})",
            r"\b(?:assembly|legislature|parliament|council|chamber)\b[^\n]{0,35}?\(?(\d{2,5})\s+(?:seats?|members?)",
            r"(?:majority mark|majority threshold)\s*[:=]?\s*(\d{2,5})",
        ]:
            for match in re.finditer(pattern, cleaned, flags=re.IGNORECASE):
                matched_text = match.group(0).lower()
                if (
                    "assembly size" not in matched_text
                    and "legislative assembly size" not in matched_text
                    and "majority mark" not in matched_text
                    and "majority threshold" not in matched_text
                    and re.search(r"\b(probability|chance|cross(?:es)?|threshold|hung)\b", matched_text)
                ):
                    continue
                value = float(match.group(1))
                if "majority" in matched_text:
                    value = (value - 1) * 2
                return value if 1 <= value <= 100000 else 0.0
        return self._infer_total_count_from_historical_rows(cleaned)

    def _infer_total_count_from_historical_rows(self, text: str) -> float:
        """Infer contest size from historical rows that list multiple seat counts.

        This is deliberately generic: it does not know parties, regions, or
        domains. It looks for lines that read like comparable historical
        election/seat rows, sums named actor seat counts before vote-share text,
        and ignores legislative-year numbers and threshold phrases.
        """
        row_totals: List[float] = []
        chunks = self._historical_row_chunks(text)
        for line in chunks:
            line_l = line.lower()
            if not re.search(r"\b(assembly|legislative|state election|election|seat)\b", line_l):
                continue
            if re.search(r"\b(lok sabha|parliament|house of commons|congressional)\b", line_l) and not re.search(r"assembly[- ]segment", line_l):
                continue
            if re.search(r"\b(cross(?:es)?|threshold|probability|chance)\b", line_l):
                continue

            seat_part = re.split(
                r"\b(?:approx(?:imate)? vote share|vote share|turnout|main meaning|meaning|issues?|forecast|probability)\b",
                line,
                maxsplit=1,
                flags=re.IGNORECASE,
            )[0]
            values: List[float] = []
            for match in re.finditer(r"\b[A-Za-z][A-Za-z()/&+.' -]{1,45}\s+(\d{1,4})(?=\s*(?:seats?|,|\.|$|\)))", seat_part):
                value = float(match.group(1))
                if value in {1900, 2000, 2011, 2014, 2016, 2019, 2021, 2024, 2026}:
                    continue
                if 0 < value < 10000:
                    values.append(value)
            if len(values) >= 2:
                total = sum(values)
                if 20 <= total <= 100000:
                    row_totals.append(total)
        return max(row_totals) if row_totals else 0.0

    def _historical_row_chunks(self, text: str) -> List[str]:
        raw = text or ""
        chunks = list(raw.splitlines())
        chunks.extend(re.split(r"(?=\b(?:19|20)\d{2}\s+(?:assembly|legislative|state election|election)\b)", raw, flags=re.IGNORECASE))
        chunks.extend(re.split(r"(?=\b(?:19|20)\d{2}\s+[A-Z][A-Za-z ]{0,30}:)", raw))
        return [chunk.strip() for chunk in chunks if chunk and chunk.strip()]

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
