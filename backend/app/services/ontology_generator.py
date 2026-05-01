"""
Ontology generation service.
Endpoint 1 analyzes user input and creates entity/relation type definitions.
"""

import json
import logging
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
        "market": [
            "stock", "bond", "equity", "yield", "volatility", "earnings", "rates",
            "credit spread", "market shock", "portfolio", "index", "liquidity",
        ],
        "business": [
            "business strategy", "sales", "customer", "pricing", "competitor", "market share",
            "go-to-market", "retention", "churn", "product launch", "brand",
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
        scores[domain] = sum(1 for keyword in keywords if keyword in lowered)

    # Prefer concrete event domains when scores tie with broad macro/market language.
    priority = ["election", "oil", "ai_future", "geopolitics", "business", "social", "market", "macro"]
    return max(priority, key=lambda domain: (scores.get(domain, 0), -priority.index(domain))) if any(scores.values()) else "other"


# Ontology generation system prompt.
ONTOLOGY_SYSTEM_PROMPT = """You are an expert knowledge-graph ontology designer. Analyze the supplied documents and simulation requirement, then design entity and relationship types for a social/public-opinion simulation.

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

1. Return exactly 10 entity types.
2. The final two entity types must be `Person` and `Organization`.
3. The first eight entity types should be specific actors inferred from the input.
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
        additional_context: Optional[str] = None
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
            additional_context
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
                temperature=0.3,
                max_tokens=4096
            )
        except Exception as exc:
            logger.warning("Ontology LLM generation failed; using deterministic fallback: %s", exc)
            result = self._fallback_ontology(simulation_requirement, document_texts, additional_context)
        
        # Validate and post-process.
        result = self._validate_and_process(result)
        
        return result

    def generate_fallback(
        self,
        document_texts: List[str],
        simulation_requirement: str,
        additional_context: Optional[str] = None
    ) -> Dict[str, Any]:
        """Generate a deterministic ontology without calling the LLM."""
        return self._validate_and_process(
            self._fallback_ontology(simulation_requirement, document_texts, additional_context)
        )
    
    # Maximum text sent to the LLM.
    MAX_TEXT_LENGTH_FOR_LLM = 50000
    
    def _build_user_message(
        self,
        document_texts: List[str],
        simulation_requirement: str,
        additional_context: Optional[str]
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
        
        message += """
Design entity types and relationship types for a social/public-opinion simulation.

Required rules:
1. Return exactly 10 entity types.
2. The final two must be fallback types: Person and Organization.
3. The first eight must be specific actor types based on the input.
4. Entity types must be real actors, not abstract concepts.
5. Attribute names cannot use reserved names such as name, uuid, or group_id.
"""
        
        return message

    def _fallback_ontology(
        self,
        simulation_requirement: str,
        document_texts: List[str],
        additional_context: Optional[str]
    ) -> Dict[str, Any]:
        """Return a safe English ontology when the LLM is unavailable or returns invalid JSON."""
        text = " ".join([simulation_requirement or "", additional_context or "", " ".join(document_texts or [])]).lower()
        domain = _score_domain(text)

        if domain == "election":
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
            summary = "Fallback election ontology for parties, voter blocs, regional observers, alliances, turnout, and polling."
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
            'Generated by Horizon XL for social/public-opinion simulation',
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
