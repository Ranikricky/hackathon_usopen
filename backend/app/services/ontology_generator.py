"""
Ontology generation service.

This module deliberately avoids domain-specific fallback rosters. If the LLM is
unavailable or returns weak output, the fallback derives actors from the prompt,
uploaded context, and research packet. Domain-specific names such as parties,
companies, leaders, agencies, commodities, products, or movements may appear only
when they are present in that input context.
"""

import logging
import random
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from typing import Any, Dict, List, Optional, Tuple

from ..config import Config
from ..utils.llm_client import LLMClient
from ..utils.locale import get_language_instruction

logger = logging.getLogger(__name__)


CONTROL_ENTITY_TYPES = [
    {
        "name": "SimulationModerator",
        "description": "Neutral host keeping debate focused and sequenced.",
        "examples": ["moderator", "debate host"],
    },
    {
        "name": "ExternalResearchScout",
        "description": "Finds approved external source pointers outside graph memory.",
        "examples": ["research scout", "source finder"],
    },
    {
        "name": "EvidenceAuditor",
        "description": "Checks claims, dates, source quality, and leakage risk.",
        "examples": ["fact checker", "source auditor"],
    },
    {
        "name": "QuantitativeSynthesizer",
        "description": "Turns debated claims into numeric scenario paths.",
        "examples": ["scenario quant", "forecast modeler"],
    },
    {
        "name": "NegotiationMediator",
        "description": "Surfaces disagreements and explores compromise or bargaining.",
        "examples": ["mediator", "negotiator"],
    },
]


CONTROL_EDGE_TYPES: List[Tuple[str, str, str, str]] = [
    ("MODERATES_DISCUSSION", "Keeps simulation rounds focused on the prompt.", "SimulationModerator", "NegotiationMediator"),
    ("FINDS_EXTERNAL_EVIDENCE", "Finds source pointers for the next debate pocket.", "ExternalResearchScout", "EvidenceAuditor"),
    ("AUDITS_EVIDENCE", "Checks whether claims are supported and timely.", "EvidenceAuditor", "QuantitativeSynthesizer"),
    ("SYNTHESIZES_FORECASTS", "Converts validated signals into numeric scenarios.", "QuantitativeSynthesizer", "SimulationModerator"),
    ("MEDIATES_DISAGREEMENT", "Organizes contested assumptions and bargaining positions.", "NegotiationMediator", "SimulationModerator"),
    ("CHALLENGES_ASSUMPTION", "Challenges weak causal or numeric assumptions.", "EvidenceAuditor", "SimulationModerator"),
    ("RETRIEVES_NUMERIC_EVIDENCE", "Retrieves numbers for quantitative synthesis.", "ExternalResearchScout", "QuantitativeSynthesizer"),
    ("VALIDATES_NUMERIC_CLAIMS", "Checks units, dates, and forecast plausibility.", "EvidenceAuditor", "QuantitativeSynthesizer"),
]


GENERIC_FALLBACK_ACTORS: List[Tuple[str, str]] = [
    ("PrimaryDecisionMaker", "Actor with direct authority over choices or resources."),
    ("AffectedParticipantGroup", "People or groups experiencing ground-level effects."),
    ("ResourceController", "Actor controlling money, supply, access, capacity, or timing."),
    ("DomainExpertAnalyst", "Expert interpreting evidence and producing assumptions."),
    ("GroundSignalReporter", "Observer reporting local, operational, public, or field signals."),
    ("CounterpartyActor", "Actor creating opposition, competition, friction, or alternatives."),
    ("DataProvider", "Actor supplying numbers, records, measurements, or source material."),
    ("NarrativeAmplifier", "Actor shaping attention, framing, sentiment, or legitimacy."),
]


GENERIC_EDGE_TYPES: List[Tuple[str, str, str, str]] = [
    ("INFLUENCES", "Influences another actor's behavior or expectations.", "Organization", "Person"),
    ("CONTESTS_WITH", "Competes, disagrees, or creates counter-pressure.", "Organization", "Organization"),
    ("NEGOTIATES_WITH", "Negotiates terms, coordination, alliances, or tradeoffs.", "Person", "Organization"),
    ("REPORTS_SIGNAL", "Reports observed evidence, sentiment, or field signals.", "Person", "Organization"),
    ("PROVIDES_DATA", "Supplies numeric evidence, records, or source material.", "Organization", "QuantitativeSynthesizer"),
    ("REPRESENTS_INTERESTS", "Represents lived experience or group interests.", "Person", "Organization"),
    ("REACTS_TO", "Revises behavior after another actor's signal.", "Organization", "Organization"),
    ("AMPLIFIES", "Amplifies information, narrative, sentiment, or attention.", "Organization", "Person"),
]


ACTOR_WORDS = {
    "actor", "actors", "agent", "agents", "analyst", "analysts", "auditor", "auditors",
    "authority", "authorities", "buyer", "buyers", "campaign", "campaigns", "candidate",
    "candidates", "citizen", "citizens", "community", "communities", "company", "companies",
    "competitor", "competitors", "consumer", "consumers", "developer", "developers",
    "executive", "executives", "expert", "experts", "firm", "firms", "government",
    "governments", "group", "groups", "household", "households", "institution",
    "institutions", "investor", "investors", "journalist", "journalists", "leader",
    "leaders", "maker", "makers", "media", "mediator", "mediators", "ministry",
    "observer", "observers", "official", "officials", "operator", "operators",
    "organizer", "organizers", "bloc", "blocs", "block", "blocks",
    "organization", "organizations", "participant", "participants", "party", "parties",
    "people", "platform", "platforms", "pollster", "pollsters", "producer", "producers",
    "provider", "providers", "owner", "owners", "landlord", "landlords", "influencer",
    "influencers", "bank", "banks", "agency", "agencies", "association", "associations",
    "lab", "labs", "court", "courts", "council", "councils", "regulator", "regulators",
    "reporter", "reporters", "researcher", "researchers", "scientist", "scientists",
    "segment", "segments", "strategist", "strategists", "supplier", "suppliers",
    "trader", "traders", "union", "unions", "user", "users", "voter", "voters",
    "watchdog", "watchdogs", "worker", "workers",
    "miner", "miners", "refiner", "refiners", "manufacturer", "manufacturers",
    "automaker", "automakers", "logistics",
}


NON_ACTOR_PHRASES = {
    "input pack", "simulation question", "historical baseline", "current context",
    "target variables", "scenario paths", "data tables", "final prompt", "copy paste",
    "date", "section", "expected", "forecast horizon", "time pocket", "time pockets",
    "boom baseline", "current state snapshot", "scenario synthesis", "response pocket",
    "shock pocket", "baseline pocket", "inventory correction",
    "markdown format", "full context", "research packet", "source notes",
    "discovery queries", "generated at", "external web result", "no readable excerpt",
    "landlines", "sms-to-web", "source discovered",
    "agent disagreement", "then explain agent disagreement",
    "output numeric forecasts", "numeric forecasts first",
    "historical", "required", "create", "using", "available", "forecast",
    "make a concrete claim", "cite book canon evidence", "cite book-canon evidence",
    "cite book canon evidence or foreshadowing", "cite book-canon evidence or foreshadowing",
    "challenge another agent", "explain what would change their view",
    "separate fact inference and speculation", "avoid show canon leakage",
    "agents must not just announce their roles", "must not just announce their roles",
}

PROCESS_OR_PARAMETER_ROLE_PHRASES = {
    "simulation moderator", "moderator", "negotiation mediator",
    "evidence auditor", "evidence auditors", "external research scout",
    "research scout", "data retrieval analyst", "quantitative synthesizer",
    "scenario synthesizer", "scenario integrator", "quant scenario", "quant / scenario",
    "forecast horizon", "target variable", "state variable", "scenario path",
    "time pocket", "pocket", "validation", "required output",
}

NON_ACTOR_UNIT_OR_METRIC_WORDS = {
    "usd", "lfp", "kwh", "price", "prices", "pricing", "rate", "rates",
    "growth", "margin", "pressure", "index", "probability", "forecast",
    "forecasts", "path", "paths", "scenario", "scenarios", "baseline",
    "snapshot", "pocket", "synthesis", "inventory", "cover", "spodumene",
    "carbonate", "concentrate", "cost", "costs", "metric", "ton", "tons",
    "tonne", "tonnes", "balance", "premium", "deficit", "oversupply",
    "historical", "required", "create", "the", "at", "development", "design",
    "performance", "safety", "technology", "traceability", "industrialization",
}

NON_ACTOR_SENTENCE_WORDS = {
    "faced", "faces", "face", "enabled", "enables", "enable", "created",
    "creates", "create", "drove", "drives", "drive", "reduced", "reduces",
    "increase", "increases", "increased", "decrease", "decreases", "decreased",
    "balanced", "balances", "balance", "against", "from", "through", "because",
    "while", "after", "before", "during",
}


RESEARCH_PACKET_MARKERS = [
    "=== EXTERNAL_RESEARCH_PACKET ===",
    "# External Research Packet",
    "## Source Notes",
    "## Discovery Queries",
]


def _to_pascal_case(name: str) -> str:
    """Convert arbitrary text to PascalCase."""
    name = re.sub(r"^\s*(?:a|an|the)\s+", "", name or "", flags=re.IGNORECASE)
    parts = re.split(r"[^a-zA-Z0-9]+", name)
    words: List[str] = []
    for part in parts:
        split = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", part)
        split = re.sub(r"([a-z])([A-Z])", r"\1_\2", split)
        words.extend(split.split("_"))
    formatted = []
    for word in words:
        if not word:
            continue
        formatted.append(word.upper() if re.fullmatch(r"[A-Z0-9]{2,8}", word) else word.capitalize())
    result = "".join(formatted)
    return result or "Unknown"


def _space_pascal_name(name: str) -> str:
    spaced = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", name or "")
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", spaced)
    return spaced.lower()


def _safe_description(text: str, fallback: str) -> str:
    cleaned = re.sub(r"\s+", " ", text or "").strip()
    return (cleaned or fallback)[:97] + ("..." if len(cleaned or fallback) > 100 else "")


def _context_label(text: str) -> str:
    """Create a short run label from the prompt without mapping to fixed domains."""
    cleaned = re.sub(r"https?://\S+", " ", text or "")
    cleaned = re.sub(r"[^a-zA-Z0-9 %/_-]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    for pattern in [
        r"(?:simulate|forecast|predict|analyze|model)\s+(.{8,120})",
        r"(?:using only information available up to [^,.;]+,?\s*)?(.{8,120})",
    ]:
        match = re.search(pattern, cleaned, flags=re.IGNORECASE)
        if match:
            phrase = match.group(1).strip()
            phrase = re.split(r"\b(?:based on|using|with|from|do not|produce|output)\b", phrase, maxsplit=1, flags=re.IGNORECASE)[0]
            return " ".join(phrase.split()[:8]).strip() or "custom simulation"
    return " ".join(cleaned.split()[:8]).strip() or "custom simulation"


def _substantive_context_for_domain_checks(text: str) -> str:
    """Remove forbidden examples and report/validation instructions before domain checks."""
    cleaned = text or ""
    cleaned = re.split(
        r"\n\s*(?:=+\s*)?(?:VALIDATION REQUIREMENTS|REQUIRED REPORT FORMAT|STYLE REQUIREMENTS)\b",
        cleaned,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    cleaned = re.sub(
        r"\n\s*(?:do\s+not\s+use|do\s+not\s+include|avoid|forbidden|must\s+not\s+use)\s*:\s*(?:\n\s*[-*•]\s*.+?)+(?=\n\s*\n|\n\s*=+|\Z)",
        "\n",
        cleaned,
        flags=re.IGNORECASE,
    )
    return cleaned


def _is_election_context(text: str) -> bool:
    lowered = _substantive_context_for_domain_checks(text).lower()
    return bool(re.search(
        r"\b(election|polling|vote\s+share|seat\s+share|turnout|voters?|candidate|candidates|ballot|hung\s+assembly|majority\s+mark)\b",
        lowered,
    ))


def _is_narrative_context(text: str) -> bool:
    lowered = _substantive_context_for_domain_checks(text).lower()
    return bool(re.search(
        r"\b(novel|book[- ]canon|canon|fiction|story|storyline|characters?|chapter|unpublished|"
        r"song\s+of\s+ice\s+and\s+fire|asoiaf|winds\s+of\s+winter|foreshadowing|prophecy|throne|thrones?)\b",
        lowered,
    ))


def _is_instruction_like_actor_name(name: str, description: str = "") -> bool:
    """Reject imperative prompt instructions that were accidentally parsed as actors."""
    spaced = _space_pascal_name(name or "")
    combined = f"{spaced} {description or ''}".lower()
    imperative_patterns = [
        r"^(?:make|cite|challenge|explain|separate|avoid|do|produce|output|write|generate|include|clearly)\b",
        r"\bagents?\s+must\b",
        r"\bmust\s+not\s+just\s+announce\b",
        r"\bconcrete\s+claim\b",
        r"\bbook[- ]canon\s+evidence\b",
        r"\bwhat\s+would\s+change\s+their\s+view\b",
        r"\bfact\s+inference\s+and\s+speculation\b",
        r"\bshow\s+canon\s+leakage\b",
    ]
    return any(re.search(pattern, combined, flags=re.IGNORECASE) for pattern in imperative_patterns)


def _extract_numbered_agent_list(text: str, limit: int = 40) -> List[Tuple[str, str]]:
    """Extract agents from sections like "Create 14 agents: 1. ... 2. ..."."""
    if not text:
        return []

    normalized = text.replace("—", "-").replace("–", "-")
    section_match = re.search(
        r"(?:create|generate|use|define)\s+(?:\d+\s+)?(?:[\w -]+\s+)?agents?\s*:?\s*(.+?)(?:\n\s*(?:agents?\s+must|agents?\s+should|each\s+agent|for every agent|agent behavior rules?|run sequential|run the simulation|required time[- ]?pockets?|time[- ]?pockets?|debate format|validation requirements|required report format|style requirements|run four scenarios|required output|rules)\b|$)",
        normalized,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not section_match:
        return []

    section = section_match.group(1)
    negative = re.search(
        r"\n\s*(?:do\s+not\s+use|do\s+not\s+include|avoid|forbidden|must\s+not\s+use)\b\s*[:\n]",
        section,
        flags=re.IGNORECASE,
    )
    if negative:
        section = section[:negative.start()]
    items: List[Tuple[str, str]] = []
    for line in section.splitlines():
        # Keep one numbered/bulleted line as one actor. The previous DOTALL
        # regex swallowed intervening category headings, producing fused names
        # like "NuclearExpertDiplomaticStateActors".
        match = re.match(r"^\s*(?:\d+[\.)]|[-*•])\s+(.+?)\s*$", line)
        if not match:
            continue
        raw = re.sub(r"\s+", " ", match.group(1)).strip(" .;:-")
        raw = re.split(r"\b(?:bias|trusted evidence|blind spots|numeric forecast)\b", raw, maxsplit=1, flags=re.IGNORECASE)[0].strip()
        if not raw or len(raw) > 90:
            continue
        if any(phrase in raw.lower() for phrase in PROCESS_OR_PARAMETER_ROLE_PHRASES):
            continue
        name = _to_pascal_case(raw)
        if name and name not in {"Unknown", "Person", "Organization"}:
            items.append((name, f"Explicitly requested simulation agent: {raw}."))
        if len(items) >= limit:
            break
    return _dedupe_agent_tuples(items)


def _dedupe_agent_tuples(agents: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    """Deduplicate agent tuples by normalized entity name while preserving order."""
    seen = set()
    deduped = []
    for name, description in agents:
        normalized = _to_pascal_case(name)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append((normalized, description))
    return deduped


def _extract_explicit_agent_types(text: str, limit: int = 40) -> List[Tuple[str, str]]:
    """Extract roles from explicit agent sections in the prompt/context."""
    if not text:
        return []

    numbered_agents = _extract_numbered_agent_list(text, limit=limit)
    if numbered_agents:
        return numbered_agents

    normalized = text.replace("—", "-").replace("–", "-")
    included_agent_items: List[Tuple[str, str]] = []
    for match in re.finditer(
        r"(?:agents?[^.\n]{0,180}?\b(?:including|from\s+the\s+prompt\s*:)|simulation\s+should\s+consider|should\s+consider|at\s+minimum[^.\n]{0,80}?\bconsider)\s+([^.\n]+)",
        normalized,
        flags=re.IGNORECASE,
    ):
        included_agent_items.extend(_actor_items_from_text(match.group(1), limit=limit))
    if included_agent_items:
        return _dedupe_agent_tuples(included_agent_items)[:limit]

    sections = []
    for pattern in [
        r"agent architecture.*?(?:target variables|time-pocket|scenario paths|data tables|final horizon|$)",
        r"agents include.*?(?:\.|\n\n|$)",
        r"agents?:.*?(?:\.|\n\n|$)",
    ]:
        match = re.search(pattern, normalized, flags=re.IGNORECASE | re.DOTALL)
        if match:
            sections.append(match.group(0))

    if not sections:
        return []

    candidate_text = "\n".join(sections)
    candidate_text = re.sub(r"\bas previously defined\b", "", candidate_text, flags=re.IGNORECASE)
    candidate_text = re.sub(r"agent architecture|agents include|agents?", "", candidate_text, flags=re.IGNORECASE)
    candidate_text = re.sub(r"\bfor\s+horizon\s+xl\s*:?", "", candidate_text, flags=re.IGNORECASE)
    candidate_text = re.sub(r"\b\d+\s*-\s*\d+\b", "", candidate_text)
    if " - " in candidate_text:
        candidate_text = candidate_text.split(" - ", 1)[1]
    return _actor_items_from_text(candidate_text, limit=limit)


def _without_external_research(text: str) -> str:
    """Keep prompt/upload text separate from provisional web research snippets."""
    if not text:
        return ""
    earliest = len(text)
    for marker in RESEARCH_PACKET_MARKERS:
        idx = text.find(marker)
        if idx >= 0:
            earliest = min(earliest, idx)
    return text[:earliest].strip()


def _quality_actor_items(text: str, limit: int = 18) -> List[Tuple[str, str]]:
    """Infer actors, filtering out research artifacts and metric/result fragments."""
    blocked_name_fragments = {
        "result", "results", "landline", "landlines", "sms", "web", "query",
        "source", "snippet", "excerpt", "uncertaintybuild", "analysisactor",
        "simulate", "forecast", "direction", "approach", "online", "background",
        "context", "then explain", "numeric forecasts", "using", "may", "run",
        "output", "assembly", "bengal", "probability",
        "make concrete claim", "cite book canon evidence", "challenge another",
        "explain what would change", "separate fact inference", "avoid show canon",
    }
    items = _actor_items_from_text(text, limit=limit * 2)
    filtered: List[Tuple[str, str]] = []
    for name, description in items:
        spaced = _space_pascal_name(name)
        words = set(re.findall(r"[a-z0-9]+", spaced))
        if len(words) >= 5 and words & NON_ACTOR_SENTENCE_WORDS:
            continue
        if any(fragment in spaced for fragment in blocked_name_fragments):
            continue
        if _is_instruction_like_actor_name(name, description):
            continue
        if any(stop in spaced for stop in NON_ACTOR_PHRASES):
            continue
        if any(phrase in spaced for phrase in PROCESS_OR_PARAMETER_ROLE_PHRASES):
            continue
        if words and words <= NON_ACTOR_UNIT_OR_METRIC_WORDS:
            continue
        filtered.append((name, description))
        if len(filtered) >= limit:
            break
    return _dedupe_agent_tuples(filtered)


def _is_low_quality_entity_name(name: str, description: str = "") -> bool:
    """Reject metric/search-artifact names that are not credible actor classes."""
    spaced = _space_pascal_name(name or "")
    combined = f"{spaced} {description or ''}".lower()
    if _is_instruction_like_actor_name(name, description):
        return True
    blocked_fragments = {
        "landline", "sms", "query", "snippet", "excerpt", "source notes",
        "external research packet", "no readable excerpt", "generated at",
        "background context", "mixed mode", "online 400", "direction actor",
        "simulate a", "forecast vote", "produce numeric", "build relevant",
        "using actor", "uncertainty build", "expert blog analysis",
        "then explain", "agent disagreement", "numeric forecasts",
        "using actor", "may actor", "run actor", "output actor", "assembly actor",
        "bengal actor", "boom baseline", "crash and inventory correction",
        "current state snapshot", "response pocket", "shock pocket",
        "scenario synthesis pocket", "target variable", "required output",
    }
    if any(fragment in combined for fragment in blocked_fragments):
        return True
    words = [word for word in re.findall(r"[a-z0-9]+", spaced) if word]
    if not words:
        return True
    if len(words) >= 5 and set(words) & NON_ACTOR_SENTENCE_WORDS:
        return True
    if any(phrase in spaced for phrase in PROCESS_OR_PARAMETER_ROLE_PHRASES):
        return True
    if set(words) <= NON_ACTOR_UNIT_OR_METRIC_WORDS:
        return True
    if "actor" in words and any(word in NON_ACTOR_UNIT_OR_METRIC_WORDS for word in words) and not any(word in ACTOR_WORDS - {"actor", "actors", "agent", "agents"} for word in words):
        return True
    if "actor" in words and not any(word in ACTOR_WORDS - {"actor", "actors", "agent", "agents"} for word in words):
        return True
    return False


def _actor_items_from_text(text: str, limit: int = 18) -> List[Tuple[str, str]]:
    """Infer actor categories and named actors from arbitrary context text."""
    candidates: List[str] = []
    source_text = (text or "").replace("U.S.", "US").replace("U.K.", "UK")

    # If the user asks for metrics by named stakeholder/group, keep those names
    # as actors too. This is generic: it reads labels from target rows such as
    # "X vote share and seats" or "probability of X majority".
    metric_label_patterns = [
        r"^\s*(?:[-*•]|\d{1,3}[.)])\s*([A-Za-z][A-Za-z0-9&/ .'-]{1,60}?)\s+(?:vote\s+share|seat\s+share|seats?)\b",
        r"\bprobability\s+(?:of\s+)?([A-Za-z][A-Za-z0-9&/ .'-]{1,50}?)\s+(?:majority|win|wins|crosses|exceeds|above|below)\b",
    ]
    for pattern in metric_label_patterns:
        for match in re.finditer(pattern, source_text, flags=re.IGNORECASE | re.MULTILINE):
            label = re.sub(r"\b(?:overall|statewide|regional|minority|women|youth|turnout|hung|assembly)\b", " ", match.group(1), flags=re.IGNORECASE)
            label = re.sub(r"\s+", " ", label).strip(" .,:;-")
            if label.lower().startswith(("probability", "chance")):
                continue
            if label and 2 <= len(label) <= 60:
                candidates.append(label)

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
            for item in re.split(r",|;|\n|\band\b", segment, flags=re.IGNORECASE):
                cleaned = re.sub(r"^[\s\-*•\d.)]+", "", item)
                cleaned = re.sub(r"^\s*(?:a|an|the)\s+", "", cleaned, flags=re.IGNORECASE)
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
                cleaned = re.sub(r"^(?:include|includes|including|considering|with)\s+", "", cleaned, flags=re.IGNORECASE)
                cleaned = re.split(r"\b(?:only if|if supported|when supported)\b", cleaned, maxsplit=1, flags=re.IGNORECASE)[0]
                cleaned = re.sub(r"[^a-zA-Z0-9&/ _.'-]+", " ", cleaned)
                cleaned = re.sub(r"\s+", " ", cleaned).strip(" -_/")
                if 2 <= len(cleaned) <= 70 and len(cleaned.split()) <= 5:
                    candidates.append(cleaned if _looks_like_actor_phrase(cleaned) else f"{cleaned} actor")

    listish = re.split(r",|;|\n|\band\b", source_text, flags=re.IGNORECASE)
    for raw in listish:
        cleaned = re.sub(r"^[\s\-*•\d.)]+", "", raw)
        cleaned = re.sub(r"^\s*(?:a|an|the)\s+", "", cleaned, flags=re.IGNORECASE)
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
        cleaned = re.sub(r"^(?:include|includes|including|considering|with)\s+", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.split(r"\b(?:only if|if supported|when supported)\b", cleaned, maxsplit=1, flags=re.IGNORECASE)[0]
        if ":" in cleaned or "=" in cleaned:
            parts = re.split(r"[:=]", cleaned, maxsplit=1)
            left = parts[0].strip().lower()
            right = parts[1].strip() if len(parts) > 1 else ""
            cleaned = right if left in {"agent", "agents", "agent architecture", "target variables"} and right else parts[0]
        cleaned = re.sub(r"[^a-zA-Z0-9&/ _.'-]+", " ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" -_/")
        if _looks_like_actor_phrase(cleaned):
            candidates.append(cleaned)

    for match in re.finditer(r"\b([A-Z][A-Za-z0-9&.'-]*(?:\s+[A-Z][A-Za-z0-9&.'-]*){0,4})\b", source_text):
        phrase = match.group(1).strip()
        if _looks_like_named_actor(phrase, source_text):
            candidates.append(phrase)

    for match in re.finditer(r"\b([A-Z]{3,8}(?:\([A-Z]+\))?)\b", source_text):
        phrase = match.group(1).strip()
        if _looks_like_named_actor(phrase, source_text):
            candidates.append(phrase)

    lowered_text = source_text.lower()
    cohort_rules = [
        (r"\bwomen\b|\bfemale\b", "Women Voters" if "voter" in lowered_text or "election" in lowered_text else "Women Participants"),
        (r"\bminority\b|\bminorities\b|\bcommunity\b|\bcommunities\b", "Minority Or Community Participants"),
        (r"\byouth\b|\bstudent\b|\bunemployment\b|\bjobs?\b", "Youth Or Employment-Exposed Participants"),
        (r"\brural\b|\bpoor\b|\bwelfare\b|\bbeneficiar", "Rural Or Welfare-Exposed Participants"),
        (r"\burban\b|\bmiddle[- ]class\b|\bcivic\b", "Urban Middle-Class Participants"),
        (r"\bworker\b|\blabor\b|\blabour\b|\bunion\b", "Workers Or Labor Participants"),
        (r"\bconsumer\b|\bdemand\b|\bhousehold\b", "Consumers Or Households"),
        (r"\bbusiness\b|\bindustry\b|\bfirm\b|\bcompany\b", "Business Or Industry Participants"),
        (r"\bregion\b|\bregional\b|\bswing\b", "Regional Signal Observers"),
    ]
    for pattern, label in cohort_rules:
        if re.search(pattern, lowered_text):
            candidates.append(label)

    results: List[Tuple[str, str]] = []
    seen = set()
    for phrase in candidates:
        phrase = re.sub(r"\s+", " ", phrase).strip()
        lowered = phrase.lower()
        if lowered in seen or any(stop in lowered for stop in NON_ACTOR_PHRASES):
            continue
        if any(phrase_stop in lowered for phrase_stop in PROCESS_OR_PARAMETER_ROLE_PHRASES):
            continue
        entity_name = _to_pascal_case(phrase)
        if not entity_name or entity_name in {"Person", "Organization", "Unknown"}:
            continue
        if entity_name in {name for name, _ in results}:
            continue
        seen.add(lowered)
        suffix = "Actor" if not any(word in lowered.split() for word in ACTOR_WORDS) else ""
        if suffix and not entity_name.endswith("Actor"):
            entity_name = f"{entity_name}Actor"
        results.append((entity_name, f"Context-derived actor from input or research: {phrase}."))
        if len(results) >= limit:
            break
    return results


def _looks_like_actor_phrase(phrase: str) -> bool:
    if not phrase or len(phrase) < 3:
        return False
    words = [word.lower().strip(" .'\"") for word in phrase.split()]
    if len(words) > 6:
        return False
    if any(stop in " ".join(words) for stop in NON_ACTOR_PHRASES):
        return False
    if set(words) & NON_ACTOR_SENTENCE_WORDS:
        return False
    if set(words) <= NON_ACTOR_UNIT_OR_METRIC_WORDS:
        return False
    return any(word in ACTOR_WORDS for word in words)


def _looks_like_named_actor(phrase: str, full_text: str) -> bool:
    lowered = phrase.lower()
    if len(phrase) < 2 or any(stop in lowered for stop in NON_ACTOR_PHRASES):
        return False
    if lowered in {"horizon xl", "source notes", "external research packet"}:
        return False
    if lowered in NON_ACTOR_UNIT_OR_METRIC_WORDS:
        return False
    words = re.findall(r"[A-Za-z0-9]+", phrase)
    if len(words) == 1 and words[0].lower() not in ACTOR_WORDS and not re.fullmatch(r"[A-Z]{3,8}(?:\([A-Z]+\))?", phrase):
        return False
    window_pattern = re.escape(phrase)
    match = re.search(window_pattern, full_text or "")
    if not match:
        return False
    start = max(0, match.start() - 80)
    end = min(len(full_text), match.end() + 80)
    window = full_text[start:end].lower()
    if re.fullmatch(r"[A-Z]{3,8}(?:\([A-Z]+\))?", phrase):
        return any(word in window for word in ACTOR_WORDS) or any(term in window for term in ["strength", "risk", "advantage", "response", "discipline", "policy", "supply"])
    return any(word in window for word in ACTOR_WORDS)


def _select_run_agent_types(
    agents: List[Tuple[str, str]],
    generation_seed: Optional[str],
    limit: int = 8,
) -> List[Tuple[str, str]]:
    """Keep central prompt actors but rotate secondary roles per fresh run."""
    if len(agents) <= limit:
        return agents
    def score(agent: Tuple[str, str]) -> int:
        name, description = agent
        spaced = _space_pascal_name(name)
        combined = f"{spaced} {description or ''}".lower()
        value = 0
        if re.search(r"\b[A-Z]{2,8}\b", description or ""):
            value += 8
        for term in [
            "party", "strategist", "voter", "voters", "bloc", "minority", "women",
            "youth", "rural", "urban", "worker", "labor", "labour", "consumer",
            "poll", "data", "research", "quant", "media", "watchdog", "business",
            "industry", "government", "regulator", "candidate", "campaign",
        ]:
            if term in combined:
                value += 2
        if any(fragment in combined for fragment in [" using.", " may.", " run.", " output.", " assembly.", " bengal."]):
            value -= 8
        return value

    ranked = sorted(enumerate(agents), key=lambda pair: (-score(pair[1]), pair[0]))
    anchors = [agent for _, agent in ranked[: max(3, min(8, limit // 2))]]
    anchor_names = {name for name, _ in anchors}
    pool = [agent for agent in agents if agent[0] not in anchor_names]
    rng = random.Random(generation_seed or random.random())
    rng.shuffle(pool)
    return anchors + pool[: max(0, limit - len(anchors))]


def _prompt_scope_score(text: str) -> int:
    """Estimate simulation breadth from the prompt without domain-specific branches."""
    lowered = (text or "").lower()
    line_items = len(re.findall(r"(?:^|\n)\s*(?:\d+[\.)]|[-*•])\s+", text or ""))
    complexity_terms = [
        "region", "regional", "scenario", "scenarios", "agent", "agents",
        "numeric", "forecast", "probability", "confidence", "uncertainty",
        "table", "matrix", "turnout", "vote", "seat", "monthly", "quarterly",
        "daily", "weekly", "source", "research", "web", "debate", "simulate",
        "alliance", "swing", "baseline", "historical", "sensitivity",
    ]
    term_hits = sum(1 for term in complexity_terms if term in lowered)
    return min(40, line_items + term_hits)


def _target_entity_count(explicit_count: int, inferred_count: int, text: str) -> int:
    """Choose ontology size from explicit prompt scope, with generous safety bounds."""
    if explicit_count:
        # Preserve every explicitly requested agent, then add orchestration roles
        # and context-derived named actors without squeezing prompt actors out.
        base = max(explicit_count, inferred_count) + len(CONTROL_ENTITY_TYPES) + 2
    else:
        scope = _prompt_scope_score(text)
        base = max(12, min(30, 12 + scope // 2, inferred_count + len(CONTROL_ENTITY_TYPES) + 2))
    return max(6, min(48, base))


def _target_edge_count(entity_count: int, relationship_count: int, text: str) -> int:
    """Choose relationship density from actor count and prompt scope."""
    scope = _prompt_scope_score(text)
    base = max(12, entity_count + scope // 2, relationship_count)
    return max(8, min(96, base))


def _agent_role(name: str, context_text: str = "") -> str:
    """Classify an agent/entity name into a broad role for relationship design."""
    lowered = re.sub(r"([a-z])([A-Z])", r"\1 \2", name).lower()
    words = set(re.findall(r"[a-z0-9]+", lowered))
    election_like = _is_election_context(context_text)
    narrative_like = _is_narrative_context(context_text)
    if election_like and any(term in words for term in ["strategist", "campaign", "party", "candidate"]):
        return "campaign"
    if narrative_like and any(term in lowered for term in [
        "strategist", "faction", "court", "regime", "military", "naval", "magic",
        "occult", "watch", "company", "militant", "king", "queen", "lord",
        "advisor", "claimant", "dynasty", "prophecy", "dragon", "throne",
    ]):
        return "power_actor"
    if any(term in words for term in ["strategist", "planner", "adviser", "advisor", "diplomat", "hardliner", "security", "military", "government", "official", "council", "operations"]):
        return "power_actor"
    if any(term in words for term in ["voter", "beneficiary", "rural", "urban", "minority", "youth", "worker", "consumer", "public", "household"]):
        return "constituency"
    if any(term in words for term in ["pollster", "data", "quant", "model"]):
        return "data"
    if "data scientist" in lowered:
        return "data"
    if any(term in words for term in ["journalist", "media", "narrative", "influencer"]):
        return "narrative"
    if any(term in words for term in ["observer", "booth", "field", "reporter", "watchdog", "auditor"]):
        return "ground_signal"
    if any(term in words for term in ["business", "industry", "market", "investor", "trader", "producer", "supplier"]):
        return "economic_signal"
    if any(term in words for term in ["negotiator", "mediator", "alliance", "coalition"]):
        return "negotiator"
    if any(term in words for term in ["moderator", "research", "synthesizer"]):
        return "process"
    return "actor"


def _edge(name: str, description: str, source: str, target: str) -> Tuple[str, str, str, str]:
    return (name.upper(), _safe_description(description, description), _to_pascal_case(source), _to_pascal_case(target))


def _relationship_edges_for_agents(agents: List[Tuple[str, str]], context_text: str = "") -> List[Tuple[str, str, str, str]]:
    """Create role-aware relationships from explicit/context-derived agents.

    This stays domain-general: it uses role words from the prompt-derived agent
    names instead of any pre-baked domain roster.
    """
    names = [_to_pascal_case(name) for name, _ in agents]
    election_like = _is_election_context(context_text)
    narrative_like = _is_narrative_context(context_text)
    roles = {name: _agent_role(name, context_text) for name in names}
    campaigns = [name for name, role in roles.items() if role == "campaign"]
    power_actors = [name for name, role in roles.items() if role == "power_actor"]
    constituencies = [name for name, role in roles.items() if role == "constituency"]
    data_agents = [name for name, role in roles.items() if role == "data"]
    narrative_agents = [name for name, role in roles.items() if role == "narrative"]
    ground_agents = [name for name, role in roles.items() if role == "ground_signal"]
    economic_agents = [name for name, role in roles.items() if role == "economic_signal"]
    negotiators = [name for name, role in roles.items() if role == "negotiator"]

    edges: List[Tuple[str, str, str, str]] = []

    if election_like:
        for source in campaigns[:4]:
            for target in constituencies[:6]:
                edges.append(_edge("TARGETS_AND_MOBILIZES", "Campaign actor targets or mobilizes this participant bloc.", source, target))

        for idx, source in enumerate(campaigns[:4]):
            for target in campaigns[idx + 1:4]:
                edges.append(_edge("CONTESTS_ELECTORAL_SPACE", "Competes with another actor over support, seats, resources, or legitimacy.", source, target))
    elif narrative_like:
        for idx, source in enumerate(power_actors[:8]):
            for target in power_actors[idx + 1:8]:
                edges.append(_edge("CONTESTS_POWER_AND_LEGITIMACY", "Competes over power, legitimacy, survival, alliances, or narrative outcomes.", source, target))
        for source in narrative_agents[:4]:
            for target in power_actors[:6]:
                edges.append(_edge("INTERPRETS_STORY_CAUSALITY", "Interprets foreshadowing, motives, risks, and story consequences for this actor.", source, target))
        for source in ground_agents[:4]:
            for target in power_actors[:4]:
                edges.append(_edge("CREATES_STORY_PRESSURE_FOR", "Ground-level pressure changes the actor's options or legitimacy.", source, target))
    else:
        contest_pool = (campaigns + power_actors + economic_agents + negotiators)[:8]
        for idx, source in enumerate(contest_pool):
            for target in contest_pool[idx + 1:8]:
                edges.append(_edge("CONTESTS_POWER_OR_RESOURCES", "Competes with another actor over resources, authority, market share, legitimacy, or options.", source, target))

    for source in negotiators[:3]:
        for target in (campaigns if election_like else (power_actors + economic_agents + constituencies))[:4]:
            if source != target:
                edges.append(_edge("NEGOTIATES_ALIGNMENT", "Negotiates alliance, coordination, or vote-transfer assumptions.", source, target))

    for source in narrative_agents[:3]:
        for target in (constituencies[:3] + campaigns[:3]):
            edges.append(_edge("SHAPES_PUBLIC_NARRATIVE", "Frames claims, scandals, sentiment, or legitimacy for this actor.", source, target))

    for source in data_agents[:4]:
        edges.append(_edge("SUPPLIES_FORECAST_DATA", "Provides numeric evidence, survey signals, model outputs, or uncertainty estimates.", source, "QuantitativeSynthesizer"))
        edges.append(_edge("SUBMITS_EVIDENCE_FOR_AUDIT", "Sends quantitative claims for source-quality and leakage checks.", source, "EvidenceAuditor"))

    for source in ground_agents[:5]:
        edges.append(_edge("REPORTS_GROUND_SIGNAL", "Reports local field evidence, operational signals, or governance risk.", source, "EvidenceAuditor"))
        edges.append(_edge("INFORMS_FORECAST_ASSUMPTIONS", "Feeds local signal into numeric scenario assumptions.", source, "QuantitativeSynthesizer"))

    for source in economic_agents[:3]:
        edges.append(_edge("REPORTS_ECONOMIC_SENTIMENT", "Reports business, industry, jobs, market, or resource sentiment.", source, "QuantitativeSynthesizer"))

    for source in constituencies[:6]:
        edges.append(_edge("EXPRESSES_PARTICIPANT_PREFERENCE", "Represents lived experience, turnout propensity, or demand-side behavior.", source, "QuantitativeSynthesizer"))

    edges.extend(CONTROL_EDGE_TYPES)
    deduped: List[Tuple[str, str, str, str]] = []
    seen = set()
    for edge_tuple in edges:
        key = (edge_tuple[0], edge_tuple[2], edge_tuple[3])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(edge_tuple)
    return deduped or list(GENERIC_EDGE_TYPES)


def _entity(name: str, description: str, examples: Optional[List[str]] = None) -> Dict[str, Any]:
    return {
        "name": _to_pascal_case(name),
        "description": _safe_description(description, f"{name} actor."),
        "attributes": [
            {"name": "role", "type": "text", "description": "Actor role in the simulation"},
            {"name": "position", "type": "text", "description": "Public stance or institutional position"},
        ],
        "examples": examples or [],
    }


def _ensure_control_entities(entity_types: List[Dict[str, Any]], max_count: int) -> List[Dict[str, Any]]:
    control_names = {item["name"] for item in CONTROL_ENTITY_TYPES}
    generic_names = {"Person", "Organization"}
    domain_entities = []
    seen = set()
    for entity in entity_types:
        name = _to_pascal_case(str(entity.get("name", "")))
        if not name or name in seen or name in control_names or name in generic_names:
            continue
        seen.add(name)
        entity["name"] = name
        domain_entities.append(entity)

    control_count = min(len(CONTROL_ENTITY_TYPES), max_count)
    generic_reserve = min(len(generic_names), max(0, max_count - control_count))
    selected = domain_entities[: max(0, max_count - control_count - generic_reserve)]
    selected.extend(_entity(item["name"], item["description"], item.get("examples")) for item in CONTROL_ENTITY_TYPES[:control_count])
    if len(selected) < max_count:
        selected.append(_entity("Person", "Any individual person not fitting another specific type.", ["ordinary participant"]))
    if len(selected) < max_count:
        selected.append(_entity("Organization", "Any organization not fitting another specific type.", ["community group"]))
    return selected[:max_count]


def _ensure_control_edges(edge_types: List[Dict[str, Any]], max_count: int) -> List[Dict[str, Any]]:
    seen = set()
    domain_edges = []
    control_names = {name for name, _, _, _ in CONTROL_EDGE_TYPES}
    for edge in edge_types:
        name = str(edge.get("name", "")).upper()
        targets = tuple(
            (str(st.get("source", "")), str(st.get("target", "")))
            for st in edge.get("source_targets", []) or []
            if isinstance(st, dict)
        )
        key = (name, targets)
        if not name or key in seen or name in control_names:
            continue
        seen.add(key)
        edge["name"] = name
        domain_edges.append(edge)

    control_edges = [
        {
            "name": name,
            "description": description,
            "source_targets": [{"source": source, "target": target}],
            "attributes": [],
        }
        for name, description, source, target in CONTROL_EDGE_TYPES
    ]
    control_count = min(len(control_edges), max_count)
    selected = domain_edges[: max(0, max_count - control_count)]
    selected.extend(control_edges[:control_count])
    return selected[:max_count]


ONTOLOGY_SYSTEM_PROMPT = """You are an expert knowledge-graph ontology designer. Analyze the supplied prompt, optional documents, URLs, and research context, then design entity and relationship types for a future simulation.

IMPORTANT: Return valid JSON only. Do not include markdown or commentary.

Horizon XL builds a simulation graph where each entity is a real-world actor that can speak, respond, influence others, or transmit information. Relationships describe institutional links, information flows, agreement/disagreement, reporting, regulation, collaboration, negotiation, and rivalry.

Do not reuse any previous run's actors. Domain-specific nouns must come from the supplied prompt, uploaded context, URLs, or research packet, not from a preset template.

Return this JSON shape:
{
  "entity_types": [
    {
      "name": "EntityTypeNameInEnglishPascalCase",
      "description": "Short English description, max 100 characters",
      "attributes": [
        {"name": "english_snake_case_attribute", "type": "text", "description": "Attribute description"}
      ],
      "examples": ["example actor 1", "example actor 2"]
    }
  ],
  "edge_types": [
    {
      "name": "RELATIONSHIP_TYPE_IN_UPPER_SNAKE_CASE",
      "description": "Short English description, max 100 characters",
      "source_targets": [{"source": "SourceEntityType", "target": "TargetEntityType"}],
      "attributes": []
    }
  ],
  "analysis_summary": "Brief English summary of the ontology design"
}

Design rules:
1. If the prompt explicitly lists agents, preserve those listed agent roles before adding any inferred roles.
2. Return enough entity types to cover explicit agents plus process roles; do not truncate an explicit agent list to 10.
3. Reserve process roles for SimulationModerator, ExternalResearchScout, EvidenceAuditor, QuantitativeSynthesizer, and NegotiationMediator.
4. Use remaining slots for prompt/research-derived causal actors, affected groups, decision makers, data providers, and narrative actors.
4. Include Person and Organization only when useful and when there is room.
5. Entity types must be real actors, not abstract concepts.
6. Do not turn issues, signals, metrics, or slogans into entity types unless the prompt names them as actors.
7. Relationship types should encode specific information flows, mobilization, rivalry, negotiation, evidence supply, reporting, auditing, and numeric synthesis.
8. Relationship source_targets must reference defined entity types where possible.
9. All output text must be English.
"""


class OntologyGenerator:
    """Generate ontology definitions for graph construction."""

    MAX_TEXT_LENGTH_FOR_LLM = 50000

    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm_client = llm_client or LLMClient()

    def generate(
        self,
        document_texts: List[str],
        simulation_requirement: str,
        additional_context: Optional[str] = None,
        generation_seed: Optional[str] = None,
    ) -> Dict[str, Any]:
        user_message = self._build_user_message(
            document_texts,
            simulation_requirement,
            additional_context,
            generation_seed,
        )
        lang_instruction = get_language_instruction()
        system_prompt = (
            f"{ONTOLOGY_SYSTEM_PROMPT}\n\n{lang_instruction}\n"
            "IMPORTANT: Every output field must be English only. "
            "Entity type names MUST be English PascalCase. "
            "Relationship type names MUST be English UPPER_SNAKE_CASE. "
            "Attribute names MUST be English snake_case."
        )

        try:
            executor = ThreadPoolExecutor(max_workers=1)
            try:
                future = executor.submit(
                    self.llm_client.chat_json,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_message},
                    ],
                    temperature=0.7,
                    max_tokens=4096,
                )
                result = future.result(timeout=Config.ONTOLOGY_LLM_TIMEOUT_SECONDS)
            finally:
                executor.shutdown(wait=False, cancel_futures=True)
        except FutureTimeoutError:
            logger.warning(
                "Ontology LLM timed out after %.1fs; using context-derived fallback.",
                Config.ONTOLOGY_LLM_TIMEOUT_SECONDS,
            )
            result = self._fallback_ontology(simulation_requirement, document_texts, additional_context, generation_seed)
        except Exception as exc:
            logger.warning("Ontology LLM generation failed; using context-derived fallback: %s", exc)
            result = self._fallback_ontology(simulation_requirement, document_texts, additional_context, generation_seed)

        full_text = "\n".join([simulation_requirement or "", additional_context or "", "\n".join(document_texts or [])])
        primary_text = "\n".join([simulation_requirement or "", additional_context or ""]).strip()
        explicit_agents = (
            _extract_explicit_agent_types(primary_text)
            or _extract_explicit_agent_types(_without_external_research(full_text))
        )
        processed = self._validate_and_process(result, explicit_agents=explicit_agents, source_text=full_text)
        if self._looks_like_stale_default(processed, simulation_requirement, document_texts, additional_context, explicit_agents=explicit_agents):
            logger.warning("Ontology output looked stale or weak; using context-derived fallback.")
            processed = self._validate_and_process(
                self._fallback_ontology(simulation_requirement, document_texts, additional_context, generation_seed),
                explicit_agents=explicit_agents,
                source_text=full_text,
            )
        return processed

    def generate_fallback(
        self,
        document_texts: List[str],
        simulation_requirement: str,
        additional_context: Optional[str] = None,
        generation_seed: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self._validate_and_process(
            self._fallback_ontology(simulation_requirement, document_texts, additional_context, generation_seed),
            explicit_agents=_extract_explicit_agent_types(
                "\n".join([simulation_requirement or "", additional_context or "", "\n".join(document_texts or [])])
            ),
            source_text="\n".join([simulation_requirement or "", additional_context or "", "\n".join(document_texts or [])]),
        )

    def _build_user_message(
        self,
        document_texts: List[str],
        simulation_requirement: str,
        additional_context: Optional[str],
        generation_seed: Optional[str] = None,
    ) -> str:
        combined_text = "\n\n---\n\n".join(document_texts or [])
        original_length = len(combined_text)
        if len(combined_text) > self.MAX_TEXT_LENGTH_FOR_LLM:
            combined_text = combined_text[: self.MAX_TEXT_LENGTH_FOR_LLM]
            combined_text += (
                f"\n\n...(Input had {original_length} characters; only the first "
                f"{self.MAX_TEXT_LENGTH_FOR_LLM} characters were used for ontology analysis)..."
            )

        message = f"""## Simulation Requirement

{simulation_requirement}

## Document / Research Context

{combined_text}
"""
        if additional_context:
            message += f"""
## Additional Context

{additional_context}
"""
        message += f"""
## Fresh Run Constraint

Generation run id: {generation_seed or "fresh-unspecified-run"}

This is a new simulation. Do not reuse cached ontology, previous agent sets, or built-in domain rosters.
If the prompt explicitly lists agents, prioritize those roles.
If no agents are listed, infer concrete actors from the prompt, uploaded context, URLs, and research packet.
Research packet items are provisional and should create research/evidence actors only when useful.
For repeated prompts, vary secondary observers and personas while preserving core causal actors from the input.
"""
        return message

    def _build_ontology_payload(
        self,
        entity_types: List[Tuple[str, str]],
        edge_types: List[Tuple[str, str, str, str]],
        summary: str,
    ) -> Dict[str, Any]:
        return {
            "entity_types": [_entity(name, description) for name, description in entity_types],
            "edge_types": [
                {
                    "name": name,
                    "description": description,
                    "source_targets": [{"source": source, "target": target}],
                    "attributes": [],
                }
                for name, description, source, target in edge_types
            ],
            "analysis_summary": summary,
        }

    def _fallback_ontology(
        self,
        simulation_requirement: str,
        document_texts: List[str],
        additional_context: Optional[str],
        generation_seed: Optional[str] = None,
    ) -> Dict[str, Any]:
        full_text = "\n".join([simulation_requirement or "", additional_context or "", "\n".join(document_texts or [])])
        primary_text = "\n".join([simulation_requirement or "", additional_context or ""]).strip()
        non_research_text = _without_external_research(full_text)

        # Actor creation must be anchored in the user prompt/uploaded context.
        # Web research can inform evidence later, but search snippets should not
        # become agents just because a search-result page mentioned a metric.
        explicit_agents = _extract_explicit_agent_types(primary_text) or _extract_explicit_agent_types(non_research_text)
        context_agents = _quality_actor_items(primary_text, limit=30) or _quality_actor_items(non_research_text, limit=30)
        inferred_agents = _dedupe_agent_tuples((explicit_agents or []) + context_agents)
        if not inferred_agents:
            inferred_agents = _quality_actor_items(non_research_text)
        if explicit_agents:
            inferred_agents = _dedupe_agent_tuples(inferred_agents)
        else:
            inferred_agents = _select_run_agent_types(
                inferred_agents,
                generation_seed,
                limit=max(12, min(28, 10 + _prompt_scope_score(primary_text or non_research_text) // 2)),
            )

        if not inferred_agents:
            inferred_agents = list(GENERIC_FALLBACK_ACTORS)

        entity_types = inferred_agents + [
            ("Person", "Any individual person not fitting another specific type."),
            ("Organization", "Any organization not fitting another specific type."),
        ]
        edge_types = _relationship_edges_for_agents(inferred_agents, full_text)
        summary = f"Context-derived fallback ontology for {_context_label(full_text)}."
        return self._build_ontology_payload(entity_types, edge_types, summary)

    def _validate_and_process(
        self,
        result: Dict[str, Any],
        explicit_agents: Optional[List[Tuple[str, str]]] = None,
        source_text: str = "",
    ) -> Dict[str, Any]:
        if not isinstance(result, dict):
            result = {}
        result.setdefault("entity_types", [])
        result.setdefault("edge_types", [])
        result.setdefault("analysis_summary", "")

        explicit_agents = explicit_agents or []
        explicit_names = [_to_pascal_case(name) for name, _ in explicit_agents]
        explicit_name_set = set(explicit_names)
        actor_source_text = _without_external_research(source_text)

        entity_name_map: Dict[str, str] = {}
        normalized_entities = []
        for entity in result.get("entity_types", []):
            if not isinstance(entity, dict):
                continue
            original = str(entity.get("name") or "Unknown")
            name = _to_pascal_case(original)
            description = _safe_description(entity.get("description", ""), f"{name} actor.")
            if explicit_name_set and name not in explicit_name_set and name not in {"Person", "Organization"}:
                continue
            if _is_low_quality_entity_name(name, description):
                continue
            entity_name_map[original] = name
            normalized_entities.append({
                "name": name,
                "description": description,
                "attributes": self._normalize_attributes(entity.get("attributes")),
                "examples": entity.get("examples") if isinstance(entity.get("examples"), list) else [],
            })

        existing_names = {entity["name"] for entity in normalized_entities}
        inferred_supplements = [] if explicit_agents else _quality_actor_items(actor_source_text, limit=30)
        supplemental_agents = _dedupe_agent_tuples((explicit_agents or []) + inferred_supplements)
        for name, description in supplemental_agents:
            normalized_name = _to_pascal_case(name)
            if normalized_name not in existing_names:
                normalized_entities.append(_entity(normalized_name, description))
                existing_names.add(normalized_name)

        normalized_edges = []
        for edge in result.get("edge_types", []):
            if not isinstance(edge, dict):
                continue
            source_targets = []
            for st in edge.get("source_targets", []) or []:
                if not isinstance(st, dict):
                    continue
                source = entity_name_map.get(st.get("source"), st.get("source") or "Organization")
                target = entity_name_map.get(st.get("target"), st.get("target") or "Person")
                source_targets.append({"source": _to_pascal_case(str(source)), "target": _to_pascal_case(str(target))})
            normalized_edges.append({
                "name": str(edge.get("name") or "RELATED_TO").upper(),
                "description": _safe_description(edge.get("description", ""), "Relationship between simulation actors."),
                "source_targets": source_targets or [{"source": "Organization", "target": "Person"}],
                "attributes": edge.get("attributes") if isinstance(edge.get("attributes"), list) else [],
            })

        if explicit_agents:
            explicit_edges = self._normalize_edge_tuples(_relationship_edges_for_agents(explicit_agents, actor_source_text))
            existing_edge_keys = {
                (edge["name"], tuple((st["source"], st["target"]) for st in edge["source_targets"]))
                for edge in normalized_edges
            }
            for edge in explicit_edges:
                key = (edge["name"], tuple((st["source"], st["target"]) for st in edge["source_targets"]))
                if key not in existing_edge_keys:
                    normalized_edges.append(edge)
                    existing_edge_keys.add(key)

        input_text = actor_source_text or "\n".join([str(result.get("analysis_summary") or ""), " ".join(explicit_names)])
        max_entities = _target_entity_count(len(explicit_names), len(normalized_entities), input_text)
        max_edges = _target_edge_count(max_entities, len(normalized_edges), input_text)
        result["entity_types"] = _ensure_control_entities(normalized_entities, max_entities)
        result["edge_types"] = _ensure_control_edges(normalized_edges, max_edges)

        seen = set()
        deduped_entities = []
        for entity in result["entity_types"]:
            name = entity.get("name", "")
            if name and name not in seen:
                seen.add(name)
                deduped_entities.append(entity)
        result["entity_types"] = deduped_entities[:max_entities]

        entity_names = {entity["name"] for entity in result["entity_types"]}
        for fallback in [
            _entity("Person", "Any individual person not fitting other specific person types.", ["ordinary participant"]),
            _entity("Organization", "Any organization not fitting other specific organization types.", ["community group"]),
        ]:
            if fallback["name"] not in entity_names and len(result["entity_types"]) < max_entities:
                result["entity_types"].append(fallback)
                entity_names.add(fallback["name"])

        seen_edges = set()
        deduped_edges = []
        for edge in result["edge_types"]:
            name = edge.get("name", "")
            valid_targets = []
            for st in edge.get("source_targets", []) or []:
                if not isinstance(st, dict):
                    continue
                source = _to_pascal_case(str(st.get("source", "")))
                target = _to_pascal_case(str(st.get("target", "")))
                if source in entity_names and target in entity_names:
                    valid_targets.append({"source": source, "target": target})
            if not valid_targets:
                continue
            edge["source_targets"] = valid_targets
            targets = tuple((st["source"], st["target"]) for st in valid_targets)
            key = (name, targets)
            if name and key not in seen_edges:
                seen_edges.add(key)
                deduped_edges.append(edge)
        result["edge_types"] = deduped_edges[:max_edges]
        result["analysis_summary"] = str(result.get("analysis_summary") or "Context-derived ontology.")
        return result

    def _normalize_edge_tuples(self, edge_tuples: List[Tuple[str, str, str, str]]) -> List[Dict[str, Any]]:
        return [
            {
                "name": name.upper(),
                "description": _safe_description(description, "Relationship between simulation actors."),
                "source_targets": [{"source": _to_pascal_case(source), "target": _to_pascal_case(target)}],
                "attributes": [],
            }
            for name, description, source, target in edge_tuples
        ]

    def _normalize_attributes(self, attributes: Any) -> List[Dict[str, str]]:
        normalized = []
        for attr in attributes if isinstance(attributes, list) else []:
            if not isinstance(attr, dict):
                continue
            name = re.sub(r"[^a-zA-Z0-9_]+", "_", str(attr.get("name") or "attribute")).lower().strip("_")
            if name in {"name", "uuid", "group_id", "created_at", "summary"}:
                name = f"{name}_value"
            normalized.append({
                "name": name or "attribute",
                "type": str(attr.get("type") or "text"),
                "description": _safe_description(attr.get("description", ""), "Attribute description."),
            })
        return normalized or [
            {"name": "role", "type": "text", "description": "Actor role in the simulation"},
            {"name": "position", "type": "text", "description": "Public stance or institutional position"},
        ]

    def _looks_like_stale_default(
        self,
        result: Dict[str, Any],
        simulation_requirement: str,
        document_texts: List[str],
        additional_context: Optional[str],
        explicit_agents: Optional[List[Tuple[str, str]]] = None,
    ) -> bool:
        """Detect weak rosters that contain no prompt-derived actor signal."""
        input_text = "\n".join([simulation_requirement or "", additional_context or "", "\n".join(document_texts or [])])
        explicit_agents = explicit_agents or _extract_explicit_agent_types(input_text)
        if explicit_agents:
            expected = {_to_pascal_case(name).lower() for name, _ in explicit_agents}
            actual = {str(entity.get("name", "")).lower() for entity in result.get("entity_types", [])}
            preserved_ratio = len(expected & actual) / max(1, len(expected))
            return preserved_ratio < 0.75

        derived = _actor_items_from_text(input_text, limit=20)
        if not derived:
            return False
        derived_tokens = {
            token.lower()
            for name, _ in derived
            for token in re.findall(r"[A-Za-z0-9]{3,}", name)
        }
        entity_tokens = {
            token.lower()
            for entity in result.get("entity_types", [])
            for token in re.findall(r"[A-Za-z0-9]{3,}", str(entity.get("name", "")))
        }
        control_tokens = {
            token.lower()
            for item in CONTROL_ENTITY_TYPES
            for token in re.findall(r"[A-Za-z0-9]{3,}", item["name"])
        }
        return not bool((entity_tokens - control_tokens) & derived_tokens)

    def generate_python_code(self, ontology: Dict[str, Any]) -> str:
        code_lines = [
            '"""',
            "Custom entity type definitions",
            "Generated by Horizon XL for context-derived future simulation",
            '"""',
            "",
            "from pydantic import Field",
            "from zep_cloud.external_clients.ontology import EntityModel, EntityText, EdgeModel",
            "",
            "",
            "# ============== Entity Type Definitions ==============",
            "",
        ]

        for entity in ontology.get("entity_types", []):
            name = _to_pascal_case(entity["name"])
            desc = _safe_description(entity.get("description", ""), f"A {name} entity.")
            code_lines.append(f"class {name}(EntityModel):")
            code_lines.append(f'    """{desc}"""')
            attrs = entity.get("attributes") or []
            if attrs:
                for attr in attrs:
                    attr_name = re.sub(r"[^a-zA-Z0-9_]+", "_", str(attr["name"])).lower().strip("_")
                    attr_desc = _safe_description(attr.get("description", ""), attr_name)
                    code_lines.append(f"    {attr_name}: EntityText = Field(")
                    code_lines.append(f'        description="{attr_desc}",')
                    code_lines.append("        default=None")
                    code_lines.append("    )")
            else:
                code_lines.append("    pass")
            code_lines.append("")
            code_lines.append("")

        code_lines.append("# ============== Relationship Type Definitions ==============")
        code_lines.append("")

        for edge in ontology.get("edge_types", []):
            name = str(edge["name"]).upper()
            class_name = _to_pascal_case(name)
            desc = _safe_description(edge.get("description", ""), f"A {name} relationship.")
            code_lines.append(f"class {class_name}(EdgeModel):")
            code_lines.append(f'    """{desc}"""')
            attrs = edge.get("attributes") or []
            if attrs:
                for attr in attrs:
                    attr_name = re.sub(r"[^a-zA-Z0-9_]+", "_", str(attr["name"])).lower().strip("_")
                    attr_desc = _safe_description(attr.get("description", ""), attr_name)
                    code_lines.append(f"    {attr_name}: EntityText = Field(")
                    code_lines.append(f'        description="{attr_desc}",')
                    code_lines.append("        default=None")
                    code_lines.append("    )")
            else:
                code_lines.append("    pass")
            code_lines.append("")
            code_lines.append("")

        return "\n".join(code_lines)
