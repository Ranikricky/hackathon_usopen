"""
Graph API routes.
Project context is persisted server-side.
"""

import os
import re
import uuid
import traceback
import threading
from html.parser import HTMLParser
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from flask import request, jsonify

from . import graph_bp
from ..config import Config
from ..services.ontology_generator import OntologyGenerator
from ..services.domain_simulation_planner import DomainSimulationPlanner
from ..services.graph_builder import GraphBuilderService
from ..services.external_research import ExternalResearchService
from ..services.domain_contract import build_domain_contract, domain_plan_from_contract
from ..services.text_processor import TextProcessor
from ..utils.file_parser import FileParser
from ..utils.logger import get_logger
from ..utils.locale import t, get_locale, set_locale
from ..models.task import TaskManager, TaskStatus
from ..models.project import ProjectManager, ProjectStatus
from ..services.local_graph_repair import get_or_repair_local_graph

logger = get_logger('horizonxl.api')


def allowed_file(filename: str) -> bool:
    """Return whether the file extension is allowed."""
    if not filename or '.' not in filename:
        return False
    ext = os.path.splitext(filename)[1].lower().lstrip('.')
    return ext in Config.ALLOWED_EXTENSIONS


def _is_placeholder_secret(value: str | None) -> bool:
    """Return whether a secret is missing or still set to a placeholder."""
    if not value:
        return True
    normalized = value.strip().lower()
    return normalized in {"placeholder_key", "your_api_key_here", "changeme"}


URL_PATTERN = re.compile(r"https?://[^\s<>'\"\\)]+")
RESEARCH_PACKET_MARKERS = [
    "=== EXTERNAL_RESEARCH_PACKET ===",
    "# External Research Packet",
    "## Source Notes",
    "## Discovery Queries",
]


def _without_external_research_packet(text: str) -> str:
    """Return user/uploaded context only, excluding provisional web research."""
    if not text:
        return ""
    earliest = len(text)
    for marker in RESEARCH_PACKET_MARKERS:
        idx = text.find(marker)
        if idx >= 0:
            earliest = min(earliest, idx)
    return text[:earliest].strip()


def _extract_research_source_blocks(text: str, limit: int = 12) -> list[dict]:
    """Parse the external research markdown into source-level evidence blocks."""
    if not text or "## Source Notes" not in text:
        return []
    blocks: list[dict] = []
    headings = list(re.finditer(
        r"^### Source\s+(\d+):\s*(.+?)\s*$",
        text,
        flags=re.IGNORECASE | re.MULTILINE,
    ))
    for idx, match in enumerate(headings):
        block_end = headings[idx + 1].start() if idx + 1 < len(headings) else len(text)
        block = text[match.end():block_end]
        title = re.sub(r"\s+", " ", match.group(2)).strip()

        def field(label: str) -> str:
            field_match = re.search(rf"^- {re.escape(label)}:\s*(.+)$", block, flags=re.IGNORECASE | re.MULTILINE)
            return re.sub(r"\s+", " ", field_match.group(1)).strip() if field_match else ""

        url = field("URL")
        source_type = field("Type")
        query = field("Query")
        caveat = field("Caveat")
        excerpt_lines = []
        for line in block.splitlines():
            if re.match(r"^- (?:URL|Type|Query|Caveat):", line, flags=re.IGNORECASE):
                continue
            if not line.strip():
                continue
            excerpt_lines.append(line.strip())
        excerpt_text = " ".join(excerpt_lines)
        excerpt_text = re.sub(r"^- (?:URL|Type|Query|Caveat):.*$", " ", excerpt_text, flags=re.IGNORECASE | re.MULTILINE)
        excerpt_text = re.sub(r"\s+", " ", excerpt_text).strip()
        if title or url or excerpt_text:
            blocks.append({
                "source_index": match.group(1),
                "title": title,
                "url": url,
                "source_type": source_type,
                "query": query,
                "caveat": caveat,
                "excerpt": excerpt_text[:1400],
            })
        if len(blocks) >= limit:
            break
    return blocks


def _compact_claim_text(title: str, excerpt: str) -> str:
    """Create a short auditable claim from source title/excerpt."""
    cleaned = re.sub(r"\s+", " ", excerpt or "").strip()
    if cleaned and cleaned != "No readable excerpt fetched.":
        first_sentence = re.split(r"(?<=[.!?])\s+", cleaned, maxsplit=1)[0]
        if 40 <= len(first_sentence) <= 280:
            return first_sentence
        return cleaned[:280].strip()
    return (title or "External source discovered")[:280]


def _is_instruction_or_structural_fragment(text: str) -> bool:
    """Reject process instructions, JSON/dict fragments, and list ordinals as facts."""
    lowered = re.sub(r"\s+", " ", str(text or "")).strip().lower()
    if not lowered:
        return True
    structural_patterns = [
        "pocket_id",
        "'start':",
        '"start":',
        "'end':",
        '"end":',
        "'events':",
        '"events":',
        "target variables",
        "required outputs",
        "forecast the following",
        "time pockets include",
        "targets contain placeholder",
        "style requirements",
        "agent must",
        "agents must",
        "for every agent",
        "each agent",
        "do not use",
        "do not invent",
        "separate confirmed facts",
        "evidence auditor must",
        "run a structured",
        "run the simulation",
        "produce numeric",
        "clearly separate facts",
        "what are the most likely paths",
        "next 90 days, divided into",
        "forecast horizon",
    ]
    if any(pattern in lowered for pattern in structural_patterns):
        return True
    if re.match(r"^(?:days?\s+\d+|days?\s+\d+\s*[–-]\s*\d+|scenario synthesis|forecast ledger|current state)\b", lowered):
        return True
    if lowered.startswith(("{", "}", "[", "]")) or re.search(r"[{}]{1,}.*:", lowered):
        return True
    if re.match(r"^\d{1,2}\.\s+", lowered):
        # A numbered instruction/list item is not a numeric fact unless it also
        # contains a real measurement, date, money amount, percentage, or range.
        without_ordinal = re.sub(r"^\d{1,2}\.\s+", "", lowered)
        has_real_measure = bool(re.search(
            r"[$€£₹]\s?\d|\b(?:19|20)\d{2}\b|\d[\d,]*(?:\.\d+)?\s*(?:%|percent|bps|"
            r"million|billion|trillion|m\b|bn\b|tn\b|kwh|mwh|gwh|twh|tons?|tonnes?|"
            r"barrels?|bpd|mb/d|usd|dollars?|seats?|votes?|months?|years?)|"
            r"\d[\d,]*(?:\.\d+)?\s*[-–]\s*\d[\d,]*(?:\.\d+)?",
            without_ordinal,
            flags=re.IGNORECASE,
        ))
        if not has_real_measure:
            return True
    return False


def _extract_numeric_fact_texts(text: str, limit: int = 24) -> list[str]:
    """Extract short text spans that contain numbers/units for evidence review."""
    if not text:
        return []
    facts: list[str] = []
    seen: set[str] = set()
    clauses = re.split(r"(?<=[.!?])\s+|\n|;|\|", text)
    numeric_pattern = re.compile(
        r"(?:[$€£₹]\s?\d[\d,]*(?:\.\d+)?|"
        r"\b(?:usd|eur|gbp|inr|dollars?)\s?\d[\d,]*(?:\.\d+)?|"
        r"\d[\d,]*(?:\.\d+)?\s*(?:%|percent|bps|basis points|million|billion|trillion|m|bn|tn|"
        r"kwh|mwh|gwh|twh|tons?|tonnes?|metric tons?|per metric ton|barrels?|bpd|mb/d|usd|dollars?|"
        r"seats?|votes?|months?|years?|days?|vessels?|ships?|seafarers?|routes?|premiums?|rates?)|"
        r"\b\d{4}\s*[-–]\s*\d{2,4}\b|"
        r"\b\d[\d,]*(?:\.\d+)?\s*[-–]\s*\d[\d,]*(?:\.\d+)?\b)",
        flags=re.IGNORECASE,
    )
    for clause in clauses:
        cleaned = re.sub(r"\s+", " ", clause).strip(" -•")
        if len(cleaned) < 12:
            continue
        if _is_instruction_or_structural_fragment(cleaned):
            continue
        if not numeric_pattern.search(cleaned):
            continue
        if len(cleaned) > 320:
            numeric_match = numeric_pattern.search(cleaned)
            if not numeric_match:
                continue
            start = max(0, numeric_match.start() - 80)
            end = min(len(cleaned), numeric_match.end() + 220)
            window = cleaned[start:end].strip(" ,:;-")
            if len(window) < 12:
                continue
            if _is_instruction_or_structural_fragment(window):
                continue
            key = window.lower()
            if key in seen:
                continue
            seen.add(key)
            facts.append(window)
            if len(facts) >= limit:
                return facts
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        facts.append(cleaned)
        if len(facts) >= limit:
            break
    return facts


def _target_token_map(nodes: list[dict], target_uuids: list[str]) -> dict[str, set[str]]:
    """Build searchable target tokens for linking evidence facts to variables."""
    result: dict[str, set[str]] = {}
    node_by_uuid = {node.get("uuid"): node for node in nodes}
    for uuid_value in target_uuids:
        node = node_by_uuid.get(uuid_value) or {}
        raw = " ".join([
            str(node.get("name") or ""),
            str(node.get("summary") or ""),
            str((node.get("attributes") or {}).get("target_variable") or ""),
        ])
        tokens = {
            token
            for token in re.findall(r"[a-zA-Z0-9]{3,}", raw.lower())
            if token not in {
                "target", "variable", "prompt", "required", "index", "count",
                "percent", "currency", "forecast", "simulated",
            }
        }
        result[uuid_value] = tokens
    return result


class _HTMLTextExtractor(HTMLParser):
    """Extract readable HTML text without extra dependencies."""

    def __init__(self):
        super().__init__()
        self._parts = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style', 'noscript'}:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in {'script', 'style', 'noscript'} and self._skip_depth > 0:
            self._skip_depth -= 1
        if tag in {'p', 'br', 'div', 'li', 'section', 'article', 'tr', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}:
            self._parts.append('\n')

    def handle_data(self, data):
        if self._skip_depth > 0:
            return
        cleaned = data.strip()
        if cleaned:
            self._parts.append(cleaned)
            self._parts.append(' ')

    def get_text(self) -> str:
        return ''.join(self._parts)


def _extract_urls(*texts: str) -> list[str]:
    """Extract and de-duplicate URLs while preserving order."""
    seen = set()
    urls = []
    for text in texts:
        if not text:
            continue
        for match in URL_PATTERN.findall(text):
            candidate = match.strip().rstrip('.,;')
            parsed = urlparse(candidate)
            if parsed.scheme in {'http', 'https'} and parsed.netloc and candidate not in seen:
                seen.add(candidate)
                urls.append(candidate)
    return urls


def _fetch_url_text(url: str, max_bytes: int = 2_000_000) -> str:
    """Fetch URL text from HTML or plain-text pages."""
    try:
        req = Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; Horizon XL/1.0)",
                "Accept": "text/html,text/plain;q=0.9,*/*;q=0.5",
            },
        )
        with urlopen(req, timeout=15) as resp:
            raw = resp.read(max_bytes)
            content_type = (resp.headers.get('Content-Type') or '').lower()
            charset = resp.headers.get_content_charset() or 'utf-8'

        if not raw:
            return ""

        try:
            decoded = raw.decode(charset, errors='ignore')
        except Exception:
            decoded = raw.decode('utf-8', errors='ignore')

        if 'text/html' in content_type or '<html' in decoded[:2000].lower():
            parser = _HTMLTextExtractor()
            parser.feed(decoded)
            text = parser.get_text()
        else:
            text = decoded

        return TextProcessor.preprocess_text(text)
    except Exception as exc:
        logger.warning(f"URL fetch failed: {url}, error={exc}")
        return ""


def _infer_local_agent_population(simulation_requirement: str, entity_names: list[str]) -> dict[str, int]:
    """Infer local graph node counts from prompt scope and actor-type importance."""
    text = (simulation_requirement or "").lower()
    orchestration_names = {
        "SimulationModerator",
        "ExternalResearchScout",
        "EvidenceAuditor",
        "DataRetrievalAnalyst",
        "QuantitativeSynthesizer",
        "NegotiationMediator",
    }
    custom_names = [
        name for name in entity_names
        if name not in {"Person", "Organization"} and name not in orchestration_names
    ]
    if not custom_names:
        return {name: 1 for name in entity_names}

    complexity_terms = [
        "region", "locality", "segment", "group", "people", "participant",
        "population", "public", "scenario", "variable", "metric", "numeric",
        "monthly", "weekly", "daily", "data", "source", "research", "web",
        "scrape", "forecast", "uncertainty", "timeline", "signal",
    ]
    complexity = sum(1 for term in complexity_terms if term in text)
    target_count = max(10, min(36, 10 + complexity + max(0, len(custom_names) - 5)))

    control_count = 6
    fallback_count = sum(1 for name in entity_names if name in {"Person", "Organization"})
    causal_target = max(len(custom_names), target_count - control_count - fallback_count)

    weights = []
    for name in custom_names:
        lowered = name.lower()
        weight = 1.0
        if any(term in lowered for term in ["voter", "people", "public", "consumer", "worker", "beneficiary", "household", "patient", "student"]):
            weight = 3.5
        elif any(term in lowered for term in ["observer", "analyst", "pollster", "journalist", "field", "booth"]):
            weight = 1.8
        elif any(term in lowered for term in ["moderator", "auditor", "research", "synthesizer"]):
            weight = 0.8
        weights.append(weight)

    counts = {name: 1 for name in custom_names}
    remaining = max(0, causal_target - len(custom_names))
    total_weight = sum(weights) or 1.0
    extras = [int(remaining * weight / total_weight) for weight in weights]
    while sum(extras) < remaining:
        idx = max(range(len(weights)), key=lambda i: weights[i] - extras[i] * 0.01)
        extras[idx] += 1
    for name, extra in zip(custom_names, extras):
        counts[name] += extra

    for name in entity_names:
        if name in {"Person", "Organization"} or name in orchestration_names:
            counts[name] = 1
    return counts


def _local_instance_name(entity_name: str, copy_index: int, total: int, simulation_requirement: str) -> str:
    """Create a readable instance name for repeated local graph agents."""
    if total <= 1:
        return entity_name
    lowered = entity_name.lower()
    if "voter" in lowered or "beneficiary" in lowered or "people" in lowered or "participant" in lowered:
        labels = ["High-Exposure", "Low-Exposure", "Early-Mover", "Late-Mover", "Information-Rich", "Information-Poor", "Resource-Constrained", "Swing"]
    elif "observer" in lowered:
        labels = ["Field", "Local", "Network", "Operational", "Boundary", "Contrarian", "Signal-Rich", "Sparse-Signal"]
    elif "analyst" in lowered or "pollster" in lowered:
        labels = ["Model-Led", "Ground-Signal", "Source-Auditing", "Skeptical", "Scenario", "Data", "Consensus", "Tail-Risk"]
    elif "strategist" in lowered or "campaign" in lowered:
        labels = ["Core", "Field", "Narrative", "Data", "Coordination", "Tradeoff", "Adaptation", "Countermove"]
    else:
        labels = ["A", "B", "C", "D", "E", "F", "G", "H"]
    label = labels[(copy_index - 1) % len(labels)]
    return f"{label} {entity_name} {copy_index}"


def _build_local_graph_from_ontology(
    graph_id: str,
    ontology: dict,
    simulation_requirement: str = "",
    generation_seed: str = "",
    parameter_context: str = "",
    domain_contract: dict | None = None,
) -> dict:
    """
    Build a local graph from the ontology when Zep is not configured.
    This keeps the local demo and workflow usable.
    """
    entity_types = ontology.get("entity_types", []) or []
    edge_types = ontology.get("edge_types", []) or []
    planning_context = parameter_context or simulation_requirement or ""
    prompt_context = _without_external_research_packet(planning_context) or simulation_requirement or planning_context
    try:
        domain_plan = domain_plan_from_contract(domain_contract) if domain_contract else {}
        if not domain_plan:
            domain_plan = DomainSimulationPlanner().fallback_plan(prompt_context)
    except Exception as exc:
        logger.warning("Could not derive graph parameter layer from prompt/context: %s", exc)
        domain_plan = {}

    nodes = []
    entity_uuid_map: dict[str, list[str]] = {}
    orchestration_entity_names = {
        "SimulationModerator",
        "ExternalResearchScout",
        "EvidenceAuditor",
        "DataRetrievalAnalyst",
        "QuantitativeSynthesizer",
        "NegotiationMediator",
    }
    def is_actor_entity(entity_name: str, summary: str = "") -> bool:
        spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", entity_name or "").lower()
        combined = f"{spaced} {summary or ''}".lower()
        if entity_name in {"Person", "Organization"}:
            return True
        if entity_name in orchestration_entity_names:
            return True
        if re.search(r"\b(?:usd|lfp|kwh|mwh|gwh|twh|price|pricing|rate|growth|index|table|forecast|scenario|pocket|baseline|snapshot|synthesis|output|target|variable|evidence source|numeric fact)\b", combined):
            return False
        return bool(re.search(
            r"\b(?:actor|actors|agent|agents|people|person|persons|voter|voters|consumer|consumers|buyer|buyers|seller|sellers|worker|workers|household|households|community|communities|cohort|cohorts|class|classes|group|groups|organization|organizations|institution|institutions|company|companies|firm|firms|agency|agencies|government|governments|regulator|regulators|party|parties|campaign|campaigns|candidate|candidates|bank|banks|investor|investors|trader|traders|producer|producers|supplier|suppliers|miner|miners|refiner|refiners|manufacturer|manufacturers|automaker|automakers|operator|operators|analyst|analysts|scientist|scientists|researcher|researchers|journalist|journalists|media|watchdog|watchdogs|auditor|auditors|moderator|moderators|mediator|mediators|expert|experts|official|officials|leader|leaders|owner|owners|developer|developers|lab|labs|union|unions)\b",
            combined,
        ))

    filtered_entity_types = [
        entity for entity in entity_types
        if is_actor_entity((entity or {}).get("name") or "", (entity or {}).get("description") or "")
    ]
    specific_actor_types = [
        entity for entity in filtered_entity_types
        if (entity or {}).get("name") not in {"Person", "Organization"}
    ]
    if specific_actor_types:
        filtered_entity_types = specific_actor_types
    entity_types = filtered_entity_types
    entity_names = [(entity or {}).get("name") or f"EntityType{idx}" for idx, entity in enumerate(entity_types, start=1)]
    instance_counts = _infer_local_agent_population(simulation_requirement, entity_names)

    node_idx = 0
    for idx, entity in enumerate(entity_types, start=1):
        entity_name = (entity or {}).get("name") or f"EntityType{idx}"
        count = max(1, min(20, int(instance_counts.get(entity_name, 1))))
        entity_uuid_map.setdefault(entity_name, [])

        attr_defs = (entity or {}).get("attributes", []) or []
        for copy_index in range(1, count + 1):
            node_idx += 1
            node_uuid = f"{graph_id}_node_{node_idx:03d}"
            entity_uuid_map[entity_name].append(node_uuid)
            attributes = {str(a.get("name")): "" for a in attr_defs if a.get("name")}
            attributes.update({
                "schema_type": copy_index == 1,
                "agent_instance": True,
                "instance_index": copy_index,
                "instance_count": count,
                "generation_seed": generation_seed,
            })

            nodes.append({
                "uuid": node_uuid,
                "name": _local_instance_name(entity_name, copy_index, count, simulation_requirement),
                "labels": ["Entity", entity_name],
                "summary": (entity or {}).get("description", ""),
                "attributes": attributes,
                "created_at": None,
            })

    control_nodes = [
        ("SimulationModerator", "Keeps the discussion focused and manages turn-taking."),
        ("EvidenceAuditor", "Checks claims against graph evidence and approved external research."),
        ("ExternalResearchScout", "Fetches or summarizes approved external web/source pointers outside graph memory."),
        ("DataRetrievalAnalyst", "Extracts numbers, units, dates, and missing-data warnings."),
        ("QuantitativeSynthesizer", "Turns agent positions into numeric scenario tables and confidence bands."),
        ("NegotiationMediator", "Surfaces disagreement, pressure points, tradeoffs, and possible compromise paths."),
    ]
    for entity_name, description in control_nodes:
        if entity_name in entity_uuid_map:
            continue
        node_idx += 1
        node_uuid = f"{graph_id}_node_{node_idx:03d}"
        entity_uuid_map[entity_name] = [node_uuid]
        nodes.append({
            "uuid": node_uuid,
            "name": entity_name,
            "labels": ["Entity", entity_name],
            "summary": description,
            "attributes": {
                "agent_instance": True,
                "orchestration_agent": True,
                "generation_seed": generation_seed,
            },
            "created_at": None,
        })

    parameter_uuid_map: dict[str, list[str]] = {
        "TargetVariable": [],
        "StateVariable": [],
        "ScenarioPath": [],
        "TimePocket": [],
        "ForecastHorizon": [],
        "EvidenceSource": [],
        "EvidenceClaim": [],
        "NumericFact": [],
    }

    def add_parameter_node(kind: str, name: str, summary: str, attributes: dict | None = None) -> str:
        nonlocal node_idx
        node_idx += 1
        node_uuid = f"{graph_id}_node_{node_idx:03d}"
        safe_name = re.sub(r"\s+", " ", str(name or kind)).strip()[:120] or kind
        attrs = dict(attributes or {})
        attrs.update({
            "schema_type": False,
            "agent_instance": False,
            "parameter_node": True,
            "parameter_kind": kind,
            "generation_seed": generation_seed,
        })
        nodes.append({
            "uuid": node_uuid,
            "name": safe_name,
            "labels": ["Parameter", kind],
            "summary": summary,
            "attributes": attrs,
            "created_at": None,
        })
        parameter_uuid_map.setdefault(kind, []).append(node_uuid)
        return node_uuid

    horizon = domain_plan.get("forecast_horizon") if isinstance(domain_plan.get("forecast_horizon"), dict) else {}
    if horizon:
        add_parameter_node(
            "ForecastHorizon",
            f"Horizon: {horizon.get('start', 'auto')} to {horizon.get('end', 'auto')}",
            "Prompt-derived forecast horizon and simulation granularity.",
            {
                "start": horizon.get("start", "auto"),
                "end": horizon.get("end", "auto"),
                "granularity": horizon.get("granularity", "event_triggered"),
            },
        )

    for target in (domain_plan.get("target_variables") or [])[:36]:
        if not isinstance(target, dict):
            continue
        name = str(target.get("name") or "").strip()
        if not name:
            continue
        add_parameter_node(
            "TargetVariable",
            f"Target: {name}",
            str(target.get("description") or f"Prompt-derived target variable: {name}."),
            {
                "target_variable": name,
                "unit": target.get("unit", "index"),
                "required": bool(target.get("required", True)),
            },
        )

    for state in (domain_plan.get("state_variables") or [])[:24]:
        if not isinstance(state, dict):
            continue
        name = str(state.get("name") or "").strip()
        if not name:
            continue
        add_parameter_node(
            "StateVariable",
            f"State: {name}",
            str(state.get("directional_interpretation") or f"State variable tracking {name}."),
            {
                "state_variable": name,
                "unit": state.get("unit", "index"),
                "required": bool(state.get("required", True)),
            },
        )

    scenario_block = domain_plan.get("scenario_structure") if isinstance(domain_plan.get("scenario_structure"), dict) else {}
    for scenario in (scenario_block.get("scenarios") or [])[:12]:
        if not isinstance(scenario, dict):
            continue
        name = str(scenario.get("name") or scenario.get("id") or "").strip()
        if not name:
            continue
        add_parameter_node(
            "ScenarioPath",
            f"Scenario: {name}",
            str(scenario.get("description") or f"Scenario path: {name}."),
            {
                "scenario_id": scenario.get("id") or name,
                "required": bool(scenario.get("required", True)),
            },
        )

    for pocket in (domain_plan.get("time_pockets") or [])[:24]:
        if not isinstance(pocket, dict):
            continue
        label = str(pocket.get("label") or pocket.get("pocket_id") or "").strip()
        if not label:
            continue
        add_parameter_node(
            "TimePocket",
            f"Pocket: {label}",
            "Sequential simulation pocket derived from the requested horizon/context.",
            {
                "pocket_id": pocket.get("pocket_id"),
                "start": pocket.get("start", "auto"),
                "end": pocket.get("end", "auto"),
                "events": pocket.get("events", []),
            },
        )

    source_seen: set[str] = set()
    source_uuid_by_url: dict[str, str] = {}
    for match in URL_PATTERN.finditer(planning_context):
        url = match.group(0).strip(").,;")
        if url in source_seen:
            continue
        source_seen.add(url)
        parsed = urlparse(url)
        source_uuid = add_parameter_node(
            "EvidenceSource",
            f"Source: {parsed.netloc or url}",
            "Prompt or research packet source pointer used as evidence context.",
            {"url": url, "source_host": parsed.netloc},
        )
        source_uuid_by_url[url] = source_uuid
        if len(source_seen) >= 12:
            break

    edges = []
    edge_idx = 0

    def add_edge(source_uuid: str, target_uuid: str, name: str, fact: str, attributes: dict | None = None) -> None:
        nonlocal edge_idx
        if not source_uuid or not target_uuid or source_uuid == target_uuid:
            return
        source_node = next((node for node in nodes if node["uuid"] == source_uuid), {})
        target_node = next((node for node in nodes if node["uuid"] == target_uuid), {})
        edge_idx += 1
        edges.append({
            "uuid": f"{graph_id}_edge_{edge_idx:04d}",
            "name": name,
            "fact": fact,
            "fact_type": name,
            "source_node_uuid": source_uuid,
            "target_node_uuid": target_uuid,
            "source_node_name": source_node.get("name", ""),
            "target_node_name": target_node.get("name", ""),
            "attributes": attributes or {},
            "created_at": None,
            "valid_at": None,
            "invalid_at": None,
            "expired_at": None,
            "episodes": [],
        })

    def first_node(name: str) -> str:
        uuids = entity_uuid_map.get(name) or []
        return uuids[0] if uuids else ""

    quant_uuid = first_node("QuantitativeSynthesizer")
    auditor_uuid = first_node("EvidenceAuditor")
    moderator_uuid = first_node("SimulationModerator")
    research_uuid = first_node("ExternalResearchScout")
    data_uuid = first_node("DataRetrievalAnalyst")
    mediator_uuid = first_node("NegotiationMediator")
    target_uuids = parameter_uuid_map.get("TargetVariable", [])[:12]
    target_tokens = _target_token_map(nodes, target_uuids)

    def link_fact_to_targets(fact_uuid: str, fact_text: str, max_links: int = 3) -> None:
        lowered = (fact_text or "").lower()
        linked = 0
        for target_uuid, tokens in target_tokens.items():
            if tokens and any(token in lowered for token in tokens):
                add_edge(fact_uuid, target_uuid, "INFORMS_TARGET", "Numeric/evidence fact informs this requested target variable.", {"evidence_relation": True})
                linked += 1
                if linked >= max_links:
                    return
        if linked == 0:
            for target_uuid in target_uuids[: min(max_links, len(target_uuids))]:
                add_edge(fact_uuid, target_uuid, "MAY_INFORM_TARGET", "Evidence fact may be relevant but needs auditor/quant review before use.", {"evidence_relation": True, "weak_link": True})

    # Convert provisional web research into evidence/claim/fact nodes. This is
    # the research lane of the graph: sources and claims inform agents, but do
    # not become agents or target variables by accident.
    for source_block in _extract_research_source_blocks(planning_context, limit=12):
        url = source_block.get("url") or ""
        source_uuid = source_uuid_by_url.get(url)
        if not source_uuid and url:
            parsed = urlparse(url)
            source_uuid = add_parameter_node(
                "EvidenceSource",
                f"Source: {parsed.netloc or url}",
                "External research source pointer used as evidence context.",
                {"url": url, "source_host": parsed.netloc},
            )
            source_uuid_by_url[url] = source_uuid

        claim_text = _compact_claim_text(source_block.get("title", ""), source_block.get("excerpt", ""))
        claim_uuid = add_parameter_node(
            "EvidenceClaim",
            f"Claim: {source_block.get('title') or claim_text}",
            claim_text,
            {
                "source_url": url,
                "source_title": source_block.get("title", ""),
                "source_type": source_block.get("source_type", ""),
                "query": source_block.get("query", ""),
                "caveat": source_block.get("caveat", ""),
                "from_external_research": True,
                "audited": False,
            },
        )
        add_edge(source_uuid, claim_uuid, "SUPPORTS_EVIDENCE_CLAIM", "External source provides this provisional evidence claim.", {"evidence_relation": True})
        add_edge(research_uuid, claim_uuid, "RETRIEVES_CLAIM", "Research scout brings this claim into the debate for review.", {"evidence_relation": True})
        add_edge(auditor_uuid, claim_uuid, "AUDITS_CLAIM", "Evidence auditor must check source quality, date, and leakage risk.", {"evidence_relation": True})

        for fact_text in _extract_numeric_fact_texts(" ".join([source_block.get("title", ""), source_block.get("excerpt", "")]), limit=3):
            fact_uuid = add_parameter_node(
                "NumericFact",
                f"Numeric fact: {fact_text[:80]}",
                fact_text,
                {
                    "source_url": url,
                    "source_title": source_block.get("title", ""),
                    "from_external_research": True,
                    "audited": False,
                },
            )
            add_edge(claim_uuid, fact_uuid, "CONTAINS_NUMERIC_FACT", "Evidence claim contains this extracted numeric fact.", {"evidence_relation": True})
            add_edge(data_uuid, fact_uuid, "EXTRACTS_NUMERIC_FACT", "Data retrieval analyst extracts units, dates, and numbers from this fact.", {"evidence_relation": True})
            add_edge(auditor_uuid, fact_uuid, "VALIDATES_NUMERIC_FACT", "Evidence auditor validates numeric fact before quant synthesis.", {"evidence_relation": True})
            link_fact_to_targets(fact_uuid, fact_text)

    prompt_fact_claim_uuid = ""
    prompt_numeric_facts = _extract_numeric_fact_texts(prompt_context, limit=24)
    if prompt_numeric_facts:
        source_uuid = add_parameter_node(
            "EvidenceSource",
            "Source: user prompt and uploaded context",
            "User-provided prompt/files are primary evidence and simulation constraints.",
            {"source_host": "user_input", "from_user_context": True},
        )
        claim_uuid = add_parameter_node(
            "EvidenceClaim",
            "Claim: user-provided numeric context",
            "The user prompt or uploaded files contain numeric context that should anchor the simulation.",
            {"source_host": "user_input", "from_user_context": True, "audited": True},
        )
        prompt_fact_claim_uuid = claim_uuid
        add_edge(source_uuid, claim_uuid, "SUPPORTS_EVIDENCE_CLAIM", "User-provided context supplies this evidence claim.", {"evidence_relation": True})
        add_edge(auditor_uuid, claim_uuid, "AUDITS_CLAIM", "Evidence auditor checks user-supplied context for cutoff and consistency.", {"evidence_relation": True})

    for fact_text in prompt_numeric_facts:
        fact_uuid = add_parameter_node(
            "NumericFact",
            f"Numeric fact: {fact_text[:80]}",
            fact_text,
            {"source_host": "user_input", "from_user_context": True, "audited": True},
        )
        if prompt_fact_claim_uuid:
            add_edge(prompt_fact_claim_uuid, fact_uuid, "CONTAINS_NUMERIC_FACT", "User-provided evidence claim contains this numeric fact.", {"evidence_relation": True})
        add_edge(data_uuid, fact_uuid, "EXTRACTS_NUMERIC_FACT", "Data retrieval analyst extracts units, dates, and numbers from this user-provided fact.", {"evidence_relation": True})
        add_edge(auditor_uuid, fact_uuid, "VALIDATES_NUMERIC_FACT", "Evidence auditor validates numeric fact before quant synthesis.", {"evidence_relation": True})
        link_fact_to_targets(fact_uuid, fact_text)

    for target_uuid in parameter_uuid_map.get("TargetVariable", []):
        add_edge(quant_uuid, target_uuid, "TRACKS_TARGET", "Quantitative synthesizer must produce numeric paths for this target variable.", {"parameter_relation": True})
        add_edge(data_uuid, target_uuid, "SUPPLIES_NUMERIC_EVIDENCE", "Data retrieval analyst extracts numeric evidence for this target variable.", {"parameter_relation": True})
        add_edge(auditor_uuid, target_uuid, "VALIDATES_TARGET", "Evidence auditor checks whether this target variable is sufficiently supported.", {"parameter_relation": True})
        add_edge(moderator_uuid, target_uuid, "KEEPS_DEBATE_ON_TARGET", "Simulation moderator keeps agent debate anchored to this target variable.", {"parameter_relation": True})

    for scenario_uuid in parameter_uuid_map.get("ScenarioPath", []):
        add_edge(quant_uuid, scenario_uuid, "SYNTHESIZES_SCENARIO", "Quantitative synthesizer creates a numeric path for this scenario.", {"parameter_relation": True})
        add_edge(moderator_uuid, scenario_uuid, "TESTS_SCENARIO_ASSUMPTIONS", "Moderator forces the debate to test scenario assumptions.", {"parameter_relation": True})
        for target_uuid in parameter_uuid_map.get("TargetVariable", [])[:12]:
            add_edge(target_uuid, scenario_uuid, "FORECASTED_UNDER_SCENARIO", "Target variable must be forecast under this scenario path.", {"parameter_relation": True})

    for pocket_uuid in parameter_uuid_map.get("TimePocket", []):
        add_edge(moderator_uuid, pocket_uuid, "RUNS_TIME_POCKET", "Moderator runs this sequential simulation pocket.", {"parameter_relation": True})
        add_edge(mediator_uuid, pocket_uuid, "MEDIATES_POCKET_DISAGREEMENT", "Mediator summarizes unresolved disagreement before the next pocket.", {"parameter_relation": True})
        for state_uuid in parameter_uuid_map.get("StateVariable", [])[:8]:
            add_edge(pocket_uuid, state_uuid, "UPDATES_STATE", "This time pocket updates the simulation state variable.", {"parameter_relation": True})

    for source_uuid in parameter_uuid_map.get("EvidenceSource", []):
        add_edge(research_uuid, source_uuid, "RETRIEVES_SOURCE", "External research scout retrieves or summarizes this source pointer.", {"parameter_relation": True})
        add_edge(auditor_uuid, source_uuid, "AUDITS_SOURCE", "Evidence auditor checks source timing, relevance, and leakage risk.", {"parameter_relation": True})

    causal_uuids = [
        uuid_value
        for entity_name, uuid_values in entity_uuid_map.items()
        if entity_name not in {name for name, _ in control_nodes}
        for uuid_value in uuid_values[:3]
    ]
    for idx, source_uuid in enumerate(causal_uuids[:72]):
        if not target_uuids:
            break
        add_edge(source_uuid, target_uuids[idx % len(target_uuids)], "INFLUENCES_TARGET", "Causal agent contributes assumptions, behavior, or evidence pressure to this requested target.", {"parameter_relation": True})

    for edge_def in edge_types:
        edge_name = (edge_def or {}).get("name") or "RELATED_TO"
        edge_fact = (edge_def or {}).get("description", "")
        source_targets = (edge_def or {}).get("source_targets", []) or []
        for st in source_targets:
            source_name = (st or {}).get("source")
            target_name = (st or {}).get("target")
            source_uuids = entity_uuid_map.get(source_name) or []
            target_uuids = entity_uuid_map.get(target_name) or []
            if not source_uuids or not target_uuids:
                continue
            max_links = min(max(len(source_uuids), len(target_uuids)), 12)
            for link_idx in range(max_links):
                source_uuid = source_uuids[link_idx % len(source_uuids)]
                target_uuid = target_uuids[link_idx % len(target_uuids)]
                edge_idx += 1
                edges.append({
                    "uuid": f"{graph_id}_edge_{edge_idx:04d}",
                    "name": edge_name,
                    "fact": edge_fact,
                    "fact_type": edge_name,
                    "source_node_uuid": source_uuid,
                    "target_node_uuid": target_uuid,
                    "source_node_name": source_name,
                    "target_node_name": target_name,
                    "attributes": {"schema_relation": link_idx == 0, "agent_instance_relation": True},
                    "created_at": None,
                    "valid_at": None,
                    "invalid_at": None,
                    "expired_at": None,
                    "episodes": [],
                })

    return {
        "graph_id": graph_id,
        "nodes": nodes,
        "edges": edges,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "mode": "local_ontology_fallback",
        "agent_population": {
            "inferred_instance_counts": instance_counts,
            "control_agents": [name for name, _ in control_nodes],
        },
    }


# ============== Project Management ==============

@graph_bp.route('/project/<project_id>', methods=['GET'])
def get_project(project_id: str):
    """
    Get project details.
    """
    project = ProjectManager.get_project(project_id)
    
    if not project:
        return jsonify({
            "success": False,
            "error": t('api.projectNotFound', id=project_id)
        }), 404

    return jsonify({
        "success": True,
        "data": project.to_dict()
    })


@graph_bp.route('/project/list', methods=['GET'])
def list_projects():
    """
    List projects.
    """
    limit = request.args.get('limit', 50, type=int)
    projects = ProjectManager.list_projects(limit=limit)
    
    return jsonify({
        "success": True,
        "data": [p.to_dict() for p in projects],
        "count": len(projects)
    })


@graph_bp.route('/project/<project_id>', methods=['DELETE'])
def delete_project(project_id: str):
    """
    Delete a project.
    """
    success = ProjectManager.delete_project(project_id)
    
    if not success:
        return jsonify({
            "success": False,
            "error": t('api.projectDeleteFailed', id=project_id)
        }), 404

    return jsonify({
        "success": True,
        "message": t('api.projectDeleted', id=project_id)
    })


@graph_bp.route('/project/<project_id>/reset', methods=['POST'])
def reset_project(project_id: str):
    """
    Reset a project so its graph can be rebuilt.
    """
    project = ProjectManager.get_project(project_id)
    
    if not project:
        return jsonify({
            "success": False,
            "error": t('api.projectNotFound', id=project_id)
        }), 404

    # Reset to the latest safe graph-building state.
    if project.ontology:
        project.status = ProjectStatus.ONTOLOGY_GENERATED
    else:
        project.status = ProjectStatus.CREATED
    
    project.graph_id = None
    project.graph_build_task_id = None
    project.error = None
    ProjectManager.save_project(project)
    
    return jsonify({
        "success": True,
        "message": t('api.projectReset', id=project_id),
        "data": project.to_dict()
    })


# ============== Endpoint 1: Upload/Input and Ontology Generation ==============

@graph_bp.route('/ontology/generate', methods=['POST'])
def generate_ontology():
    """
    Upload files or prompt-only input, then generate ontology definitions.
    """
    project = None
    try:
        logger.info("=== Starting ontology generation ===")
        
        # Request parameters.
        simulation_requirement = request.form.get('simulation_requirement', '')
        request_project_id = request.form.get('project_id', '').strip()
        submitted_domain_contract = request.form.get('domain_contract', '').strip()
        project_name = request.form.get('project_name', 'Unnamed Project')
        additional_context = request.form.get('additional_context', '')
        uploaded_files = request.files.getlist('files')
        all_uploaded_file_fields = []
        for field_name in request.files:
            for uploaded in request.files.getlist(field_name):
                if uploaded and uploaded.filename:
                    all_uploaded_file_fields.append(uploaded)

        # Be forgiving about frontend handoff failures. If the prompt was
        # attached as prompt_input.txt but the form field was empty, recover it
        # from the uploaded text file instead of failing Step 1.
        if not simulation_requirement.strip():
            for file in uploaded_files + [
                item for item in all_uploaded_file_fields
                if item not in uploaded_files
            ]:
                if not file or not file.filename or not allowed_file(file.filename):
                    continue
                try:
                    position = file.stream.tell()
                    raw = file.read()
                    file.stream.seek(position)
                    recovered = raw.decode("utf-8", errors="ignore").strip()
                    if recovered:
                        simulation_requirement = recovered
                        logger.info("Recovered simulation requirement from uploaded file: %s", file.filename)
                        break
                except Exception as recover_exc:
                    logger.warning("Could not recover simulation requirement from %s: %s", file.filename, recover_exc)
            if not simulation_requirement.strip() and additional_context.strip():
                simulation_requirement = additional_context.strip()
        
        logger.debug(f"Project name: {project_name}")
        logger.debug(f"Simulation requirement: {simulation_requirement[:100]}...")
        
        if not simulation_requirement or not simulation_requirement.strip():
            return jsonify({
                "success": False,
                "error": t('api.requireSimulationRequirement'),
                "debug": {
                    "form_keys": sorted(list(request.form.keys())),
                    "file_names": [
                        f"{field}:{file.filename}"
                        for field in request.files
                        for file in request.files.getlist(field)
                        if file and file.filename
                    ],
                    "content_type": request.content_type,
                }
            }), 400
        
        if _is_placeholder_secret(Config.LLM_API_KEY):
            return jsonify({
                "success": False,
                "error": "LLM_API_KEY is not configured. Please update the project .env file with a valid key."
            }), 400
        
        # Create or reuse the pre-ontology project produced by the Domain
        # Contract approval step.
        if request_project_id:
            project = ProjectManager.get_project(request_project_id)
            if not project:
                return jsonify({
                    "success": False,
                    "error": t('api.projectNotFound', id=request_project_id)
                }), 404
            project.name = project.name or project_name
        else:
            project = ProjectManager.create_project(name=project_name)
        project.simulation_requirement = simulation_requirement
        project.generation_seed = project.generation_seed or f"{project.project_id}:{uuid.uuid4().hex[:12]}"
        if submitted_domain_contract:
            try:
                import json
                submitted_contract = json.loads(submitted_domain_contract)
                if isinstance(submitted_contract, dict):
                    submitted_contract["approved"] = True
                    submitted_contract["project_id"] = project.project_id
                    ProjectManager.save_domain_contract(project.project_id, submitted_contract)
            except Exception as contract_parse_exc:
                logger.warning("Submitted Domain Contract could not be parsed: %s", contract_parse_exc)
        logger.info(f"Created project: {project.project_id}")
        
        # Input strategy:
        # 1) Prefer uploaded files.
        # 2) If no files are available, fetch URLs found in the prompt/context.
        # 3) If still empty, use the prompt text as a semantic seed.
        document_texts = []
        all_text = ""
        input_mode = "files"
        
        for file in uploaded_files:
            if file and file.filename and allowed_file(file.filename):
                # Save file under the project directory.
                file_info = ProjectManager.save_file_to_project(
                    project.project_id, 
                    file, 
                    file.filename
                )
                project.files.append({
                    "filename": file_info["original_filename"],
                    "size": file_info["size"]
                })
                
                # Extract text.
                text = FileParser.extract_text(file_info["path"])
                text = TextProcessor.preprocess_text(text)
                if text:
                    document_texts.append(text)
                    all_text += f"\n\n=== {file_info['original_filename']} ===\n{text}"
        
        if not document_texts:
            input_mode = "web"
            candidate_urls = _extract_urls(simulation_requirement, additional_context)
            if candidate_urls:
                logger.info(f"No usable uploaded files found. Fetching URLs: {candidate_urls}")
            for url in candidate_urls[:5]:
                text = _fetch_url_text(url)
                if not text:
                    continue
                document_texts.append(text)
                all_text += f"\n\n=== URL: {url} ===\n{text}"
                project.files.append({
                    "filename": url,
                    "size": len(text)
                })
        
        if not document_texts:
            input_mode = "prompt"
            seed_text_parts = [simulation_requirement.strip(), additional_context.strip()]
            seed_text = TextProcessor.preprocess_text("\n\n".join([p for p in seed_text_parts if p]))
            if seed_text:
                document_texts.append(seed_text)
                all_text = f"\n\n=== PROMPT_INPUT ===\n{seed_text}"
                project.files.append({
                    "filename": "prompt_input.txt",
                    "size": len(seed_text)
                })

        # External research is an additional evidence stream. It does not replace
        # user-provided inputs; it becomes a provisional source packet that the
        # ontology, graph, and later agents can audit and debate.
        try:
            research_packet = ExternalResearchService().collect(
                prompt=simulation_requirement,
                additional_context=additional_context,
            )
            research_markdown = research_packet.get("markdown") if isinstance(research_packet, dict) else ""
            if research_markdown:
                ProjectManager.save_external_research(project.project_id, research_packet)
                project.external_research = {
                    "enabled": True,
                    "source_count": len(research_packet.get("sources", [])),
                    "query_count": len(research_packet.get("queries", [])),
                }
                document_texts.append(research_markdown)
                all_text += f"\n\n=== EXTERNAL_RESEARCH_PACKET ===\n{research_markdown}"
                project.files.append({
                    "filename": "external_research_packet.md",
                    "size": len(research_markdown)
                })
                logger.info(
                    "External research packet added. sources=%s queries=%s",
                    project.external_research["source_count"],
                    project.external_research["query_count"],
                )
        except Exception as research_exc:
            # Research should improve grounding, not block prompt/file workflows.
            logger.warning("External research discovery skipped: %s", research_exc)
        
        if not document_texts:
            ProjectManager.delete_project(project.project_id)
            return jsonify({
                "success": False,
                "error": t('api.noDocProcessed')
            }), 400
        
        logger.info(f"Input extraction complete. mode={input_mode}, documents={len(document_texts)}")
        
        # Persist extracted text.
        project.total_text_length = len(all_text)
        ProjectManager.save_extracted_text(project.project_id, all_text)
        logger.info(f"Text extraction complete. characters={len(all_text)}")

        domain_contract = ProjectManager.get_domain_contract(project.project_id)
        if not domain_contract:
            try:
                plan = DomainSimulationPlanner().plan(
                    user_question=simulation_requirement,
                    document_text=all_text,
                    project_id=project.project_id,
                )
                plan["generation_seed"] = project.generation_seed
                domain_contract = build_domain_contract(
                    plan,
                    simulation_requirement,
                    project_id=project.project_id,
                    approved=True,
                )
                ProjectManager.save_domain_contract(project.project_id, domain_contract)
            except Exception as contract_exc:
                logger.warning("Domain Contract generation during ontology step failed: %s", contract_exc)
                domain_contract = {}
        else:
            domain_contract["approved"] = True
            ProjectManager.save_domain_contract(project.project_id, domain_contract)

        if domain_contract:
            project.domain_contract = domain_contract
            project.evidence = list(domain_contract.get("evidence") or [])
            project.instructions = list(domain_contract.get("instructions") or [])
            project.targets = list(domain_contract.get("targets") or [])
            project.actors = list(domain_contract.get("actors") or [])
            project.output_requirements = list(domain_contract.get("output_requirements") or [])
            project.rejected_prompt_fragments = list(domain_contract.get("rejected_prompt_fragments") or [])

        contract_plan = domain_plan_from_contract(domain_contract)
        contract_context = (
            "=== APPROVED_DOMAIN_CONTRACT ===\n"
            f"{domain_contract}\n\n"
            "The ontology must derive actors, targets, evidence lanes, and relationships from this approved contract. "
            "Treat rejected_prompt_fragments as instructions/output format, not actors or targets."
        )
        
        # Generate ontology fresh for every project. We do not reuse previous
        # ontology/agent outputs; the project generation seed nudges the LLM and
        # fallback path to vary secondary actors on repeat prompts.
        logger.info("Generating ontology definition...")
        generator = OntologyGenerator()
        ontology = generator.generate(
            document_texts=document_texts,
            simulation_requirement=contract_plan.get("user_question") or simulation_requirement,
            additional_context="\n\n".join(part for part in [contract_context, additional_context] if part),
            generation_seed=project.generation_seed,
        )
        
        # Save ontology to the project.
        entity_count = len(ontology.get("entity_types", []))
        edge_count = len(ontology.get("edge_types", []))
        logger.info(f"Ontology generation complete: entity_types={entity_count}, edge_types={edge_count}")
        
        project.ontology = {
            "entity_types": ontology.get("entity_types", []),
            "edge_types": ontology.get("edge_types", [])
        }
        project.analysis_summary = ontology.get("analysis_summary", "")
        project.status = ProjectStatus.ONTOLOGY_GENERATED
        ProjectManager.save_project(project)
        logger.info(f"=== Ontology generation finished === project_id={project.project_id}")
        
        return jsonify({
            "success": True,
            "data": {
                "project_id": project.project_id,
                "project_name": project.name,
                "ontology": project.ontology,
                "analysis_summary": project.analysis_summary,
                "generation_seed": project.generation_seed,
                "external_research": project.external_research,
                "domain_contract": project.domain_contract,
                "evidence": project.evidence,
                "instructions": project.instructions,
                "targets": project.targets,
                "actors": project.actors,
                "output_requirements": project.output_requirements,
                "rejected_prompt_fragments": project.rejected_prompt_fragments,
                "files": project.files,
                "total_text_length": project.total_text_length
            }
        })
        
    except Exception as e:
        if project:
            project.status = ProjectStatus.FAILED
            project.error = str(e)
            ProjectManager.save_project(project)
        logger.error(f"Ontology generation failed: {e}")
        logger.error(traceback.format_exc())
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


# ============== Endpoint 2: Build Graph ==============

@graph_bp.route('/build', methods=['POST'])
def build_graph():
    """
    Build a graph from a generated ontology and extracted text.
    """
    try:
        logger.info("=== Starting graph build ===")

        # Parse request.
        data = request.get_json() or {}
        project_id = data.get('project_id')
        # Horizon XL should not block the flow on external graph memory.
        # Use the deterministic local graph by default; callers can opt into
        # Zep explicitly with {"use_zep_graph": true}.
        use_zep_graph = bool(data.get("use_zep_graph")) and not _is_placeholder_secret(Config.ZEP_API_KEY)
        use_local_graph = not use_zep_graph
        logger.debug(f"Request params: project_id={project_id}")
        
        if not project_id:
            return jsonify({
                "success": False,
                "error": t('api.requireProjectId')
            }), 400
        
        # Load project.
        project = ProjectManager.get_project(project_id)
        if not project:
            return jsonify({
                "success": False,
                "error": t('api.projectNotFound', id=project_id)
            }), 404

        # Check project status.
        force = data.get('force', False)
        
        if project.status == ProjectStatus.CREATED:
            return jsonify({
                "success": False,
                "error": t('api.ontologyNotGenerated')
            }), 400
        
        if project.status == ProjectStatus.GRAPH_BUILDING and not force:
            return jsonify({
                "success": False,
                "error": t('api.graphBuilding'),
                "task_id": project.graph_build_task_id
            }), 400
        
        # Reset status when a rebuild is explicitly requested.
        if force and project.status in [ProjectStatus.GRAPH_BUILDING, ProjectStatus.FAILED, ProjectStatus.GRAPH_COMPLETED]:
            project.status = ProjectStatus.ONTOLOGY_GENERATED
            project.graph_id = None
            project.graph_build_task_id = None
            project.error = None
        
        # Build configuration.
        graph_name = data.get('graph_name', project.name or 'Horizon XL Graph')
        chunk_size = data.get('chunk_size', project.chunk_size or Config.DEFAULT_CHUNK_SIZE)
        chunk_overlap = data.get('chunk_overlap', project.chunk_overlap or Config.DEFAULT_CHUNK_OVERLAP)
        
        # Update project configuration.
        project.chunk_size = chunk_size
        project.chunk_overlap = chunk_overlap
        
        # Load extracted text.
        text = ProjectManager.get_extracted_text(project_id)
        if not text:
            return jsonify({
                "success": False,
                "error": t('api.textNotFound')
            }), 400
        
        # Load ontology.
        ontology = project.ontology
        if not ontology:
            return jsonify({
                "success": False,
                "error": t('api.ontologyNotFound')
            }), 400
        domain_contract = ProjectManager.get_domain_contract(project_id) or {}
        
        # Create async task.
        task_manager = TaskManager()
        task_id = task_manager.create_task(f"Build graph: {graph_name}")
        logger.info(f"Created graph build task: task_id={task_id}, project_id={project_id}")
        
        # Update project status.
        project.status = ProjectStatus.GRAPH_BUILDING
        project.graph_build_task_id = task_id
        ProjectManager.save_project(project)

        def complete_with_local_graph(mode: str, reason: str = ""):
            """Build a deterministic local graph when external graph memory is unavailable."""
            if reason:
                logger.warning(f"[{task_id}] Falling back to local graph. reason={reason}")
            task_manager.update_task(
                task_id,
                status=TaskStatus.PROCESSING,
                message="Building local graph from ontology...",
                progress=70
            )

            graph_id = f"local_{uuid.uuid4().hex[:16]}"
            graph_data = _build_local_graph_from_ontology(
                graph_id,
                ontology,
                simulation_requirement=(domain_contract.get("user_question") if domain_contract else None) or project.simulation_requirement or "",
                generation_seed=project.generation_seed or "",
                parameter_context="\n\n".join(
                    part for part in [
                        text or "",
                        project.simulation_requirement or "",
                    ] if part
                ),
                domain_contract=domain_contract,
            )
            ProjectManager.save_local_graph(project_id, graph_data)

            project.graph_id = graph_id
            project.status = ProjectStatus.GRAPH_COMPLETED
            project.error = None
            ProjectManager.save_project(project)

            node_count = graph_data.get("node_count", 0)
            edge_count = graph_data.get("edge_count", 0)

            task_manager.update_task(
                task_id,
                status=TaskStatus.COMPLETED,
                message=t('progress.graphBuildComplete'),
                progress=100,
                result={
                    "project_id": project_id,
                    "graph_id": graph_id,
                    "node_count": node_count,
                    "edge_count": edge_count,
                    "chunk_count": 0,
                    "mode": mode,
                    "fallback_reason": reason,
                }
            )
            return graph_id, graph_data

        # Local fallback mode: if Zep is not configured, build a visual graph directly from ontology.
        if use_local_graph:
            logger.info(f"[{task_id}] ZEP_API_KEY is not configured. Using local graph fallback.")
            complete_with_local_graph(
                mode="local_ontology_fallback",
                reason="ZEP_API_KEY is not configured"
            )

            return jsonify({
                "success": True,
                "data": {
                    "project_id": project_id,
                    "task_id": task_id,
                    "message": t('api.graphBuildStarted', taskId=task_id),
                    "mode": "local_ontology_fallback",
                }
            })
        
        # Capture locale before spawning background thread
        current_locale = get_locale()

        # Start background task.
        def build_task():
            set_locale(current_locale)
            build_logger = get_logger('horizonxl.build')
            try:
                build_logger.info(f"[{task_id}] Starting graph build...")
                task_manager.update_task(
                    task_id, 
                    status=TaskStatus.PROCESSING,
                    message=t('progress.initGraphService')
                )
                
                # Create graph build service.
                builder = GraphBuilderService(api_key=Config.ZEP_API_KEY)
                
                # Chunk text.
                task_manager.update_task(
                    task_id,
                    message=t('progress.textChunking'),
                    progress=5
                )
                chunks = TextProcessor.split_text(
                    text, 
                    chunk_size=chunk_size, 
                    overlap=chunk_overlap
                )
                total_chunks = len(chunks)
                
                # Create graph.
                task_manager.update_task(
                    task_id,
                    message=t('progress.creatingZepGraph'),
                    progress=10
                )
                graph_id = builder.create_graph(name=graph_name)
                
                # Update project graph_id.
                project.graph_id = graph_id
                ProjectManager.save_project(project)
                
                # Set ontology.
                task_manager.update_task(
                    task_id,
                    message=t('progress.settingOntology'),
                    progress=15
                )
                builder.set_ontology(graph_id, ontology)
                
                # Add text. The progress_callback signature is (msg, progress_ratio).
                def add_progress_callback(msg, progress_ratio):
                    progress = 15 + int(progress_ratio * 40)  # 15% - 55%
                    task_manager.update_task(
                        task_id,
                        message=msg,
                        progress=progress
                    )
                
                task_manager.update_task(
                    task_id,
                    message=t('progress.addingChunks', count=total_chunks),
                    progress=15
                )
                
                episode_uuids = builder.add_text_batches(
                    graph_id, 
                    chunks,
                    batch_size=3,
                    progress_callback=add_progress_callback
                )
                
                # Wait until Zep has processed each episode.
                task_manager.update_task(
                    task_id,
                    message=t('progress.waitingZepProcess'),
                    progress=55
                )
                
                def wait_progress_callback(msg, progress_ratio):
                    progress = 55 + int(progress_ratio * 35)  # 55% - 90%
                    task_manager.update_task(
                        task_id,
                        message=msg,
                        progress=progress
                    )
                
                builder._wait_for_episodes(episode_uuids, wait_progress_callback)
                
                # Fetch graph data.
                task_manager.update_task(
                    task_id,
                    message=t('progress.fetchingGraphData'),
                    progress=95
                )
                graph_data = builder.get_graph_data(graph_id)
                
                # Update project status.
                project.status = ProjectStatus.GRAPH_COMPLETED
                ProjectManager.save_project(project)
                
                node_count = graph_data.get("node_count", 0)
                edge_count = graph_data.get("edge_count", 0)
                build_logger.info(f"[{task_id}] Graph build complete: graph_id={graph_id}, nodes={node_count}, edges={edge_count}")
                
                # Complete task.
                task_manager.update_task(
                    task_id,
                    status=TaskStatus.COMPLETED,
                    message=t('progress.graphBuildComplete'),
                    progress=100,
                    result={
                        "project_id": project_id,
                        "graph_id": graph_id,
                        "node_count": node_count,
                        "edge_count": edge_count,
                        "chunk_count": total_chunks
                    }
                )
                
            except Exception as e:
                # External graph memory can fail because of expired/invalid keys.
                # Keep the user moving by producing a local graph instead of
                # leaving the project in a dead failed state.
                error_text = str(e)
                if "401" in error_text or "unauthorized" in error_text.lower():
                    build_logger.warning(f"[{task_id}] Zep authorization failed; using local graph fallback: {error_text}")
                    complete_with_local_graph(
                        mode="local_graph_after_zep_auth_failure",
                        reason="Zep authorization failed"
                    )
                    return

                # Mark project as failed for non-auth errors.
                build_logger.error(f"[{task_id}] Graph build failed: {error_text}")
                build_logger.debug(traceback.format_exc())
                
                project.status = ProjectStatus.FAILED
                project.error = error_text
                ProjectManager.save_project(project)
                
                task_manager.update_task(
                    task_id,
                    status=TaskStatus.FAILED,
                    message=t('progress.buildFailed', error=str(e)),
                    error=traceback.format_exc()
                )
        
        # Start background thread.
        thread = threading.Thread(target=build_task, daemon=True)
        thread.start()
        
        return jsonify({
            "success": True,
            "data": {
                "project_id": project_id,
                "task_id": task_id,
                "message": t('api.graphBuildStarted', taskId=task_id)
            }
        })
        
    except Exception as e:
        logger.error(f"启动图谱构建失败: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== Task Query Endpoints ==============

@graph_bp.route('/task/<task_id>', methods=['GET'])
def get_task(task_id: str):
    """
    Query task status.
    """
    task = TaskManager().get_task(task_id)
    
    if not task:
        return jsonify({
            "success": False,
            "error": t('api.taskNotFound', id=task_id)
        }), 404
    
    return jsonify({
        "success": True,
        "data": task.to_dict()
    })


@graph_bp.route('/tasks', methods=['GET'])
def list_tasks():
    """
    List all tasks.
    """
    tasks = TaskManager().list_tasks()
    
    return jsonify({
        "success": True,
        "data": tasks,
        "count": len(tasks)
    })


# ============== Graph Data Endpoints ==============

@graph_bp.route('/data/<graph_id>', methods=['GET'])
def get_graph_data(graph_id: str):
    """
    Get graph data, including nodes and edges.
    """
    try:
        if graph_id.startswith("local_"):
            local_graph = get_or_repair_local_graph(graph_id)
            if not local_graph:
                return jsonify({
                    "success": True,
                    "data": {
                        "graph_id": graph_id,
                        "nodes": [],
                        "edges": [],
                        "node_count": 0,
                        "edge_count": 0,
                        "mode": "missing_local_graph",
                        "stale": True,
                        "warning": (
                            f"Graph memory for {graph_id} is unavailable. "
                            "The simulation chat can continue from saved report and simulation state."
                        ),
                    }
                })
            return jsonify({
                "success": True,
                "data": local_graph
            })

        if _is_placeholder_secret(Config.ZEP_API_KEY):
            return jsonify({
                "success": False,
                "error": t('api.zepApiKeyMissing')
            }), 500
        
        builder = GraphBuilderService(api_key=Config.ZEP_API_KEY)
        graph_data = builder.get_graph_data(graph_id)
        
        return jsonify({
            "success": True,
            "data": graph_data
        })
        
    except Exception as e:
        logger.error(f"Error getting graph data for {graph_id}: {str(e)}")
        return jsonify({
            "success": True,
            "data": {
                "graph_id": graph_id,
                "nodes": [],
                "edges": [],
                "node_count": 0,
                "edge_count": 0,
                "mode": "degraded_graph_error",
                "stale": True,
                "warning": (
                    f"Graph memory could not be loaded for {graph_id}: {str(e)}. "
                    "The structured simulation can continue from prompt, uploaded context, "
                    "external research, and saved simulation state."
                ),
            }
        })


@graph_bp.route('/delete/<graph_id>', methods=['DELETE'])
def delete_graph(graph_id: str):
    """
    Delete a Zep graph.
    """
    try:
        if graph_id.startswith("local_"):
            project = ProjectManager.get_project_by_graph_id(graph_id)
            if not project:
                return jsonify({
                    "success": False,
                    "error": f"Graph does not exist: {graph_id}"
                }), 404
            project.graph_id = None
            project.graph_build_task_id = None
            ProjectManager.save_project(project)
            return jsonify({
                "success": True,
                "message": t('api.graphDeleted', id=graph_id)
            })

        if _is_placeholder_secret(Config.ZEP_API_KEY):
            return jsonify({
                "success": False,
                "error": t('api.zepApiKeyMissing')
            }), 500
        
        builder = GraphBuilderService(api_key=Config.ZEP_API_KEY)
        builder.delete_graph(graph_id)
        
        return jsonify({
            "success": True,
            "message": t('api.graphDeleted', id=graph_id)
        })
        
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500
