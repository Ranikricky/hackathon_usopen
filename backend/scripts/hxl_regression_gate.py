#!/usr/bin/env python3
"""Horizon XL cross-domain regression gate.

This script exercises the structured simulation stack without depending on a
live browser or LLM call. It exists to catch generic quality regressions that a
single demo prompt will miss:

- placeholder target variables
- metric artifacts becoming agents
- empty transcripts
- reports that validate but contain no requested forecast fields
- output adapters falling back to generic prose

It is intentionally prompt-diverse. Do not tune production code to these exact
examples; if a test fails, fix the generic extraction/simulation behavior.
"""

from __future__ import annotations

import sys
import re
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.agent_generation_engine import AgentGenerationEngine
from app.services.domain_simulation_planner import DomainSimulationPlanner
from app.services.numeric_validation import NumericValidationService
from app.services.ontology_generator import OntologyGenerator
from app.services.output_adapters.report_adapter import StructuredReportAdapter
from app.services.structured_simulation_runner import StructuredSimulationRunner


PLACEHOLDER_TARGETS = {
    "target_variables",
    "numeric_outputs",
    "following_numeric_outputs",
    "the_following_numeric_outputs",
    "thefollowingnumericoutputs",
    "forecast_the_following_numeric_outputs",
    "primary_outcome",
}

AGENT_ARTIFACT_TERMS = {
    "scenario_synthesis",
    "synthesis_pocket",
    "forecast_table",
    "numeric_outputs",
    "usd",
    "kwh",
    "metric_ton",
    "percent",
}

PROMPT_CASES: List[Dict[str, Any]] = [
    {
        "name": "west_bengal_election",
        "prompt": """
Using only information available up to May 1, 2026, simulate the 2026 West
Bengal Legislative Assembly election. Do not use actual results, future
commentary, or post-counting data. Forecast TMC vote share and seats, BJP vote
share and seats, Left/Congress vote share and seats, turnout, women turnout,
minority turnout, opposition vote split index, polarization index, probability
of TMC majority, probability of BJP majority, and probability of a hung
assembly. Model regions separately: Kolkata, South 24 Parganas, North Bengal,
Jungle Mahal, Malda-Murshidabad, Nadia, Bardhaman, and Darjeeling Hills. Include
party strategists, poll/data analysts, booth workers, women voters, minority
voters, rural welfare beneficiaries, urban middle-class voters, youth employment
voters, media, watchdogs, research/data auditors, mediator, moderator, and
quantitative synthesizer.

Assembly size: 294 seats. Majority mark: 148 seats.
Historical baseline:
- 2011 Assembly: TMC 184 seats, Congress 42 seats, Left Front 62 seats, turnout around 84%.
- 2016 Assembly: TMC 211 seats, Congress 44 seats, CPI(M) 26 seats, BJP 3 seats; TMC vote share around 44.9%, BJP around 10.2%.
- 2021 Assembly: TMC 215 seats, BJP 77 seats; TMC vote share around 48%, BJP around 38%.
- 2024 Lok Sabha in Bengal: TMC 29 seats, BJP 12 seats, Congress 1 seat. Approx vote share: TMC 46.2%, BJP 39.1%, Left/Congress around 10% combined.
Current context: TMC has welfare, women voter, minority consolidation, and rural booth-network advantages; BJP has main-opposition status, North Bengal/Jungle Mahal strength, and an anti-corruption narrative; Left/Congress can still split local contests.
""",
        "expected_terms": ["vote_share", "seats", "turnout"],
        "expected_agents": ["voter", "poll", "moderator"],
        "expected_domain": ["election"],
        "min_agents": 12,
        "expected_vote_winner": "tmc",
        "expected_seat_winner": "tmc",
        "max_other_vote_share": 30,
    },
    {
        "name": "lithium_supply_chain",
        "prompt": """
Using only information available up to May 29, 2026, simulate the global
lithium battery supply chain for the next 24 months. Forecast China
battery-grade lithium carbonate price in USD/metric ton, spodumene concentrate
price in USD/metric ton, global EV sales growth rate, stationary storage battery
demand growth rate, mine supply growth rate, lithium inventory cover in months,
LFP battery pack cost in USD/kWh, battery-maker gross margin pressure index,
probability of supply deficit, probability of prolonged oversupply, and
probability of a project cancellation wave. Include lithium miners, Chinese
refiners, battery manufacturers, automakers, EV consumers, grid storage buyers,
commodity traders, regulators, shipping/logistics, market data analysts,
evidence auditor, external research scout, moderator, mediator, and quant.
""",
        "expected_terms": ["price", "growth", "inventory", "cost"],
        "expected_agents": ["miner", "consumer", "quant"],
        "expected_domain": ["commodity", "supply"],
        "min_agents": 12,
        "min_unique_forecast_dates": 20,
    },
    {
        "name": "ai_adoption",
        "prompt": """
Using only information available up to May 30, 2026, simulate enterprise AI
adoption from Q3 2026 through Q4 2030.

Core forecast targets:
1. Enterprise AI adoption rate, percent of firms using AI in production
2. Enterprise AI workflow automation rate, percent of workflows materially assisted by AI
3. Annual enterprise AI software spend, USD billion
4. Annual AI infrastructure/cloud spend, USD billion
5. Productivity uplift index, 0-100
6. Labor displacement pressure index, 0-100
7. AI governance/regulation pressure index, 0-100
8. Model capability diffusion index, 0-100
9. Open-source adoption share, percent
10. Probability of major AI safety/regulatory shock, percent
11. Probability of AI capex slowdown, percent
12. Probability of broad enterprise ROI disappointment, percent

Include frontier labs, open-source developers, cloud providers, enterprise CIOs,
enterprise CFOs, line-of-business managers, workers, regulators, security teams,
consultants, investors, consumers, evidence auditor, research scout, quant, and
moderator. Run base, upside, downside, and tail scenarios.
""",
        "expected_terms": ["adoption", "capex", "risk", "rate"],
        "expected_agents": ["worker", "regulator", "lab"],
        "expected_domain": ["ai", "adoption"],
        "min_agents": 10,
    },
    {
        "name": "housing_rent_policy",
        "prompt": """
Using only information available today, simulate how a large city rent-control
expansion could affect the rental housing market over the next 24 months.

Core forecast targets:
1. Median asking rent growth rate, percent
2. Rental vacancy rate, percent
3. New rental construction starts, count
4. Landlord maintenance deferral index, 0-100
5. Tenant displacement pressure index, 0-100
6. Probability of a legal challenge delaying implementation, percent
7. Probability of supply contraction, percent

Include tenants, small landlords, large property owners, developers, city
officials, housing advocates, neighborhood businesses, legal experts, lenders,
data analysts, evidence auditor, external research scout, mediator, moderator,
and quantitative synthesizer. Do not invent post-cutoff outcomes.
""",
        "expected_terms": ["rent", "rate", "index", "probability"],
        "expected_agents": ["tenant", "landlord", "developer"],
        "expected_domain": ["policy", "housing", "rent"],
        "min_agents": 12,
    },
    {
        "name": "geopolitical_shipping_risk",
        "prompt": """
Using only information available today, simulate a 12-month geopolitical
shipping-risk scenario for a critical maritime corridor.

Core forecast targets:
1. Shipping delay index, 0-100
2. Insurance premium change, percent
3. Freight cost increase, percent
4. Probability of naval escalation, percent
5. Probability of supply-chain rerouting, percent
6. Importer inventory buffer, weeks

Include shipping operators, insurers, importers, exporters, naval/security
officials, port authorities, commodity traders, households/consumers, media,
research scout, evidence auditor, mediator, moderator, and quant. Run scenarios
for contained disruption, prolonged disruption, de-escalation, and tail shock.
""",
        "expected_terms": ["index", "premium", "cost", "probability"],
        "expected_agents": ["shipping", "insurer", "consumer"],
        "expected_domain": ["geopolitical", "risk"],
        "min_agents": 12,
    },
    {
        "name": "asoiaf_narrative",
        "prompt": """
Using book-canon evidence only, simulate the next unpublished major A Song of
Ice and Fire story installment. Do not use HBO show canon. Forecast who is most
likely to die, survive, betray, form alliances, reveal secrets, win or lose key
battles, gain power, lose power, claim a throne, or trigger a magic/prophecy
reveal.

Create agents:
1. Northern political strategist
2. Bolton regime defender
3. Stannis military/logistics analyst
4. Night's Watch insider
5. Melisandre fire-magic interpreter
6. Bran weirwood-magic interpreter
7. King's Landing court analyst
8. Tyrell strategist
9. Cersei faction loyalist
10. Varys/Aegon regime-change strategist
11. Golden Company military analyst
12. Dorne/Arianne political observer
13. Daenerys court advisor
14. Meereenese politics analyst
15. Tyrion game-theory strategist
16. Ironborn/Euron occult-risk analyst
17. Oldtown/Citadel knowledge analyst
18. Faceless Men/Arya identity analyst
19. Sansa/Littlefinger Vale strategist
20. Riverlands vengeance/Brotherhood observer
21. Smallfolk/common-people suffering observer
22. Narrative structure analyst
23. Prophecy skeptic
24. Prophecy believer
25. Faith Militant political actor
26. Evidence auditor
27. Debate moderator

Agents must not just announce their roles. They must:
- make a concrete claim
- cite book-canon evidence or foreshadowing
- challenge another agent
- explain what would change their view
- separate fact, inference, and speculation
- avoid show-canon leakage

TIME-POCKET SIMULATION:
Pocket 1: Immediate aftermath of the last published book state
Pocket 2: Northern convergence around Winterfell and the Wall
Pocket 3: King's Landing and Aegon/Cersei/Tyrell/Faith struggle
Pocket 4: Daenerys, Tyrion, Euron, Victarion, and dragon/Essos convergence
Pocket 5: Endgame shock synthesis for deaths, alliances, reveals, and thrones
""",
        "expected_terms": ["death", "survival", "betrayal", "alliance", "battle", "power", "throne"],
        "expected_agents": ["northern", "stannis", "daenerys", "smallfolk", "prophecy"],
        "expected_domain": ["narrative", "literary"],
        "min_agents": 14,
        "forbidden_edges": ["CONTESTS_ELECTORAL_SPACE"],
        "forbidden_ontology_terms": ["make", "cite", "challenge", "explain", "separate", "avoid"],
    },
]


def _names(items: Iterable[Dict[str, Any]]) -> List[str]:
    return [str(item.get("name") or "").lower() for item in items]


def _contains_any(values: Iterable[str], expected: Iterable[str]) -> bool:
    joined = " ".join(values).lower()
    return any(term.lower() in joined for term in expected)


def _assert(condition: bool, message: str, failures: List[str]) -> None:
    if not condition:
        failures.append(message)


def run_case(case: Dict[str, Any]) -> List[str]:
    failures: List[str] = []
    planner = DomainSimulationPlanner()
    plan = planner.fallback_plan(case["prompt"])
    ontology = OntologyGenerator().generate_fallback([], case["prompt"], generation_seed=case["name"])
    agents = AgentGenerationEngine().generate_agents(plan, evidence_summary=case["prompt"], use_llm=False)
    simulation_id = f"gate_{case['name']}_{uuid.uuid4().hex[:8]}"
    state = StructuredSimulationRunner().run(
        simulation_id=simulation_id,
        project_id=f"gate_project_{case['name']}",
        domain_plan=plan,
        agents=agents,
        evidence_text=case["prompt"],
        graph_context={
            "mode": "regression_gate",
            "nodes": [],
            "edges": [],
            "facts": [{"summary": case["prompt"][:1200]}],
        },
    )
    state_dict = state.to_dict()
    validation = NumericValidationService().validate(state_dict)
    report = StructuredReportAdapter().render({**state_dict, "validation": validation})
    markdown = report.get("markdown", "")
    transcript_text = " ".join(str(turn.get("message") or "") for turn in state_dict.get("discussion_transcript") or [])

    target_names = _names(plan.get("target_variables") or [])
    domain_label = str(plan.get("domain") or "").lower()
    agent_names = _names(agents)
    agent_roles = [str(agent.get("role") or "").lower() for agent in agents]
    agent_text = agent_names + agent_roles
    ontology_entities = _names(ontology.get("entity_types") or [])
    ontology_edges = [str(edge.get("name") or "").upper() for edge in ontology.get("edge_types") or []]
    final = (state_dict.get("aggregated_outputs") or {}).get("final_outcome") or {}
    forecast_dates = {
        str(point.get("date"))
        for output in state_dict.get("agent_outputs") or []
        for point in output.get("forecast_path") or []
        if point.get("date")
    }

    _assert(len(target_names) >= 3, f"{case['name']}: fewer than 3 targets: {target_names}", failures)
    _assert(
        _contains_any([domain_label], case["expected_domain"]),
        f"{case['name']}: domain label is not useful: {domain_label}",
        failures,
    )
    _assert(
        len(domain_label.split()) <= 7 and not domain_label.startswith(("the ", "a ", "an ")),
        f"{case['name']}: domain label looks like a prompt fragment: {domain_label}",
        failures,
    )
    _assert(
        not any(target in PLACEHOLDER_TARGETS for target in target_names),
        f"{case['name']}: placeholder targets leaked: {target_names}",
        failures,
    )
    _assert(
        _contains_any(target_names, case["expected_terms"]),
        f"{case['name']}: target names do not reflect expected terms: {target_names}",
        failures,
    )
    _assert(
        len(agents) >= case["min_agents"],
        f"{case['name']}: too few agents: {len(agents)} < {case['min_agents']}",
        failures,
    )
    _assert(
        _contains_any(agent_text, case["expected_agents"]),
        f"{case['name']}: expected agent families missing: {agent_text[:12]}",
        failures,
    )
    _assert(
        not any(term in " ".join(agent_text) for term in AGENT_ARTIFACT_TERMS),
        f"{case['name']}: metric/report artifact became an agent: {agent_text}",
        failures,
    )
    forbidden_terms = case.get("forbidden_ontology_terms") or []
    if forbidden_terms:
        ontology_joined = " ".join(ontology_entities).lower()
        _assert(
            not any(term in ontology_joined for term in forbidden_terms),
            f"{case['name']}: instruction text became ontology actors: {ontology_entities}",
            failures,
        )
    forbidden_edges = set(case.get("forbidden_edges") or [])
    _assert(
        not (forbidden_edges & set(ontology_edges)),
        f"{case['name']}: stale/domain-specific relationship edge leaked: {sorted(forbidden_edges & set(ontology_edges))}",
        failures,
    )
    _assert(validation.get("passed") is True, f"{case['name']}: validation failed: {validation}", failures)
    _assert(
        len(state_dict.get("discussion_transcript") or []) >= 10,
        f"{case['name']}: transcript is too thin",
        failures,
    )
    _assert(
        final.get("target_forecast") or final.get("vote_share_forecast") or final.get("seat_forecast"),
        f"{case['name']}: no final forecast fields: {final}",
        failures,
    )
    if case.get("expected_vote_winner"):
        vote_forecast = final.get("vote_share_forecast") or {}
        expected = case["expected_vote_winner"]
        expected_value = vote_forecast.get(expected)
        other_values = [
            float(value)
            for actor, value in vote_forecast.items()
            if actor != expected and isinstance(value, (int, float))
        ]
        _assert(
            isinstance(expected_value, (int, float)) and all(float(expected_value) > value for value in other_values),
            f"{case['name']}: expected vote-share leader `{expected}` not reflected in final forecast: {vote_forecast}",
            failures,
        )
    if case.get("expected_seat_winner"):
        seat_forecast = final.get("seat_forecast") or {}
        expected = case["expected_seat_winner"]
        expected_value = seat_forecast.get(expected)
        other_values = [
            float(value)
            for actor, value in seat_forecast.items()
            if actor != expected and isinstance(value, (int, float))
        ]
        _assert(
            isinstance(expected_value, (int, float)) and all(float(expected_value) > value for value in other_values),
            f"{case['name']}: expected seat leader `{expected}` not reflected in final forecast: {seat_forecast}",
            failures,
        )
    if case.get("max_other_vote_share") is not None:
        vote_forecast = final.get("vote_share_forecast") or {}
        other_value = vote_forecast.get("others", vote_forecast.get("other"))
        _assert(
            not isinstance(other_value, (int, float)) or float(other_value) <= float(case["max_other_vote_share"]),
            f"{case['name']}: residual/other vote share looks implausibly dominant: {vote_forecast}",
            failures,
        )
    if case.get("min_unique_forecast_dates"):
        _assert(
            len(forecast_dates) >= int(case["min_unique_forecast_dates"]),
            f"{case['name']}: forecast horizon too short: {len(forecast_dates)} dates {sorted(forecast_dates)[:8]}",
            failures,
        )
    _assert(
        "No final forecast fields were available" not in markdown,
        f"{case['name']}: report says final forecast unavailable",
        failures,
    )
    _assert(
        "thefollowingnumericoutputs" not in markdown.replace("_", "").lower(),
        f"{case['name']}: placeholder target appears in report",
        failures,
    )
    forbidden_transcript_fragments = [
        "my reasoning profile",
        "from a specialized vantage point created from the prompt",
        "from the operating lane closest",
        "i will use my lane carefully",
        "assigned causal channel",
        "stress-test the simulation from this role",
        "agents must not just announce",
        "separate fact, inference",
        "debate moderator ::",
        "may overweight its own information access",
        "update forecasts only when new evidence changes",
        "represents or moves part of the simulation outcome",
        "context-derived knowledge associated with",
        "what i can personally observe is update forecasts",
    ]
    lower_transcript = transcript_text.lower()
    _assert(
        not any(fragment in lower_transcript for fragment in forbidden_transcript_fragments)
        and not re.search(r"\b(?:iq|eq)\b|iq_score|eq_score", lower_transcript),
        f"{case['name']}: transcript still exposes robotic/internal wording",
        failures,
    )
    _assert(
        all(term in lower_transcript for term in ["pushing back", "answer", "evidence", "revise"]),
        f"{case['name']}: transcript lacks debate-like challenge/revision language",
        failures,
    )

    print(
        f"[{case['name']}] targets={len(target_names)} agents={len(agents)} "
        f"pockets={len(state_dict.get('time_pockets') or [])} "
        f"turns={len(state_dict.get('discussion_transcript') or [])} "
        f"quality={validation.get('numeric_quality_score')}"
    )
    print("  targets:", ", ".join(target_names[:12]))
    print("  sample agents:", ", ".join(agent_names[:8]))
    return failures


def main() -> int:
    all_failures: List[str] = []
    for case in PROMPT_CASES:
        all_failures.extend(run_case(case))

    if all_failures:
        print("\nHORIZON XL REGRESSION GATE FAILED")
        for failure in all_failures:
            print(f"- {failure}")
        return 1

    print("\nHORIZON XL REGRESSION GATE PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
