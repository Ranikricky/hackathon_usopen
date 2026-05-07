"""
Context-derived simulation planning.

Horizon XL should not carry hidden rosters for any specific domain. This
planner asks the LLM to infer the domain label, target variables, agent
archetypes, evidence needs, time pockets, and population allocation from the
prompt, uploaded context, and external research packet. If the LLM is
unavailable, the deterministic fallback is still generic and actor-derived.
"""

import re
from copy import deepcopy
from typing import Any, Dict, List, Optional

from ..utils.llm_client import LLMClient
from ..utils.logger import get_logger

logger = get_logger("horizonxl.services.domain_simulation_planner")


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
    "actor", "agent", "analyst", "auditor", "authority", "buyer", "campaign",
    "candidate", "citizen", "community", "company", "competitor", "consumer",
    "developer", "executive", "expert", "firm", "government", "group",
    "household", "institution", "investor", "journalist", "leader", "maker",
    "media", "mediator", "ministry", "observer", "official", "operator",
    "negotiator",
    "organization", "participant", "party", "people", "platform", "platforms",
    "pollster", "producer", "provider", "providers", "owner", "owners",
    "landlord", "landlords", "influencer", "influencers", "bank", "banks",
    "agency", "agencies", "association", "associations", "lab", "labs",
    "court", "courts", "council", "councils", "regulator", "reporter",
    "researcher", "scientist", "segment", "strategist", "supplier", "trader",
    "union", "user", "voter", "watchdog", "worker",
}


def _snake(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", value or "").strip("_").lower()
    return cleaned or "target_variable"


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


def _split_items(text: str) -> List[str]:
    pieces = re.split(r",|;|\n|\band\b", text or "", flags=re.IGNORECASE)
    out = []
    for piece in pieces:
        cleaned = re.sub(r"^[\s\-*•\d.)]+", "", piece)
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
    items: List[str] = []
    for line in block.splitlines():
        cleaned = line.strip()
        numbered = re.match(r"^(?:[-*•]|\d{1,3}[.)])\s*(.+?)\s*$", cleaned)
        if not numbered:
            continue
        value = re.sub(r"\s+", " ", numbered.group(1)).strip(" .,:;-")
        if 2 <= len(value) <= 240:
            items.append(value)
        if len(items) >= limit:
            break
    return items


def _extract_explicit_agent_items(text: str) -> List[str]:
    return _extract_numbered_items_after_heading(
        text,
        r"(?:create|generate|use|define)\s*(?:\d{1,3}\s+)?(?:[a-z ]+)?agents?\s*(?:as previously defined)?\s*[:\n]",
        r"(?:\n\s*(?:For every agent|Target Variables|Forecast these|Run the simulation|Time[- ]?Pocket|Scenario Paths|Run four scenarios|Required output|Rules)\b|\n\s*\d+\.\s*(?:Target Variables|Time[- ]?Pocket|Scenario Paths|Required output|Rules)\b)",
        limit=80,
    )


def _extract_explicit_target_items(text: str) -> List[str]:
    bullet_targets = []
    collecting = False
    for line in (text or "").splitlines():
        if re.search(r"(?:the simulation must forecast|must forecast)\s*:\s*$", line, flags=re.IGNORECASE):
            collecting = True
            continue
        if not collecting:
            continue
        match = re.match(r"^\s*[-*•]\s*(.+?)\s*$", line)
        if match:
            bullet_targets.append(match.group(1).strip(" .,:;-"))
            continue
        if bullet_targets and line.strip():
            break

    items = _extract_numbered_items_after_heading(
        text,
        r"(?:forecast these numeric variables|target variables|required numeric variables)\s*[:\n]",
        r"(?:\n\s*(?:Create\s+\d{1,3}\s+.*agents|For every agent|Run the simulation|Time[- ]?Pocket|Scenario Paths|Run four scenarios|Required output|Rules)\b|\n\s*\d+\.\s*(?:Agent Architecture|Time[- ]?Pocket|Scenario Paths|Required output|Rules)\b)",
        limit=100,
    )
    merged = []
    seen = set()
    for item in bullet_targets + items:
        key = item.lower()
        if key not in seen:
            merged.append(item)
            seen.add(key)
    return merged


def _extract_time_pocket_items(text: str) -> List[str]:
    return _extract_numbered_items_after_heading(
        text,
        r"(?:run the simulation in sequential time pockets|time[- ]?pocket simulation plan|simulate sequentially|time[- ]?pockets?)\s*[:\n]",
        r"(?:\n\s*(?:Run four scenarios|Scenario Paths|Required output|Rules)\b|\n\s*\d+\.\s*(?:Scenario Paths|Required output|Rules)\b|$)",
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


def _extract_target_variables(text: str) -> List[Dict[str, Any]]:
    source_text = (text or "").replace("U.S.", "US").replace("U.K.", "UK")
    sections = []
    explicit_targets = _extract_explicit_target_items(source_text)
    target_section = re.search(
        r"target variables?\s*(?:[:=-]|\n)(.*?)(?:agent|scenario|time[- ]?pocket|final|$)",
        source_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if target_section:
        sections.append(target_section.group(1))
    for match in re.finditer(r"(?:forecast|predict|estimate|simulate)\s+([^.;\n]+)", source_text, flags=re.IGNORECASE):
        sections.append(match.group(1))
    items = []
    seen = set()
    for raw in explicit_targets:
        for target in _expand_target_item(raw):
            name = target["name"]
            if name in seen or name in {"the", "future", "scenario"}:
                continue
            seen.add(name)
            items.append(target)

    max_targets = 40 if len(explicit_targets) > 12 else 18
    if explicit_targets:
        return items or [{
            "name": "primary_outcome",
            "unit": "index",
            "required": True,
            "description": "Primary simulated outcome requested by the user.",
        }]

    for section in sections:
        cleaned_section = _clean_target_section(section)
        for raw in _split_items(cleaned_section):
            lowered = raw.lower()
            if any(stop in lowered for stop in ["using only", "information available", "based on", "different agents"]):
                continue
            if len(raw.split()) > 6:
                continue
            name = _snake(raw)
            if name in seen or name in {"the", "future", "scenario"}:
                continue
            seen.add(name)
            items.append({
                "name": name,
                "unit": _infer_unit(lowered),
                "required": True,
                "description": f"Prompt-derived target variable: {raw}.",
            })
            if len(items) >= max_targets:
                break
    return items or [{
        "name": "primary_outcome",
        "unit": "index",
        "required": True,
        "description": "Primary simulated outcome requested by the user.",
    }]


def _expand_target_item(raw: str) -> List[Dict[str, Any]]:
    """Split compact prompt targets into atomic numeric variables.

    This stays domain-generic: it looks for reusable output concepts such as
    share, seats, turnout, probability, index, and count rather than hardcoded
    parties, countries, sectors, or topics.
    """
    text = re.sub(r"\s+", " ", raw or "").strip(" .,:;-")
    lowered = text.lower()
    if not text:
        return []

    target_terms = [
        ("vote_share", "percent", r"\bvote\s+share\b"),
        ("seat_share", "percent", r"\bseat\s+share\b"),
        ("seats", "count", r"\bseats?\b"),
        ("turnout", "percent", r"\bturnout\b"),
        ("probability", "percent", r"\bprobability\b|\bchance\b|\bwin\s+probability\b"),
        ("index", "index", r"\bindex\b"),
        ("rate", "percent", r"\brate\b"),
        ("share", "percent", r"\bshare\b"),
        ("count", "count", r"\bcount\b|\bnumber\b"),
    ]
    matches = [(suffix, unit) for suffix, unit, pattern in target_terms if re.search(pattern, lowered)]
    match_suffixes = {suffix for suffix, _ in matches}
    if "vote_share" in match_suffixes or "seat_share" in match_suffixes:
        matches = [(suffix, unit) for suffix, unit in matches if suffix != "share"]

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
    cleaned = re.split(
        r"\b(?:using|based on|considering|with help from|while considering|taking into account|driven by|affected by|impacted by|because of|given)\b",
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
    if any(term in text for term in ["share", "rate", "turnout", "percentage", "probability", "chance"]):
        return "percent"
    if any(term in text for term in ["price", "cost", "revenue", "sales", "capex", "value"]):
        return "currency_or_index"
    if any(term in text for term in ["seat", "count", "number", "volume"]):
        return "count"
    return "index"


def _extract_agent_archetypes(text: str, target_variables: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    explicit_agents = _extract_explicit_agent_items(text)
    explicit_sections = []
    for pattern in [
        r"agent architecture.*?(?:target variables|time[- ]?pocket|scenario paths|data tables|final|$)",
        r"agents include.*?(?:\.|\n\n|$)",
        r"agents?\s*(?:[:=-]|\n)(.*?)(?:target variables|time[- ]?pocket|scenario paths|data tables|final|$)",
    ]:
        match = re.search(pattern, text or "", flags=re.IGNORECASE | re.DOTALL)
        if match:
            explicit_sections.append(match.group(0))

    candidates = list(explicit_agents)
    if not candidates:
        for section in explicit_sections:
            candidates.extend(_split_items(section))
    if not candidates:
        candidates.extend(_actor_candidates_from_context(text))

    archetypes = []
    seen = set()
    max_archetypes = max(12, min(40, len(candidates) if explicit_agents else 20))
    for raw in candidates:
        if not _looks_actorish(raw):
            continue
        name = _title_role(raw)
        if name.lower() in seen:
            continue
        seen.add(name.lower())
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
        "strategist", "negotiator", "journalist", "media", "pollster",
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
    return f"{base} Cohort"


def _should_output_numbers(name: str) -> bool:
    """Only numeric-capable roles should own forecast paths."""
    lowered = (name or "").lower()
    non_numeric_terms = [
        "voter", "consumer", "worker", "household", "beneficiary", "community",
        "cohort", "common", "public", "people", "rural poor", "urban middle",
        "youth", "student", "patient", "citizen", "resident", "observer",
        "booth-level worker", "field worker", "minority", "women", "middle-class",
        "middle class", "rural", "urban", "poor", "grassroots", "booth",
        "journalist", "media", "narrative", "watchdog", "governance",
    ]
    numeric_terms = [
        "quant", "data", "pollster", "scientist", "economist", "researcher",
        "forecaster", "model", "statistic", "auditor", "synthesizer", "retrieval",
        "numeric", "survey", "polling", "measurement",
    ]
    if any(term in lowered for term in non_numeric_terms):
        return False
    if any(term in lowered for term in ["strategist", "negotiator", "campaign", "party", "executive", "operator"]):
        return False
    if any(term in lowered for term in numeric_terms):
        return True
    return True


def _actor_candidates_from_context(text: str) -> List[str]:
    candidates = []
    source_text = (text or "").replace("U.S.", "US").replace("U.K.", "UK")
    for pattern in [
        r"(?:considering|using|involving|include|includes|including|with)\s+([^.;\n]+)",
        r"(?:driven by|impacted by|affected by)\s+([^.;\n]+)",
    ]:
        for match in re.finditer(pattern, source_text, flags=re.IGNORECASE):
            for item in _split_items(match.group(1)):
                if 2 <= len(item) <= 70 and len(item.split()) <= 5:
                    candidates.append(item if _looks_actorish(item) else f"{item} actor")
    for item in _split_items(source_text):
        if _looks_actorish(item):
            candidates.append(item)
    for match in re.finditer(r"\b([A-Z][A-Za-z0-9&.'-]*(?:\s+[A-Z][A-Za-z0-9&.'-]*){0,4}|[A-Z]{2,8})\b", source_text):
        phrase = match.group(1)
        start = max(0, match.start() - 80)
        end = min(len(source_text), match.end() + 80)
        window = source_text[start:end].lower()
        if any(word in window for word in ACTOR_HINT_WORDS):
            candidates.append(phrase)
    return candidates


def _looks_actorish(value: str) -> bool:
    lowered = (value or "").lower()
    if not lowered or any(stop in lowered for stop in ["target variable", "scenario", "data table", "copy paste", "simulation question"]):
        return False
    words = set(re.findall(r"[a-z0-9]+", lowered))
    return bool(words & ACTOR_HINT_WORDS) or bool(re.fullmatch(r"[A-Z]{3,8}", value.strip()))


def _title_role(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9&/ _.'-]+", " ", value or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    words = cleaned.split()
    if len(words) > 6:
        cleaned = " ".join(words[:6])
    return cleaned.title() if not re.fullmatch(r"[A-Z]{2,8}", cleaned) else cleaned


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

    def plan(
        self,
        user_question: str,
        document_text: str = "",
        project_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        user_question = (user_question or "").strip()
        if not user_question:
            raise ValueError("A user question or simulation requirement is required.")

        combined = f"{user_question}\n{document_text or ''}"
        try:
            if self.llm_client is None:
                self.llm_client = LLMClient()
            raw = self._plan_with_llm(user_question, document_text)
            plan = self._normalize_plan(raw, combined)
        except Exception as exc:
            logger.warning(f"Planner LLM failed, using context-derived fallback: {exc}")
            plan = self._fallback_plan(combined)

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

    def _fallback_plan(self, combined_text: str) -> Dict[str, Any]:
        targets = _extract_target_variables(combined_text)
        archetypes = _extract_agent_archetypes(combined_text, targets)
        raw = {
            "domain": _context_label(combined_text),
            "user_question": combined_text.split("\n", 1)[0],
            "target_variables": targets,
            "forecast_horizon": self._infer_horizon(combined_text),
            "required_agent_archetypes": archetypes,
            "state_variables": self._infer_state_variables(combined_text, targets),
            "scenario_structure": deepcopy(SCENARIOS),
            "required_outputs": list(DEFAULT_REQUIRED_OUTPUTS),
            "validation_requirements": list(DEFAULT_VALIDATION_REQUIREMENTS),
        }
        return self._normalize_plan(raw, combined_text)

    def _normalize_plan(self, raw: Dict[str, Any], combined_text: str) -> Dict[str, Any]:
        plan = raw if isinstance(raw, dict) else {}
        extracted_targets = _extract_target_variables(combined_text)
        targets = self._merge_target_variables(
            self._normalize_target_variables(plan.get("target_variables") or extracted_targets),
            extracted_targets,
        )
        extracted_archetypes = _extract_agent_archetypes(combined_text, targets)
        archetypes = self._merge_context_agents(
            self._normalize_agents(plan.get("required_agent_archetypes") or extracted_archetypes),
            extracted_archetypes,
        )
        normalized = {
            "domain": str(plan.get("domain") or _context_label(combined_text) or "custom simulation"),
            "user_question": str(plan.get("user_question") or combined_text.split("\n", 1)[0]),
            "target_variables": targets,
            "forecast_horizon": self._normalize_horizon(plan.get("forecast_horizon") or self._infer_horizon(combined_text)),
            "required_agent_archetypes": archetypes,
            "state_variables": self._normalize_state_variables(plan.get("state_variables") or self._infer_state_variables(combined_text, targets)),
            "scenario_structure": self._normalize_scenarios(plan.get("scenario_structure"), combined_text),
            "required_outputs": self._ensure_list(plan.get("required_outputs"), DEFAULT_REQUIRED_OUTPUTS),
            "validation_requirements": self._ensure_list(plan.get("validation_requirements"), DEFAULT_VALIDATION_REQUIREMENTS),
        }
        normalized["time_pockets"] = self._normalize_time_pockets(
            plan.get("time_pockets") or _extract_time_pocket_items(combined_text),
            normalized["forecast_horizon"],
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
        for word in ["daily", "weekly", "monthly", "quarterly", "yearly"]:
            if word in lowered:
                granularity = word
                break
        match = re.search(r"(?:from|between)\s+([A-Za-z0-9 ,/-]+?)\s+(?:to|through|-)\s+([A-Za-z0-9 ,/-]+)", text or "", re.IGNORECASE)
        return {
            "start": match.group(1).strip() if match else "auto",
            "end": match.group(2).strip() if match else "auto",
            "granularity": granularity,
        }

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
        merged = list(primary or [])
        seen = {str(item.get("name", "")).lower() for item in merged}
        for item in extracted or []:
            name = str(item.get("name", "")).lower()
            if not name or name in seen:
                continue
            merged.append(item)
            seen.add(name)
            if len(merged) >= 40:
                break
        return merged or extracted

    def _normalize_horizon(self, value: Any) -> Dict[str, Any]:
        horizon = value if isinstance(value, dict) else {}
        granularity = str(horizon.get("granularity") or "event_triggered")
        if granularity not in {"daily", "weekly", "monthly", "quarterly", "yearly", "event_triggered"}:
            granularity = "event_triggered"
        return {
            "start": str(horizon.get("start") or "auto"),
            "end": str(horizon.get("end") or "auto"),
            "granularity": granularity,
        }

    def _normalize_time_pockets(self, value: Any, horizon: Dict[str, Any]) -> List[Dict[str, Any]]:
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
        return [{
            "pocket_id": "pocket_001",
            "label": f"{horizon.get('granularity', 'event_triggered')} simulation pocket",
            "start": horizon.get("start") or "auto",
            "end": horizon.get("end") or "auto",
            "events": [],
        }]

    def _normalize_agents(self, values: Any) -> List[Dict[str, Any]]:
        out = []
        for item in values if isinstance(values, list) else []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "Context Actor").strip()
            out.append({
                "name": name,
                "causal_role": str(item.get("causal_role") or "Influences the requested outcome."),
                "information_advantage": str(item.get("information_advantage") or "Has context-relevant information."),
                "likely_bias": str(item.get("likely_bias") or "May overweight its own incentives or information set."),
                "population_share": item.get("population_share"),
                "instance_count": item.get("instance_count"),
                "subtypes": self._ensure_list(item.get("subtypes"), _generic_subtypes(name)),
                "numeric_output_required": bool(
                    item["numeric_output_required"]
                    if "numeric_output_required" in item
                    else _should_output_numbers(name)
                ),
            })
        return out or _extract_agent_archetypes("", [{"name": "primary_outcome"}])

    def _merge_context_agents(
        self,
        primary: List[Dict[str, Any]],
        extracted: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        merged = list(primary or [])
        seen = {str(item.get("name", "")).lower() for item in merged}
        for item in extracted or []:
            name = str(item.get("name", "")).lower()
            if not name or name in seen:
                continue
            merged.append(item)
            seen.add(name)
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
            if "cohort" in name or "affected people" in causal_role:
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
