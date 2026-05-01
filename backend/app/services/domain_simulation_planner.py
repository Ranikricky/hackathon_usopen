"""
Domain-general simulation planning.

This service turns a user question plus uploaded context into a structured
simulation blueprint. The blueprint is intentionally domain-neutral: later
layers should use it to generate agents, time pockets, validation rules, and
outputs without hard-coding a single use case such as macro forecasting.
"""

import re
from copy import deepcopy
from typing import Any, Dict, List, Optional

from ..utils.llm_client import LLMClient
from ..utils.logger import get_logger

logger = get_logger("horizonxl.services.domain_simulation_planner")


def _keyword_present(text: str, keyword: str) -> bool:
    """Match terms safely so short keywords like 'ai' do not match unrelated words."""
    pattern = re.escape(keyword.lower()).replace(r"\ ", r"\s+")
    return re.search(rf"(?<![a-z0-9]){pattern}(?![a-z0-9])", text) is not None


DOMAINS = {
    "macro",
    "election",
    "oil",
    "ai_future",
    "geopolitics",
    "transport",
    "market",
    "social",
    "business",
    "healthcare",
    "climate",
    "real_estate",
    "crypto",
    "supply_chain",
    "education",
    "policy",
    "technology",
    "sports",
    "consumer",
    "other",
}

SCENARIOS = {
    "base_case": True,
    "upside_case": True,
    "downside_case": True,
    "tail_case": True,
}

DEFAULT_REQUIRED_OUTPUTS = [
    "numeric_table",
    "agent_forecasts",
    "scenario_paths",
    "confidence_bands",
    "report",
    "charts",
]

DEFAULT_VALIDATION_REQUIREMENTS = [
    "all_required_agents_have_forecasts",
    "all_required_target_variables_have_numeric_values",
    "forecast_horizon_complete",
    "scenario_paths_complete",
]


ORCHESTRATION_AGENT_TEMPLATES = [
    {
        "name": "Simulation Moderator",
        "role": "Keeps the discussion on the simulation question, enforces turn order, and summarizes unresolved disagreements.",
        "count": 1,
        "numeric_output_required": False,
    },
    {
        "name": "Evidence Auditor",
        "role": "Checks whether claims are supported by graph evidence, uploaded context, or explicitly supplied external research pointers.",
        "count": 1,
        "numeric_output_required": False,
    },
    {
        "name": "Quantitative Synthesizer",
        "role": "Converts agent claims into numeric paths, confidence bands, disagreement ranges, and missing-data warnings.",
        "count": 1,
        "numeric_output_required": True,
    },
]


RESEARCH_AGENT_TEMPLATES = [
    {
        "name": "External Research Scout",
        "role": "Collects fresh context from user-provided URLs or approved scraping/search tools outside the graph.",
        "count": 1,
        "external_to_graph": True,
        "numeric_output_required": False,
    },
    {
        "name": "Data Retrieval Analyst",
        "role": "Looks for numeric tables, dates, units, source caveats, and data gaps needed by the simulation.",
        "count": 1,
        "external_to_graph": True,
        "numeric_output_required": True,
    },
]


DOMAIN_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "macro": {
        "target_variables": [
            {"name": "unemployment_rate", "unit": "percent", "required": True, "description": "Headline unemployment rate forecast."},
            {"name": "real_gdp_growth", "unit": "percent annualized", "required": False, "description": "Output growth path driving labor-market demand."},
            {"name": "inflation_rate", "unit": "percent", "required": False, "description": "Inflation pressure relevant to policy response."},
            {"name": "credit_stress_index", "unit": "index", "required": False, "description": "Credit-market stress and funding conditions."},
        ],
        "state_variables": [
            {"name": "labor_market_slack", "unit": "index", "directional_interpretation": "Higher means weaker labor demand.", "required": True},
            {"name": "credit_conditions", "unit": "index", "directional_interpretation": "Higher means tighter credit.", "required": True},
            {"name": "household_demand", "unit": "index", "directional_interpretation": "Higher means stronger consumption.", "required": False},
        ],
        "agents": [
            ("Central Bank", "Sets policy and interprets inflation/labor tradeoffs.", "High-quality macro and financial data.", "Institutional bias toward gradualism."),
            ("Commercial Banks", "Controls credit supply to firms and households.", "Loan book deterioration and underwriting changes.", "May understate balance-sheet risk."),
            ("Businesses", "Translate demand expectations into hiring and investment.", "Order books, margins, local demand.", "May anchor on recent operating conditions."),
            ("Households and Workers", "Represent income stress, consumption, and labor supply.", "Lived experience and local labor conditions.", "May overreact to local shocks."),
            ("Academic Economists", "Model historical macro relationships.", "Historical data, recession analogs, published methods.", "May underweight model breaks."),
            ("Media and Market Narratives", "Amplifies public sentiment and consensus views.", "Narrative velocity and public framing.", "May chase dominant stories."),
        ],
        "granularity": "monthly",
    },
    "election": {
        "target_variables": [
            {"name": "vote_share", "unit": "percent", "required": True, "description": "Candidate or party vote share."},
            {"name": "turnout", "unit": "percent", "required": True, "description": "Eligible voter turnout."},
            {"name": "win_probability", "unit": "probability", "required": True, "description": "Probability of victory."},
        ],
        "state_variables": [
            {"name": "polling_margin", "unit": "percentage points", "directional_interpretation": "Higher favors the focal candidate or party.", "required": True},
            {"name": "turnout_energy", "unit": "index", "directional_interpretation": "Higher means stronger likely turnout.", "required": True},
        ],
        "agents": [
            ("Voter Blocs", "Shift turnout and vote choice.", "Local sentiment and issue salience.", "May be poorly sampled.", 0.50, ["rural welfare voters", "urban middle-class voters", "minority voters", "youth/job-seeking voters", "regional swing voters"]),
            ("Campaign Strategists", "Allocate resources and shape messaging.", "Internal polling and field data.", "May overrate campaign impact.", 0.14, ["incumbent strategist", "main opposition strategist"]),
            ("Pollsters", "Measure public opinion.", "Survey data and weighting models.", "May miss turnout composition.", 0.10, ["survey pollster", "exit-poll analyst", "seat modeler"]),
            ("Media", "Frames race dynamics and scandals.", "Narrative reach and attention.", "May amplify short-term swings.", 0.08, ["local journalist", "national political journalist"]),
            ("Field Workers", "Report booth-level turnout, mobilization, and last-mile signals.", "Ground organization and polling-day feedback.", "May overread their own booth network.", 0.10, ["booth worker", "district organizer", "civil society observer"]),
            ("Alliance and Smaller Party Actors", "Influence vote splits, tactical transfers, and coalition math.", "Seat-sharing and local candidate strength.", "May overstate bargaining power.", 0.08, ["alliance negotiator", "minor-party broker"]),
        ],
        "granularity": "weekly",
    },
    "oil": {
        "target_variables": [
            {"name": "brent_price", "unit": "USD/barrel", "required": True, "description": "Brent crude oil price forecast."},
            {"name": "wti_price", "unit": "USD/barrel", "required": False, "description": "WTI crude oil price forecast."},
            {"name": "supply_demand_balance", "unit": "million barrels/day", "required": True, "description": "Global supply minus demand balance."},
        ],
        "state_variables": [
            {"name": "inventory_pressure", "unit": "index", "directional_interpretation": "Higher means inventories are tight.", "required": True},
            {"name": "geopolitical_risk_premium", "unit": "USD/barrel", "directional_interpretation": "Higher means more risk premium in price.", "required": True},
        ],
        "agents": [
            ("OPEC", "Controls coordinated supply decisions.", "Quota discipline and spare capacity.", "May overstate cohesion."),
            ("US Shale Producers", "Respond to price and financing conditions.", "Rig plans and production economics.", "May lag price signals."),
            ("China Demand", "Moves marginal demand expectations.", "Industrial activity and imports.", "May be opaque or delayed."),
            ("Traders", "Price short-term risk and positioning.", "Flows, spreads, options, inventories.", "May overreact to headlines."),
            ("Refiners", "Translate crude into product demand.", "Crack spreads and utilization.", "May focus on local bottlenecks."),
            ("Governments and Shipping", "Drive sanctions, logistics, and strategic reserves.", "Policy and route disruption data.", "May introduce abrupt regime changes."),
        ],
        "granularity": "monthly",
    },
    "ai_future": {
        "target_variables": [
            {"name": "enterprise_adoption_rate", "unit": "percent", "required": True, "description": "Share of enterprises deploying AI in production."},
            {"name": "ai_capex", "unit": "USD", "required": True, "description": "Capital expenditure for AI infrastructure."},
            {"name": "regulation_score", "unit": "index", "required": True, "description": "Regulatory restrictiveness or clarity."},
        ],
        "state_variables": [
            {"name": "model_capability_index", "unit": "index", "directional_interpretation": "Higher means more capable models.", "required": True},
            {"name": "deployment_friction", "unit": "index", "directional_interpretation": "Higher means slower adoption.", "required": True},
        ],
        "agents": [
            ("Frontier Labs", "Advance model capability and product availability.", "Roadmaps, compute, evals.", "May hype capability."),
            ("Open Source Developers", "Diffuse capabilities and reduce costs.", "Community releases and tooling.", "May underweight compliance constraints."),
            ("Enterprises", "Adopt or reject production systems.", "Budget, workflow, procurement data.", "May move slower than pilots imply."),
            ("Regulators", "Set constraints and obligations.", "Policy priorities and enforcement signals.", "May lag technical change."),
            ("Investors", "Fund infrastructure and startups.", "Capital flows and valuation pressure.", "May extrapolate booms."),
            ("Workers and Consumers", "Create adoption resistance or demand.", "Lived impact and trust signals.", "May respond unevenly by sector."),
        ],
        "granularity": "quarterly",
    },
}

DOMAIN_TEMPLATES.update({
    "transport": {
        "target_variables": [
            {"name": "commute_delay_minutes", "unit": "minutes", "required": True, "description": "Expected average commute delay."},
            {"name": "service_disruption_index", "unit": "index", "required": True, "description": "Severity of route cancellations, crowding, and reliability loss."},
            {"name": "ridership_shift", "unit": "percent", "required": False, "description": "Share of riders shifting to alternate modes."},
        ],
        "state_variables": [
            {"name": "labor_action_intensity", "unit": "index", "directional_interpretation": "Higher means broader or longer work stoppage.", "required": True},
            {"name": "alternate_capacity", "unit": "index", "directional_interpretation": "Higher means more substitute transport capacity.", "required": True},
            {"name": "public_tolerance", "unit": "index", "directional_interpretation": "Higher means less public backlash from disruption.", "required": False},
        ],
        "agents": [
            ("Transit Authority", "Controls service plans, public alerts, and contingency operations.", "Route capacity, staffing, and service data.", "May understate disruption risk.", 0.14, ["agency operations", "communications lead"]),
            ("Operator Union", "Controls strike participation and labor demands.", "Worker sentiment and negotiation stance.", "May overstate bargaining leverage.", 0.16, ["union negotiator", "rank-and-file operator"]),
            ("Commuter Segments", "Create demand shifts and lived delay outcomes.", "Ground-level commute conditions.", "May vary by route and income.", 0.34, ["daily rail commuter", "bus-dependent rider", "suburban commuter", "low-income worker"]),
            ("City Government", "Coordinates public response and political pressure.", "Emergency planning and negotiation leverage.", "May prioritize optics.", 0.12, ["mayor office", "transport department"]),
            ("Employers and Schools", "Adjust attendance, hours, and remote-work policies.", "Workplace absenteeism and schedule flexibility.", "May not represent hourly workers.", 0.10, ["large employer", "school administrator"]),
            ("Mobility and Traffic Analysts", "Quantify delay paths and alternate-mode capacity.", "Traffic, rideshare, road, and service data.", "May miss behavioral adaptation.", 0.14, ["traffic analyst", "mobility provider", "local journalist"]),
        ],
        "granularity": "daily",
    },
    "geopolitics": {
        "target_variables": [
            {"name": "escalation_probability", "unit": "probability", "required": True, "description": "Probability of escalation or de-escalation over the horizon."},
            {"name": "policy_shift_index", "unit": "index", "required": True, "description": "Magnitude of expected diplomatic, military, or sanctions policy change."},
            {"name": "market_or_public_risk_impact", "unit": "index", "required": False, "description": "Downstream risk impact on markets or public conditions."},
        ],
        "state_variables": [
            {"name": "military_pressure", "unit": "index", "directional_interpretation": "Higher means greater conflict pressure.", "required": True},
            {"name": "diplomatic_channel_strength", "unit": "index", "directional_interpretation": "Higher means more credible de-escalation channels.", "required": True},
            {"name": "domestic_political_constraint", "unit": "index", "directional_interpretation": "Higher means leaders have less room to compromise.", "required": False},
        ],
        "agents": [
            ("State Decision Makers", "Set military, diplomatic, or sanctions strategy.", "Internal policy constraints and red lines.", "May posture publicly.", 0.22, ["incumbent government", "rival government"]),
            ("Military and Security Actors", "Create facts on the ground and escalation risks.", "Operational readiness and incident data.", "May overstate deterrence.", 0.18, ["military command", "intelligence analyst"]),
            ("Diplomats and Mediators", "Transmit offers, constraints, and off-ramps.", "Back-channel information.", "May overrate negotiated outcomes.", 0.14, ["formal diplomat", "third-party mediator"]),
            ("Allies and External Powers", "Shift incentives through support or pressure.", "Alliance commitments and aid signals.", "May pursue own agenda.", 0.16, ["ally government", "regional power"]),
            ("Affected Public and Civil Society", "Shape legitimacy, protest, migration, and humanitarian pressure.", "Ground-level lived impact.", "May be under-sampled.", 0.20, ["border community", "urban public", "diaspora voice"]),
            ("Media and Risk Analysts", "Frame narratives and assess probabilities.", "Open-source signals and market reaction.", "May overweight visible events.", 0.10, ["local journalist", "OSINT analyst"]),
        ],
        "granularity": "event_triggered",
    },
    "market": {
        "target_variables": [
            {"name": "price_level", "unit": "index", "required": True, "description": "Primary market price or index path."},
            {"name": "volatility", "unit": "percent", "required": True, "description": "Expected volatility or uncertainty."},
            {"name": "liquidity_stress", "unit": "index", "required": False, "description": "Market depth and funding stress."},
        ],
        "state_variables": [
            {"name": "risk_appetite", "unit": "index", "directional_interpretation": "Higher means stronger demand for risk assets.", "required": True},
            {"name": "positioning_pressure", "unit": "index", "directional_interpretation": "Higher means crowded positioning or forced-flow risk.", "required": True},
        ],
        "agents": [
            ("Long-Only Investors", "Allocate capital based on fundamentals and mandates.", "Portfolio flows and benchmark constraints.", "May be slow to reduce exposure.", 0.22, ["institutional investor", "retail investor"]),
            ("Hedge Funds and Traders", "Move near-term price through positioning.", "Flow, options, and liquidity signals.", "May overreact to catalysts.", 0.18, ["macro trader", "event-driven trader"]),
            ("Issuers and Corporates", "Affect supply, buybacks, guidance, and credit quality.", "Internal operating data.", "May smooth negative news.", 0.16, ["corporate issuer", "credit officer"]),
            ("Policy and Central Bank Watchers", "Interpret rates, regulation, and liquidity.", "Policy reaction function.", "May overfit central-bank language.", 0.14, ["rates strategist", "regulatory analyst"]),
            ("Retail/Public Sentiment", "Creates momentum, panic, or adoption waves.", "Social and brokerage behavior.", "May be reflexive.", 0.20, ["optimistic retail cohort", "panic-selling cohort"]),
            ("Market Data Analysts", "Extract numbers and scenario paths.", "Prices, volumes, spreads, and option surfaces.", "May miss narrative shocks.", 0.10, ["quant analyst", "liquidity analyst"]),
        ],
        "granularity": "weekly",
    },
    "business": {
        "target_variables": [
            {"name": "revenue_growth", "unit": "percent", "required": True, "description": "Expected revenue growth path."},
            {"name": "market_share", "unit": "percent", "required": True, "description": "Expected share of target market."},
            {"name": "margin", "unit": "percent", "required": False, "description": "Profitability or contribution margin path."},
        ],
        "state_variables": [
            {"name": "customer_demand", "unit": "index", "directional_interpretation": "Higher means stronger demand.", "required": True},
            {"name": "competitive_pressure", "unit": "index", "directional_interpretation": "Higher means stronger competitive threat.", "required": True},
        ],
        "agents": [
            ("Leadership Team", "Sets strategy, investment, pricing, and positioning.", "Internal goals and constraints.", "May defend existing plans.", 0.16, ["CEO view", "finance view"]),
            ("Customer Segments", "Determine adoption, churn, and willingness to pay.", "Lived buyer friction and needs.", "May vary sharply by segment.", 0.34, ["enterprise buyer", "SMB buyer", "price-sensitive customer", "power user"]),
            ("Sales and Channel Teams", "Translate demand into pipeline and conversion.", "Pipeline, objections, and channel feedback.", "May overstate close probability.", 0.14, ["field sales", "channel partner"]),
            ("Competitors", "Change pricing, features, and distribution.", "Competitive moves and incentives.", "May trigger retaliation.", 0.16, ["incumbent competitor", "new entrant"]),
            ("Product and Operations", "Controls delivery quality and roadmap.", "Build capacity and bottlenecks.", "May underweight market narrative.", 0.12, ["product lead", "operations lead"]),
            ("Investors and Analysts", "Frame expectations and capital access.", "Capital market sentiment.", "May prefer short-term metrics.", 0.08, ["board investor", "industry analyst"]),
        ],
        "granularity": "monthly",
    },
    "consumer": {
        "target_variables": [
            {"name": "demand_index", "unit": "index", "required": True, "description": "Demand or adoption path for the product/category."},
            {"name": "purchase_intent", "unit": "percent", "required": True, "description": "Share likely to buy or switch."},
            {"name": "price_sensitivity", "unit": "index", "required": False, "description": "Sensitivity to price changes."},
        ],
        "state_variables": [
            {"name": "consumer_confidence", "unit": "index", "directional_interpretation": "Higher means stronger willingness to spend.", "required": True},
            {"name": "trend_velocity", "unit": "index", "directional_interpretation": "Higher means faster narrative or taste spread.", "required": True},
        ],
        "agents": [
            ("Consumer Segments", "Drive category demand, switching, and retention.", "Ground-level preferences and budget pressure.", "May be heterogeneous.", 0.46, ["value seeker", "premium buyer", "youth trend adopter", "family household", "loyal customer"]),
            ("Retailers and Channels", "Control shelf space, availability, and promotions.", "Store traffic and sell-through.", "May optimize locally.", 0.14, ["offline retailer", "online marketplace"]),
            ("Brands and Marketers", "Shape positioning and campaigns.", "Brand strategy and budget.", "May overrate messaging.", 0.14, ["incumbent brand", "challenger brand"]),
            ("Influencers and Media", "Accelerate narratives and social proof.", "Attention and sentiment signals.", "May amplify fads.", 0.10, ["creator", "review outlet"]),
            ("Suppliers and Operators", "Constrain availability, price, and quality.", "Inventory and logistics.", "May lag demand swings.", 0.08, ["supplier", "store operator"]),
            ("Consumer Researchers", "Measure behavior and forecast demand.", "Survey, panel, and transaction data.", "May miss fast-moving subcultures.", 0.08, ["survey researcher", "data analyst"]),
        ],
        "granularity": "monthly",
    },
    "policy": {
        "target_variables": [
            {"name": "policy_passage_probability", "unit": "probability", "required": True, "description": "Probability that the policy is enacted or implemented."},
            {"name": "impact_index", "unit": "index", "required": True, "description": "Expected magnitude of policy impact."},
            {"name": "compliance_cost", "unit": "currency or index", "required": False, "description": "Expected cost for affected parties."},
        ],
        "state_variables": [
            {"name": "political_support", "unit": "index", "directional_interpretation": "Higher means stronger support.", "required": True},
            {"name": "implementation_capacity", "unit": "index", "directional_interpretation": "Higher means easier implementation.", "required": True},
        ],
        "agents": [
            ("Policymakers", "Draft, negotiate, and pass policy.", "Legislative priorities and vote counts.", "May overstate feasibility.", 0.20, ["ruling coalition", "opposition lawmaker"]),
            ("Regulators and Agencies", "Implement and enforce rules.", "Administrative capacity and enforcement data.", "May protect jurisdiction.", 0.16, ["agency official", "enforcement officer"]),
            ("Affected Citizens", "Create public pressure and real-world impact.", "Lived costs and benefits.", "May be unevenly represented.", 0.28, ["beneficiary", "taxpayer", "worker", "local community"]),
            ("Industry and Lobby Groups", "Influence details and compliance behavior.", "Operational cost and lobbying channels.", "May understate public benefits.", 0.16, ["industry association", "large firm"]),
            ("Courts and Watchdogs", "Constrain legality and accountability.", "Legal challenge risk.", "May slow implementation.", 0.10, ["court observer", "civil society watchdog"]),
            ("Policy Analysts and Media", "Estimate impact and shape narrative.", "Data, models, and public framing.", "May focus on salient controversies.", 0.10, ["policy analyst", "journalist"]),
        ],
        "granularity": "event_triggered",
    },
})


class DomainSimulationPlanner:
    """Create and normalize domain-general simulation blueprints."""

    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm_client = llm_client

    def plan(
        self,
        user_question: str,
        document_text: str = "",
        project_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Return a normalized simulation plan for the prompt and context."""
        user_question = (user_question or "").strip()
        if not user_question:
            raise ValueError("A user question or simulation requirement is required.")

        try:
            if self.llm_client is None:
                self.llm_client = LLMClient()
            raw = self._plan_with_llm(user_question, document_text)
            plan = self._normalize_plan(raw, user_question)
        except Exception as exc:
            logger.warning(f"Planner LLM failed, using deterministic fallback: {exc}")
            plan = self._fallback_plan(user_question)

        if project_id:
            plan["project_id"] = project_id

        cutoff = self._detect_cutoff_date(user_question)
        if cutoff:
            plan["cutoff_date"] = cutoff
            plan["future_leakage_policy"] = {
                "enabled": True,
                "blocked_after": cutoff,
                "violations": [],
            }
        else:
            plan.setdefault("cutoff_date", None)
            plan.setdefault("future_leakage_policy", {
                "enabled": False,
                "blocked_after": None,
                "violations": [],
            })

        return plan

    def _plan_with_llm(self, user_question: str, document_text: str) -> Dict[str, Any]:
        context_excerpt = (document_text or "")[:12000]
        system = (
            "You are Horizon XL's domain simulation planner. Return only valid JSON. "
            "Classify the user's future-facing question into a domain and produce a "
            "domain-general simulation blueprint. Keep every field in English. Do not "
            "include future actual outcomes when the user asks for a blind simulation."
        )
        prompt = f"""
Create a simulation blueprint using exactly this top-level schema:
{{
  "domain": "macro | election | oil | ai_future | geopolitics | market | social | business | healthcare | climate | real_estate | crypto | supply_chain | education | policy | technology | sports | consumer | other",
  "user_question": "...",
  "target_variables": [
    {{"name": "...", "unit": "...", "required": true, "description": "..."}}
  ],
  "forecast_horizon": {{
    "start": "...",
    "end": "...",
    "granularity": "daily | weekly | monthly | quarterly | yearly | event_triggered"
  }},
  "required_agent_archetypes": [
    {{
      "name": "...",
      "causal_role": "...",
      "information_advantage": "...",
      "likely_bias": "...",
      "population_share": 0.25,
      "subtypes": ["..."],
      "numeric_output_required": true
    }}
  ],
  "agent_population": {{
    "target_agent_count": 18,
    "allocation_basis": "Explain why some archetypes need multiple agents.",
    "allocations": [
      {{
        "archetype_name": "...",
        "population_share": 0.5,
        "instance_count": 6,
        "rationale": "...",
        "subtypes": ["..."]
      }}
    ],
    "orchestration_agents": [
      {{"name": "Simulation Moderator", "role": "Keeps debate focused.", "count": 1, "numeric_output_required": false}},
      {{"name": "Evidence Auditor", "role": "Checks claims against evidence.", "count": 1, "numeric_output_required": false}},
      {{"name": "Quantitative Synthesizer", "role": "Builds numeric scenario tables.", "count": 1, "numeric_output_required": true}}
    ],
    "research_agents": [
      {{"name": "External Research Scout", "role": "Collects approved external source pointers outside the graph.", "count": 1, "external_to_graph": true, "numeric_output_required": false}},
      {{"name": "Data Retrieval Analyst", "role": "Extracts numbers, units, dates, and data gaps.", "count": 1, "external_to_graph": true, "numeric_output_required": true}}
    ]
  }},
  "discussion_architecture": {{
    "moderated": true,
    "loop": ["moderator frames the pocket", "research/data agents inject evidence", "causal agents revise", "numeric synthesizer aggregates", "evidence auditor flags gaps"],
    "anti_drift_rules": ["Every round must reference target variables."]
  }},
  "external_research_policy": {{
    "enabled": true,
    "outside_graph": true,
    "allowed_inputs": ["user_provided_urls", "uploaded_files", "approved_search_or_scraping_tool_results"],
    "injection_point": "before each debate pocket and before numeric synthesis"
  }},
  "state_variables": [
    {{
      "name": "...",
      "unit": "...",
      "directional_interpretation": "...",
      "required": true
    }}
  ],
  "scenario_structure": {{
    "base_case": true,
    "upside_case": true,
    "downside_case": true,
    "tail_case": true
  }},
  "required_outputs": ["numeric_table", "agent_forecasts", "scenario_paths", "confidence_bands", "report", "charts"],
  "validation_requirements": [
    "all_required_agents_have_forecasts",
    "all_required_target_variables_have_numeric_values",
    "forecast_horizon_complete",
    "scenario_paths_complete"
  ]
}}

User question:
{user_question}

Uploaded/project context excerpt:
{context_excerpt}

Planning rules:
- Do not assume exactly 10 agents. Decide target_agent_count from domain complexity, number of target variables, time horizon, and how much the outcome depends on mass/public behavior.
- If voters, consumers, workers, households, patients, students, or common people drive the outcome, allocate multiple instances and subtypes to that archetype instead of one token representative.
- Always include moderator/evidence/numeric/research control roles in agent_population; these are process agents, not causal stakeholder agents.
- Research agents collect source pointers outside graph memory and inject them between debate rounds; the graph remains the durable evidence memory.
"""
        return self.llm_client.chat_json(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=4096,
        )

    def _fallback_plan(self, user_question: str) -> Dict[str, Any]:
        domain = self._classify_domain(user_question)
        template = deepcopy(DOMAIN_TEMPLATES.get(domain) or self._generic_template(domain))
        agents = [
            {
                "name": name,
                "causal_role": causal_role,
                "information_advantage": info,
                "likely_bias": bias,
                "population_share": share if isinstance(share, (int, float)) else None,
                "subtypes": subtypes if isinstance(subtypes, list) else [],
                "numeric_output_required": True,
            }
            for name, causal_role, info, bias, *rest in template["agents"]
            for share, subtypes in [(
                rest[0] if len(rest) > 0 else None,
                rest[1] if len(rest) > 1 else [],
            )]
        ]
        plan = self._normalize_plan({
            "domain": domain,
            "user_question": user_question,
            "target_variables": template["target_variables"],
            "forecast_horizon": {
                "start": "auto",
                "end": "auto",
                "granularity": template.get("granularity", "event_triggered"),
            },
            "required_agent_archetypes": agents,
            "state_variables": template["state_variables"],
            "scenario_structure": deepcopy(SCENARIOS),
            "required_outputs": list(DEFAULT_REQUIRED_OUTPUTS),
            "validation_requirements": list(DEFAULT_VALIDATION_REQUIREMENTS),
        }, user_question)
        plan["agent_population"] = self._build_agent_population_plan(user_question, domain, plan["required_agent_archetypes"])
        plan["discussion_architecture"] = self._build_discussion_architecture(user_question)
        plan["external_research_policy"] = self._build_external_research_policy(user_question)
        return plan

    def _normalize_plan(self, raw: Dict[str, Any], user_question: str) -> Dict[str, Any]:
        plan = raw if isinstance(raw, dict) else {}
        domain = str(plan.get("domain") or self._classify_domain(user_question)).strip()
        if domain not in DOMAINS:
            domain = "other"

        fallback = self._fallback_plan_without_llm(domain, user_question)
        normalized = {
            "domain": domain,
            "user_question": str(plan.get("user_question") or user_question),
            "target_variables": self._normalize_target_variables(plan.get("target_variables") or fallback["target_variables"]),
            "forecast_horizon": self._normalize_horizon(plan.get("forecast_horizon") or fallback["forecast_horizon"]),
            "required_agent_archetypes": self._normalize_agents(plan.get("required_agent_archetypes") or fallback["required_agent_archetypes"]),
            "state_variables": self._normalize_state_variables(plan.get("state_variables") or fallback["state_variables"]),
            "scenario_structure": self._normalize_scenarios(plan.get("scenario_structure")),
            "required_outputs": self._ensure_list(plan.get("required_outputs"), DEFAULT_REQUIRED_OUTPUTS),
            "validation_requirements": self._ensure_list(plan.get("validation_requirements"), DEFAULT_VALIDATION_REQUIREMENTS),
        }
        normalized["agent_population"] = self._normalize_agent_population(
            plan.get("agent_population"),
            normalized["required_agent_archetypes"],
            normalized["domain"],
            user_question,
        )
        normalized["discussion_architecture"] = plan.get("discussion_architecture") or self._build_discussion_architecture(user_question)
        normalized["external_research_policy"] = plan.get("external_research_policy") or self._build_external_research_policy(user_question)
        return normalized

    def _fallback_plan_without_llm(self, domain: str, user_question: str) -> Dict[str, Any]:
        template = deepcopy(DOMAIN_TEMPLATES.get(domain) or self._generic_template(domain))
        return {
            "domain": domain,
            "user_question": user_question,
            "target_variables": template["target_variables"],
            "forecast_horizon": {"start": "auto", "end": "auto", "granularity": template.get("granularity", "event_triggered")},
            "required_agent_archetypes": [
                {
                    "name": name,
                    "causal_role": causal_role,
                    "information_advantage": info,
                    "likely_bias": bias,
                    "population_share": share if isinstance(share, (int, float)) else None,
                    "subtypes": subtypes if isinstance(subtypes, list) else [],
                    "numeric_output_required": True,
                }
                for name, causal_role, info, bias, *rest in template["agents"]
                for share, subtypes in [(
                    rest[0] if len(rest) > 0 else None,
                    rest[1] if len(rest) > 1 else [],
                )]
            ],
            "state_variables": template["state_variables"],
        }

    def _generic_template(self, domain: str) -> Dict[str, Any]:
        return {
            "target_variables": [
                {"name": f"{domain}_outcome_index", "unit": "index", "required": True, "description": "Primary forecast outcome for the simulation question."}
            ],
            "state_variables": [
                {"name": "system_pressure", "unit": "index", "directional_interpretation": "Higher means stronger pressure toward outcome change.", "required": True},
                {"name": "stakeholder_confidence", "unit": "index", "directional_interpretation": "Higher means stronger confidence in the current path.", "required": True},
            ],
            "agents": [
                ("Incumbent Decision Makers", "Can directly alter policy, strategy, or resource allocation.", "Internal plans and operational constraints.", "May defend the status quo."),
                ("Market or Public Participants", "Aggregate demand, behavior, and reaction pressure.", "Ground-level behavior and sentiment.", "May react nonlinearly."),
                ("Analysts and Experts", "Interpret evidence and form forecasts.", "Historical analogs and data models.", "May overfit prior regimes."),
                ("Media and Narrative Brokers", "Amplify frames and attention.", "Public attention and message velocity.", "May overweight salient events."),
                ("Opposition or Competitors", "Exploit weaknesses and create counter-pressure.", "Alternative strategy and adversarial incentives.", "May overstate downside."),
            ],
            "granularity": "event_triggered",
        }

    def _classify_domain(self, text: str) -> str:
        lowered = text.lower()
        keyword_map = {
            "election": ["election", "vote", "turnout", "candidate", "campaign", "poll", "seat share", "constituency"],
            "oil": ["oil", "brent", "wti", "opec", "crude", "barrel", "inventory", "refinery", "shale"],
            "ai_future": ["ai", "artificial intelligence", "frontier model", "model capability", "open-source", "enterprise adoption", "gpu", "llm"],
            "geopolitics": ["geopolitic", "war", "sanction", "diplomacy", "conflict", "country risk", "military", "border"],
            "transport": ["transport", "transportation", "transit", "commute", "commuter", "metro", "subway", "bus", "rail", "train", "traffic", "strike", "labor action", "public service", "ridership", "delay"],
            "healthcare": ["healthcare", "hospital", "patient", "doctor", "drug", "vaccine", "public health", "insurance", "pharma"],
            "climate": ["climate", "carbon", "emissions", "renewable", "solar", "wind", "grid", "energy transition", "flood", "drought"],
            "real_estate": ["real estate", "housing price", "home price", "mortgage", "rent", "tenant", "landlord", "developer"],
            "crypto": ["crypto", "bitcoin", "ethereum", "stablecoin", "defi", "blockchain", "token", "on-chain"],
            "supply_chain": ["supply chain", "logistics", "shipping", "port", "freight", "supplier", "lead time", "manufacturing"],
            "education": ["education", "school", "student", "teacher", "university", "college", "tuition", "enrollment"],
            "policy": ["policy", "regulation", "regulator", "law", "bill", "legislation", "court", "compliance", "tax", "subsidy"],
            "technology": ["technology adoption", "software", "saas", "platform", "app", "developer", "cloud", "cybersecurity", "api"],
            "sports": ["sports", "team", "league", "match", "tournament", "player", "coach", "injury", "playoff"],
            "consumer": ["consumer", "retail", "shopping", "brand", "fashion", "restaurant", "spending", "loyalty", "store traffic"],
            "business": ["business", "strategy", "sales", "customer", "pricing", "competitor", "market share"],
            "social": ["social", "media narrative", "consumer trend", "public opinion", "culture", "influencer", "sentiment"],
            "market": ["stock", "bond", "market", "equity", "yield", "volatility", "commodity", "portfolio"],
            "macro": ["unemployment", "gdp", "inflation", "interest rate", "recession", "macro", "fed", "central bank"],
        }
        priority = [
            "election", "oil", "ai_future", "geopolitics", "transport", "healthcare", "climate",
            "real_estate", "crypto", "supply_chain", "education", "policy",
            "technology", "sports", "consumer", "business", "social", "market", "macro",
        ]
        scores = {
            domain: sum(1 for keyword in keywords if _keyword_present(lowered, keyword))
            for domain, keywords in keyword_map.items()
        }
        if any(scores.values()):
            return max(priority, key=lambda domain: (scores.get(domain, 0), -priority.index(domain)))
        return "other"

    def _detect_cutoff_date(self, text: str) -> Optional[str]:
        patterns = [
            r"(?:available|known|data|information)\s+(?:only\s+)?(?:up\s+)?to\s+([A-Za-z]{3,9}\s+\d{4}|\d{4}-\d{2}-\d{2}|\d{4})",
            r"(?:as\s+of|through|before)\s+([A-Za-z]{3,9}\s+\d{4}|\d{4}-\d{2}-\d{2}|\d{4})",
            r"(?:cutoff|cut-off)\s*(?:date)?\s*[:=]?\s*([A-Za-z]{3,9}\s+\d{4}|\d{4}-\d{2}-\d{2}|\d{4})",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return None

    def _normalize_target_variables(self, values: Any) -> List[Dict[str, Any]]:
        out = []
        for item in values if isinstance(values, list) else []:
            if not isinstance(item, dict):
                continue
            out.append({
                "name": str(item.get("name") or "target_variable"),
                "unit": str(item.get("unit") or "unit"),
                "required": bool(item.get("required", True)),
                "description": str(item.get("description") or ""),
            })
        return out or [{"name": "target_variable", "unit": "unit", "required": True, "description": "Primary simulated outcome."}]

    def _normalize_horizon(self, value: Any) -> Dict[str, Any]:
        horizon = value if isinstance(value, dict) else {}
        granularity = str(horizon.get("granularity") or "event_triggered")
        allowed = {"daily", "weekly", "monthly", "quarterly", "yearly", "event_triggered"}
        if granularity not in allowed:
            granularity = "event_triggered"
        return {
            "start": str(horizon.get("start") or "auto"),
            "end": str(horizon.get("end") or "auto"),
            "granularity": granularity,
        }

    def _normalize_agents(self, values: Any) -> List[Dict[str, Any]]:
        out = []
        for item in values if isinstance(values, list) else []:
            if not isinstance(item, dict):
                continue
            out.append({
                "name": str(item.get("name") or "Agent"),
                "causal_role": str(item.get("causal_role") or "Influences the target outcome."),
                "information_advantage": str(item.get("information_advantage") or "Has relevant domain evidence."),
                "likely_bias": str(item.get("likely_bias") or "May overweight familiar information."),
                "population_share": item.get("population_share"),
                "instance_count": item.get("instance_count"),
                "subtypes": self._ensure_list(item.get("subtypes"), []),
                "numeric_output_required": bool(item.get("numeric_output_required", True)),
            })
        return out or [{
            "name": "Generalist Analyst",
            "causal_role": "Interprets evidence and forecasts the target outcome.",
            "information_advantage": "Broad evidence synthesis.",
            "likely_bias": "May smooth uncertainty into consensus.",
            "population_share": 1.0,
            "instance_count": 1,
            "subtypes": [],
            "numeric_output_required": True,
        }]

    def _normalize_state_variables(self, values: Any) -> List[Dict[str, Any]]:
        out = []
        for item in values if isinstance(values, list) else []:
            if not isinstance(item, dict):
                continue
            out.append({
                "name": str(item.get("name") or "state_variable"),
                "unit": str(item.get("unit") or "unit"),
                "directional_interpretation": str(item.get("directional_interpretation") or "Higher means more pressure on the target outcome."),
                "required": bool(item.get("required", True)),
            })
        return out or [{"name": "system_pressure", "unit": "index", "directional_interpretation": "Higher means more outcome pressure.", "required": True}]

    def _normalize_scenarios(self, value: Any) -> Dict[str, bool]:
        scenario = value if isinstance(value, dict) else {}
        return {key: bool(scenario.get(key, default)) for key, default in SCENARIOS.items()}

    def _ensure_list(self, value: Any, fallback: List[str]) -> List[str]:
        if not isinstance(value, list) or not value:
            return list(fallback)
        return [str(item) for item in value if str(item).strip()] or list(fallback)

    def _normalize_agent_population(
        self,
        value: Any,
        archetypes: List[Dict[str, Any]],
        domain: str,
        user_question: str,
    ) -> Dict[str, Any]:
        if isinstance(value, dict) and value.get("allocations"):
            population = dict(value)
            population.setdefault("target_agent_count", self._infer_target_agent_count(user_question, domain, archetypes))
            population.setdefault("orchestration_agents", deepcopy(ORCHESTRATION_AGENT_TEMPLATES))
            population.setdefault("research_agents", deepcopy(RESEARCH_AGENT_TEMPLATES))
            population.setdefault("allocation_basis", "Planner supplied allocation normalized by Horizon XL.")
            return population
        return self._build_agent_population_plan(user_question, domain, archetypes)

    def _build_agent_population_plan(
        self,
        user_question: str,
        domain: str,
        archetypes: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        target_count = self._infer_target_agent_count(user_question, domain, archetypes)
        orchestration_count = sum(int(a.get("count", 1)) for a in ORCHESTRATION_AGENT_TEMPLATES)
        research_count = sum(int(a.get("count", 1)) for a in RESEARCH_AGENT_TEMPLATES)
        causal_slots = max(len(archetypes), target_count - orchestration_count - research_count)

        shares = self._normalized_population_shares(archetypes, domain, user_question)
        allocations = []
        remaining = causal_slots
        for idx, archetype in enumerate(archetypes):
            if idx == len(archetypes) - 1:
                count = max(1, remaining)
            else:
                count = max(1, round(causal_slots * shares[idx]))
                remaining -= count
            archetype["instance_count"] = count
            archetype["population_share"] = round(shares[idx], 3)
            allocations.append({
                "archetype_name": archetype.get("name", f"Archetype {idx + 1}"),
                "population_share": round(shares[idx], 3),
                "instance_count": count,
                "rationale": self._allocation_rationale(archetype, domain),
                "subtypes": archetype.get("subtypes") or self._default_subtypes(archetype.get("name", ""), domain),
            })

        return {
            "target_agent_count": target_count,
            "causal_agent_count": sum(item["instance_count"] for item in allocations),
            "allocation_basis": (
                "Agent count is inferred from scope, number of target variables, domain complexity, "
                "and whether mass public/consumer/voter behavior drives the outcome."
            ),
            "allocations": allocations,
            "orchestration_agents": deepcopy(ORCHESTRATION_AGENT_TEMPLATES),
            "research_agents": deepcopy(RESEARCH_AGENT_TEMPLATES),
        }

    def _infer_target_agent_count(self, user_question: str, domain: str, archetypes: List[Dict[str, Any]]) -> int:
        text = user_question.lower()
        complexity_terms = [
            "region", "scenario", "segment", "bloc", "state", "district", "country",
            "monthly", "weekly", "daily", "next", "years", "seats", "vote share",
            "turnout", "supply", "demand", "geopolitical", "policy", "consumer",
            "poll", "web scrape", "research", "numeric", "forecast",
        ]
        score = sum(1 for term in complexity_terms if term in text)
        base = 12 if domain in {"election", "geopolitics", "consumer", "social"} else 10
        target = base + min(16, score) + max(0, len(archetypes) - 5)
        if any(term in text for term in ["vast", "broad", "all possible", "multiple scenarios", "full context"]):
            target += 6
        return max(8, min(40, target))

    def _normalized_population_shares(
        self,
        archetypes: List[Dict[str, Any]],
        domain: str,
        user_question: str,
    ) -> List[float]:
        if not archetypes:
            return [1.0]
        explicit = [
            item.get("population_share")
            for item in archetypes
            if isinstance(item.get("population_share"), (int, float)) and item.get("population_share") > 0
        ]
        if len(explicit) == len(archetypes):
            total = sum(float(x) for x in explicit) or 1.0
            return [float(x) / total for x in explicit]

        weights = []
        text = user_question.lower()
        for item in archetypes:
            name = str(item.get("name", "")).lower()
            weight = 1.0
            if any(term in name for term in ["voter", "people", "public", "consumer", "household", "worker", "patient", "student"]):
                weight = 3.0 if domain in {"election", "consumer", "social", "healthcare", "education"} else 2.0
            if any(term in name for term in ["pollster", "analyst", "expert", "media"]):
                weight = max(weight, 1.2)
            if any(term in name for term in ["moderator", "auditor", "research", "synthesizer"]):
                weight = 0.8
            weights.append(weight)

        if any(term in text for term in ["people", "voter", "consumer", "public", "common people", "turnout"]):
            weights = [
                weight * 1.8 if any(term in str(item.get("name", "")).lower() for term in ["voter", "people", "public", "consumer", "household", "worker"]) else weight
                for weight, item in zip(weights, archetypes)
            ]
        total = sum(weights) or 1.0
        return [weight / total for weight in weights]

    def _default_subtypes(self, archetype_name: str, domain: str) -> List[str]:
        name = archetype_name.lower()
        if domain == "election" and any(term in name for term in ["voter", "people", "public"]):
            return ["rural voter", "urban voter", "minority voter", "youth voter", "women welfare voter", "regional swing voter"]
        if any(term in name for term in ["consumer", "public", "people"]):
            return ["price-sensitive participant", "loyal participant", "skeptical participant", "high-engagement participant"]
        if "analyst" in name or "pollster" in name:
            return ["model-based analyst", "ground-signal analyst", "skeptical analyst"]
        return []

    def _allocation_rationale(self, archetype: Dict[str, Any], domain: str) -> str:
        name = archetype.get("name", "Agent")
        share = archetype.get("population_share")
        if isinstance(share, (int, float)):
            return f"{name} receives {share:.0%} style representation because this role materially moves the {domain} outcome."
        return f"{name} receives representation based on causal relevance and information advantage."

    def _build_discussion_architecture(self, user_question: str) -> Dict[str, Any]:
        return {
            "moderated": True,
            "loop": [
                "moderator frames the pocket question",
                "research/data agents inject new permitted evidence",
                "causal agents update positions and numbers",
                "mediator summarizes disagreements and unresolved questions",
                "quantitative synthesizer produces scenario tables",
                "evidence auditor blocks unsupported claims or flags missing data",
            ],
            "anti_drift_rules": [
                "Every round must reference the target variables or state variables.",
                "Claims that require data must be routed to research/data agents.",
                "The moderator must restate unresolved disagreements before the next pocket.",
                "Report generation is blocked until numeric and evidence validation passes.",
            ],
        }

    def _build_external_research_policy(self, user_question: str) -> Dict[str, Any]:
        wants_research = bool(re.search(r"web\s*scrap|scrape|research|latest|news|article|source|poll", user_question, re.IGNORECASE))
        return {
            "enabled": wants_research,
            "outside_graph": True,
            "allowed_inputs": ["user_provided_urls", "uploaded_files", "approved_search_or_scraping_tool_results"],
            "injection_point": "before each debate pocket and before numeric synthesis",
            "requirements": [
                "Research pointers must include source, date, extracted claim, numeric values when present, and confidence/caveat.",
                "Debating agents must explicitly say whether each new pointer changes their forecast.",
                "External research does not overwrite graph memory unless explicitly saved as evidence.",
            ],
        }
