"""Domain report template registry.

Templates are structural lenses over the same Forecast Ledger. They must not
invent new facts, agents, forecasts, or causal claims.
"""

from __future__ import annotations

from typing import Dict, List


TEMPLATE_REGISTRY: Dict[str, List[str]] = {
    "election_forecast": [
        "Result in One Page",
        "Map and Coalition Logic",
        "Vote, Seat, Turnout, and Probability Tables",
        "Regional Swing Story",
        "Actors, Ground Signals, and Disagreement",
        "Scenarios and Watchpoints",
        "Evidence Caveats",
    ],
    "commodity_market_note": [
        "Market Call",
        "Supply-Demand Balance",
        "Price Path and Risk Premium",
        "Bottlenecks and Shock Channels",
        "Trader, Producer, Consumer, and Policy Signals",
        "Scenario Table",
        "Evidence Caveats",
    ],
    "ai_adoption_whitepaper": [
        "Executive Thesis",
        "Adoption Path",
        "Capability, Capex, Regulation, and Labor Channels",
        "Agent Debate",
        "Scenario Implications",
        "Charts and Tables",
        "Method Caveats",
    ],
    "housing_policy_brief": [
        "Policy Readout",
        "Affected Households and Institutions",
        "Prices, Access, Supply, and Distribution Effects",
        "Implementation Risks",
        "Scenario Comparison",
        "Evidence Caveats",
    ],
    "geopolitical_risk_memo": [
        "Risk Memo",
        "Actors and Leverage",
        "Trigger Events",
        "Escalation and De-escalation Paths",
        "Scenario Probabilities",
        "Signals to Monitor",
    ],
    "narrative_fiction_forecast": [
        "Story Forecast",
        "Character Fate Board",
        "Alliances, Betrayals, Reveals, and Battles",
        "Foreshadowing Evidence",
        "Scenario Paths",
        "Uncertainty and Authorial Wildcards",
    ],
    "business_strategy_memo": [
        "Strategic Recommendation",
        "Customer, Competitor, and Channel Signals",
        "Market Scenarios",
        "Operating Risks",
        "Decision Triggers",
        "Evidence Caveats",
    ],
    "generic_forecast_memo": [
        "Forecast Readout",
        "Scenario Logic",
        "Actors and Incentives",
        "Numeric Ledger",
        "Debate and Disagreement",
        "Evidence Caveats",
    ],
}


def select_template_id(domain: str = "", requested: str = "") -> str:
    if requested in TEMPLATE_REGISTRY:
        return requested
    domain_l = (domain or "").lower()
    if domain_l == "election":
        return "election_forecast"
    if domain_l in {"oil", "commodity", "market"}:
        return "commodity_market_note"
    if domain_l == "ai_future":
        return "ai_adoption_whitepaper"
    if domain_l in {"geopolitics", "geopolitical"}:
        return "geopolitical_risk_memo"
    if domain_l in {"business", "strategy"}:
        return "business_strategy_memo"
    if domain_l in {"narrative", "fiction"}:
        return "narrative_fiction_forecast"
    return "generic_forecast_memo"


def template_sections(template_id: str) -> List[str]:
    return TEMPLATE_REGISTRY.get(template_id) or TEMPLATE_REGISTRY["generic_forecast_memo"]
