"""
Context-derived simulation planning.

Horizon XL should not carry hidden rosters for any specific domain. This
planner asks the LLM to infer the domain label, target variables, agent
archetypes, evidence needs, time pockets, and population allocation from the
prompt, uploaded context, and external research packet. If the LLM is
unavailable, the deterministic fallback is still generic and actor-derived.
"""

import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from copy import deepcopy
from typing import Any, Dict, List, Optional

from ..config import Config
from ..utils.llm_client import LLMClient
from ..utils.logger import get_logger

logger = get_logger("horizonxl.services.domain_simulation_planner")


class LLMUnavailableError(RuntimeError):
    """Raised when user-facing planning requires the LLM but it is unreachable."""


SCENARIO_FLAGS = {
    "base_case": True,
    "upside_case": True,
    "downside_case": True,
    "tail_case": True,
}


DEFAULT_SCENARIO_PATHS = [
    {
        "id": "base_case",
        "name": "Base case",
        "description": "Most likely path given the evidence currently available.",
        "required": True,
    },
    {
        "id": "upside_case",
        "name": "Upside case",
        "description": "Path where favorable drivers dominate relative to the base case.",
        "required": True,
    },
    {
        "id": "downside_case",
        "name": "Downside case",
        "description": "Path where adverse drivers dominate relative to the base case.",
        "required": True,
    },
    {
        "id": "tail_case",
        "name": "Tail risk case",
        "description": "Low-probability, high-impact path that stresses the assumptions.",
        "required": True,
    },
]


SCENARIOS = {
    **SCENARIO_FLAGS,
    "scenarios": DEFAULT_SCENARIO_PATHS,
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
        "role": "Keeps the debate on the user's question, frames each time pocket, enforces turn order, and stops drift.",
        "count": 1,
        "numeric_output_required": False,
    },
    {
        "name": "Negotiation Mediator",
        "role": "Identifies contested assumptions, bargaining positions, compromise paths, and unresolved disagreements.",
        "count": 1,
        "numeric_output_required": False,
    },
    {
        "name": "Evidence Auditor",
        "role": "Checks claims against uploaded context, graph evidence, external source pointers, dates, and future-leakage rules.",
        "count": 1,
        "numeric_output_required": False,
    },
    {
        "name": "Quantitative Synthesizer",
        "role": "Converts agent positions into numeric paths, confidence bands, disagreement ranges, and missing-data warnings.",
        "count": 1,
        "numeric_output_required": True,
    },
]


RESEARCH_AGENT_TEMPLATES = [
    {
        "name": "External Research Scout",
        "role": "Runs or consumes approved web/source discovery outside the graph and injects source pointers into debate pockets.",
        "count": 1,
        "external_to_graph": True,
        "numeric_output_required": False,
    },
    {
        "name": "Data Retrieval Analyst",
        "role": "Extracts numbers, dates, units, table fields, source caveats, and missing-data requirements from available context.",
        "count": 1,
        "external_to_graph": True,
        "numeric_output_required": True,
    },
]


ACTOR_HINT_WORDS = {
    "actor", "actors", "agent", "agents", "analyst", "analysts", "auditor", "auditors",
    "authority", "authorities", "buyer", "buyers", "campaign", "campaigns",
    "candidate", "candidates", "citizen", "citizens", "community", "communities", "company", "companies", "competitor", "competitors", "consumer", "consumers",
    "developer", "executive", "expert", "firm", "government", "group",
    "household", "institution", "investor", "journalist", "leader", "maker",
    "media", "mediator", "mediators", "ministry", "ministries", "observer", "observers", "official", "officials", "operator", "operators",
    "negotiator", "negotiators", "planner", "planners", "adviser", "advisers",
    "advisor", "advisors", "diplomat", "diplomats", "underwriter", "underwriters",
    "witness", "witnesses", "coordinator", "coordinators", "hardliner", "hardliners",
    "moderator", "moderators", "scout", "scouts", "synthesizer", "synthesizers",
    "integrator", "integrators", "quant", "quants",
    "organization", "organizations", "participant", "participants", "party", "parties", "people", "platform", "platforms",
    "pollster", "pollsters", "producer", "provider", "providers", "owner", "owners",
    "landlord", "landlords", "influencer", "influencers", "bank", "banks",
    "agency", "agencies", "association", "associations", "lab", "labs",
    "court", "courts", "council", "councils", "regulator", "regulators", "reporter", "reporters",
    "researcher", "researchers", "scientist", "scientists", "segment", "segments", "strategist", "strategists", "supplier", "suppliers", "trader", "traders",
    "miner", "miners", "refiner", "refiners", "manufacturer", "manufacturers",
    "automaker", "automakers", "logistics", "shipper", "shippers",
    "policy", "regulatory",
    "union", "unions", "user", "users", "voter", "voters", "watchdog", "watchdogs", "worker", "workers",
}


NON_ACTOR_ARTIFACT_TERMS = {
    "url", "urls", "source", "sources", "citation", "citations",
    "external research", "research packet", "agent background context",
    "external research packet", "discovery queries", "research paper literature review",
    "news analysis recent context", "only information available today",
    "information available today", "using only information available today",
    "latest evidence data numbers source",
    "background context", "uploaded context", "document text", "data table",
    "required output", "final prompt", "copy paste", "simulation input pack",
    "rules", "target variable", "target variables", "scenario paths",
    "forecast horizon", "validation requirement", "validation requirements",
    "agent architecture", "agent architecture for horizon xl", "agents",
    "horizon xl", "mirofish", "step", "required output table",
    "every agent", "force every agent", "force every agent to",
    "roles", "their roles", "incentives", "information advantages",
    "biases", "trusted data", "ignored factors", "confidence bands",
    "ranges", "causal assumptions", "expected vote seat ranges",
    "trigger events", "numeric forecast table", "the numeric forecast table",
    "probabilities", "post polling reflection", "repoll post polling reflection",
    "repoll", "polling reflection", "current state", "baseline",
    "agent disagreement", "then explain agent disagreement",
    "output numeric forecasts", "numeric forecasts first",
    "sir",
    "time pocket", "time pockets", "event triggered pocket", "baseline pocket",
    "response pocket", "shock pocket", "synthesis pocket", "scenario synthesis",
    "current state snapshot", "current-state snapshot", "historical baseline",
    "campaign phase", "voting phase", "seat synthesis", "social media virality phase",
    "social-media virality phase", "influencer spread phase", "generic public discourse phase",
    "mediator leverage", "agent debate", "debate format", "mechanism clash",
    "opening claims", "evidence checks", "cross questioning", "cross-questioning",
    "rebuttals", "concessions", "forecast revisions",
    "boom baseline", "inventory correction", "forecast table", "charts",
    "executive report", "whitepaper", "news article",
    "shortage", "shortages", "fear", "public fatigue", "medicine access",
    "banking payment difficulty", "family level stress", "rumor environment",
    "usd", "lfp", "kwh", "twh", "mwh", "gwh",
}

INSTRUCTION_ACTOR_PATTERNS = [
    r"\bmake\s+(?:a\s+)?concrete\s+claim\b",
    r"\bcite\s+.+\bevidence\b",
    r"\bchallenge\s+(?:another|other|an)\s+agent\b",
    r"\bexplain\s+what\s+would\s+change\s+(?:their|your|the)\s+view\b",
    r"\bseparate\s+fact\b.*\binference\b.*\bspeculation\b",
    r"\bavoid\s+.+\bleakage\b",
    r"\bagents?\s+must\s+not\s+just\s+announce\b",
    r"\bagents?\s+(?:must|should|need\s+to)\s+(?:make|cite|challenge|explain|separate|avoid)\b",
    r"\bfor\s+every\s+agent\b",
    r"\beach\s+agent\s+(?:must|should|needs?)\b",
]


NON_TARGET_ARTIFACT_TERMS = {
    "table", "historical_baseline_table", "region_wise_forecast_table",
    "agent_forecast_table", "scenario_comparison_table", "swing_sensitivity_matrix",
    "agent_disagreement_summary", "key_uncertainty_drivers", "missing_data_warnings",
    "probability_estimates", "scenario_probabilities", "confidence_bands",
    "required_output", "required_outputs", "data_tables", "final_horizon_xl_prompt",
    "majority_mark", "research_paper", "literature_review", "news_analysis",
    "numeric_outputs", "following_numeric_outputs", "the_following_numeric_outputs",
    "the_following_numeric_output", "forecast_the_following_numeric_outputs",
    "these_numeric_variables", "forecast_these_numeric_variables",
    "numeric_forecasts_first", "output_numeric_forecasts_first",
    "then_explain_agent_disagreement", "explain_agent_disagreement",
    "target_variable", "target_variables", "primary_outcome",
    "light_probability_bands_such_as", "probability_bands_such_as",
    "probability_bands_such_as_the",
    "the_following_probability_bands_such_as",
    "probability_bands", "fake_probabilities", "fake_probability",
    "fake_probabilities_probability",
    "table_by_month", "monthly_table", "forecast_table_by_month",
    "outlook", "supply_chain_outlook", "global_outlook",
}

GENERIC_METRIC_TERMS = [
    ("vote_share", "percent", r"\bvote\s+share\b"),
    ("seat_share", "percent", r"\bseat\s+share\b"),
    ("seats", "count", r"\bseats?\b"),
    ("turnout", "percent", r"\bturnout\b"),
    ("probability", "percent", r"\bprobabilit(?:y|ies)\b|\bchance\b|\bwin\s+probability\b"),
    ("adoption_rate", "percent", r"\badoption\s+rate\b"),
    ("growth", "percent", r"\bgrowth\b"),
    ("rate", "percent", r"\brate\b"),
    ("share", "percent", r"\bshare\b"),
    ("price", "currency_or_index", r"\bprices?\b|\bpricing\b"),
    ("cost", "currency_or_index", r"\bcosts?\b"),
    ("balance", "count_or_index", r"\bbalance\b"),
    ("deficit_surplus", "count_or_index", r"\bdeficit\b|\bsurplus\b|\bshortfall\b"),
    ("premium", "currency_or_index", r"\bpremium\b"),
    ("margin", "percent_or_currency", r"\bmargin\b"),
    ("spread", "percent_or_currency", r"\bspread\b"),
    ("inventory", "count_or_index", r"\binventor(?:y|ies)\b"),
    ("swing", "index", r"\bswings?\b"),
    ("split", "index", r"\bsplit\b|\bsplitting\b"),
    ("dynamics", "index", r"\bdynamics?\b"),
    ("band", "range", r"\bbands?\b|\branges?\b"),
    ("capex", "currency", r"\bcapex\b|\bcapital expenditure\b"),
    ("score", "index", r"\bscore\b"),
    ("index", "index", r"\bindex\b"),
    ("volume", "count", r"\bvolume\b"),
    ("count", "count", r"\bcount\b|\bnumber\b"),
]

PROCESS_ROLE_TERMS = (
    "moderator",
    "simulation moderator",
    "moderator actor",
    "negotiation mediator",
    "mediator actor",
    "evidence auditor",
    "research scout",
    "external research scout",
    "data retrieval analyst",
    "quantitative synthesizer",
    "quantitative synthesizer actor",
    "scenario synthesizer",
    "scenario integrator",
)

SOURCE_SENTENCE_FRAGMENT_TERMS = {
    "according", "reported", "expects", "expect", "expected", "might", "could",
    "would", "should", "needs", "need", "sticks", "increase", "decrease",
    "reduce", "expand", "expanded", "slowed", "deferred", "sought", "reached",
    "surged", "fell", "spent", "remained", "became", "released", "updated",
    "loading", "read", "click", "request", "sample", "discount", "offer",
}


def _snake(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", value or "").strip("_").lower()
    return cleaned or "target_variable"


def _role_key(value: str) -> str:
    """Normalize names for duplicate detection without erasing meaning."""
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def _context_label(text: str) -> str:
    cleaned = re.sub(r"https?://\S+", " ", text or "")
    cleaned = re.sub(r"[^a-zA-Z0-9 %/_-]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    for pattern in [
        r"(?:simulate|forecast|predict|analyze|model)\s+(.{8,120})",
        r"(?:question|prompt)\s*[:=-]\s*(.{8,120})",
    ]:
        match = re.search(pattern, cleaned, flags=re.IGNORECASE)
        if match:
            phrase = match.group(1)
            phrase = re.split(r"\b(?:using|based on|with|from|do not|produce|output)\b", phrase, maxsplit=1, flags=re.IGNORECASE)[0]
            return " ".join(phrase.split()[:8]).strip().lower() or "custom simulation"
    return " ".join(cleaned.split()[:8]).strip().lower() or "custom simulation"


def _infer_domain_label(text: str) -> str:
    """Return a concise, user-readable domain label.

    This is a broad taxonomy, not a domain roster. It keeps the UI from showing
    chopped prompt fragments such as "the global lithium outlook for the" while
    still letting the prompt/context decide the actual actors and targets.
    """
    lowered = (text or "").lower()
    narrative_signal = len(re.findall(
        r"\b(novel|book[- ]canon|canon|fiction|story|storyline|characters?|chapter|unpublished|"
        r"song\s+of\s+ice\s+and\s+fire|asoiaf|winds\s+of\s+winter|foreshadowing|prophecy|throne|thrones?)\b",
        lowered,
        flags=re.IGNORECASE,
    ))
    commodity_signal = len(re.findall(
        r"\b(oil|brent|wti|gas|lithium|copper|commodity|commodities|mining|mines?|smelters?|refiners?|supply chain|inventory|spodumene|battery-grade)\b",
        lowered,
        flags=re.IGNORECASE,
    ))
    infrastructure_signal = len(re.findall(
        r"\b(grid|power|electricity|data[- ]center|interconnection|utility|utilities|bottleneck|capacity|buildout)\b",
        lowered,
        flags=re.IGNORECASE,
    ))
    ai_signal = len(re.findall(
        r"\b(ai|artificial intelligence|frontier labs?|open[- ]source|model capability|enterprise adoption|compute|gpu|capex)\b",
        lowered,
        flags=re.IGNORECASE,
    ))
    geopolitical_signal = len(re.findall(
        r"\b(geopolitic|war|conflict|sanction|diplomacy|military|border|treaty|alliance|naval|maritime corridor|shipping-risk|shipping risk|rerouting|escalation)\b",
        lowered,
        flags=re.IGNORECASE,
    ))

    if narrative_signal:
        return "literary narrative simulation"

    # Mixed prompts are common: e.g. copper + data centers + AI buildout. Do
    # not let one AI phrase swallow a resource/commodity bottleneck question.
    if geopolitical_signal:
        return "geopolitical risk simulation"
    if commodity_signal and infrastructure_signal:
        return "commodity and infrastructure supply-chain forecasting"
    if commodity_signal >= 2 and commodity_signal >= ai_signal:
        return "commodity supply-chain forecasting"

    rules = [
        (r"\b(election|assembly election|vote share|seat share|turnout|polling|voters?|hung assembly|ballot)\b", "election forecasting"),
        (r"\b(gdp|inflation|unemployment|employment|recession|central bank|interest rate|macro(?:economic)?)\b", "macroeconomic forecasting"),
        (r"\b(ai|artificial intelligence|frontier labs?|open[- ]source|model capability|enterprise adoption|compute|gpu|capex)\b", "AI adoption and policy simulation"),
        (r"\b(geopolitic|war|conflict|sanction|diplomacy|military|border|treaty|alliance|naval|maritime corridor|shipping-risk|shipping risk|rerouting|escalation)\b", "geopolitical risk simulation"),
        (r"\b(rent[- ]control|rental housing|housing market|landlords?|tenants?|vacancy|tenant displacement|housing displacement|zoning|housing policy)\b", "housing policy simulation"),
        (r"\b(oil|brent|wti|gas|lithium|copper|commodity|commodities|mining|refiner|supply chain|inventory|spodumene|battery-grade)\b", "commodity supply-chain forecasting"),
        (r"\b(stock|equity|bond|credit|spread|yield|earnings|market shock|volatility|portfolio)\b", "market scenario simulation"),
        (r"\b(consumer|brand|demand trend|social media|narrative|creator|audience|sentiment)\b", "consumer and narrative simulation"),
        (r"\b(strategy|business model|pricing|launch|competitive|go[- ]to[- ]market|customer|churn|revenue)\b", "business strategy simulation"),
        (r"\b(policy|regulation|regulatory|law|public program|subsidy|tax)\b", "policy impact simulation"),
    ]
    for pattern, label in rules:
        if re.search(pattern, lowered, flags=re.IGNORECASE):
            return label
    return _context_label(text)


def _normalize_domain_label(value: Any, text: str) -> str:
    supplied = re.sub(r"\s+", " ", str(value or "")).strip(" .,:;-").lower()
    inferred = _infer_domain_label(text)
    # If the prompt itself contains a strong broad-domain signal, trust that
    # over an LLM-supplied label. This prevents a bad planner response from
    # calling a lithium or AI prompt "election forecasting" while still keeping
    # actors/targets fully context-derived.
    if inferred != _context_label(text):
        return inferred
    if not supplied:
        return inferred
    if supplied in {"custom simulation", "other", "simulation"}:
        return inferred
    if len(supplied.split()) > 7:
        return inferred
    if supplied.startswith(("the ", "a ", "an ")) and len(supplied.split()) > 3:
        return inferred
    if re.search(r"\b(outlook|using only|for the next|based on|with help)\b", supplied):
        return inferred
    return supplied


def _clean_horizon_bound(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(value or "")).strip(" .,:;-")
    cleaned = re.split(
        r"\b(?:for|forecast(?:ing)?|predict(?:ing)?|estimate(?:ing)?|including|with|using|based on)\b",
        cleaned,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .,:;-")
    return cleaned or "auto"


def _looks_like_horizon_bound(value: str) -> bool:
    """Prevent prose such as 'between fear and loyalty' becoming a date range."""
    text = re.sub(r"\s+", " ", str(value or "").strip(" .,:;-"))
    lowered = text.lower()
    if not text:
        return False
    if re.search(r"\bper\s+(?:day|week|month|year)\b", lowered):
        return False
    if re.search(r"\b(?:vessels?|traffic|barrels?|tons?|tonnes?|price|cost|premium|fewer than|more than|roughly|approximately|before the crisis|during the disruption)\b", lowered):
        return False
    if re.search(
        r"\b(?:19|20)\d{2}\b|\bq[1-4]\b|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\b|"
        r"\b(?:today|current|cutoff|future|phase|pocket|book|chapter|volume|season|episode)\b",
        lowered,
        flags=re.IGNORECASE,
    ):
        return True
    if re.search(r"\b(?:next|coming|following)\s+\d{1,3}\s+(?:days?|weeks?|months?|quarters?|years?)\b", lowered):
        return True
    if re.match(r"^\d{1,3}\s+(?:days?|weeks?|months?|quarters?|years?)$", lowered):
        return True
    return False


def _month_number(value: str) -> int:
    lookup = {
        "jan": 1, "january": 1,
        "feb": 2, "february": 2,
        "mar": 3, "march": 3,
        "apr": 4, "april": 4,
        "may": 5,
        "jun": 6, "june": 6,
        "jul": 7, "july": 7,
        "aug": 8, "august": 8,
        "sep": 9, "sept": 9, "september": 9,
        "oct": 10, "october": 10,
        "nov": 11, "november": 11,
        "dec": 12, "december": 12,
    }
    return lookup.get(str(value or "").lower()[:3], 1)


def _month_label(year: int, month: int) -> str:
    labels = ["", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
    return f"{labels[max(1, min(12, month))]} {year}"


def _add_months(year: int, month: int, delta: int) -> tuple[int, int]:
    absolute = (year * 12 + month - 1) + delta
    return absolute // 12, absolute % 12 + 1


def _parse_month_year(value: str) -> tuple[int, int] | None:
    text = str(value or "")
    month_pattern = (
        r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
        r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    )
    match = re.search(month_pattern + r"\s+\d{1,2},?\s*(20\d{2}|19\d{2})", text, flags=re.IGNORECASE)
    if match:
        return int(match.group(2)), _month_number(match.group(1))
    match = re.search(month_pattern + r"\s+(20\d{2}|19\d{2})", text, flags=re.IGNORECASE)
    if match:
        return int(match.group(2)), _month_number(match.group(1))
    match = re.search(r"\b(20\d{2}|19\d{2})[-/](\d{1,2})\b", text)
    if match:
        return int(match.group(1)), max(1, min(12, int(match.group(2))))
    match = re.search(r"\b(20\d{2}|19\d{2})\b", text)
    if match:
        return int(match.group(1)), 1
    return None


def _split_items(text: str) -> List[str]:
    pieces = re.split(r",|;|\n|(?<!\d)\.(?!\d)|\band\b", text or "", flags=re.IGNORECASE)
    out = []
    for piece in pieces:
        cleaned = re.sub(r"^[\s\-*•\d.)]+", "", piece)
        cleaned = re.sub(
            r"^(?:the\s+)?(?:simulation|model|prompt)\s+(?:should|must|can)\s+(?:consider|include|use|involve)\s+",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(
            r"^(?:should|must|can)\s+(?:consider|include|use|involve)\s+",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(
            r"^(?:create|generate|use|define)\s+(?:dynamic\s+)?(?:agents?|actors?)\s+(?:such\s+as|including|with|as)\s+",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(r"^(?:include|includes|including|considering|with)\s+", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.split(r"\b(?:only if|if supported|when supported)\b", cleaned, maxsplit=1, flags=re.IGNORECASE)[0]
        if ":" in cleaned or "=" in cleaned:
            parts = re.split(r"[:=]", cleaned, maxsplit=1)
            left = parts[0].strip().lower()
            right = parts[1].strip() if len(parts) > 1 else ""
            cleaned = right if left in {"agent", "agents", "agent architecture", "target variables"} and right else parts[0]
        cleaned = re.sub(r"[^a-zA-Z0-9 %/_.'-]+", " ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" -_/")
        if 3 <= len(cleaned) <= 80:
            out.append(cleaned)
    return out


def _strip_metric_modifiers(value: str) -> str:
    """Remove scenario/horizon wording while keeping the actual metric noun."""
    cleaned = re.sub(r"\s+", " ", value or "").strip(" .,:;-")
    cleaned = re.sub(
        r"\b(?:daily|weekly|monthly|quarterly|yearly|annual|base|upside|downside|tail|case|path|paths)\b",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .,:;-")
    return cleaned or value


def _extract_numbered_items_after_heading(
    text: str,
    heading_pattern: str,
    stop_pattern: str,
    limit: int = 80,
) -> List[str]:
    """Extract explicitly enumerated prompt items without treating prose as actors."""
    source = text or ""
    match = re.search(heading_pattern, source, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return []
    start = match.end()
    tail = source[start:]
    stop = re.search(stop_pattern, tail, flags=re.IGNORECASE | re.DOTALL)
    block = tail[:stop.start()] if stop else tail
    negative = re.search(
        r"\n\s*(?:do\s+not\s+use|do\s+not\s+include|avoid|forbidden|must\s+not\s+use)\b\s*[:\n]",
        block,
        flags=re.IGNORECASE,
    )
    if negative:
        block = block[:negative.start()]
    hard_stop = re.search(
        r"\n\s*(?:=+\s*)?(?:AGENT BEHAVIOR RULES|DEBATE FORMAT|VALIDATION REQUIREMENTS|REQUIRED REPORT FORMAT|STYLE REQUIREMENTS|SCENARIOS TO MODEL|SCENARIO PATHS|REQUIRED AGENTS|REQUIRED TIME[- ]?POCKETS?)\b",
        block,
        flags=re.IGNORECASE,
    )
    if hard_stop:
        block = block[:hard_stop.start()]
    items: List[str] = []
    for line in block.splitlines():
        cleaned = line.strip()
        numbered = re.match(r"^(?:[-*•]|(?:pocket\s*)?\d{1,3}[:.)])\s*(.+?)\s*$", cleaned, flags=re.IGNORECASE)
        if not numbered:
            continue
        value = re.sub(r"\s+", " ", numbered.group(1)).strip(" .,:;-")
        if 2 <= len(value) <= 240:
            items.append(value)
        if len(items) >= limit:
            break
    return items


def _extract_inline_output_items(text: str) -> List[str]:
    """Extract comma-list outputs from prose like 'Produce price, balance, growth...'."""
    items: List[str] = []
    source = re.sub(r"\s+", " ", (text or "").replace("U.S.", "US").replace("U.K.", "UK"))
    for match in re.finditer(
        r"\b(?:forecast|predict|estimate|simulate|produce|output|return|generate|include)\s+([^.\n;]+)",
        source,
        flags=re.IGNORECASE,
    ):
        fragment = match.group(1)
        fragment = re.split(
            r"\b(?:with|using|based on|from|by|before|after|then|and then|for every agent|do not|rules?)\b",
            fragment,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0]
        expanded_probability_items = _expand_probability_list_phrases(fragment)
        if expanded_probability_items:
            items.extend(expanded_probability_items)
        for item in _split_items(fragment):
            cleaned = _strip_metric_modifiers(item)
            if re.search(r"^probabilit(?:y|ies)\s+(?:for|of)\b", cleaned, flags=re.IGNORECASE):
                continue
            if (
                cleaned
                and len(cleaned.split()) <= 14
                and any(re.search(pattern, cleaned, flags=re.IGNORECASE) for _, _, pattern in GENERIC_METRIC_TERMS)
                and not _is_non_target_artifact(cleaned)
            ):
                items.append(cleaned)
    return items


def _expand_shared_metric_phrases(section: str) -> List[str]:
    """Recover phrases like 'women and minority turnout' before comma splitting."""
    expanded: List[str] = []
    metric_words = [
        "turnout", "vote share", "seat share", "share", "rate", "probability",
        "index", "swing", "split", "price", "growth", "balance", "premium",
        "margin", "spread", "score",
    ]
    for metric in metric_words:
        pattern = rf"\b([A-Za-z][A-Za-z0-9 /-]{{1,50}}?)\s+(?:and|&)\s+([A-Za-z][A-Za-z0-9 /-]{{1,50}}?)\s+{re.escape(metric)}\b"
        for match in re.finditer(pattern, section or "", flags=re.IGNORECASE):
            left = _strip_metric_modifiers(match.group(1))
            right = _strip_metric_modifiers(match.group(2))
            for side in [left, right]:
                side = re.sub(r"\b(?:overall|statewide|regional)\b", " ", side, flags=re.IGNORECASE)
                side = re.sub(r"\s+", " ", side).strip(" .,:;-")
                if side:
                    expanded.append(f"{side} {metric}")
    return expanded


def _is_weak_target_fragment(raw: str) -> bool:
    lowered = (raw or "").strip().lower()
    if not lowered:
        return True
    if any(re.search(pattern, lowered, flags=re.IGNORECASE) for _, _, pattern in GENERIC_METRIC_TERMS):
        return False
    if re.fullmatch(r"[A-Z]{2,8}", raw.strip()):
        return False
    if lowered in {"gdp", "inflation", "unemployment", "employment", "turnover", "revenue", "sales", "capex"}:
        return False
    return True


def _dedupe_shadowed_targets(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    names = {str(item.get("name") or "") for item in items}
    has_specific_price = any(name.endswith("_price") or name.endswith("_prices") for name in names if name != "price")
    has_inventory_balance = "inventory_balance" in names
    out = []
    seen = set()
    for item in items:
        name = str(item.get("name") or "")
        if not name or name in seen:
            continue
        if name == "price" and has_specific_price:
            continue
        if name in {"statewide_balance", "statewide_inventory", "inventory"} and has_inventory_balance:
            continue
        seen.add(name)
        out.append(item)
    return out


def _extract_explicit_agent_items(text: str) -> List[str]:
    items = _extract_numbered_items_after_heading(
        text,
        r"(?:^|\n)\s*(?:required\s+agents?|(?:create|generate|use|define)\s+(?:meaningful\s+)?(?:\d{1,3}\s+)?(?:[a-z ]+\s+)?agents?)\s*(?:as previously defined)?\s*[:\n]",
        r"(?:\n\s*(?:Agents?\s+(?:must|should)|Each agent|For every agent|Agent behavior rules?|Target Variables|Forecast these|Run the simulation|Required Time[- ]?Pockets?|Time[- ]?Pocket|Scenario Paths|Scenarios?\s+to\s+model|Run four scenarios|Debate Format|Validation Requirements|Required Report Format|Style Requirements|Required output|Rules)\b|\n\s*\d+\.\s*(?:Target Variables|Required Time[- ]?Pockets?|Time[- ]?Pocket|Scenario Paths|Scenarios?\s+to\s+model|Required output|Rules)\b)",
        limit=80,
    )
    source = text or ""
    # Only treat explicit headings as agent lists. A prior looser regex matched
    # ordinary sentences like "do not use influencer agents..." and polluted the
    # actor set with prompt instructions.
    heading_list_pattern = (
        r"(?:^|\n)\s*(?:#+\s*)?(?:\d{1,2}[.)]\s*)?"
        r"(?:agent architecture|agents(?:\s+to\s+create)?|required agents?)"
        r"(?:\s+for\s+horizon\s+xl)?\s*(?:[:—-]|\n)\s*"
        r"(.+?)(?=\n\s*(?:=+\s*)?"
        r"(?:target variables?|forecast these|required target|time[- ]?pockets?|"
        r"scenario paths?|scenarios?\s+to\s+model|required output|rules|"
        r"agent behavior rules?|debate format|validation requirements?|"
        r"required report format|style requirements)\b|"
        r"\n\s*\d{1,2}[.)]\s*(?:target|scenario|time|required output|rules)\b|$)"
    )
    include_line_pattern = (
        r"(?:^|\n)\s*(?:[-*•]\s*)?"
        r"(?:agents?\s+include|required agents?\s+include)\s*[:—-]?\s*"
        r"(.+?)(?=\n\s*\n|\n\s*(?:target|scenario|time|required|rules|agent behavior|debate)\b|$)"
    )
    for match in list(re.finditer(heading_list_pattern, source, flags=re.IGNORECASE | re.DOTALL)) + list(
        re.finditer(include_line_pattern, source, flags=re.IGNORECASE | re.DOTALL)
    ):
        block = re.sub(r"(?m)^\s*=+\s*$", " ", match.group(1))
        block = re.sub(r"\([^)]*\)", " ", block)
        numbered_lines = []
        for line in block.splitlines():
            numbered = re.match(r"^\s*(?:[-*•]|(?:pocket\s*)?\d{1,3}[:.)])\s*(.+?)\s*$", line, flags=re.IGNORECASE)
            if numbered:
                numbered_lines.append(numbered.group(1).strip(" .,:;-"))
        candidate_items = numbered_lines if numbered_lines else _split_items(block)
        for item in candidate_items:
            cleaned = re.sub(r"\bas previously defined\b", " ", item, flags=re.IGNORECASE)
            cleaned = re.sub(r"\s+", " ", cleaned).strip(" .,:;-")
            if cleaned:
                items.append(cleaned)

    for match in re.finditer(
        r"(?:simulation|model|prompt)\s+(?:should|must|can)\s+(?:consider|include|use|involve)\s+([^.\n;]+)",
        source,
        flags=re.IGNORECASE,
    ):
        for item in _split_items(match.group(1)):
            if item:
                items.append(item)

    for match in re.finditer(
        r"(?:at\s+minimum\s+)?(?:consider|include|involve)\s+([^.\n;]+?)\s+(?:as\s+agents?|as\s+actors?|as\s+participants?)",
        source,
        flags=re.IGNORECASE,
    ):
        for item in _split_items(match.group(1)):
            if item:
                items.append(item)

    merged = []
    seen = set()
    for item in items:
        if _is_non_actor_artifact(item) or _is_placeholder_actor(item):
            continue
        key = item.lower()
        if key not in seen:
            merged.append(item)
            seen.add(key)
    return merged


def _extract_explicit_target_items(text: str) -> List[str]:
    bullet_targets = []
    collecting = False
    for line in (text or "").splitlines():
        if re.search(
            r"(?:core\s+forecast\s+targets?|forecast\s+targets?|the simulation must forecast|must forecast|forecast(?:\s+the)?\s+following\s+numeric\s+outputs?|forecast\s+these\s+numeric\s+variables|required\s+numeric\s+variables|target\s+variables?(?:\s+that\s+must\s+be\s+numeric)?|target\s+variables?[^:\n]{0,80})\s*:\s*$",
            line,
            flags=re.IGNORECASE,
        ):
            collecting = True
            continue
        if not collecting:
            continue
        match = re.match(r"^\s*[-*•]\s*(.+?)\s*$", line)
        if match:
            bullet_targets.append(match.group(1).strip(" .,:;-"))
            continue
        if bullet_targets and re.match(r"^\s*(?:[A-Z][A-Za-z /-]{2,60}|[A-Z0-9][A-Za-z0-9 /-]{2,60})\s*:\s*$", line.strip()):
            break
        if bullet_targets and line.strip():
            break

    items = _extract_numbered_items_after_heading(
        text,
        r"(?:core\s+forecast\s+targets?|forecast\s+targets?|forecast(?:\s+the)?\s+following\s+numeric\s+outputs?|forecast these numeric variables|target variables?(?:\s+that\s+must\s+be\s+numeric)?|target variables?[^:\n]{0,80}|required numeric variables)\s*[:\n]",
        r"(?:\n\s*(?:Historical baseline|Current political setup|Regional structure|Key forces|Scenarios?\s+to\s+model|Scenario Paths|Required Agents?|Create\s+(?:dynamic\s+)?(?:\d{1,3}\s+)?.*agents?|For every agent|Agent behavior rules?|Run the simulation|Required Time[- ]?Pockets?|Time[- ]?Pocket|Debate Format|Validation Requirements|Required Report Format|Style Requirements|Run four scenarios|Required output|Rules)\b|\n\s*\d+\.\s*(?:Historical|Current|Agent Architecture|Scenarios?\s+to\s+model|Required Agents?|Required Time[- ]?Pockets?|Time[- ]?Pocket|Scenario Paths|Required output|Rules)\b)",
        limit=100,
    )
    merged = []
    seen = set()
    inline_items = _extract_inline_output_items(text)

    for item in bullet_targets + items + inline_items:
        key = item.lower()
        if _is_non_target_artifact(item):
            continue
        if key not in seen:
            merged.append(item)
            seen.add(key)
    return merged


def _extract_time_pocket_items(text: str) -> List[str]:
    return _extract_numbered_items_after_heading(
        text,
        r"(?:required\s+time[- ]?pockets?|run the simulation in sequential time pockets|time[- ]?pocket simulation plan|time[- ]?pocket simulation|simulate sequentially|time[- ]?pockets?)\s*[:\n]",
        r"(?:\n\s*(?:Do not use|Do not include|Avoid|Run four scenarios|Scenario Paths|Debate Format|Validation Requirements|Required Report Format|Required output|Rules)\b|\n\s*\d+\.\s*(?:Scenario Paths|Debate Format|Validation Requirements|Required output|Rules)\b|$)",
        limit=80,
    )


def _extract_scenario_items(text: str) -> List[Dict[str, Any]]:
    raw_items = _extract_numbered_items_after_heading(
        text,
        r"(?:run four scenarios|scenario paths?|scenario structure|run scenarios)\s*[:\n]",
        r"(?:\n\s*(?:Required output|Rules|Final Horizon|Data Tables)\b|\n\s*\d+\.\s*(?:Required output|Rules|Final Horizon|Data Tables)\b|$)",
        limit=40,
    )
    scenarios: List[Dict[str, Any]] = []
    seen = set()
    for idx, item in enumerate(raw_items, start=1):
        name, description = item, item
        if ":" in item:
            left, right = item.split(":", 1)
            name = left.strip(" .:-")
            description = right.strip(" .:-") or item
        scenario_id = _snake(name)
        if not scenario_id or scenario_id in seen:
            scenario_id = f"scenario_{idx:02d}"
        seen.add(scenario_id)
        scenarios.append({
            "id": scenario_id,
            "name": name,
            "description": description,
            "required": True,
        })
    return scenarios


def _looks_like_llm_connectivity_issue(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(
        marker in message
        for marker in [
            "connection error",
            "could not resolve host",
            "nodename nor servname",
            "name or service not known",
            "temporary failure in name resolution",
            "timed out",
            "network is unreachable",
        ]
    )


def _extract_target_variables(text: str) -> List[Dict[str, Any]]:
    source_text = (text or "").replace("U.S.", "US").replace("U.K.", "UK")
    # Output checklists often contain words like "probability estimates" or
    # "scenario comparison table". Those are report requirements, not state
    # variables. Keep target extraction focused on the prompt before the output
    # checklist unless an explicit target section already captured the metrics.
    target_source_text = re.split(
        r"\n\s*(?:Required output|Required outputs|Final output|Output requirements|Rules)\s*:",
        source_text,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    normalized_source_text = re.sub(r"\s+", " ", target_source_text)
    sections = []
    explicit_targets = _extract_explicit_target_items(source_text)
    target_section = re.search(
        r"target variables?\s*(?:[:=-]|\n)(.*?)(?:agent|scenario|time[- ]?pocket|final|$)",
        target_source_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if target_section:
        sections.append(target_section.group(1))
    for match in re.finditer(r"(?:forecast|predict|estimate|simulate|produce|output|return|generate)\s+([^.;]+)", normalized_source_text, flags=re.IGNORECASE):
        sections.append(match.group(1))
    items = []
    seen = set()
    for raw in explicit_targets:
        for target in _expand_target_item(raw):
            name = target["name"]
            if name in seen or name in {"the", "future", "scenario"} or _is_non_target_artifact(name):
                continue
            seen.add(name)
            items.append(target)

    max_targets = 40 if len(explicit_targets) > 12 else 18
    # If the user supplied an explicit target list, trust it. Otherwise fall
    # back to outcome phrases from the broader prompt.
    if not explicit_targets:
        for section in sections:
            cleaned_section = _clean_target_section(section)
            section_items = (
                _expand_probability_list_phrases(cleaned_section)
                + _expand_shared_metric_phrases(cleaned_section)
                + _split_items(cleaned_section)
            )
            for raw in section_items:
                lowered = raw.lower()
                if any(stop in lowered for stop in ["using only", "information available", "based on", "different agents"]):
                    continue
                if len(raw.split()) > 14:
                    continue
                if _is_weak_target_fragment(raw):
                    continue
                for target in _expand_target_item(raw):
                    name = target["name"]
                    if name in seen or name in {"the", "future", "scenario"} or _is_non_target_artifact(name):
                        continue
                    seen.add(name)
                    items.append(target)
                    if len(items) >= max_targets:
                        break
                if len(items) >= max_targets:
                    break
    items = _dedupe_shadowed_targets(_repair_compact_election_targets(items, source_text))
    fallback_targets = _domain_target_fallbacks(source_text)
    return items or fallback_targets or [{
        "name": "primary_outcome",
        "unit": "index",
        "required": True,
        "description": "Primary simulated outcome requested by the user.",
    }]


def _expand_probability_list_phrases(text: str) -> List[str]:
    """Expand generic phrases like "probabilities for A, B, and C".

    This is deliberately domain-agnostic. It only recognizes a metric family
    followed by a comma/and list, then turns each listed outcome into an
    independent metric target.
    """
    source = re.sub(r"\s+", " ", text or "").strip()
    if not source:
        return []
    results: List[str] = []
    patterns = [
        r"\bprobabilit(?:y|ies)\s+(?:for|of)\s+(.+?)(?:\s+\b(?:with|using|based on|from|over|during|under)\b|$)",
        r"\b(?:chance|risk|odds)\s+(?:of|for)\s+(.+?)(?:\s+\b(?:with|using|based on|from|over|during|under)\b|$)",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, source, flags=re.IGNORECASE):
            tail = match.group(1).strip(" .,:;-")
            tail = re.sub(r"\b(?:and\s+)?what\s+would\s+change\s+the\s+forecast\b.*$", "", tail, flags=re.IGNORECASE)
            parts = _split_items(tail)
            # `_split_items` may return the original phrase if it sees no
            # delimiter. Keep only phrases that clearly came from a list.
            if len(parts) < 2:
                continue
            for part in parts:
                cleaned = part.strip(" .,:;-")
                cleaned = re.sub(r"^probabilit(?:y|ies)\s+(?:of|for)\s+", "", cleaned, flags=re.IGNORECASE).strip(" .,:;-")
                if not cleaned:
                    continue
                if re.search(r"\b(evidence|assumptions?|disputes?|revisions?|report|forecast)\b", cleaned, flags=re.IGNORECASE):
                    continue
                results.append(f"{cleaned} probability")
    return results


def _repair_compact_election_targets(items: List[Dict[str, Any]], text: str) -> List[Dict[str, Any]]:
    """Make compact election phrases like "Party vote share and seats" atomic."""
    if not re.search(r"\b(election|assembly election|vote share|seat share|seats?|turnout|hung assembly)\b", text or "", re.IGNORECASE):
        return items

    repaired: List[Dict[str, Any]] = []
    seen = set()

    def add(item: Dict[str, Any]) -> None:
        name = item.get("name")
        if not name or name in seen or _is_non_target_artifact(name):
            return
        seen.add(name)
        repaired.append(item)

    for item in items:
        if item.get("name") != "seats":
            add(item)

    party_labels = []
    for item in repaired:
        name = item.get("name", "")
        if name.endswith("_vote_share"):
            party_labels.append(name[: -len("_vote_share")])
    for match in re.finditer(r"\b([A-Z][A-Z/&-]{1,18})\s+(?:vote\s+share|seats?)\b", text or ""):
        label = _snake(match.group(1))
        if label and label not in party_labels:
            party_labels.append(label)

    if any(item.get("name") == "seats" for item in items):
        for label in party_labels[:8]:
            add({
                "name": f"{label}_seats",
                "unit": "count",
                "required": True,
                "description": f"Prompt-derived election target for {label}: seats.",
            })

    return repaired


def _domain_target_fallbacks(text: str) -> List[Dict[str, Any]]:
    """Derive structured targets from domain language without hardcoding a case.

    This is intentionally pattern-based. For an election prompt, for example,
    party labels are read from the prompt itself, then converted into vote/seat
    outputs. The code does not know any fixed place, party, or election.
    """
    lowered = (text or "").lower()
    targets: List[Dict[str, Any]] = []
    seen = set()

    def add(name: str, unit: str, description: str) -> None:
        key = _snake(name)
        if not key or key in seen or _is_non_target_artifact(key):
            return
        seen.add(key)
        targets.append({
            "name": key,
            "unit": unit,
            "required": True,
            "description": description,
        })

    if re.search(r"\b(election|assembly election|vote share|seat share|seats?|turnout|hung assembly)\b", lowered):
        labels = []
        for match in re.finditer(r"\b[A-Z][A-Z/&-]{1,18}\b", text or ""):
            label = match.group(0).strip("/&-")
            if label in {"US", "UK", "API", "LLM", "JSON", "CSV"}:
                continue
            if label not in labels:
                labels.append(label)
        for match in re.finditer(r"\b([A-Z][a-z]+(?:/[A-Z][a-z]+)+)\b", text or ""):
            label = match.group(1)
            if label not in labels:
                labels.append(label)

        for label in labels[:6]:
            clean = _snake(label)
            add(f"{clean}_vote_share", "percent", f"Prompt-derived election target for {label}: vote share.")
            add(f"{clean}_seats", "count", f"Prompt-derived election target for {label}: seats.")

        add("overall_turnout", "percent", "Prompt-derived election target: overall turnout.")
        add("women_turnout", "percent", "Prompt-derived election target: women turnout, if relevant evidence exists.")
        add("minority_turnout", "percent", "Prompt-derived election target: minority/community turnout, if relevant evidence exists.")
        add("regional_swing_index", "index", "Prompt-derived election target: regional swing pressure.")
        add("opposition_vote_split_index", "index", "Prompt-derived election target: opposition vote splitting pressure.")
        for label in labels[:3]:
            add(f"{_snake(label)}_majority_probability", "percent", f"Prompt-derived election target for {label}: majority probability.")
        add("hung_assembly_probability", "percent", "Prompt-derived election target: no-clear-majority probability.")

    if re.search(
        r"\b(novel|book[- ]canon|canon|fiction|story|characters?|die|death|survive|survival|betray|betrayal|"
        r"alliances?|reveals?|secret|battle|throne|power|prophecy|foreshadowing)\b",
        lowered,
    ):
        add("character_death_probability", "percent", "Prompt-derived narrative target: probability a character dies.")
        add("character_survival_probability", "percent", "Prompt-derived narrative target: probability a character survives.")
        add("betrayal_probability", "percent", "Prompt-derived narrative target: probability of betrayal or role reversal.")
        add("alliance_shift_probability", "percent", "Prompt-derived narrative target: probability of alliance change.")
        add("battle_outcome_probability", "percent", "Prompt-derived narrative target: probability of battle outcome paths.")
        add("power_gain_probability", "percent", "Prompt-derived narrative target: probability an actor gains power.")
        add("power_loss_probability", "percent", "Prompt-derived narrative target: probability an actor loses power.")
        add("identity_reveal_probability", "percent", "Prompt-derived narrative target: probability of identity or secret reveal.")
        add("throne_claim_probability", "percent", "Prompt-derived narrative target: probability of throne or legitimacy claim success.")
        add("magic_revelation_probability", "percent", "Prompt-derived narrative target: probability of magic or prophecy revelation.")

    return targets


def _expand_target_item(raw: str) -> List[Dict[str, Any]]:
    """Split compact prompt targets into atomic numeric variables.

    This stays domain-generic: it looks for reusable output concepts such as
    share, seats, turnout, probability, index, and count rather than hardcoded
    parties, countries, sectors, or topics.
    """
    text = _strip_metric_modifiers(re.sub(r"\s+", " ", raw or "").strip(" .,:;-"))
    lowered = text.lower()
    if not text:
        return []

    target_terms = GENERIC_METRIC_TERMS
    matches = [(suffix, unit) for suffix, unit, pattern in target_terms if re.search(pattern, lowered)]
    match_suffixes = {suffix for suffix, _ in matches}
    if "vote_share" in match_suffixes or "seat_share" in match_suffixes:
        matches = [(suffix, unit) for suffix, unit in matches if suffix != "share"]

    if "probability" in match_suffixes and re.search(r"\bprobabilit(?:y|ies)\b", lowered):
        probability_phrase = re.sub(r"\b(?:of|that|whether)\b", " ", text, flags=re.IGNORECASE)
        probability_phrase = re.sub(r"\b(?:percent|percentage|pct)\b", " ", probability_phrase, flags=re.IGNORECASE)
        probability_phrase = re.sub(r"\s+", " ", probability_phrase).strip(" .,:;-")
        name = _snake(probability_phrase)
        return [{
            "name": name if "probability" in name else f"{name}_probability",
            "unit": "percent",
            "required": True,
            "description": f"Prompt-required probability target from '{text}'.",
        }]

    no_connector = not re.search(r"\s+(?:and|&|/)\s+", text, flags=re.IGNORECASE)
    compound_metric_phrase = bool(re.search(
        r"\b(?:in|per)\b|\bgrowth\s+rate\b|\bdemand\s+growth\s+rate\b|\bsupply\s+growth\s+rate\b|"
        r"\bmargin\s+pressure\s+index\b|\bpressure\s+index\b|\binventory\s+cover\b",
        lowered,
        flags=re.IGNORECASE,
    ))
    if len(matches) >= 2 and no_connector and compound_metric_phrase:
        return [{
            "name": _snake(text),
            "unit": _infer_unit(lowered),
            "required": True,
            "description": f"Prompt-required target variable: {text}.",
        }]

    if len(matches) >= 2 and not re.search(r"\s+(?:and|&|/)\s+", text, flags=re.IGNORECASE) and len(text.split()) <= 4:
        return [{
            "name": _snake(text),
            "unit": _infer_unit(lowered),
            "required": True,
            "description": f"Prompt-required target variable: {text}.",
        }]

    connector_split = re.split(r"\s+(?:and|&|/)\s+", text, flags=re.IGNORECASE)
    if len(connector_split) > 1 and any(re.search(r"\bturnout\b|\bindex\b", part, flags=re.IGNORECASE) for part in connector_split):
        expanded = []
        for part in connector_split:
            part_clean = part.strip(" .,:;-")
            if not part_clean:
                continue
            if not any(re.search(pattern, part_clean, flags=re.IGNORECASE) for _, _, pattern in target_terms):
                inherited = "turnout" if "turnout" in lowered else ("index" if "index" in lowered else "")
                part_clean = f"{part_clean} {inherited}".strip()
            expanded.extend(_expand_target_item(part_clean))
        return expanded

    if "probability" in match_suffixes and re.search(r"\b(?:crosses|cross|over|above|below|under|exceeds?|majority|wins?|hung)\b", lowered):
        probability_phrase = re.sub(r"\b(?:of|that|whether)\b", " ", text, flags=re.IGNORECASE)
        probability_phrase = re.sub(r"\bseats?\b", " ", probability_phrase, flags=re.IGNORECASE)
        name = _snake(probability_phrase)
        return [{
            "name": name if "probability" in name else f"{name}_probability",
            "unit": "percent",
            "required": True,
            "description": f"Prompt-required probability target from '{text}'.",
        }]

    if len(matches) >= 2:
        actor_phrase = text
        for suffix, _, pattern in target_terms:
            actor_phrase = re.sub(pattern, " ", actor_phrase, flags=re.IGNORECASE)
        actor_phrase = re.sub(r"\b(?:and|by|of|for|overall|statewide|regional)\b", " ", actor_phrase, flags=re.IGNORECASE)
        actor_phrase = re.sub(r"\s+", " ", actor_phrase).strip(" .,:;-")
        expanded = []
        for suffix, unit in matches:
            prefix = _snake(actor_phrase) if actor_phrase else "statewide"
            name = f"{prefix}_{suffix}" if prefix and suffix not in prefix else prefix or suffix
            expanded.append({
                "name": _snake(name),
                "unit": unit,
                "required": True,
                "description": f"Prompt-required atomic target from '{text}': {suffix.replace('_', ' ')}.",
            })
        return expanded

    name = _snake(text)
    return [{
        "name": name,
        "unit": _infer_unit(lowered),
        "required": True,
        "description": f"Prompt-required target variable: {text}.",
    }]


def _clean_target_section(section: str) -> str:
    """Keep requested outcomes separate from drivers, evidence, and horizon text."""
    cleaned = (section or "").replace("U.S.", "US").replace("U.K.", "UK")
    nested = list(re.finditer(r"(?:forecast|predict|estimate|simulate)\s+(.+)$", cleaned, flags=re.IGNORECASE))
    if nested:
        cleaned = nested[-1].group(1)
    cleaned = re.sub(
        r"^(?:daily|weekly|monthly|quarterly|yearly)?\s*paths?\s+from\s+.+?\s+(?:for|of)\s+",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.split(
        r"\b(?:using|based on|considering|with help from|while considering|taking into account|driven by|affected by|impacted by|because of|given)\b",
        cleaned,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    cleaned = re.split(
        r"\bwith\s+(?:agent|agents|debate|disagreement|explanation|confidence|uncertainty|evidence|sources?|charts?|report)\b",
        cleaned,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    cleaned = re.split(
        r"\b(?:over|for|during|through|until)\s+(?:the\s+)?(?:next|coming|following|\d+\s+(?:day|days|week|weeks|month|months|quarter|quarters|year|years)|\d{4})",
        cleaned,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    return cleaned.strip(" .,:;-") or section


def _infer_unit(text: str) -> str:
    if re.search(r"\b(?:usd|dollars?)\b.*\b(?:trillion|tn)\b|\b(?:trillion|tn)\b.*\b(?:usd|dollars?)\b", text):
        return "USD trillion"
    if re.search(r"\b(?:usd|dollars?)\b.*\b(?:billion|bn)\b|\b(?:billion|bn)\b.*\b(?:usd|dollars?)\b", text):
        return "USD billion"
    if re.search(r"\b(?:usd|dollars?)\b.*\b(?:million|mn)\b|\b(?:million|mn)\b.*\b(?:usd|dollars?)\b", text):
        return "USD million"
    if re.search(r"\b(?:usd|dollars?)\b.*\b(?:metric\s*ton|tonne|ton|mt)\b|\b(?:metric\s*ton|tonne|ton|mt)\b.*\b(?:usd|dollars?)\b", text):
        return "USD/metric ton"
    if re.search(r"\b(?:usd|dollars?)\b.*\b(?:kg|kilogram)\b|\b(?:kg|kilogram)\b.*\b(?:usd|dollars?)\b", text):
        return "USD/kg"
    if re.search(r"\b(?:usd|dollars?)\b.*\b(?:kwh|mwh|gwh)\b|\b(?:kwh|mwh|gwh)\b.*\b(?:usd|dollars?)\b", text):
        energy_unit = "kWh" if "kwh" in text else ("MWh" if "mwh" in text else "GWh")
        return f"USD/{energy_unit}"
    if re.search(r"\b(?:usd|dollars?)\b.*\b(?:barrel|bbl)\b|\b(?:barrel|bbl)\b.*\b(?:usd|dollars?)\b", text):
        return "USD/barrel"
    if re.search(r"\b(?:thousand|000)\s+(?:metric\s*)?(?:tons?|tonnes?|mt)\b", text):
        return "thousand metric tons"
    if re.search(r"\b(?:metric\s*)?(?:tons?|tonnes?|mt)\b", text):
        return "metric tons"
    if re.search(r"\b(?:month|months)\b", text):
        return "months"
    if "index" in text:
        return "index"
    if re.search(r"\b(?:share|rate|turnout|percent|percentage|probability|chance)\b", text):
        return "percent"
    if any(term in text for term in ["price", "cost", "revenue", "capex", "value", "premium"]):
        return "currency_or_index"
    if any(term in text for term in ["seat", "count", "number", "volume", "inventory"]):
        return "count"
    if any(term in text for term in ["balance", "margin", "spread"]):
        return "percent_or_index"
    return "index"


def _strip_non_actor_instruction_sections(text: str) -> str:
    """Keep actor discovery focused on simulation substance, not validation/report instructions."""
    return re.split(
        r"\n\s*(?:=+\s*)?(?:AGENT BEHAVIOR RULES|DEBATE FORMAT|VALIDATION REQUIREMENTS|REQUIRED REPORT FORMAT|STYLE REQUIREMENTS)\b",
        text or "",
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]


def _extract_agent_archetypes(text: str, target_variables: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    actor_source_text = _strip_non_actor_instruction_sections(text)
    explicit_agents = _extract_explicit_agent_items(actor_source_text)
    explicit_sections = []
    for pattern in [
        r"agent architecture.*?(?:target variables|time[- ]?pocket|scenario paths|data tables|final|$)",
        r"agents include.*?(?:\.|\n\n|$)",
        r"agents?\s*(?:[:=-]|\n)(.*?)(?:target variables|time[- ]?pocket|scenario paths|data tables|final|$)",
    ]:
        match = re.search(pattern, actor_source_text or "", flags=re.IGNORECASE | re.DOTALL)
        if match:
            explicit_sections.append(match.group(0))

    target_names = {
        _snake(str(target.get("name") or ""))
        for target in target_variables or []
        if isinstance(target, dict)
    }

    candidates = list(explicit_agents)
    if not candidates:
        for section in explicit_sections:
            candidates.extend(_split_items(section))
    if not candidates:
            candidates.extend(_actor_candidates_from_context(actor_source_text))

    # If the prompt gives a substantial explicit roster, respect it. Inferred
    # population cohorts are useful when the prompt is underspecified, but they
    # become filler when a user has already named the room.
    participant_candidates = [] if len(explicit_agents) >= 10 else _participant_candidates_from_context(actor_source_text)
    archetypes = []
    seen = set()
    max_archetypes = max(
        12,
        min(40, (len(candidates) + len(participant_candidates) + 8) if explicit_agents else 24)
    )

    def add_candidates(raw_candidates: List[str]) -> None:
        nonlocal archetypes
        for raw in raw_candidates:
            if (
                _is_non_actor_artifact(raw)
                or _is_placeholder_actor(raw)
                or _looks_like_target_actor_artifact(raw, target_names)
                or not _looks_actorish(raw)
            ):
                continue
            name = _title_role(raw)
            if _is_process_role_actor(name):
                continue
            key = _role_key(name)
            if key in seen:
                continue
            seen.add(key)
            archetypes.append({
                "name": name,
                "causal_role": f"Represents or moves part of the simulation outcome through {raw}.",
                "information_advantage": f"Context-derived knowledge associated with {raw}.",
                "likely_bias": "May overweight its own information access, incentives, or lived context.",
                "population_share": None,
                "subtypes": _generic_subtypes(name),
                "numeric_output_required": _should_output_numbers(name),
            })
            if len(archetypes) >= max_archetypes:
                break

    add_candidates(candidates)

    # If explicit prompt sections contain only instructions such as "force every
    # agent to..." or refer to a missing previous pack, recover from the broader
    # context instead of preserving a junk roster.
    if len(archetypes) < 3:
        add_candidates(_actor_candidates_from_context(text))
    # Affected people should be present whenever the prompt implies them, even
    # when the institutional/analyst roster is already large.
    add_candidates(participant_candidates)

    archetypes = _ensure_participant_cohorts(archetypes, text)

    if archetypes:
        return archetypes

    target_names = ", ".join(variable["name"] for variable in target_variables)
    return [
        {
            "name": "Primary Decision Makers",
            "causal_role": f"Can directly alter resources, strategy, rules, or timing affecting {target_names}.",
            "information_advantage": "Internal plans, constraints, and authority signals.",
            "likely_bias": "May defend prior decisions or understate implementation risk.",
            "population_share": 0.18,
            "subtypes": ["formal authority", "operational decision maker"],
            "numeric_output_required": False,
        },
        {
            "name": "Affected Participant Groups",
            "causal_role": f"Create ground-level behavior, demand, response, or pressure affecting {target_names}.",
            "information_advantage": "Lived experience, local conditions, and behavioral feedback.",
            "likely_bias": "May reflect local intensity more than broad averages.",
            "population_share": 0.34,
            "subtypes": ["high-exposure participant", "low-exposure participant", "skeptical participant", "swing participant"],
            "numeric_output_required": False,
        },
        {
            "name": "Resource Controllers",
            "causal_role": f"Control supply, money, access, distribution, capacity, or bottlenecks affecting {target_names}.",
            "information_advantage": "Operational constraints, capacity, inventory, budgets, or flow data.",
            "likely_bias": "May hide fragility or overstate control.",
            "population_share": 0.16,
            "subtypes": ["capacity holder", "funding holder"],
            "numeric_output_required": False,
        },
        {
            "name": "Independent Analysts",
            "causal_role": f"Convert evidence into forecasts, assumptions, and uncertainty for {target_names}.",
            "information_advantage": "Models, historical analogs, comparative data, and source synthesis.",
            "likely_bias": "May smooth uncertainty or overfit previous patterns.",
            "population_share": 0.16,
            "subtypes": ["model-based analyst", "ground-signal analyst"],
            "numeric_output_required": True,
        },
        {
            "name": "Narrative and Information Brokers",
            "causal_role": f"Shape attention, framing, trust, and signal spread around {target_names}.",
            "information_advantage": "Narrative velocity, public reaction, and source visibility.",
            "likely_bias": "May overweight salient or recent stories.",
            "population_share": 0.16,
            "subtypes": ["mainstream narrator", "local signal broker"],
            "numeric_output_required": False,
        },
    ]


def _ensure_participant_cohorts(archetypes: List[Dict[str, Any]], text: str) -> List[Dict[str, Any]]:
    """Derive actual affected cohorts from prompt-derived observer/analyst roles."""
    existing = {str(item.get("name", "")).lower() for item in archetypes}
    additions = []

    for archetype in archetypes:
        source_name = str(archetype.get("name") or "").strip()
        cohort_name = _cohort_from_observer_role(source_name)
        if not cohort_name or cohort_name.lower() in existing:
            continue
        additions.append({
            "name": cohort_name,
            "causal_role": (
                f"Represents the actual affected people implied by `{source_name}`. "
                "Their aggregate choices, lived reactions, adoption, refusal, turnout, demand, or compliance can move the outcome."
            ),
            "information_advantage": (
                "Ground-level lived experience, local incentives, social pressure, material constraints, "
                "and behavioral response not visible from analyst or institutional roles alone."
            ),
            "likely_bias": "May overweight local experience, immediate material benefit, identity pressure, or recent salient events.",
            "population_share": 0.0,
            "subtypes": _generic_subtypes(cohort_name) or [
                "high-exposure participant",
                "low-exposure participant",
                "swing participant",
                "skeptical participant",
            ],
            "numeric_output_required": False,
        })
        existing.add(cohort_name.lower())

    if not additions:
        return archetypes

    return archetypes + additions[:12]


def _cohort_from_observer_role(name: str) -> Optional[str]:
    """Turn 'X analyst/observer' into 'X cohort' when X is not just an institution."""
    if not name:
        return None
    lowered = name.lower()
    excluded_role_terms = [
        "strategist", "negotiator", "journalist", "media", "pollster", "poll/data", "poll", "data",
        "analyst",
        "scientist", "watchdog", "auditor", "business", "industry",
        "official", "government", "bank", "regulator", "company",
        "organization", "institution", "party", "moderator", "mediator",
        "research", "data retrieval", "synthesizer",
    ]
    if any(term in lowered for term in excluded_role_terms):
        return None

    base = re.sub(
        r"\b(?:analyst|observer|representative|advocate|interpreter|tracker|voice|proxy|profile|persona)\b",
        "",
        name,
        flags=re.IGNORECASE,
    )
    base = re.sub(r"\s+", " ", base).strip(" -_/")
    if not base or base.lower() == lowered:
        return None
    if len(base.split()) > 5:
        return None
    if base.lower() in {"primary", "independent", "external"}:
        return None
    group_terms = {
        "voter", "voters", "consumer", "consumers", "worker", "workers",
        "household", "households", "resident", "residents", "citizen", "citizens",
        "community", "communities", "beneficiary", "beneficiaries", "student", "students",
        "tenant", "tenants", "homeowner", "homeowners", "patient", "patients",
        "farmer", "farmers", "user", "users", "women", "minority", "youth",
        "rural", "urban", "middle-class", "expatriate", "ground-truth",
    }
    base_words = set(re.findall(r"[a-z-]+", base.lower()))
    if not (base_words & group_terms):
        return None
    return f"{base} Cohort"


def _should_output_numbers(name: str) -> bool:
    """Only numeric-capable roles should own forecast paths."""
    lowered = (name or "").lower()
    lived_experience_terms = [
        "voter", "consumer", "worker", "household", "beneficiary", "community",
        "cohort", "common", "public", "people", "rural poor", "urban middle",
        "youth", "student", "patient", "citizen", "resident",
        "booth-level worker", "field worker", "minority", "women", "middle-class",
        "middle class", "rural", "urban", "poor", "grassroots", "booth",
    ]
    narrative_public_terms = [
        "journalist", "media", "narrative", "watchdog", "governance", "observer",
        "strategist", "negotiator", "campaign", "party", "executive", "operator",
        "witness", "diplomat", "planner", "adviser", "advisor", "hardliner",
        "coordinator", "representative",
    ]
    numeric_terms = [
        "quant", "data", "pollster", "scientist", "economist", "researcher", "analyst",
        "forecaster", "model", "statistic", "auditor", "synthesizer", "retrieval",
        "numeric", "survey", "polling", "measurement", "probability", "risk",
    ]
    strong_numeric_terms = [
        "quant", "data", "pollster", "scientist", "economist", "researcher", "analyst",
        "forecaster", "model", "statistic", "auditor", "synthesizer", "retrieval",
        "numeric", "survey", "polling", "measurement",
    ]
    if _is_process_role_actor(name):
        return False
    has_numeric_skill = any(term in lowered for term in numeric_terms)
    # Lived-experience roles should shape assumptions and rebut models, not emit
    # fake precision, unless the prompt explicitly casts them as data/poll/quant roles.
    if any(term in lowered for term in lived_experience_terms) and not any(
        term in lowered for term in ["data", "poll", "pollster", "survey", "statistic", "quant", "model"]
    ):
        return False
    if any(term in lowered for term in narrative_public_terms) and not any(
        term in lowered for term in strong_numeric_terms
    ):
        return False
    if has_numeric_skill:
        return True
    if any(term in lowered for term in narrative_public_terms):
        return False
    return False


def _actor_candidates_from_context(text: str) -> List[str]:
    candidates = []
    source_text = re.sub(r"\s+", " ", (text or "").replace("U.S.", "US").replace("U.K.", "UK"))
    for pattern in [
        r"(?:considering|using|involving|include|includes|including|with)\s+([^.;\n]+)",
        r"(?:driven by|impacted by|affected by)\s+([^.;\n]+)",
    ]:
        for match in re.finditer(pattern, source_text, flags=re.IGNORECASE):
            segment = match.group(1)
            if re.search(r"\bonly\s+information\s+available\b|\bavailable\s+(?:up\s+)?to\b", segment, flags=re.IGNORECASE):
                if re.search(r"\bconsidering\b", segment, flags=re.IGNORECASE):
                    segment = re.split(r"\bconsidering\b", segment, maxsplit=1, flags=re.IGNORECASE)[-1]
                else:
                    continue
            for item in _split_items(segment):
                if 2 <= len(item) <= 70 and len(item.split()) <= 5 and not _is_non_actor_artifact(item):
                    candidates.append(item if _looks_actorish(item) else f"{item} actor")
    for item in _split_items(source_text):
        if _looks_actorish(item):
            candidates.append(item)
    for match in re.finditer(r"\b([A-Z][A-Za-z0-9&.'-]*(?:\s+[A-Z][A-Za-z0-9&.'-]*){0,4}|[A-Z]{2,8})\b", source_text):
        phrase = match.group(1)
        if phrase.lower() in {"using", "forecast", "produce", "output", "return", "generate"}:
            continue
        after = source_text[match.end(): match.end() + 60].lower()
        if any(metric in after for metric in [" price", " prices", " rate", " share", " seats", " turnout", " forecast"]) and not _looks_actorish(phrase):
            continue
        start = max(0, match.start() - 80)
        end = min(len(source_text), match.end() + 80)
        window = source_text[start:end].lower()
        if any(word in window for word in ACTOR_HINT_WORDS):
            candidates.append(phrase)
    return candidates


def _participant_candidates_from_context(text: str) -> List[str]:
    """Infer broad affected cohorts from words in the prompt without fixed domain rosters."""
    lowered = (text or "").lower()
    election_like = bool(re.search(r"\b(?:voters?|turnout|polling|election|vote share|seat share|assembly)\b", lowered))
    narrative_like = bool(re.search(
        r"\b(novel|book[- ]canon|canon|fiction|story|characters?|unpublished|winds\s+of\s+winter|asoiaf|song\s+of\s+ice\s+and\s+fire|prophecy|foreshadowing)\b",
        lowered,
    ))
    market_like = bool(re.search(r"\b(?:price|market|oil|commodity|supply|demand|inventory|trader|refiner|shipping|risk premium|spread|margin)\b", lowered))
    ai_like = bool(re.search(r"\b(?:ai|model capability|frontier lab|open[- ]source|regulation|enterprise adoption|capex)\b", lowered))
    analyst_label = (
        "Data And Polling Analysts"
        if election_like
        else (
            "Canon Evidence Analysts"
            if narrative_like
            else ("Market/Data Analysts" if market_like else ("Technology/Data Analysts" if ai_like else "Data And Forecast Analysts"))
        )
    )
    candidates: List[str] = []
    cohort_rules = [
        (r"\bvoters?\b|\bturnout\b|\bpolling\b|\belection\b", "Affected Voters" if election_like else "Affected Participants"),
        (r"\bwomen\b|\bfemale\b", "Women Voters" if election_like else "Women Participants"),
        (r"\bminority\b|\bminorities\b|\bcommunity\b|\bcommunities\b", "Minority Or Community Voters" if election_like else "Minority Or Community Participants"),
        (r"\byouth\b|\bstudent\b|\bunemployment\b|\bjobs?\b", "Youth Or Employment-Exposed Voters" if election_like else "Youth Or Employment-Exposed Participants"),
        (r"\brural\b|\bpoor\b|\bwelfare\b|\bbeneficiar", "Rural Or Welfare-Exposed Voters" if election_like else "Rural Or Welfare-Exposed Participants"),
        (r"\burban\b|\bmiddle[- ]class\b|\bcivic\b", "Urban Middle-Class Voters" if election_like else "Urban Middle-Class Participants"),
        (r"\bworker\b|\blabor\b|\blabour\b|\bunion\b", "Workers Or Labor Participants"),
        (r"\bconsumer\b|\bdemand\b|\bhousehold\b", "Consumers Or Households"),
        (r"\bbusiness\b|\bindustry\b|\bfirm\b|\bcompany\b", "Business Or Industry Participants"),
        (r"\bmedia\b|\bnarrative\b|\bjournalist\b", "Media Narrative Brokers"),
        (r"\bdata\b|\bpoll\b|\bsurvey\b|\bmodel\b|\bstatistic|\bforecast\b|\bnumeric\b", analyst_label),
        (r"\bgovernance\b|\bwatchdog\b|\baudit\b|\bcorruption\b", "Governance Watchdogs"),
    ]
    for pattern, name in cohort_rules:
        if re.search(pattern, lowered):
            candidates.append(name)
    return candidates


def _is_metric_artifact_actor(value: str) -> bool:
    """Return True when a numeric target phrase is masquerading as an actor."""
    lowered = re.sub(r"\s+", " ", (value or "").strip().lower())
    if not lowered:
        return True
    metric_words = {
        "price", "prices", "cost", "costs", "rate", "rates", "share", "shares",
        "seat", "seats", "turnout", "probability", "probabilities", "chance",
        "index", "score", "margin", "spread", "growth", "capex", "inventory",
        "forecast", "forecasts", "output", "outputs",
    }
    words = set(re.findall(r"[a-z0-9]+", lowered))
    actor_context_words = {
        "actor", "actors", "agent", "agents", "analyst", "analysts", "auditor",
        "auditors", "synthesizer", "synthesizers", "moderator", "moderators",
        "mediator", "mediators", "scout", "researcher", "researchers",
        "scientist", "scientists", "pollster", "pollsters", "trader", "traders",
        "miner", "miners", "refiner", "refiners", "manufacturer",
        "manufacturers", "automaker", "automakers", "regulator", "regulators",
        "buyer", "buyers", "consumer", "consumers", "voter", "voters",
        "worker", "workers", "household", "households", "lab", "labs",
        "company", "companies", "firm", "firms", "bank", "banks", "party",
        "parties", "strategist", "strategists", "watchdog", "watchdogs",
        "provider", "providers", "developer", "developers", "official",
        "officials", "operator", "operators", "importer", "importers",
        "exporter", "exporters", "landlord", "landlords", "tenant", "tenants",
        "owner", "owners", "consultant", "consultants", "manager", "managers",
        "cio", "cios", "cfo", "cfos",
    }
    if (words & metric_words) and not (words & actor_context_words):
        return True
    if not words & metric_words:
        return False
    if re.search(
        r"\b(?:index|score|rate|share|probability|probabilities|price|prices|cost|costs|capex|turnout|seats?)\s*$",
        lowered,
    ):
        return True

    if words & actor_context_words and len(words) <= 4:
        return False
    return True


def _looks_like_target_actor_artifact(value: str, target_names: set[str]) -> bool:
    """Reject actor candidates that are really target-variable fragments."""
    cleaned = _snake(value or "")
    if not cleaned:
        return True
    if cleaned in target_names:
        return True
    if any(cleaned and (cleaned in target or target in cleaned) for target in target_names):
        if not re.search(
            r"(agents?|actors?|analysts?|auditors?|moderators?|mediators?|scouts?|"
            r"strategists?|planners?|advisers?|advisors?|diplomats?|experts?|"
            r"underwriters?|witnesses?|coordinators?|hardliners?|synthesizers?|integrators?|quants?|"
            r"labs?|providers?|developers?|cios?|cfos?|managers?|workers?|consumers?|"
            r"regulators?|teams?|consultants?|investors?|miners?|refiners?|manufacturers?|"
            r"automakers?|buyers?|traders?|operators?|landlords?|tenants?)$",
            cleaned,
        ):
            return True
    if re.search(r"(^|_)percent(_|$)|(^|_)usd(_|$)|(^|_)0_100($|_)", cleaned):
        return True
    if cleaned in {"roi", "rate", "share", "index", "probability", "capex", "price", "cost"}:
        return True
    return False


def _looks_actorish(value: str) -> bool:
    lowered = (value or "").lower()
    if not lowered or any(stop in lowered for stop in ["target variable", "data table", "copy paste", "simulation question", "every agent", "force every agent"]):
        return False
    if _is_non_actor_artifact(value):
        return False
    if _is_metric_artifact_actor(value):
        return False
    if re.search(r"\b(?:pocket|baseline|snapshot|synthesis|forecast table|report|charts?)\b", lowered):
        return False
    words = set(re.findall(r"[a-z0-9]+", lowered))
    if words and words <= {"usd", "lfp", "kwh", "twh", "mwh", "gwh", "ev"}:
        return False
    if re.fullmatch(r"[A-Z]{2,8}", value.strip()):
        context_name = value.strip().lower()
        # Acronyms are actors only when they are not bare units/technologies.
        return len(context_name) > 2 and context_name not in {"usd", "lfp", "kwh", "twh", "mwh", "gwh"}
    return bool(words & ACTOR_HINT_WORDS)


def _is_non_actor_artifact(value: str) -> bool:
    """Reject extraction artifacts that describe files/metadata, not causal actors."""
    lowered = re.sub(r"\s+", " ", (value or "").strip().lower())
    if not lowered:
        return True
    if any(re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in INSTRUCTION_ACTOR_PATTERNS):
        return True
    if lowered in NON_ACTOR_ARTIFACT_TERMS:
        return True
    words = re.findall(r"[a-z0-9]+", lowered)
    if any(term in words for term in SOURCE_SENTENCE_FRAGMENT_TERMS):
        return True
    if len(words) > 5 and not (set(words) & ACTOR_HINT_WORDS) and not re.search(
        r"\b(?:actors?|agents?|analysts?|auditors?|mediators?|moderators?|scouts?|synthesizers?|"
        r"miners?|refiners?|manufacturers?|automakers?|buyers?|consumers?|traders?|regulators?|"
        r"operators?|voters?|workers?|households?|participants?|communities?|watchdogs?)\b$",
        lowered,
    ):
        return True
    if lowered.startswith(("http://", "https://", "www.")):
        return True
    if re.match(r"^(?:simulate|forecast|predict|estimate|produce|output|return|generate)\b", lowered):
        return True
    if re.fullmatch(r"(?:url|source|query|result|snippet|title|markdown|json|csv|table)s?", lowered):
        return True
    if _is_metric_artifact_actor(value):
        return True
    if re.search(r"\b(?:pocket|baseline|snapshot|synthesis)\b", lowered):
        return True
    word_set = set(words)
    if word_set and word_set <= {"usd", "lfp", "kwh", "twh", "mwh", "gwh", "ev"}:
        return True
    return any(term in lowered for term in NON_ACTOR_ARTIFACT_TERMS)


def _is_non_target_artifact(value: str) -> bool:
    """Reject output-format labels that are not simulated target variables."""
    lowered = _snake(value or "")
    plain = re.sub(r"\s+", " ", (value or "").strip().lower())
    if not lowered:
        return True
    if lowered in NON_TARGET_ARTIFACT_TERMS:
        return True
    if re.search(r"(?:^|_)such_as(?:_|$)|(?:^|_)following(?:_|$).*(?:outputs?|variables?)", lowered):
        return True
    if any(term in lowered for term in [
        "research_paper",
        "literature_review",
        "news_analysis",
        "recent_context",
        "blog_post",
        "source_pointer",
        "majority_mark",
    ]):
        return True
    if plain.startswith(("required output", "appendix", "markdown", "report")):
        return True
    return False


def _is_placeholder_target(value: str) -> bool:
    lowered = _snake(value or "")
    if not lowered:
        return True
    if lowered in NON_TARGET_ARTIFACT_TERMS:
        return True
    placeholder_patterns = [
        r"^(?:the_)?following_(?:numeric_)?outputs?$",
        r".*_(?:such_as)$",
        r".*_(?:such_as)_.+",
        r".*probability_bands_such_as.*",
        r"^(?:forecast_)?(?:the_)?following_(?:numeric_)?outputs?$",
        r"^(?:requested_)?(?:target|metric|numeric|output)_variables?$",
        r"^(?:primary|main|overall)_outcome$",
    ]
    return any(re.fullmatch(pattern, lowered) for pattern in placeholder_patterns)


def _is_placeholder_actor(value: str) -> bool:
    """Reject LLM/fallback placeholders that are target labels pretending to be actors."""
    lowered = re.sub(r"\s+", " ", (value or "").strip().lower())
    if not lowered:
        return True
    if any(re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in INSTRUCTION_ACTOR_PATTERNS):
        return True
    if re.fullmatch(r"(?:forecast|predict|estimate|simulate)\s+.+\s+actor(?:\s+\d+)?", lowered):
        return True
    if re.fullmatch(r".+\s+(?:variable|target|table|forecast|projection|output)(?:\s+actor)?(?:\s+\d+)?", lowered):
        return True
    if re.fullmatch(
        r".*\b(?:share|turnout|swing|swings|dynamics|bands?|probability|probabilities|scenario|scenarios|path|paths|rate|index|price|prices)\s+actor(?:\s+\d+)?",
        lowered,
    ):
        return True
    if re.search(r"\b(?:using|in production|materially assisted|0[- ]?100|usd|percent|percentage)\b", lowered):
        return True
    if re.fullmatch(r".*\d+\s+actor", lowered):
        return True
    return False


def _is_process_role_actor(value: str) -> bool:
    """Process-control roles are added separately, not as causal stakeholders."""
    lowered = re.sub(r"\s+", " ", (value or "").strip().lower())
    if not lowered:
        return False
    return any(term in lowered for term in PROCESS_ROLE_TERMS)


def _title_role(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9&/ _.'-]+", " ", value or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    words = cleaned.split()
    if len(words) > 6:
        cleaned = " ".join(words[:6])
    titled = cleaned.title() if not re.fullmatch(r"[A-Z]{2,8}", cleaned) else cleaned
    acronym_map = {
        " Ev ": " EV ",
        " Ai ": " AI ",
        " Gdp ": " GDP ",
        " Usd ": " USD ",
        " Lfp ": " LFP ",
        " Kwh": " kWh",
        " Wti ": " WTI ",
        " Opec ": " OPEC ",
    }
    padded = f" {titled} "
    for old, new in acronym_map.items():
        padded = padded.replace(old, new)
    cleaned_title = padded.strip()
    cleaned_title = re.sub(r"\s+Actor$", "", cleaned_title)
    return cleaned_title


def _generic_subtypes(name: str) -> List[str]:
    lowered = name.lower()
    if any(term in lowered for term in ["people", "participant", "consumer", "voter", "worker", "citizen", "household", "community"]):
        return ["high-exposure group", "low-exposure group", "swing group", "skeptical group"]
    if any(term in lowered for term in ["analyst", "researcher", "expert", "pollster", "scientist"]):
        return ["model-led", "field-signal-led", "skeptical"]
    if any(term in lowered for term in ["government", "official", "leader", "executive", "maker"]):
        return ["public-facing", "operational"]
    return []


class DomainSimulationPlanner:
    """Create and normalize simulation blueprints without fixed domain rosters."""

    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm_client = llm_client

    def fallback_plan(self, combined_text: str) -> Dict[str, Any]:
        """Build the deterministic generic plan without calling the LLM."""
        return self._fallback_plan(combined_text or "")

    def plan(
        self,
        user_question: str,
        document_text: str = "",
        project_id: Optional[str] = None,
        allow_fallback: Optional[bool] = None,
    ) -> Dict[str, Any]:
        user_question = (user_question or "").strip()
        if not user_question:
            raise ValueError("A user question or simulation requirement is required.")

        combined = f"{user_question}\n{document_text or ''}"
        prompt_target_source = user_question
        if allow_fallback is None:
            allow_fallback = not Config.LLM_REQUIRED_FOR_PLANNING
        try:
            if self.llm_client is None:
                self.llm_client = LLMClient(timeout=Config.PLANNER_LLM_TIMEOUT_SECONDS)
            executor = ThreadPoolExecutor(max_workers=1)
            try:
                future = executor.submit(self._plan_with_llm, user_question, document_text)
                raw = future.result(timeout=Config.PLANNER_LLM_TIMEOUT_SECONDS)
            finally:
                executor.shutdown(wait=False, cancel_futures=True)
            plan = self._normalize_plan(raw, combined, target_text=prompt_target_source)
        except FutureTimeoutError:
            message = f"Planner LLM timed out after {Config.PLANNER_LLM_TIMEOUT_SECONDS:.1f}s"
            if not allow_fallback:
                raise LLMUnavailableError(message)
            logger.warning("%s, using context-derived fallback", message)
            plan = self._fallback_plan(combined, target_text=prompt_target_source)
        except Exception as exc:
            if not allow_fallback:
                raise LLMUnavailableError(
                    f"Planner LLM is required but unavailable: {exc}"
                ) from exc
            if _looks_like_llm_connectivity_issue(exc):
                logger.debug(
                    "Planner LLM unavailable from this runtime, using context-derived fallback: %s",
                    exc,
                )
            else:
                logger.warning(f"Planner LLM failed, using context-derived fallback: {exc}")
            plan = self._fallback_plan(combined, target_text=prompt_target_source)

        if project_id:
            plan["project_id"] = project_id

        cutoff = self._detect_cutoff_date(user_question)
        plan["cutoff_date"] = cutoff
        plan["future_leakage_policy"] = {
            "enabled": bool(cutoff),
            "blocked_after": cutoff,
            "violations": [],
        }
        return plan

    def _plan_with_llm(self, user_question: str, document_text: str) -> Dict[str, Any]:
        context_excerpt = (document_text or "")[:12000]
        system = (
            "You are Horizon XL's context-derived simulation planner. Return only valid JSON. "
            "Do not use preset domain rosters. Infer domain label, agents, target variables, "
            "time pockets, and research needs from the prompt, uploaded context, and research packet."
        )
        prompt = f"""
Create a simulation blueprint using this schema:
{{
  "domain": "short free-text label derived from the prompt, not a fixed category",
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
    "allocation_basis": "Why this many agents and copies are needed.",
    "allocations": [
      {{
        "archetype_name": "...",
        "population_share": 0.5,
        "instance_count": 6,
        "rationale": "...",
        "subtypes": ["..."]
      }}
    ],
    "orchestration_agents": {ORCHESTRATION_AGENT_TEMPLATES},
    "research_agents": {RESEARCH_AGENT_TEMPLATES}
  }},
  "discussion_architecture": {{
    "moderated": true,
    "loop": ["moderator frames pocket", "research/data agents inject evidence", "causal agents revise", "mediator probes disagreement", "quant synthesizes", "auditor flags gaps"],
    "anti_drift_rules": ["Every round must reference target variables."]
  }},
  "external_research_policy": {{
    "enabled": true,
    "outside_graph": true,
    "allowed_inputs": ["user_provided_urls", "uploaded_files", "approved_search_or_scraping_tool_results"],
    "injection_point": "before each debate pocket and before numeric synthesis"
  }},
  "state_variables": [
    {{"name": "...", "unit": "...", "directional_interpretation": "...", "required": true}}
  ],
  "scenario_structure": {SCENARIOS},
  "required_outputs": {DEFAULT_REQUIRED_OUTPUTS},
  "validation_requirements": {DEFAULT_VALIDATION_REQUIREMENTS}
}}

User question:
{user_question}

Uploaded/project/research context excerpt:
{context_excerpt}

Rules:
- Domain-specific agents may be named only if the prompt/context/research implies them.
- Do not assume exactly 10 agents. Decide count from scope, target variables, horizon, and how much mass/public behavior matters.
- If common people, voters, consumers, workers, households, users, patients, students, or affected communities materially drive outcomes, allocate multiple instances/subtypes to them.
- Always include the moderator, mediator, evidence auditor, quantitative synthesizer, external research scout, and data retrieval analyst.
- External research happens outside graph memory but its findings must be injected into debate pockets and audited before numeric synthesis.
- If the user gives a cutoff date, prevent future leakage and mark sources after that date as violations.
"""
        return self.llm_client.chat_json(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            temperature=0.25,
            max_tokens=5000,
        )

    def _fallback_plan(self, combined_text: str, target_text: Optional[str] = None) -> Dict[str, Any]:
        target_source = target_text or combined_text
        targets = _extract_target_variables(target_source)
        archetypes = _extract_agent_archetypes(combined_text, targets)
        raw = {
            "domain": _infer_domain_label(target_source),
            "user_question": target_source.split("\n", 1)[0],
            "target_variables": targets,
            "forecast_horizon": self._infer_horizon(target_source),
            "required_agent_archetypes": archetypes,
            "state_variables": self._infer_state_variables(combined_text, targets),
            "scenario_structure": deepcopy(SCENARIOS),
            "required_outputs": list(DEFAULT_REQUIRED_OUTPUTS),
            "validation_requirements": list(DEFAULT_VALIDATION_REQUIREMENTS),
        }
        return self._normalize_plan(raw, combined_text, target_text=target_source)

    def _normalize_plan(self, raw: Dict[str, Any], combined_text: str, target_text: Optional[str] = None) -> Dict[str, Any]:
        plan = raw if isinstance(raw, dict) else {}
        target_source = target_text or combined_text
        extracted_targets = _extract_target_variables(target_source)
        has_explicit_prompt_targets = bool(_extract_explicit_target_items(target_source))
        raw_target_values = extracted_targets if has_explicit_prompt_targets else (plan.get("target_variables") or extracted_targets)
        targets = self._merge_target_variables(
            self._normalize_target_variables(raw_target_values),
            extracted_targets,
        )
        has_explicit_prompt_agents = bool(_extract_explicit_agent_items(target_source))
        agent_source = target_source if has_explicit_prompt_agents else combined_text
        extracted_archetypes = _extract_agent_archetypes(agent_source, targets)
        raw_agent_values = extracted_archetypes if has_explicit_prompt_agents else (plan.get("required_agent_archetypes") or extracted_archetypes)
        archetypes = self._merge_context_agents(
            self._normalize_agents(raw_agent_values),
            extracted_archetypes,
        )
        normalized = {
            "domain": _normalize_domain_label(plan.get("domain"), target_source),
            "user_question": str(plan.get("user_question") or target_source.split("\n", 1)[0]),
            "source_summary": re.sub(r"\s+", " ", combined_text or "").strip()[:6000],
            "target_variables": targets,
            "forecast_horizon": self._normalize_horizon(plan.get("forecast_horizon") or self._infer_horizon(target_source)),
            "required_agent_archetypes": archetypes,
            "state_variables": self._normalize_state_variables(plan.get("state_variables") or self._infer_state_variables(combined_text, targets)),
            "scenario_structure": self._normalize_scenarios(plan.get("scenario_structure"), target_source),
            "required_outputs": self._ensure_list(plan.get("required_outputs"), DEFAULT_REQUIRED_OUTPUTS),
            "validation_requirements": self._ensure_list(plan.get("validation_requirements"), DEFAULT_VALIDATION_REQUIREMENTS),
        }
        normalized["time_pockets"] = self._normalize_time_pockets(
            plan.get("time_pockets") or _extract_time_pocket_items(target_source),
            normalized["forecast_horizon"],
            target_source,
        )
        normalized["agent_population"] = self._normalize_agent_population(
            plan.get("agent_population"),
            normalized["required_agent_archetypes"],
            combined_text,
        )
        normalized["discussion_architecture"] = plan.get("discussion_architecture") or self._build_discussion_architecture()
        normalized["external_research_policy"] = plan.get("external_research_policy") or self._build_external_research_policy(combined_text)
        return normalized

    def _infer_horizon(self, text: str) -> Dict[str, str]:
        lowered = (text or "").lower()
        granularity = "event_triggered"
        narrative_like = bool(re.search(
            r"\b(novel|book[- ]canon|canon|fiction|story|characters?|unpublished|winds\s+of\s+winter|asoiaf|song\s+of\s+ice\s+and\s+fire)\b",
            lowered,
        ))
        for word in ["daily", "weekly", "monthly", "quarterly", "yearly"]:
            if word in lowered:
                granularity = word
                break
        rolling_match = re.search(
            r"\b(?:next|coming|following|over\s+the\s+next|for\s+the\s+next)\s+(\d{1,3}|one|two|three|four|five|six|seven|eight|nine|ten)\s+"
            r"(day|days|week|weeks|month|months|quarter|quarters|year|years)\b",
            lowered,
            flags=re.IGNORECASE,
        )
        if rolling_match:
            unit = rolling_match.group(2).lower()
            if "day" in unit:
                granularity = "daily"
            elif "week" in unit:
                granularity = "weekly"
            elif "month" in unit:
                granularity = "monthly"
            elif "quarter" in unit:
                granularity = "quarterly"
            elif "year" in unit:
                granularity = "yearly"
        match = re.search(r"(?:from|between)\s+([A-Za-z0-9 ,/-]+?)\s+(?:to|through|-)\s+([A-Za-z0-9 ,/-]+)", text or "", re.IGNORECASE)
        if match and not (_looks_like_horizon_bound(match.group(1)) and _looks_like_horizon_bound(match.group(2))):
            match = None
        cutoff = self._detect_cutoff_date(text or "")
        if not match and rolling_match:
            rolling = self._rolling_horizon_from_cutoff(cutoff, rolling_match.group(1), rolling_match.group(2), granularity)
            if rolling:
                return rolling
        if narrative_like and not match:
            return {
                "start": cutoff or "published_canon_baseline",
                "end": "next_major_story_installment",
                "granularity": "event_triggered",
            }
        return {
            "start": _clean_horizon_bound(match.group(1)) if match else (cutoff or "current_state"),
            "end": _clean_horizon_bound(match.group(2)) if match else "requested_future_outcome",
            "granularity": granularity,
        }

    def _rolling_horizon_from_cutoff(
        self,
        cutoff: Optional[str],
        count_value: str,
        unit: str,
        granularity: str,
    ) -> Optional[Dict[str, str]]:
        count = self._word_or_number(count_value)
        if not count or count <= 0:
            return None
        parsed = _parse_month_year(cutoff or "")
        if not parsed:
            return None
        year, month = parsed
        unit_l = str(unit or "").lower()
        if "month" in unit_l:
            start_year, start_month = _add_months(year, month, 1)
            end_year, end_month = _add_months(start_year, start_month, count - 1)
            return {
                "start": _month_label(start_year, start_month),
                "end": _month_label(end_year, end_month),
                "granularity": "monthly",
            }
        if "quarter" in unit_l:
            start_year, start_month = _add_months(year, month, 3)
            end_year, end_month = _add_months(start_year, start_month, (count - 1) * 3)
            return {
                "start": _month_label(start_year, start_month),
                "end": _month_label(end_year, end_month),
                "granularity": "quarterly",
            }
        if "year" in unit_l:
            start_year = year + 1
            end_year = start_year + count - 1
            return {
                "start": str(start_year),
                "end": str(end_year),
                "granularity": "yearly",
            }
        return {
            "start": cutoff or "current_state",
            "end": f"next {count} {unit_l}",
            "granularity": granularity,
        }

    def _word_or_number(self, value: str) -> int:
        words = {
            "one": 1,
            "two": 2,
            "three": 3,
            "four": 4,
            "five": 5,
            "six": 6,
            "seven": 7,
            "eight": 8,
            "nine": 9,
            "ten": 10,
        }
        text = str(value or "").lower()
        if text.isdigit():
            return int(text)
        return words.get(text, 0)

    def _infer_state_variables(self, text: str, targets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        base = [
            {
                "name": "evidence_strength",
                "unit": "index",
                "directional_interpretation": "Higher means the current evidence base is stronger and less contradictory.",
                "required": True,
            },
            {
                "name": "actor_alignment",
                "unit": "index",
                "directional_interpretation": "Higher means influential actors are more aligned on the same path.",
                "required": True,
            },
            {
                "name": "uncertainty_pressure",
                "unit": "index",
                "directional_interpretation": "Higher means wider scenario dispersion and more tail risk.",
                "required": True,
            },
        ]
        for target in targets[:3]:
            base.append({
                "name": f"{target['name']}_momentum",
                "unit": target.get("unit", "index"),
                "directional_interpretation": f"Higher means upward pressure on {target['name']}.",
                "required": False,
            })
        return base

    def _normalize_target_variables(self, values: Any) -> List[Dict[str, Any]]:
        out = []
        for item in values if isinstance(values, list) else []:
            if not isinstance(item, dict):
                continue
            name = _snake(str(item.get("name") or "target_variable"))
            if _is_non_target_artifact(name) or _is_placeholder_target(name):
                continue
            out.append({
                "name": name,
                "unit": str(item.get("unit") or "index"),
                "required": bool(item.get("required", True)),
                "description": str(item.get("description") or f"Simulated target variable: {name}."),
            })
        return out or [{"name": "primary_outcome", "unit": "index", "required": True, "description": "Primary simulated outcome."}]

    def _merge_target_variables(
        self,
        primary: List[Dict[str, Any]],
        extracted: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        extracted_clean = [
            item for item in extracted or []
            if not _is_non_target_artifact(str(item.get("name") or "")) and not _is_placeholder_target(str(item.get("name") or ""))
        ]
        primary_clean = [
            item for item in primary or []
            if not _is_non_target_artifact(str(item.get("name") or "")) and not _is_placeholder_target(str(item.get("name") or ""))
        ]
        # If the prompt yielded concrete target variables, do not let an LLM or
        # fallback placeholder such as "primary_outcome" become the simulation
        # source of truth.
        merged = list(primary_clean or extracted_clean)
        seen = {str(item.get("name", "")).lower() for item in merged}
        for item in extracted_clean:
            name = str(item.get("name", "")).lower()
            if not name or name in seen:
                continue
            merged.append(item)
            seen.add(name)
            if len(merged) >= 40:
                break
        return merged or extracted_clean

    def _normalize_horizon(self, value: Any) -> Dict[str, Any]:
        horizon = value if isinstance(value, dict) else {}
        granularity = str(horizon.get("granularity") or "event_triggered")
        if granularity not in {"daily", "weekly", "monthly", "quarterly", "yearly", "event_triggered"}:
            granularity = "event_triggered"
        return {
            "start": _clean_horizon_bound(horizon.get("start") or "auto"),
            "end": _clean_horizon_bound(horizon.get("end") or "auto"),
            "granularity": granularity,
        }

    def _normalize_time_pockets(self, value: Any, horizon: Dict[str, Any], combined_text: str = "") -> List[Dict[str, Any]]:
        raw_items: List[Any] = value if isinstance(value, list) else []
        pockets: List[Dict[str, Any]] = []
        for idx, item in enumerate(raw_items, start=1):
            if isinstance(item, dict):
                label = str(item.get("label") or item.get("name") or f"Pocket {idx}").strip()
                start = str(item.get("start") or "auto")
                end = str(item.get("end") or "auto")
                events = item.get("events") if isinstance(item.get("events"), list) else []
            else:
                label = re.sub(r"\s+", " ", str(item)).strip(" .,:;-") or f"Pocket {idx}"
                start = "auto"
                end = "auto"
                events = []
            pockets.append({
                "pocket_id": f"pocket_{idx:03d}",
                "label": label,
                "start": start,
                "end": end,
                "events": events,
            })
        if pockets:
            return pockets[:80]
        return self._default_time_pockets(horizon, combined_text)

    def _default_time_pockets(self, horizon: Dict[str, Any], combined_text: str = "") -> List[Dict[str, Any]]:
        """Create a minimal sequential simulation when the prompt omitted pockets.

        The labels are inferred from generic evidence phases, not from a fixed
        domain roster. This avoids one-shot debates where every agent argues
        from the same static snapshot.
        """
        lowered = (combined_text or "").lower()
        narrative_like = bool(re.search(
            r"\b(novel|book[- ]canon|canon|fiction|story|characters?|unpublished|winds\s+of\s+winter|asoiaf|song\s+of\s+ice\s+and\s+fire|prophecy|foreshadowing)\b",
            lowered,
        ))
        phases = [
            ("Evidence baseline", "historical baseline, prior observations, and starting conditions"),
            ("Current-state update", "latest allowed evidence before the cutoff or current decision point"),
            ("Actor strategy and incentives", "how high-power actors respond, bargain, signal, or adapt"),
            ("Ground behavior conversion", "how affected people, consumers, voters, workers, users, or households react"),
            ("Scenario synthesis", "final cross-agent numeric synthesis and uncertainty update"),
        ]
        if narrative_like:
            phases = [
                ("Canon baseline", "published-canon facts, unresolved promises, foreshadowing, and character positions"),
                ("Immediate aftermath", "the next plausible moves from the last known story state"),
                ("Faction convergence", "alliances, betrayals, military pressure, and information asymmetries"),
                ("Magic and revelation escalation", "prophecy, identity reveals, supernatural constraints, and hidden knowledge"),
                ("Endgame shock synthesis", "death/survival, power transfer, battle outcomes, and uncertainty bands"),
            ]
        elif re.search(r"\b(?:candidate|manifesto|polling|turnout|vote|election|seat\s+share|hung\s+assembly|ballot)\b", lowered):
            phases = [
                ("Historical electoral baseline", "prior contests, vote shares, seat map, and turnout anchors"),
                ("Current political state", "latest allowed pre-result signals, alliances, issues, and public mood"),
                ("Campaign and strategy response", "party strategy, candidate/alliance incentives, media narrative, and mobilization"),
                ("Voting and turnout conversion", "voter blocs, turnout pressure, swing regions, and vote splitting"),
                ("Seat and scenario synthesis", "seat conversion, probability paths, disagreement, and uncertainty bands"),
            ]
        elif re.search(r"\b(?:price|market|oil|commodity|supply|demand|inventory|rate|inflation|gdp|unemployment)\b", lowered):
            phases = [
                ("Historical numeric baseline", "lagged macro/market data, starting levels, and model priors"),
                ("Current stress update", "latest allowed demand, supply, credit, policy, or sentiment signals"),
                ("Actor reaction pocket", "producer, buyer, policy, capital, and household/firm reactions"),
                ("Transmission pocket", "how shocks propagate through prices, employment, demand, supply, or liquidity"),
                ("Scenario synthesis", "base, upside, downside, tail paths and confidence bands"),
            ]
        return [
            {
                "pocket_id": f"pocket_{idx:03d}",
                "label": label,
                "start": horizon.get("start") or "auto",
                "end": horizon.get("end") or "auto",
                "events": [event],
            }
            for idx, (label, event) in enumerate(phases, start=1)
        ]

    def _normalize_agents(self, values: Any) -> List[Dict[str, Any]]:
        out = []
        for item in values if isinstance(values, list) else []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "Context Actor").strip()
            if _is_non_actor_artifact(name) or _is_placeholder_actor(name):
                continue
            if _is_process_role_actor(name):
                continue
            out.append({
                "name": name,
                "causal_role": str(item.get("causal_role") or "Influences the requested outcome."),
                "information_advantage": str(item.get("information_advantage") or "Has context-relevant information."),
                "likely_bias": str(item.get("likely_bias") or "May overweight its own incentives or information set."),
                "population_share": item.get("population_share"),
                "instance_count": item.get("instance_count"),
                "subtypes": self._ensure_list(item.get("subtypes"), _generic_subtypes(name)),
                "numeric_output_required": _should_output_numbers(name),
            })
        return out or _extract_agent_archetypes("", [{"name": "primary_outcome"}])

    def _merge_context_agents(
        self,
        primary: List[Dict[str, Any]],
        extracted: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        merged = list(primary or [])
        seen = {_role_key(str(item.get("name", ""))) for item in merged}
        for item in extracted or []:
            name = str(item.get("name", "")).lower()
            key = _role_key(name)
            if (
                not name
                or key in seen
                or _is_non_actor_artifact(name)
                or _is_placeholder_actor(name)
                or _is_process_role_actor(name)
            ):
                continue
            merged.append(item)
            seen.add(key)
            if len(merged) >= 40:
                break
        return merged or extracted

    def _normalize_state_variables(self, values: Any) -> List[Dict[str, Any]]:
        out = []
        for item in values if isinstance(values, list) else []:
            if not isinstance(item, dict):
                continue
            out.append({
                "name": _snake(str(item.get("name") or "state_variable")),
                "unit": str(item.get("unit") or "index"),
                "directional_interpretation": str(item.get("directional_interpretation") or "Higher means stronger pressure on the outcome."),
                "required": bool(item.get("required", True)),
            })
        return out or [{"name": "uncertainty_pressure", "unit": "index", "directional_interpretation": "Higher means more uncertainty.", "required": True}]

    def _normalize_scenarios(self, value: Any, combined_text: str = "") -> Dict[str, Any]:
        scenario = value if isinstance(value, dict) else {}
        extracted = _extract_scenario_items(combined_text)
        supplied = scenario.get("scenarios")
        scenario_paths: List[Dict[str, Any]] = []

        if isinstance(supplied, list):
            for idx, item in enumerate(supplied, start=1):
                if isinstance(item, dict):
                    name = str(item.get("name") or item.get("id") or f"Scenario {idx}").strip()
                    description = str(item.get("description") or item.get("rationale") or name).strip()
                    scenario_id = _snake(str(item.get("id") or name))
                else:
                    name = str(item).strip()
                    description = name
                    scenario_id = _snake(name)
                if not name:
                    continue
                scenario_paths.append({
                    "id": scenario_id or f"scenario_{idx:02d}",
                    "name": name,
                    "description": description,
                    "required": True if not isinstance(item, dict) else bool(item.get("required", True)),
                })

        if extracted:
            scenario_paths = extracted
        if not scenario_paths:
            scenario_paths = deepcopy(DEFAULT_SCENARIO_PATHS)

        flags = {key: bool(scenario.get(key, default)) for key, default in SCENARIO_FLAGS.items()}
        flags["scenarios"] = self._dedupe_scenario_paths(scenario_paths)
        return flags

    def _dedupe_scenario_paths(self, scenarios: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out = []
        seen = set()
        for idx, item in enumerate(scenarios, start=1):
            scenario_id = _snake(str(item.get("id") or item.get("name") or f"scenario_{idx:02d}"))
            if scenario_id in seen:
                scenario_id = f"{scenario_id}_{idx:02d}"
            seen.add(scenario_id)
            out.append({
                "id": scenario_id,
                "name": str(item.get("name") or scenario_id).strip(),
                "description": str(item.get("description") or item.get("name") or scenario_id).strip(),
                "required": bool(item.get("required", True)),
            })
            if len(out) >= 12:
                break
        return out

    def _ensure_list(self, value: Any, fallback: List[str]) -> List[str]:
        if not isinstance(value, list) or not value:
            return list(fallback)
        return [str(item) for item in value if str(item).strip()] or list(fallback)

    def _normalize_agent_population(
        self,
        value: Any,
        archetypes: List[Dict[str, Any]],
        combined_text: str,
    ) -> Dict[str, Any]:
        if isinstance(value, dict) and value.get("allocations"):
            population = dict(value)
            population["allocations"] = [
                allocation for allocation in population.get("allocations", [])
                if isinstance(allocation, dict)
                and not _is_non_actor_artifact(str(allocation.get("archetype_name") or allocation.get("name") or ""))
                and not _is_process_role_actor(str(allocation.get("archetype_name") or allocation.get("name") or ""))
            ]
            if not population["allocations"]:
                return self._build_agent_population_plan(combined_text, archetypes)
            population.setdefault("target_agent_count", self._infer_target_agent_count(combined_text, archetypes))
            population["orchestration_agents"] = self._merge_process_agents(population.get("orchestration_agents"), ORCHESTRATION_AGENT_TEMPLATES)
            population["research_agents"] = self._merge_process_agents(population.get("research_agents"), RESEARCH_AGENT_TEMPLATES)
            population.setdefault("allocation_basis", "Planner supplied allocation normalized by Horizon XL.")
            return population
        return self._build_agent_population_plan(combined_text, archetypes)

    def _merge_process_agents(self, supplied: Any, required: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        merged = [dict(item) for item in supplied if isinstance(item, dict)] if isinstance(supplied, list) else []
        names = {str(item.get("name", "")).lower() for item in merged}
        for item in required:
            if item["name"].lower() not in names:
                merged.append(deepcopy(item))
        return merged

    def _build_agent_population_plan(self, combined_text: str, archetypes: List[Dict[str, Any]]) -> Dict[str, Any]:
        target_count = self._infer_target_agent_count(combined_text, archetypes)
        process_count = sum(int(a.get("count", 1)) for a in ORCHESTRATION_AGENT_TEMPLATES + RESEARCH_AGENT_TEMPLATES)
        causal_slots = max(len(archetypes), target_count - process_count)
        shares = self._normalized_population_shares(archetypes, combined_text)
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
                "rationale": f"Allocated by causal importance, information access, and expected behavioral diversity for {archetype.get('name')}.",
                "subtypes": archetype.get("subtypes") or _generic_subtypes(archetype.get("name", "")),
            })
        return {
            "target_agent_count": target_count,
            "causal_agent_count": sum(item["instance_count"] for item in allocations),
            "allocation_basis": "Agent count is inferred from scope, target variables, horizon, context breadth, and behavioral diversity needs.",
            "allocations": allocations,
            "orchestration_agents": deepcopy(ORCHESTRATION_AGENT_TEMPLATES),
            "research_agents": deepcopy(RESEARCH_AGENT_TEMPLATES),
        }

    def _infer_target_agent_count(self, text: str, archetypes: List[Dict[str, Any]]) -> int:
        lowered = (text or "").lower()
        complexity = 0
        complexity += len(re.findall(r"\b(region|segment|group|scenario|variable|monthly|weekly|daily|source|research|numeric|forecast|timeline|uncertainty)\b", lowered))
        complexity += max(0, len(archetypes) - 4)
        if len(text or "") > 5000:
            complexity += 4
        if len(text or "") > 12000:
            complexity += 4
        base = 10
        return max(8, min(40, base + min(18, complexity)))

    def _normalized_population_shares(self, archetypes: List[Dict[str, Any]], text: str) -> List[float]:
        if not archetypes:
            return [1.0]
        explicit = [item.get("population_share") for item in archetypes]
        if all(isinstance(value, (int, float)) and value > 0 for value in explicit):
            total = sum(float(value) for value in explicit) or 1.0
            return [float(value) / total for value in explicit]
        weights = []
        for item in archetypes:
            name = str(item.get("name", "")).lower()
            causal_role = str(item.get("causal_role", "")).lower()
            weight = 1.0
            if any(term in name for term in [
                "cohort", "voter", "participant", "consumer", "worker",
                "household", "community", "beneficiary", "women", "minority",
                "rural", "urban", "youth", "student", "common"
            ]) or "affected people" in causal_role:
                weight = 3.8
            if any(term in name for term in ["data", "analyst", "research", "expert", "scientist"]):
                weight = max(weight, 1.3)
            weights.append(weight)
        total = sum(weights) or 1.0
        return [weight / total for weight in weights]

    def _build_discussion_architecture(self) -> Dict[str, Any]:
        return {
            "moderated": True,
            "loop": [
                "moderator frames the pocket question",
                "research scout and data analyst inject permitted evidence",
                "causal agents update positions and numeric forecasts",
                "negotiation mediator probes disagreement and tradeoffs",
                "quantitative synthesizer produces scenario tables",
                "evidence auditor blocks unsupported claims or flags missing data",
            ],
            "anti_drift_rules": [
                "Every round must reference the target variables or state variables.",
                "Claims needing data must be routed to research/data agents.",
                "The mediator must restate unresolved disagreements before the next pocket.",
                "Report generation is blocked until numeric and evidence validation passes.",
            ],
        }

    def _build_external_research_policy(self, text: str) -> Dict[str, Any]:
        disabled = bool(re.search(r"\b(no|disable|without)\s+(web|search|scrap|external research)\b", text or "", re.IGNORECASE))
        cutoff = self._detect_cutoff_date(text)
        return {
            "enabled": not disabled,
            "outside_graph": True,
            "allowed_inputs": ["user_provided_urls", "uploaded_files", "approved_search_or_scraping_tool_results"],
            "injection_point": "before each debate pocket and before numeric synthesis",
            "cutoff_date": cutoff,
            "requirements": [
                "Research pointers must include source, date, extracted claim, numeric values when present, and confidence/caveat.",
                "Debating agents must explicitly say whether each new pointer changes their forecast.",
                "External research does not overwrite graph memory unless explicitly saved as evidence.",
                "If a cutoff date exists, sources after that date must be flagged as leakage risks.",
            ],
        }

    def _detect_cutoff_date(self, text: str) -> Optional[str]:
        patterns = [
            r"(?:available|known|data|information)\s+(?:only\s+)?(?:up\s+)?to\s+([A-Za-z]{3,9}\s+\d{1,2},\s+\d{4}|[A-Za-z]{3,9}\s+\d{4}|\d{4}-\d{2}-\d{2}|\d{4})",
            r"(?:as\s+of|through|before)\s+([A-Za-z]{3,9}\s+\d{1,2},\s+\d{4}|[A-Za-z]{3,9}\s+\d{4}|\d{4}-\d{2}-\d{2}|\d{4})",
            r"(?:cutoff|cut-off)\s*(?:date)?\s*[:=]?\s*([A-Za-z]{3,9}\s+\d{1,2},\s+\d{4}|[A-Za-z]{3,9}\s+\d{4}|\d{4}-\d{2}-\d{2}|\d{4})",
        ]
        for pattern in patterns:
            match = re.search(pattern, text or "", flags=re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return None
