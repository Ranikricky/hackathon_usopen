"""
Ontology generation service.
Endpoint 1 analyzes user input and creates entity/relation type definitions.
"""

import json
import logging
import random
import re
from typing import Dict, Any, List, Optional
from ..utils.llm_client import LLMClient
from ..utils.locale import get_language_instruction

logger = logging.getLogger(__name__)


def _to_pascal_case(name: str) -> str:
    """Convert arbitrary names to PascalCase."""
    parts = re.split(r'[^a-zA-Z0-9]+', name)
    words = []
    for part in parts:
        words.extend(re.sub(r'([a-z])([A-Z])', r'\1_\2', part).split('_'))
    result = ''.join(word.capitalize() for word in words if word)
    return result if result else 'Unknown'


def _keyword_present(text: str, keyword: str) -> bool:
    """Match keywords as terms so short strings like 'ai' do not match random words."""
    pattern = re.escape(keyword.lower()).replace(r"\ ", r"\s+")
    return re.search(rf"(?<![a-z0-9]){pattern}(?![a-z0-9])", text) is not None


def _score_domain(text: str) -> str:
    """Infer the most likely simulation domain from prompt/context text."""
    lowered = text.lower()
    domain_keywords = {
        "election": [
            "election", "assembly", "lok sabha", "vote share", "seat share", "turnout",
            "campaign", "polling", "candidate", "party", "alliance", "constituency",
            "voter", "swing", "bjp", "tmc", "congress", "cpi", "left front", "west bengal",
        ],
        "oil": [
            "oil", "brent", "wti", "opec", "crude", "barrel", "inventory", "refinery",
            "shale", "spare capacity", "tanker", "lng", "diesel", "gasoline",
        ],
        "ai_future": [
            "ai", "artificial intelligence", "frontier model", "model capability",
            "open-source", "open source", "enterprise adoption", "gpu", "compute",
            "capex", "foundation model", "llm", "automation",
        ],
        "geopolitics": [
            "geopolitic", "war", "sanction", "diplomacy", "conflict", "military",
            "border", "country risk", "treaty", "ceasefire", "escalation", "alliance",
        ],
        "transport": [
            "transport", "transportation", "transit", "commute", "commuter", "metro",
            "subway", "bus", "rail", "train", "traffic", "strike", "labor action",
            "public service", "city service", "ridership", "delay", "route",
        ],
        "market": [
            "stock", "bond", "equity", "yield", "volatility", "earnings", "rates",
            "credit spread", "market shock", "portfolio", "index", "liquidity",
        ],
        "business": [
            "business strategy", "sales", "customer", "pricing", "competitor", "market share",
            "go-to-market", "retention", "churn", "product launch", "brand",
        ],
        "healthcare": [
            "healthcare", "hospital", "patient", "doctor", "clinic", "drug", "vaccine",
            "public health", "insurance", "medicare", "medical", "pharma", "disease",
            "epidemic", "pandemic", "clinical", "health system",
        ],
        "climate": [
            "climate", "carbon", "emissions", "renewable", "solar", "wind", "grid",
            "energy transition", "ev", "electric vehicle", "battery", "net zero",
            "weather", "drought", "flood", "heatwave", "adaptation",
        ],
        "real_estate": [
            "real estate", "housing price", "home price", "mortgage", "rent", "rental",
            "office vacancy", "commercial property", "developer", "landlord", "tenant",
            "construction", "property market", "housing supply",
        ],
        "crypto": [
            "crypto", "bitcoin", "ethereum", "stablecoin", "defi", "blockchain",
            "token", "exchange", "wallet", "mining", "validator", "on-chain",
            "liquidity pool", "etf", "halving",
        ],
        "supply_chain": [
            "supply chain", "logistics", "shipping", "port", "freight", "container",
            "supplier", "semiconductor", "inventory backlog", "lead time", "factory",
            "manufacturing", "procurement", "warehouse",
        ],
        "education": [
            "education", "school", "student", "teacher", "university", "college",
            "curriculum", "exam", "tuition", "enrollment", "edtech", "learning",
            "campus", "admissions",
        ],
        "policy": [
            "policy", "regulation", "regulator", "law", "bill", "legislation",
            "court", "compliance", "tax", "subsidy", "tariff", "permit",
            "public policy", "agency rule",
        ],
        "technology": [
            "technology adoption", "software", "saas", "platform", "app", "developer",
            "cloud", "cybersecurity", "product adoption", "hardware", "device",
            "telecom", "network", "api", "subscription",
        ],
        "sports": [
            "sports", "team", "league", "match", "tournament", "player", "coach",
            "injury", "fixture", "season", "playoff", "odds", "win probability",
        ],
        "consumer": [
            "consumer", "retail", "shopping", "brand", "fashion", "restaurant",
            "spending", "household", "loyalty", "price sensitivity", "demand trend",
            "category", "store traffic",
        ],
        "social": [
            "social", "media narrative", "public opinion", "consumer trend", "culture",
            "influencer", "sentiment", "viral", "community", "discourse",
        ],
        "macro": [
            "unemployment", "gdp", "inflation", "interest rate", "recession", "macro",
            "federal reserve", "central bank", "credit", "housing", "labor market",
        ],
    }

    scores = {}
    for domain, keywords in domain_keywords.items():
        scores[domain] = sum(1 for keyword in keywords if _keyword_present(lowered, keyword))

    # Prefer concrete event domains when scores tie with broad macro/market language.
    priority = [
        "election", "oil", "ai_future", "geopolitics", "transport", "healthcare", "climate",
        "real_estate", "crypto", "supply_chain", "education", "policy",
        "technology", "sports", "consumer", "business", "social", "market", "macro",
    ]
    return max(priority, key=lambda domain: (scores.get(domain, 0), -priority.index(domain))) if any(scores.values()) else "other"


def _extract_explicit_agent_types(text: str, limit: int = 16) -> List[tuple[str, str]]:
    """Extract user-provided agent roles from prompts before falling back to domain templates."""
    if not text:
        return []

    normalized = text.replace("—", "-").replace("–", "-")
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
    candidate_text = candidate_text.replace("/", " ")

    # Keep the right side of explanatory clauses such as "... - TMC strategist, BJP strategist".
    if " - " in candidate_text:
        candidate_text = candidate_text.split(" - ", 1)[1]

    raw_items = re.split(r",|;|\n|\band\b", candidate_text, flags=re.IGNORECASE)
    stop_phrases = {
        "as previously defined", "target variables", "time pocket simulation plan",
        "scenario paths", "data tables", "final horizon xl prompt", "defined",
    }
    results: List[tuple[str, str]] = []
    seen = set()

    for raw in raw_items:
        raw = re.split(r"\.|\bforecast\b|\btarget variables\b", raw, maxsplit=1, flags=re.IGNORECASE)[0]
        cleaned = re.sub(r"[^a-zA-Z0-9 /_-]+", " ", raw).strip(" -_/").strip()
        cleaned = re.sub(r"\s+", " ", cleaned)
        lowered = cleaned.lower()
        if not cleaned or lowered in stop_phrases:
            continue
        if len(cleaned) < 4 or len(cleaned.split()) > 6:
            continue
        if not any(keyword in lowered for keyword in [
            "agent", "strategist", "observer", "analyst", "pollster", "worker",
            "watchdog", "beneficiary", "voter", "journalist", "negotiator",
            "representative", "bloc", "official", "regulator", "trader",
            "producer", "buyer", "supplier", "developer", "operator",
            "delegate", "refiner", "minister", "reporter", "campaign",
            "party", "media", "business", "consumer",
        ]):
            continue

        entity_name = _to_pascal_case(cleaned)
        if entity_name in {"Agent", "Agents", "Person", "Organization"} or entity_name in seen:
            continue
        seen.add(entity_name)
        results.append((entity_name, f"User-defined simulation actor: {cleaned}."))
        if len(results) >= limit:
            break

    return results


def _select_run_agent_types(
    agents: List[tuple[str, str]],
    generation_seed: Optional[str],
    limit: int = 8
) -> List[tuple[str, str]]:
    """Keep core prompt actors but rotate secondary actors per run."""
    if len(agents) <= limit:
        return agents

    # Keep the first two roles because users usually list the central opposing
    # actors first, then vary the supporting observers and data actors.
    anchors = agents[:2]
    pool = agents[2:]
    rng = random.Random(generation_seed or random.random())
    rng.shuffle(pool)
    return anchors + pool[:max(0, limit - len(anchors))]


DOMAIN_ENTITY_MARKERS = {
    "election": [
        "voter", "party", "campaign", "poll", "turnout", "candidate",
        "constituency", "alliance", "booth", "tmc", "bjp", "congress",
        "minority", "women", "youth", "regional",
    ],
    "oil": ["opec", "oil", "crude", "brent", "wti", "shale", "refiner", "trader", "shipping", "inventory"],
    "ai_future": ["ai", "lab", "model", "open", "source", "enterprise", "regulator", "worker", "compute"],
    "geopolitics": ["state", "military", "diplomat", "sanction", "intelligence", "ally", "border", "government"],
    "transport": ["transit", "transport", "commuter", "operator", "union", "city", "traffic", "route", "ridership"],
    "market": ["investor", "trader", "issuer", "liquidity", "credit", "strategist", "portfolio", "market"],
    "business": ["executive", "customer", "competitor", "sales", "product", "channel", "investor", "business"],
    "healthcare": ["patient", "clinician", "provider", "payer", "pharma", "health", "regulator", "doctor"],
    "climate": ["utility", "renewable", "fossil", "grid", "emitter", "scientist", "community", "climate"],
    "real_estate": ["buyer", "owner", "builder", "lender", "broker", "tenant", "planner", "housing"],
    "crypto": ["protocol", "holder", "exchange", "validator", "defi", "stablecoin", "regulator", "chain"],
    "supply_chain": ["supplier", "manufacturer", "logistics", "port", "retailer", "procurement", "customs"],
    "education": ["student", "teacher", "administrator", "parent", "edtech", "employer", "researcher"],
    "policy": ["policymaker", "agency", "industry", "citizen", "advocate", "court", "budget", "analyst"],
    "technology": ["platform", "developer", "customer", "user", "security", "cloud", "product", "analyst"],
    "sports": ["team", "coach", "player", "medical", "analyst", "official", "fan", "data"],
    "consumer": ["consumer", "retailer", "brand", "influencer", "supplier", "pricing", "store", "researcher"],
    "social": ["influencer", "community", "media", "platform", "brand", "civil", "trend", "participant"],
    "macro": ["central", "bank", "economist", "labor", "business", "financial", "media", "government"],
}


def _ontology_matches_domain(result: Dict[str, Any], domain: str) -> bool:
    """Return False when an LLM response clearly reused the wrong domain's actors."""
    if domain in {"other", ""}:
        return True

    names = " ".join(
        str(entity.get("name", "")) for entity in result.get("entity_types", []) if isinstance(entity, dict)
    ).lower()
    markers = DOMAIN_ENTITY_MARKERS.get(domain) or []
    if not markers:
        return True
    return any(marker in names for marker in markers)


# Ontology generation system prompt.
ONTOLOGY_SYSTEM_PROMPT = """You are an expert knowledge-graph ontology designer. Analyze the supplied prompt, optional documents, URLs, and research context, then design entity and relationship types for a domain-general future simulation.

IMPORTANT: Return valid JSON only. Do not include markdown or commentary.

## Simulation Context

Horizon XL builds a simulation graph where each entity is a real-world actor that can speak, respond, influence others, or transmit information. Relationships describe institutional links, information flows, agreement/disagreement, reporting, regulation, collaboration, and rivalry.

Valid entities include concrete people, companies, organizations, government agencies, media outlets, platforms, and representative groups. Do not create entity types for abstract concepts, topics, moods, trends, or opinions.

## Output Format

Return this JSON shape:

```json
{
    "entity_types": [
        {
            "name": "EntityTypeNameInEnglishPascalCase",
            "description": "Short English description, max 100 characters",
            "attributes": [
                {
                    "name": "english_snake_case_attribute",
                    "type": "text",
                    "description": "Attribute description"
                }
            ],
            "examples": ["example actor 1", "example actor 2"]
        }
    ],
    "edge_types": [
        {
            "name": "RELATIONSHIP_TYPE_IN_UPPER_SNAKE_CASE",
            "description": "Short English description, max 100 characters",
            "source_targets": [
                {"source": "SourceEntityType", "target": "TargetEntityType"}
            ],
            "attributes": []
        }
    ],
    "analysis_summary": "Brief English summary of the ontology design"
}
```

## Design Rules

1. Return 6-10 entity types. These are actor categories, not the final number of simulation agents.
2. Include `Person` and `Organization` as fallback types when useful.
3. The specific entity types should be actor categories inferred from the input.
4. Every entity type must represent a real actor that could communicate or make decisions.
5. Use 6-10 relationship types and ensure source_targets reference defined entity types.
6. Each entity type should have 1-3 attributes. Do not use reserved names: `name`, `uuid`, `group_id`, `created_at`, `summary`.
7. All names, descriptions, attributes, examples, and analysis_summary must be in English.
"""


class OntologyGenerator:
    """
    Ontology generator.
    Analyzes input text and returns entity/relation type definitions.
    """
    
    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm_client = llm_client or LLMClient()
    
    def generate(
        self,
        document_texts: List[str],
        simulation_requirement: str,
        additional_context: Optional[str] = None,
        generation_seed: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Generate ontology definitions.
        
        Args:
            document_texts: Document text list.
            simulation_requirement: Simulation requirement.
            additional_context: Optional extra context.
            
        Returns:
            Ontology definition with entity_types and edge_types.
        """
        # Build user message.
        user_message = self._build_user_message(
            document_texts, 
            simulation_requirement,
            additional_context,
            generation_seed
        )
        
        lang_instruction = get_language_instruction()
        system_prompt = (
            f"{ONTOLOGY_SYSTEM_PROMPT}\n\n{lang_instruction}\n"
            "IMPORTANT: Every output field must be English only. "
            "Entity type names MUST be English PascalCase (e.g., 'PersonEntity', 'MediaOrganization'). "
            "Relationship type names MUST be English UPPER_SNAKE_CASE (e.g., 'WORKS_FOR'). "
            "Attribute names MUST be English snake_case. "
            "Descriptions, examples, and analysis_summary must also be English only."
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ]
        
        try:
            result = self.llm_client.chat_json(
                messages=messages,
                temperature=0.65,
                max_tokens=4096
            )
        except Exception as exc:
            logger.warning("Ontology LLM generation failed; using deterministic fallback: %s", exc)
            result = self._fallback_ontology(simulation_requirement, document_texts, additional_context, generation_seed)
        
        # Validate and post-process.
        result = self._validate_and_process(result)

        inferred_domain = _score_domain(
            " ".join([simulation_requirement or "", additional_context or "", " ".join(document_texts or [])])
        )
        if not _ontology_matches_domain(result, inferred_domain):
            logger.warning(
                "Ontology LLM output did not match inferred domain '%s'; using deterministic domain fallback.",
                inferred_domain,
            )
            result = self._validate_and_process(
                self._fallback_ontology(simulation_requirement, document_texts, additional_context, generation_seed)
            )
        
        return result

    def generate_fallback(
        self,
        document_texts: List[str],
        simulation_requirement: str,
        additional_context: Optional[str] = None,
        generation_seed: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Generate a deterministic ontology without calling the LLM."""
        return self._validate_and_process(
            self._fallback_ontology(simulation_requirement, document_texts, additional_context, generation_seed)
        )
    
    # Maximum text sent to the LLM.
    MAX_TEXT_LENGTH_FOR_LLM = 50000
    
    def _build_user_message(
        self,
        document_texts: List[str],
        simulation_requirement: str,
        additional_context: Optional[str],
        generation_seed: Optional[str] = None,
    ) -> str:
        """Build the user message."""
        
        # Combine input text.
        combined_text = "\n\n---\n\n".join(document_texts)
        original_length = len(combined_text)
        
        # Truncate only the LLM analysis input; graph building still keeps full extracted text.
        if len(combined_text) > self.MAX_TEXT_LENGTH_FOR_LLM:
            combined_text = combined_text[:self.MAX_TEXT_LENGTH_FOR_LLM]
            combined_text += f"\n\n...(Input had {original_length} characters; only the first {self.MAX_TEXT_LENGTH_FOR_LLM} characters were used for ontology analysis)..."
        
        message = f"""## Simulation Requirement

{simulation_requirement}

## Document Content

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

This is a new simulation. Do not reuse any prior ontology, cached agent set, or generic default from another run.
If the prompt explicitly lists agents or an "Agent Architecture", prioritize those actor roles.
If no agents are listed, infer concrete actor roles from the specific domain, geography, topic, institutions, affected groups, data providers, and decision-makers in this prompt.
Do not default to macroeconomic, banking, central-bank, or investment-bank actors unless the prompt is actually macro/finance.
For the same prompt in a later run, it is acceptable and desirable to vary secondary observers, data actors, and representative personas while preserving the core causal actors.

Design entity types and relationship types for a domain-general future simulation.

Required rules:
1. Return 6-10 entity types. These are actor categories, not the final number of simulation agents.
2. Include fallback types Person and Organization when useful.
3. The specific actor types must be based on the input.
4. Entity types must be real actors, not abstract concepts.
5. Attribute names cannot use reserved names such as name, uuid, or group_id.
"""
        
        return message

    def _build_ontology_payload(
        self,
        entity_types: List[tuple[str, str]],
        edge_types: List[tuple[str, str, str, str]],
        summary: str
    ) -> Dict[str, Any]:
        """Build the normalized fallback ontology payload."""
        return {
            "entity_types": [
                {
                    "name": name,
                    "description": description,
                    "attributes": [
                        {"name": "role", "type": "text", "description": "Actor role in the simulation"},
                        {"name": "position", "type": "text", "description": "Public stance or institutional position"},
                    ],
                    "examples": [],
                }
                for name, description in entity_types
            ],
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
        """Return a safe English ontology when the LLM is unavailable or returns invalid JSON."""
        text = " ".join([simulation_requirement or "", additional_context or "", " ".join(document_texts or [])]).lower()
        domain = _score_domain(text)
        explicit_agents = _extract_explicit_agent_types(
            " ".join([simulation_requirement or "", additional_context or "", " ".join(document_texts or [])])
        )

        if explicit_agents:
            explicit_agents = _select_run_agent_types(explicit_agents, generation_seed)
            entity_types = explicit_agents + [
                ("Person", "Any individual person not fitting another specific type."),
                ("Organization", "Any organization not fitting another specific type."),
            ]
            actor_names = [name for name, _ in explicit_agents]
            first = actor_names[0]
            second = actor_names[1] if len(actor_names) > 1 else "Organization"
            third = actor_names[2] if len(actor_names) > 2 else "Person"
            fourth = actor_names[3] if len(actor_names) > 3 else "Organization"
            fifth = actor_names[4] if len(actor_names) > 4 else "Person"
            sixth = actor_names[5] if len(actor_names) > 5 else "Organization"
            seventh = actor_names[6] if len(actor_names) > 6 else "Person"
            eighth = actor_names[7] if len(actor_names) > 7 else "Organization"
            edge_types = [
                ("INFLUENCES", "Influences another simulation actor or voter bloc.", first, fourth),
                ("CONTESTS_WITH", "Competes or disagrees with another actor.", first, second),
                ("NEGOTIATES_WITH", "Negotiates alliances, commitments, or coordination.", third, "Organization"),
                ("REPORTS_SIGNAL", "Reports ground, polling, market, or narrative signals.", seventh, eighth),
                ("REPRESENTS", "Represents lived experience or group interests.", fifth, "Person"),
                ("ADVISES", "Provides analysis, strategy, or recommendations.", eighth, first),
                ("REACTS_TO", "Revises behavior after another actor's signal.", sixth, second),
                ("AMPLIFIES", "Amplifies information, sentiment, or turnout effects.", "Organization", fourth),
            ]
            summary = f"Fallback {domain} ontology using explicit agent roles supplied in the prompt."
            return self._build_ontology_payload(entity_types, edge_types, summary)

        if domain == "election":
            is_bengal = any(term in text for term in ["west bengal", "bengal", "tmc", "aitc", "mamata", "bjp", "lakshmir"])
            if is_bengal:
                entity_types = [
                    ("TmcCampaignStrategist", "TMC strategist managing incumbent campaign and welfare narrative."),
                    ("BjpCampaignStrategist", "BJP strategist targeting anti-incumbency and swing regions."),
                    ("LeftCongressNegotiator", "Left/Congress actor shaping alliance and vote-split dynamics."),
                    ("MinorityVoterBlocAnalyst", "Analyst tracking Muslim/minority consolidation and turnout."),
                    ("WomenWelfareVoterBloc", "Women voter bloc influenced by welfare, safety, and delivery."),
                    ("YouthEmploymentVoterBloc", "Young voter bloc focused on jobs, migration, and opportunity."),
                    ("RegionalGroundObserver", "Local observer tracking Bengal region and constituency swings."),
                    ("PollsterDataScientist", "Election analyst forecasting vote share, seats, and uncertainty."),
                    ("Person", "Any individual person not fitting another specific type."),
                    ("Organization", "Any organization not fitting another specific type."),
                ]
            else:
                entity_types = [
                    ("RulingPartyStrategist", "Incumbent party strategist managing campaign decisions."),
                    ("OppositionPartyStrategist", "Opposition party strategist targeting swing voters."),
                    ("AllianceNegotiator", "Actor shaping alliances, seat sharing, and vote transfers."),
                    ("VoterBlocAnalyst", "Analyst representing caste, class, religion, or regional voter blocs."),
                    ("WomenWelfareVoter", "Women voter bloc influenced by welfare delivery and safety issues."),
                    ("YouthEmploymentVoter", "Young voter bloc focused on jobs, migration, and opportunity."),
                    ("RegionalObserver", "Local observer tracking constituency and regional swings."),
                    ("PollsterDataAnalyst", "Election analyst producing vote and seat forecasts."),
                    ("Person", "Any individual person not fitting another specific type."),
                    ("Organization", "Any organization not fitting another specific type."),
                ]
            if is_bengal:
                edge_types = [
                    ("MOBILIZES", "Mobilizes or persuades a voter bloc.", "TmcCampaignStrategist", "WomenWelfareVoterBloc"),
                    ("TARGETS_REGION", "Focuses campaign effort on a region or constituency.", "BjpCampaignStrategist", "RegionalGroundObserver"),
                    ("NEGOTIATES_WITH", "Negotiates alliance or seat-sharing terms.", "LeftCongressNegotiator", "Organization"),
                    ("INFLUENCES_TURNOUT", "Affects turnout behavior for a voter group.", "TmcCampaignStrategist", "MinorityVoterBlocAnalyst"),
                    ("REPORTS_SIGNAL", "Reports polling, turnout, or ground-level signal.", "RegionalGroundObserver", "PollsterDataScientist"),
                    ("FORECASTS_RESULT", "Produces vote-share or seat-share forecasts.", "PollsterDataScientist", "Organization"),
                    ("REPRESENTS_VOTERS", "Represents lived concerns of a voter bloc.", "Person", "YouthEmploymentVoterBloc"),
                    ("CONTESTS_AGAINST", "Competes electorally against another party.", "TmcCampaignStrategist", "BjpCampaignStrategist"),
                ]
            else:
                edge_types = [
                    ("MOBILIZES", "Mobilizes or persuades a voter bloc.", "Organization", "VoterBlocAnalyst"),
                    ("TARGETS_REGION", "Focuses campaign effort on a region or constituency.", "Organization", "RegionalObserver"),
                    ("NEGOTIATES_WITH", "Negotiates alliance or seat-sharing terms.", "AllianceNegotiator", "Organization"),
                    ("INFLUENCES_TURNOUT", "Affects turnout behavior for a voter group.", "Organization", "VoterBlocAnalyst"),
                    ("REPORTS_SIGNAL", "Reports polling, turnout, or ground-level signal.", "RegionalObserver", "PollsterDataAnalyst"),
                    ("FORECASTS_RESULT", "Produces vote-share or seat-share forecasts.", "PollsterDataAnalyst", "Organization"),
                    ("REPRESENTS_VOTERS", "Represents lived concerns of a voter bloc.", "Person", "VoterBlocAnalyst"),
                    ("CONTESTS_AGAINST", "Competes electorally against another party.", "Organization", "Organization"),
                ]
            summary = (
                "Fallback West Bengal election ontology for TMC, BJP, Left/Congress, voter blocs, regions, turnout, and polling."
                if is_bengal else
                "Fallback election ontology for parties, voter blocs, regional observers, alliances, turnout, and polling."
            )
        elif domain == "oil":
            entity_types = [
                ("OpecDelegate", "Producer-group actor shaping coordinated supply decisions."),
                ("ShaleProducer", "Producer responding to price, costs, and financing."),
                ("DemandAnalyst", "Analyst tracking consumption and import demand."),
                ("CommodityTrader", "Market participant pricing risk and positioning."),
                ("Refiner", "Downstream actor converting crude into products."),
                ("ShippingOperator", "Logistics actor exposed to routes and chokepoints."),
                ("EnergyMinister", "Government actor shaping policy and strategic reserves."),
                ("InventoryReporter", "Data actor reporting stocks, flows, and balances."),
                ("Person", "Any individual person not fitting another specific type."),
                ("Organization", "Any organization not fitting another specific type."),
            ]
            edge_types = [
                ("SETS_SUPPLY", "Influences crude supply availability.", "OpecDelegate", "Organization"),
                ("RESPONDS_TO_PRICE", "Changes activity based on price signals.", "ShaleProducer", "CommodityTrader"),
                ("FORECASTS_DEMAND", "Produces demand and import forecasts.", "DemandAnalyst", "Organization"),
                ("TRADES_WITH", "Trades or hedges exposure with another actor.", "CommodityTrader", "Organization"),
                ("REFINES_FOR", "Converts crude supply into product demand.", "Refiner", "Organization"),
                ("DISRUPTS_ROUTE", "Affects logistics routes or shipping costs.", "ShippingOperator", "Organization"),
                ("REGULATES_MARKET", "Sets policy that affects energy markets.", "EnergyMinister", "Organization"),
                ("REPORTS_INVENTORY", "Reports inventory or supply-demand data.", "InventoryReporter", "CommodityTrader"),
            ]
            summary = "Fallback oil-market ontology for producers, demand, traders, refiners, shipping, policy, and inventories."
        elif domain == "ai_future":
            entity_types = [
                ("FrontierLab", "AI lab advancing model capability and deployment."),
                ("OpenSourceDeveloper", "Developer community diffusing AI capabilities."),
                ("EnterpriseBuyer", "Organization adopting AI into workflows."),
                ("Regulator", "Policy actor shaping AI rules and enforcement."),
                ("ComputeProvider", "Infrastructure actor supplying chips and cloud capacity."),
                ("Investor", "Capital allocator funding AI infrastructure and startups."),
                ("WorkerGroup", "Labor group affected by automation and augmentation."),
                ("ConsumerUser", "End user shaping trust, demand, and adoption."),
                ("Person", "Any individual person not fitting another specific type."),
                ("Organization", "Any organization not fitting another specific type."),
            ]
            edge_types = [
                ("BUILDS_MODEL", "Creates or improves AI model capability.", "FrontierLab", "Organization"),
                ("RELEASES_TOOLING", "Publishes open models, tools, or workflows.", "OpenSourceDeveloper", "EnterpriseBuyer"),
                ("ADOPTS_SYSTEM", "Deploys AI systems in production.", "EnterpriseBuyer", "FrontierLab"),
                ("REGULATES", "Creates rules or enforcement pressure.", "Regulator", "Organization"),
                ("SUPPLIES_COMPUTE", "Provides compute infrastructure or chips.", "ComputeProvider", "FrontierLab"),
                ("FUNDS", "Funds labs, infrastructure, or applications.", "Investor", "Organization"),
                ("RESISTS_OR_ADAPTS", "Changes behavior in response to AI impact.", "WorkerGroup", "EnterpriseBuyer"),
                ("USES_PRODUCT", "Uses AI products and shapes demand.", "ConsumerUser", "Organization"),
            ]
            summary = "Fallback AI-future ontology for labs, open source, enterprises, regulators, compute, investors, workers, and users."
        elif domain == "geopolitics":
            entity_types = [
                ("NationalGovernment", "State actor making strategic decisions."),
                ("MilitaryCommand", "Actor controlling force posture and operations."),
                ("Diplomat", "Negotiator managing talks, treaties, and de-escalation."),
                ("SanctionsAuthority", "Actor imposing or relaxing economic restrictions."),
                ("IntelligenceAnalyst", "Actor assessing risk, intent, and capabilities."),
                ("RegionalAlly", "Aligned state or bloc influencing outcomes."),
                ("HumanitarianActor", "Civilian-impact actor tracking displacement and aid."),
                ("MediaObserver", "Narrative actor reporting and framing events."),
                ("Person", "Any individual person not fitting another specific type."),
                ("Organization", "Any organization not fitting another specific type."),
            ]
            edge_types = [
                ("NEGOTIATES_WITH", "Conducts diplomatic negotiation.", "Diplomat", "NationalGovernment"),
                ("DEPLOYS_FORCE", "Changes military posture or operations.", "MilitaryCommand", "Organization"),
                ("IMPOSES_SANCTION", "Applies economic or legal restrictions.", "SanctionsAuthority", "Organization"),
                ("ASSESSES_RISK", "Analyzes capability, intent, or escalation risk.", "IntelligenceAnalyst", "NationalGovernment"),
                ("SUPPORTS_ALLY", "Provides strategic or material support.", "RegionalAlly", "NationalGovernment"),
                ("REPORTS_IMPACT", "Reports civilian or humanitarian impact.", "HumanitarianActor", "MediaObserver"),
                ("FRAMES_EVENT", "Shapes public narrative around an event.", "MediaObserver", "Organization"),
                ("CONFLICTS_WITH", "Has adversarial strategic interaction.", "NationalGovernment", "NationalGovernment"),
            ]
            summary = "Fallback geopolitics ontology for states, military actors, diplomats, sanctions, intelligence, allies, and narratives."
        elif domain == "transport":
            entity_types = [
                ("TransitAuthority", "Public agency coordinating transit service and public communications."),
                ("OperatorUnion", "Worker or union actor shaping strike action and labor demands."),
                ("CommuterSegment", "Rider group experiencing delays, substitutions, and behavior changes."),
                ("CityGovernment", "Municipal decision maker balancing service continuity and labor politics."),
                ("TrafficOperationsAnalyst", "Analyst forecasting route delays, congestion, and capacity substitution."),
                ("BusinessEmployer", "Employer affected by commute reliability and worker attendance."),
                ("MobilityProvider", "Alternative transport provider such as taxi, rideshare, cycling, or shuttle services."),
                ("LocalJournalist", "Reporter tracking public reaction, service alerts, and negotiation signals."),
                ("Person", "Any individual person not fitting another specific type."),
                ("Organization", "Any organization not fitting another specific type."),
            ]
            edge_types = [
                ("OPERATES_SERVICE", "Operates transit routes, schedules, or service capacity.", "TransitAuthority", "CommuterSegment"),
                ("NEGOTIATES_WITH", "Negotiates labor terms or strike resolution.", "OperatorUnion", "TransitAuthority"),
                ("DISRUPTS_COMMUTE", "Creates commute delays or service disruption.", "OperatorUnion", "CommuterSegment"),
                ("COORDINATES_RESPONSE", "Coordinates public response and contingency plans.", "CityGovernment", "TransitAuthority"),
                ("FORECASTS_DELAY", "Forecasts commute delays, congestion, or route substitution.", "TrafficOperationsAnalyst", "CityGovernment"),
                ("AFFECTS_ATTENDANCE", "Changes worker attendance or operating hours.", "CommuterSegment", "BusinessEmployer"),
                ("ABSORBS_DEMAND", "Absorbs displaced travel demand.", "MobilityProvider", "CommuterSegment"),
                ("REPORTS_SIGNAL", "Reports negotiation status, ground conditions, or public sentiment.", "LocalJournalist", "Organization"),
            ]
            summary = "Fallback transport/public-service ontology for transit agencies, unions, commuters, city officials, traffic analysts, employers, mobility alternatives, and journalists."
        elif domain == "market":
            entity_types = [
                ("InstitutionalInvestor", "Large investor allocating capital and risk."),
                ("RetailInvestor", "Individual market participant shaping flows."),
                ("MarketMaker", "Liquidity provider setting spreads and execution."),
                ("CorporateIssuer", "Company whose fundamentals affect valuation."),
                ("CentralBankWatcher", "Analyst tracking policy and rate expectations."),
                ("CreditAnalyst", "Actor assessing default and balance-sheet risk."),
                ("MacroStrategist", "Analyst linking macro data to market prices."),
                ("FinancialMedia", "Media actor amplifying market narratives."),
                ("Person", "Any individual person not fitting another specific type."),
                ("Organization", "Any organization not fitting another specific type."),
            ]
            edge_types = [
                ("ALLOCATES_CAPITAL", "Moves capital across assets or sectors.", "InstitutionalInvestor", "Organization"),
                ("TRADES_ASSET", "Buys, sells, or hedges market exposure.", "RetailInvestor", "MarketMaker"),
                ("PROVIDES_LIQUIDITY", "Provides liquidity or market-making.", "MarketMaker", "Organization"),
                ("REPORTS_EARNINGS", "Releases fundamental company information.", "CorporateIssuer", "InstitutionalInvestor"),
                ("FORECASTS_POLICY", "Forecasts central-bank policy and rates.", "CentralBankWatcher", "MacroStrategist"),
                ("ASSESSES_CREDIT", "Assesses credit risk and spreads.", "CreditAnalyst", "InstitutionalInvestor"),
                ("FRAMES_MARKET", "Frames market narratives and sentiment.", "FinancialMedia", "Organization"),
                ("REACTS_TO_SHOCK", "Changes positioning after a shock.", "InstitutionalInvestor", "MarketMaker"),
            ]
            summary = "Fallback market ontology for investors, liquidity, issuers, policy watchers, credit, macro strategy, and media."
        elif domain == "business":
            entity_types = [
                ("ExecutiveTeam", "Decision makers setting company strategy."),
                ("CustomerSegment", "Buyer group shaping demand and retention."),
                ("Competitor", "Rival organization influencing market position."),
                ("SalesLeader", "Actor translating strategy into revenue execution."),
                ("ProductTeam", "Actor shaping roadmap, quality, and differentiation."),
                ("ChannelPartner", "Distributor or partner affecting reach."),
                ("IndustryAnalyst", "Expert interpreting market structure and demand."),
                ("InvestorBoard", "Capital or governance actor shaping priorities."),
                ("Person", "Any individual person not fitting another specific type."),
                ("Organization", "Any organization not fitting another specific type."),
            ]
            edge_types = [
                ("SETS_STRATEGY", "Sets strategic priorities and resource allocation.", "ExecutiveTeam", "Organization"),
                ("BUYS_FROM", "Customer segment buys from a company.", "CustomerSegment", "Organization"),
                ("COMPETES_WITH", "Competes against another organization.", "Competitor", "Organization"),
                ("DRIVES_REVENUE", "Executes sales motion or pipeline strategy.", "SalesLeader", "CustomerSegment"),
                ("BUILDS_PRODUCT", "Shapes product roadmap and capabilities.", "ProductTeam", "Organization"),
                ("DISTRIBUTES_FOR", "Expands reach through channel activity.", "ChannelPartner", "Organization"),
                ("ANALYZES_MARKET", "Analyzes market position or demand.", "IndustryAnalyst", "ExecutiveTeam"),
                ("GOVERNS", "Provides governance or capital discipline.", "InvestorBoard", "ExecutiveTeam"),
            ]
            summary = "Fallback business-strategy ontology for executives, customers, competitors, sales, product, channels, analysts, and investors."
        elif domain == "healthcare":
            entity_types = [
                ("PatientGroup", "Patient population affected by care access and outcomes."),
                ("Clinician", "Doctor, nurse, or care provider making treatment decisions."),
                ("HospitalOperator", "Provider organization managing capacity and costs."),
                ("InsurerPayer", "Payer actor shaping coverage and reimbursement."),
                ("PharmaCompany", "Drug or vaccine developer affecting treatment supply."),
                ("PublicHealthAgency", "Authority tracking disease, safety, and response."),
                ("Regulator", "Actor approving, restricting, or enforcing healthcare rules."),
                ("HealthDataAnalyst", "Analyst forecasting outcomes, demand, or burden."),
                ("Person", "Any individual person not fitting another specific type."),
                ("Organization", "Any organization not fitting another specific type."),
            ]
            edge_types = [
                ("TREATS", "Provides care or clinical intervention.", "Clinician", "PatientGroup"),
                ("OPERATES_CARE_SITE", "Runs facilities or capacity.", "HospitalOperator", "Clinician"),
                ("REIMBURSES", "Pays or reimburses care and treatment.", "InsurerPayer", "HospitalOperator"),
                ("SUPPLIES_THERAPY", "Provides drugs, vaccines, or devices.", "PharmaCompany", "HospitalOperator"),
                ("MONITORS_OUTBREAK", "Tracks health burden or public risk.", "PublicHealthAgency", "PatientGroup"),
                ("REGULATES_CARE", "Sets approval, safety, or compliance rules.", "Regulator", "Organization"),
                ("FORECASTS_DEMAND", "Forecasts patient demand or health outcomes.", "HealthDataAnalyst", "Organization"),
                ("ADVOCATES_FOR", "Advocates for access, quality, or affordability.", "PatientGroup", "Organization"),
            ]
            summary = "Fallback healthcare ontology for patients, clinicians, providers, payers, pharma, public health, regulators, and analysts."
        elif domain == "climate":
            entity_types = [
                ("EnergyUtility", "Utility actor operating grid and generation assets."),
                ("RenewableDeveloper", "Developer building clean-energy capacity."),
                ("FossilFuelProducer", "Producer exposed to transition and fuel demand."),
                ("ClimateRegulator", "Policy actor shaping emissions rules and incentives."),
                ("GridOperator", "Operator balancing reliability and transmission."),
                ("IndustrialEmitter", "Company with large emissions and abatement choices."),
                ("ClimateScientist", "Expert modeling risk, weather, and emissions pathways."),
                ("CommunityStakeholder", "Local group exposed to climate and transition impacts."),
                ("Person", "Any individual person not fitting another specific type."),
                ("Organization", "Any organization not fitting another specific type."),
            ]
            edge_types = [
                ("BUILDS_CAPACITY", "Builds generation, storage, or adaptation capacity.", "RenewableDeveloper", "EnergyUtility"),
                ("SUPPLIES_ENERGY", "Supplies fuel or power into the system.", "FossilFuelProducer", "EnergyUtility"),
                ("REGULATES_EMISSIONS", "Sets emissions rules or incentives.", "ClimateRegulator", "IndustrialEmitter"),
                ("BALANCES_GRID", "Maintains grid reliability and dispatch.", "GridOperator", "EnergyUtility"),
                ("EMITS_CARBON", "Creates emissions requiring mitigation.", "IndustrialEmitter", "Organization"),
                ("MODELS_RISK", "Models climate or transition risk.", "ClimateScientist", "ClimateRegulator"),
                ("IMPACTS_COMMUNITY", "Creates local benefits, costs, or risks.", "Organization", "CommunityStakeholder"),
                ("ADAPTS_TO_RISK", "Changes behavior in response to climate risk.", "CommunityStakeholder", "Organization"),
            ]
            summary = "Fallback climate ontology for utilities, renewables, fossil fuels, regulators, grid operators, emitters, scientists, and communities."
        elif domain == "real_estate":
            entity_types = [
                ("Homebuyer", "Buyer or renter shaping demand and affordability."),
                ("LandlordOwner", "Property owner setting rents and sale decisions."),
                ("DeveloperBuilder", "Builder creating new housing or commercial supply."),
                ("MortgageLender", "Credit provider affecting purchasing power."),
                ("RealEstateBroker", "Market intermediary observing transaction flow."),
                ("UrbanPlanner", "Policy actor shaping zoning and permits."),
                ("TenantGroup", "Renter group affected by rent and supply dynamics."),
                ("PropertyAnalyst", "Analyst forecasting prices, vacancies, and rents."),
                ("Person", "Any individual person not fitting another specific type."),
                ("Organization", "Any organization not fitting another specific type."),
            ]
            edge_types = [
                ("BUYS_OR_RENTS", "Buys or rents property from another actor.", "Homebuyer", "LandlordOwner"),
                ("BUILDS_SUPPLY", "Adds new real-estate supply.", "DeveloperBuilder", "Organization"),
                ("FINANCES_PURCHASE", "Provides mortgage or construction credit.", "MortgageLender", "Homebuyer"),
                ("BROKERS_DEAL", "Connects buyers, sellers, landlords, or tenants.", "RealEstateBroker", "Person"),
                ("ZONES_OR_PERMITS", "Shapes zoning, approvals, or permits.", "UrbanPlanner", "DeveloperBuilder"),
                ("SETS_RENT", "Sets rent or lease terms.", "LandlordOwner", "TenantGroup"),
                ("FORECASTS_MARKET", "Forecasts price, rent, vacancy, or absorption.", "PropertyAnalyst", "Organization"),
                ("RESPONDS_TO_AFFORDABILITY", "Changes behavior due to affordability pressure.", "TenantGroup", "UrbanPlanner"),
            ]
            summary = "Fallback real-estate ontology for buyers, owners, builders, lenders, brokers, planners, tenants, and analysts."
        elif domain == "crypto":
            entity_types = [
                ("ProtocolDeveloper", "Developer maintaining protocol or smart contracts."),
                ("TokenHolder", "Investor or user holding crypto assets."),
                ("ExchangeOperator", "Venue providing trading and custody access."),
                ("ValidatorMiner", "Actor securing network consensus."),
                ("DefiProtocol", "On-chain protocol offering financial functions."),
                ("StablecoinIssuer", "Issuer managing stablecoin reserves and redemption."),
                ("CryptoRegulator", "Policy actor supervising crypto markets."),
                ("OnchainAnalyst", "Analyst interpreting blockchain flows and risk."),
                ("Person", "Any individual person not fitting another specific type."),
                ("Organization", "Any organization not fitting another specific type."),
            ]
            edge_types = [
                ("MAINTAINS_PROTOCOL", "Maintains or upgrades blockchain software.", "ProtocolDeveloper", "DefiProtocol"),
                ("TRADES_TOKEN", "Buys, sells, or transfers tokens.", "TokenHolder", "ExchangeOperator"),
                ("LISTS_ASSET", "Lists or delists crypto assets.", "ExchangeOperator", "Organization"),
                ("SECURES_NETWORK", "Validates or mines network activity.", "ValidatorMiner", "DefiProtocol"),
                ("PROVIDES_LIQUIDITY", "Provides on-chain or exchange liquidity.", "TokenHolder", "DefiProtocol"),
                ("ISSUES_STABLECOIN", "Issues and redeems stable-value tokens.", "StablecoinIssuer", "TokenHolder"),
                ("REGULATES_CRYPTO", "Sets or enforces crypto-market rules.", "CryptoRegulator", "Organization"),
                ("ANALYZES_FLOW", "Analyzes on-chain transactions and risk.", "OnchainAnalyst", "Organization"),
            ]
            summary = "Fallback crypto ontology for protocols, holders, exchanges, validators, DeFi, stablecoins, regulators, and on-chain analysts."
        elif domain == "supply_chain":
            entity_types = [
                ("Supplier", "Upstream provider of materials or components."),
                ("Manufacturer", "Producer transforming inputs into goods."),
                ("LogisticsCarrier", "Carrier moving goods through transport networks."),
                ("PortOperator", "Port or terminal actor affecting throughput."),
                ("RetailerDistributor", "Downstream actor managing inventory and demand."),
                ("ProcurementManager", "Buyer managing sourcing and supplier risk."),
                ("CustomsRegulator", "Authority affecting trade clearance and tariffs."),
                ("SupplyChainAnalyst", "Analyst forecasting bottlenecks and lead times."),
                ("Person", "Any individual person not fitting another specific type."),
                ("Organization", "Any organization not fitting another specific type."),
            ]
            edge_types = [
                ("SUPPLIES_INPUT", "Supplies materials, parts, or services.", "Supplier", "Manufacturer"),
                ("MANUFACTURES_FOR", "Produces goods for another actor.", "Manufacturer", "RetailerDistributor"),
                ("TRANSPORTS_GOODS", "Moves goods through logistics networks.", "LogisticsCarrier", "Organization"),
                ("HANDLES_CARGO", "Processes cargo through ports or terminals.", "PortOperator", "LogisticsCarrier"),
                ("ORDERS_INVENTORY", "Places orders or manages stock.", "RetailerDistributor", "Supplier"),
                ("SOURCES_FROM", "Selects and manages sourcing relationships.", "ProcurementManager", "Supplier"),
                ("CLEARS_TRADE", "Applies customs, tariff, or border rules.", "CustomsRegulator", "Organization"),
                ("FORECASTS_BOTTLENECK", "Forecasts delays, shortages, or lead times.", "SupplyChainAnalyst", "Organization"),
            ]
            summary = "Fallback supply-chain ontology for suppliers, manufacturers, logistics, ports, retailers, procurement, customs, and analysts."
        elif domain == "education":
            entity_types = [
                ("StudentGroup", "Learner population affected by outcomes and access."),
                ("TeacherFaculty", "Educator delivering instruction and assessment."),
                ("SchoolAdministrator", "Institution leader allocating resources."),
                ("ParentCommunity", "Family or community actor shaping demand and trust."),
                ("EducationRegulator", "Policy actor setting standards and funding rules."),
                ("EdtechProvider", "Technology provider affecting learning delivery."),
                ("Employer", "Labor-market actor demanding skills and credentials."),
                ("EducationResearcher", "Analyst evaluating outcomes and policy effects."),
                ("Person", "Any individual person not fitting another specific type."),
                ("Organization", "Any organization not fitting another specific type."),
            ]
            edge_types = [
                ("TEACHES", "Provides instruction or academic support.", "TeacherFaculty", "StudentGroup"),
                ("ADMINISTERS_PROGRAM", "Runs school, college, or program operations.", "SchoolAdministrator", "TeacherFaculty"),
                ("ADVOCATES_FOR_STUDENTS", "Advocates around quality, access, or safety.", "ParentCommunity", "SchoolAdministrator"),
                ("REGULATES_EDUCATION", "Sets standards, funding, or accountability.", "EducationRegulator", "Organization"),
                ("PROVIDES_PLATFORM", "Provides learning technology or curriculum tools.", "EdtechProvider", "SchoolAdministrator"),
                ("HIRES_GRADUATES", "Demands skills or credentials from learners.", "Employer", "StudentGroup"),
                ("MEASURES_OUTCOME", "Analyzes learning, enrollment, or completion outcomes.", "EducationResearcher", "Organization"),
                ("RESPONDS_TO_POLICY", "Changes behavior after policy or funding shifts.", "StudentGroup", "EducationRegulator"),
            ]
            summary = "Fallback education ontology for students, teachers, administrators, parents, regulators, edtech, employers, and researchers."
        elif domain == "policy":
            entity_types = [
                ("PolicyMaker", "Official designing laws, rules, or programs."),
                ("RegulatoryAgency", "Agency implementing and enforcing rules."),
                ("AffectedIndustry", "Industry group impacted by policy choices."),
                ("CitizenGroup", "Public group affected by policy outcomes."),
                ("LobbyistAdvocate", "Actor influencing policy on behalf of interests."),
                ("CourtLegalActor", "Legal actor interpreting or challenging policy."),
                ("BudgetOffice", "Fiscal actor estimating cost and feasibility."),
                ("PolicyAnalyst", "Expert evaluating policy impact and tradeoffs."),
                ("Person", "Any individual person not fitting another specific type."),
                ("Organization", "Any organization not fitting another specific type."),
            ]
            edge_types = [
                ("DRAFTS_POLICY", "Designs or proposes policy changes.", "PolicyMaker", "RegulatoryAgency"),
                ("ENFORCES_RULE", "Implements or enforces rules.", "RegulatoryAgency", "AffectedIndustry"),
                ("AFFECTS_PUBLIC", "Changes outcomes for citizens or households.", "PolicyMaker", "CitizenGroup"),
                ("LOBBIES", "Attempts to influence policy choices.", "LobbyistAdvocate", "PolicyMaker"),
                ("CHALLENGES_POLICY", "Challenges or interprets policy legally.", "CourtLegalActor", "RegulatoryAgency"),
                ("ESTIMATES_COST", "Estimates fiscal cost or budget impact.", "BudgetOffice", "PolicyMaker"),
                ("EVALUATES_IMPACT", "Analyzes policy effectiveness and risk.", "PolicyAnalyst", "Organization"),
                ("COMPLIES_WITH", "Adapts behavior to comply with policy.", "AffectedIndustry", "RegulatoryAgency"),
            ]
            summary = "Fallback policy ontology for policymakers, agencies, industries, citizens, advocates, courts, budgets, and analysts."
        elif domain == "technology":
            entity_types = [
                ("PlatformCompany", "Technology platform shaping product and ecosystem."),
                ("DeveloperCommunity", "Builders extending tools, apps, or integrations."),
                ("EnterpriseCustomer", "Business buyer adopting technology products."),
                ("EndUser", "Individual user shaping usage and retention."),
                ("SecurityTeam", "Actor managing cybersecurity and trust."),
                ("CloudProvider", "Infrastructure provider powering technology deployment."),
                ("ProductManager", "Actor defining roadmap and adoption strategy."),
                ("TechAnalyst", "Expert forecasting adoption, competition, and risk."),
                ("Person", "Any individual person not fitting another specific type."),
                ("Organization", "Any organization not fitting another specific type."),
            ]
            edge_types = [
                ("BUILDS_PLATFORM", "Builds or operates a technology platform.", "PlatformCompany", "DeveloperCommunity"),
                ("DEVELOPS_INTEGRATION", "Builds apps, tools, or integrations.", "DeveloperCommunity", "PlatformCompany"),
                ("ADOPTS_TECH", "Adopts product or platform in operations.", "EnterpriseCustomer", "PlatformCompany"),
                ("USES_PRODUCT", "Uses and evaluates product experience.", "EndUser", "PlatformCompany"),
                ("SECURES_SYSTEM", "Protects infrastructure, data, or users.", "SecurityTeam", "Organization"),
                ("HOSTS_SERVICE", "Provides cloud or infrastructure capacity.", "CloudProvider", "PlatformCompany"),
                ("SETS_ROADMAP", "Defines product roadmap and positioning.", "ProductManager", "DeveloperCommunity"),
                ("FORECASTS_ADOPTION", "Forecasts adoption, churn, or competitive risk.", "TechAnalyst", "Organization"),
            ]
            summary = "Fallback technology ontology for platforms, developers, customers, users, security, cloud, product, and analysts."
        elif domain == "sports":
            entity_types = [
                ("TeamManagement", "Management shaping roster and strategy."),
                ("Coach", "Coach setting tactics, preparation, and selection."),
                ("Player", "Athlete whose performance affects outcomes."),
                ("MedicalStaff", "Staff managing injuries and availability."),
                ("OpponentAnalyst", "Analyst assessing rival strengths and tactics."),
                ("LeagueOfficial", "Authority setting schedule, rules, and discipline."),
                ("FanBase", "Supporter group affecting atmosphere and demand."),
                ("SportsDataAnalyst", "Analyst forecasting performance and win probability."),
                ("Person", "Any individual person not fitting another specific type."),
                ("Organization", "Any organization not fitting another specific type."),
            ]
            edge_types = [
                ("SELECTS_PLAYER", "Selects or manages player availability.", "Coach", "Player"),
                ("SETS_TACTICS", "Sets game plan and tactical approach.", "Coach", "TeamManagement"),
                ("MANAGES_ROSTER", "Controls roster, transfers, or contracts.", "TeamManagement", "Player"),
                ("TREATS_INJURY", "Assesses or treats injury risk.", "MedicalStaff", "Player"),
                ("SCOUTS_OPPONENT", "Analyzes opponent performance and tactics.", "OpponentAnalyst", "Coach"),
                ("REGULATES_MATCH", "Sets rules, schedule, or discipline.", "LeagueOfficial", "Organization"),
                ("SUPPORTS_TEAM", "Creates demand, atmosphere, or pressure.", "FanBase", "TeamManagement"),
                ("FORECASTS_RESULT", "Forecasts score, ranking, or win probability.", "SportsDataAnalyst", "Organization"),
            ]
            summary = "Fallback sports ontology for team management, coaches, players, medical staff, analysts, officials, fans, and data analysts."
        elif domain == "consumer":
            entity_types = [
                ("ConsumerSegment", "Buyer group shaping demand and preferences."),
                ("Retailer", "Seller managing assortment, price, and availability."),
                ("BrandManager", "Actor shaping positioning and marketing."),
                ("InfluencerCreator", "Attention actor affecting tastes and demand."),
                ("SupplierManufacturer", "Producer supplying products and capacity."),
                ("PricingAnalyst", "Analyst evaluating elasticity and promotions."),
                ("StoreOperator", "Operator managing traffic, service, and inventory."),
                ("ConsumerResearcher", "Researcher tracking behavior, sentiment, and loyalty."),
                ("Person", "Any individual person not fitting another specific type."),
                ("Organization", "Any organization not fitting another specific type."),
            ]
            edge_types = [
                ("BUYS_FROM", "Purchases from retailer or brand.", "ConsumerSegment", "Retailer"),
                ("STOCKS_PRODUCT", "Carries product or manages assortment.", "Retailer", "SupplierManufacturer"),
                ("POSITIONS_BRAND", "Shapes brand message and target segment.", "BrandManager", "ConsumerSegment"),
                ("INFLUENCES_DEMAND", "Influences demand or taste formation.", "InfluencerCreator", "ConsumerSegment"),
                ("SUPPLIES_PRODUCT", "Supplies finished goods or components.", "SupplierManufacturer", "Retailer"),
                ("SETS_PRICE", "Analyzes or sets price and promotions.", "PricingAnalyst", "BrandManager"),
                ("OPERATES_STORE", "Runs store, channel, or service experience.", "StoreOperator", "Retailer"),
                ("FORECASTS_TREND", "Forecasts demand, loyalty, or category trends.", "ConsumerResearcher", "Organization"),
            ]
            summary = "Fallback consumer ontology for segments, retailers, brands, influencers, suppliers, pricing, store operators, and researchers."
        elif domain == "social":
            entity_types = [
                ("Influencer", "High-reach actor shaping attention and sentiment."),
                ("CommunityGroup", "Public group with shared identity or interest."),
                ("MediaOutlet", "Publisher framing public narratives."),
                ("PlatformModerator", "Platform actor controlling visibility and rules."),
                ("BrandActor", "Organization affected by public perception."),
                ("CivilSocietyGroup", "Advocacy actor shaping public response."),
                ("TrendAnalyst", "Actor interpreting sentiment and narrative velocity."),
                ("EverydayParticipant", "Ordinary participant creating ground-level signal."),
                ("Person", "Any individual person not fitting another specific type."),
                ("Organization", "Any organization not fitting another specific type."),
            ]
            edge_types = [
                ("AMPLIFIES_NARRATIVE", "Amplifies a public narrative or frame.", "Influencer", "CommunityGroup"),
                ("REPORTS_ON", "Reports on an actor, event, or issue.", "MediaOutlet", "Organization"),
                ("MODERATES_CONTENT", "Changes visibility or rules for content.", "PlatformModerator", "CommunityGroup"),
                ("RESPONDS_TO_SENTIMENT", "Changes behavior based on public sentiment.", "BrandActor", "EverydayParticipant"),
                ("ADVOCATES_FOR", "Advocates for a position or community.", "CivilSocietyGroup", "Organization"),
                ("ANALYZES_TREND", "Analyzes sentiment or narrative movement.", "TrendAnalyst", "Organization"),
                ("PARTICIPATES_IN", "Contributes to discourse or behavior.", "EverydayParticipant", "CommunityGroup"),
                ("OPPOSES", "Publicly opposes an actor or position.", "CommunityGroup", "Organization"),
            ]
            summary = "Fallback social-narrative ontology for influencers, communities, media, platforms, brands, civil society, analysts, and participants."
        elif domain == "macro":
            entity_types = [
                ("CentralBankOfficial", "Central bank decision-maker or research staff."),
                ("GovernmentEconomist", "Government analyst publishing economic assessments."),
                ("InvestmentBankAnalyst", "Bank economist or strategist issuing market forecasts."),
                ("AcademicEconomist", "Research economist analyzing macroeconomic conditions."),
                ("BusinessExecutive", "Company leader making hiring and investment decisions."),
                ("LaborRepresentative", "Union or worker representative describing labor conditions."),
                ("MediaOutlet", "News organization reporting economic developments."),
                ("FinancialInstitution", "Bank or lender exposed to credit and funding stress."),
                ("Person", "Any individual person not fitting another specific type."),
                ("Organization", "Any organization not fitting another specific type."),
            ]
            edge_types = [
                ("PUBLISHES_FORECAST", "Publishes a public economic forecast.", "Person", "Organization"),
                ("REPORTS_ON", "Reports on another actor or economic condition.", "MediaOutlet", "Organization"),
                ("ADVISES", "Provides analysis or policy advice.", "AcademicEconomist", "GovernmentEconomist"),
                ("REGULATES", "Regulates or supervises financial actors.", "CentralBankOfficial", "FinancialInstitution"),
                ("FINANCES", "Provides credit or liquidity to another actor.", "FinancialInstitution", "BusinessExecutive"),
                ("REPRESENTS", "Represents worker or public interests.", "LaborRepresentative", "Person"),
                ("COMMENTS_ON", "Comments on another actor's forecast or action.", "Person", "Organization"),
                ("COLLABORATES_WITH", "Collaborates on analysis or decisions.", "Organization", "Organization"),
            ]
            summary = "Fallback macroeconomic ontology for forecasting, policy, finance, media, business, and labor actors."
        else:
            entity_types = [
                ("PublicOfficial", "Official actor making public decisions."),
                ("Journalist", "Reporter or commentator covering the issue."),
                ("Expert", "Subject-matter expert providing analysis."),
                ("CommunityMember", "Affected member of the public."),
                ("AdvocacyGroup", "Group advocating a public position."),
                ("Company", "Business organization involved in the issue."),
                ("GovernmentAgency", "Public institution with authority."),
                ("MediaOutlet", "News or social media organization."),
                ("Person", "Any individual person not fitting another specific type."),
                ("Organization", "Any organization not fitting another specific type."),
            ]
            edge_types = [
                ("WORKS_FOR", "Employment or formal affiliation.", "Person", "Organization"),
                ("REPORTS_ON", "Reports on an actor or event.", "MediaOutlet", "Organization"),
                ("COMMENTS_ON", "Comments on an actor or issue.", "Person", "Organization"),
                ("SUPPORTS", "Publicly supports an actor or position.", "Person", "Organization"),
                ("OPPOSES", "Publicly opposes an actor or position.", "Person", "Organization"),
                ("REGULATES", "Regulates or supervises an organization.", "GovernmentAgency", "Organization"),
                ("COLLABORATES_WITH", "Collaborates with another actor.", "Organization", "Organization"),
            ]
            summary = "Fallback public-opinion ontology for actor, media, institution, and public-response simulation."

        return {
            "entity_types": [
                {
                    "name": name,
                    "description": description,
                    "attributes": [
                        {"name": "role", "type": "text", "description": "Actor role in the simulation"},
                        {"name": "position", "type": "text", "description": "Public stance or institutional position"},
                    ],
                    "examples": [],
                }
                for name, description in entity_types
            ],
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
    
    def _validate_and_process(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """Validate and post-process the result."""
        
        # Ensure required fields exist.
        if "entity_types" not in result:
            result["entity_types"] = []
        if "edge_types" not in result:
            result["edge_types"] = []
        if "analysis_summary" not in result:
            result["analysis_summary"] = ""
        
        # Validate entity types and track name conversions for edge source_targets.
        entity_name_map = {}
        for entity in result["entity_types"]:
            # Zep requires PascalCase entity type names.
            if "name" in entity:
                original_name = entity["name"]
                entity["name"] = _to_pascal_case(original_name)
                if entity["name"] != original_name:
                    logger.warning(f"Entity type name '{original_name}' auto-converted to '{entity['name']}'")
                entity_name_map[original_name] = entity["name"]
            if "attributes" not in entity:
                entity["attributes"] = []
            if "examples" not in entity:
                entity["examples"] = []
            # Keep descriptions within the ontology API limit.
            if len(entity.get("description", "")) > 100:
                entity["description"] = entity["description"][:97] + "..."
        
        # Validate relationship types.
        for edge in result["edge_types"]:
            # Zep requires SCREAMING_SNAKE_CASE edge type names.
            if "name" in edge:
                original_name = edge["name"]
                edge["name"] = original_name.upper()
                if edge["name"] != original_name:
                    logger.warning(f"Edge type name '{original_name}' auto-converted to '{edge['name']}'")
            # Align source_targets with converted PascalCase entity names.
            for st in edge.get("source_targets", []):
                if st.get("source") in entity_name_map:
                    st["source"] = entity_name_map[st["source"]]
                if st.get("target") in entity_name_map:
                    st["target"] = entity_name_map[st["target"]]
            if "source_targets" not in edge:
                edge["source_targets"] = []
            if "attributes" not in edge:
                edge["attributes"] = []
            if len(edge.get("description", "")) > 100:
                edge["description"] = edge["description"][:97] + "..."
        
        # Zep API limit: max 10 custom entity types and max 10 custom edge types.
        MAX_ENTITY_TYPES = 10
        MAX_EDGE_TYPES = 10

        # De-duplicate by name, keeping the first occurrence.
        seen_names = set()
        deduped = []
        for entity in result["entity_types"]:
            name = entity.get("name", "")
            if name and name not in seen_names:
                seen_names.add(name)
                deduped.append(entity)
            elif name in seen_names:
                logger.warning(f"Duplicate entity type '{name}' removed during validation")
        result["entity_types"] = deduped

        # Fallback entity type definitions.
        person_fallback = {
            "name": "Person",
            "description": "Any individual person not fitting other specific person types.",
            "attributes": [
                {"name": "full_name", "type": "text", "description": "Full name of the person"},
                {"name": "role", "type": "text", "description": "Role or occupation"}
            ],
            "examples": ["ordinary citizen", "anonymous netizen"]
        }
        
        organization_fallback = {
            "name": "Organization",
            "description": "Any organization not fitting other specific organization types.",
            "attributes": [
                {"name": "org_name", "type": "text", "description": "Name of the organization"},
                {"name": "org_type", "type": "text", "description": "Type of organization"}
            ],
            "examples": ["small business", "community group"]
        }
        
        # Check whether fallback types already exist.
        entity_names = {e["name"] for e in result["entity_types"]}
        has_person = "Person" in entity_names
        has_organization = "Organization" in entity_names
        
        # Fallback types that still need to be added.
        fallbacks_to_add = []
        if not has_person:
            fallbacks_to_add.append(person_fallback)
        if not has_organization:
            fallbacks_to_add.append(organization_fallback)
        
        if fallbacks_to_add:
            current_count = len(result["entity_types"])
            needed_slots = len(fallbacks_to_add)
            
            # If adding fallbacks exceeds the limit, remove lower-priority types.
            if current_count + needed_slots > MAX_ENTITY_TYPES:
                to_remove = current_count + needed_slots - MAX_ENTITY_TYPES
                result["entity_types"] = result["entity_types"][:-to_remove]
            
            # Add fallback types.
            result["entity_types"].extend(fallbacks_to_add)
        
        # Defensive final limit checks.
        if len(result["entity_types"]) > MAX_ENTITY_TYPES:
            result["entity_types"] = result["entity_types"][:MAX_ENTITY_TYPES]
        
        if len(result["edge_types"]) > MAX_EDGE_TYPES:
            result["edge_types"] = result["edge_types"][:MAX_EDGE_TYPES]
        
        return result
    
    def generate_python_code(self, ontology: Dict[str, Any]) -> str:
        """
        Convert ontology definitions to Python code similar to ontology.py.
        
        Args:
            ontology: Ontology definition.
            
        Returns:
            Python code string.
        """
        code_lines = [
            '"""',
            'Custom entity type definitions',
            'Generated by Horizon XL for domain-general future simulation',
            '"""',
            '',
            'from pydantic import Field',
            'from zep_cloud.external_clients.ontology import EntityModel, EntityText, EdgeModel',
            '',
            '',
            '# ============== Entity Type Definitions ==============',
            '',
        ]
        
        # Generate entity types.
        for entity in ontology.get("entity_types", []):
            name = entity["name"]
            desc = entity.get("description", f"A {name} entity.")
            
            code_lines.append(f'class {name}(EntityModel):')
            code_lines.append(f'    """{desc}"""')
            
            attrs = entity.get("attributes", [])
            if attrs:
                for attr in attrs:
                    attr_name = attr["name"]
                    attr_desc = attr.get("description", attr_name)
                    code_lines.append(f'    {attr_name}: EntityText = Field(')
                    code_lines.append(f'        description="{attr_desc}",')
                    code_lines.append(f'        default=None')
                    code_lines.append(f'    )')
            else:
                code_lines.append('    pass')
            
            code_lines.append('')
            code_lines.append('')
        
        code_lines.append('# ============== Relationship Type Definitions ==============')
        code_lines.append('')
        
        # Generate relationship types.
        for edge in ontology.get("edge_types", []):
            name = edge["name"]
            # Convert to a PascalCase class name.
            class_name = ''.join(word.capitalize() for word in name.split('_'))
            desc = edge.get("description", f"A {name} relationship.")
            
            code_lines.append(f'class {class_name}(EdgeModel):')
            code_lines.append(f'    """{desc}"""')
            
            attrs = edge.get("attributes", [])
            if attrs:
                for attr in attrs:
                    attr_name = attr["name"]
                    attr_desc = attr.get("description", attr_name)
                    code_lines.append(f'    {attr_name}: EntityText = Field(')
                    code_lines.append(f'        description="{attr_desc}",')
                    code_lines.append(f'        default=None')
                    code_lines.append(f'    )')
            else:
                code_lines.append('    pass')
            
            code_lines.append('')
            code_lines.append('')
        
        # Generate type dictionaries.
        code_lines.append('# ============== Type Configuration ==============')
        code_lines.append('')
        code_lines.append('ENTITY_TYPES = {')
        for entity in ontology.get("entity_types", []):
            name = entity["name"]
            code_lines.append(f'    "{name}": {name},')
        code_lines.append('}')
        code_lines.append('')
        code_lines.append('EDGE_TYPES = {')
        for edge in ontology.get("edge_types", []):
            name = edge["name"]
            class_name = ''.join(word.capitalize() for word in name.split('_'))
            code_lines.append(f'    "{name}": {class_name},')
        code_lines.append('}')
        code_lines.append('')
        
        # Generate edge source_targets mapping.
        code_lines.append('EDGE_SOURCE_TARGETS = {')
        for edge in ontology.get("edge_types", []):
            name = edge["name"]
            source_targets = edge.get("source_targets", [])
            if source_targets:
                st_list = ', '.join([
                    f'{{"source": "{st.get("source", "Entity")}", "target": "{st.get("target", "Entity")}"}}'
                    for st in source_targets
                ])
                code_lines.append(f'    "{name}": [{st_list}],')
        code_lines.append('}')
        
        return '\n'.join(code_lines)
