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


DOMAINS = {
    "macro",
    "election",
    "oil",
    "ai_future",
    "geopolitics",
    "market",
    "social",
    "business",
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
            ("Voter Blocs", "Shift turnout and vote choice.", "Local sentiment and issue salience.", "May be poorly sampled."),
            ("Campaign Strategists", "Allocate resources and shape messaging.", "Internal polling and field data.", "May overrate campaign impact."),
            ("Pollsters", "Measure public opinion.", "Survey data and weighting models.", "May miss turnout composition."),
            ("Media", "Frames race dynamics and scandals.", "Narrative reach and attention.", "May amplify short-term swings."),
            ("Donors", "Move resources and elite signals.", "Fundraising flows and elite confidence.", "May herd around momentum."),
            ("Opposition Campaign", "Creates counter-messaging and attacks.", "Opponent research and tactical plans.", "May misread voter priorities."),
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
  "domain": "macro | election | oil | ai_future | geopolitics | market | social | business | other",
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
      "numeric_output_required": true
    }}
  ],
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
                "numeric_output_required": True,
            }
            for name, causal_role, info, bias in template["agents"]
        ]
        return self._normalize_plan({
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
                    "numeric_output_required": True,
                }
                for name, causal_role, info, bias in template["agents"]
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
        keyword_map = [
            ("election", ["election", "vote", "turnout", "candidate", "campaign", "poll"]),
            ("oil", ["oil", "brent", "wti", "opec", "crude", "barrel", "inventory"]),
            ("ai_future", ["ai", "artificial intelligence", "frontier model", "model capability", "open-source", "enterprise adoption"]),
            ("macro", ["unemployment", "gdp", "inflation", "interest rate", "recession", "macro", "fed", "central bank"]),
            ("geopolitics", ["geopolitic", "war", "sanction", "diplomacy", "conflict", "country risk"]),
            ("market", ["stock", "bond", "market", "equity", "yield", "volatility", "commodity"]),
            ("social", ["social", "media narrative", "consumer trend", "public opinion", "culture"]),
            ("business", ["business", "strategy", "sales", "customer", "pricing", "competitor"]),
        ]
        for domain, keywords in keyword_map:
            if any(keyword in lowered for keyword in keywords):
                return domain
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
                "numeric_output_required": bool(item.get("numeric_output_required", True)),
            })
        return out or [{
            "name": "Generalist Analyst",
            "causal_role": "Interprets evidence and forecasts the target outcome.",
            "information_advantage": "Broad evidence synthesis.",
            "likely_bias": "May smooth uncertainty into consensus.",
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
