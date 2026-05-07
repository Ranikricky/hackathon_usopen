"""
Structured simulation state persistence.

This file-backed model is the bridge from the current graph/report pipeline to
the new architecture where every output must be derived from a single validated
simulation state.
"""

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..config import Config


STRUCTURED_STATE_FILENAME = "structured_state.json"


@dataclass
class StructuredSimulationState:
    simulation_id: str
    project_id: str
    domain_plan: Dict[str, Any] = field(default_factory=dict)
    agents: List[Dict[str, Any]] = field(default_factory=list)
    time_pockets: List[Dict[str, Any]] = field(default_factory=list)
    state_variables: List[Dict[str, Any]] = field(default_factory=list)
    agent_outputs: List[Dict[str, Any]] = field(default_factory=list)
    discussion_transcript: List[Dict[str, Any]] = field(default_factory=list)
    scenario_outputs: Dict[str, Any] = field(default_factory=dict)
    aggregated_outputs: Dict[str, Any] = field(default_factory=dict)
    validation: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StructuredSimulationState":
        return cls(
            simulation_id=data["simulation_id"],
            project_id=data["project_id"],
            domain_plan=data.get("domain_plan") or {},
            agents=data.get("agents") or [],
            time_pockets=data.get("time_pockets") or [],
            state_variables=data.get("state_variables") or [],
            agent_outputs=data.get("agent_outputs") or [],
            discussion_transcript=data.get("discussion_transcript") or [],
            scenario_outputs=data.get("scenario_outputs") or {},
            aggregated_outputs=data.get("aggregated_outputs") or {},
            validation=data.get("validation") or {},
            created_at=data.get("created_at") or datetime.now().isoformat(),
            updated_at=data.get("updated_at") or datetime.now().isoformat(),
        )


class SimulationStateManager:
    """Read/write structured simulation state JSON for a simulation."""

    @classmethod
    def _simulation_dir(cls, simulation_id: str) -> str:
        return os.path.join(Config.OASIS_SIMULATION_DATA_DIR, simulation_id)

    @classmethod
    def _state_path(cls, simulation_id: str) -> str:
        return os.path.join(cls._simulation_dir(simulation_id), STRUCTURED_STATE_FILENAME)

    @classmethod
    def exists(cls, simulation_id: str) -> bool:
        return os.path.exists(cls._state_path(simulation_id))

    @classmethod
    def initialize(
        cls,
        simulation_id: str,
        project_id: str,
        domain_plan: Optional[Dict[str, Any]] = None,
        agents: Optional[List[Dict[str, Any]]] = None,
    ) -> StructuredSimulationState:
        domain_plan = domain_plan or {}
        state = StructuredSimulationState(
            simulation_id=simulation_id,
            project_id=project_id,
            domain_plan=domain_plan,
            agents=agents or cls.agents_from_plan(domain_plan),
            time_pockets=cls.time_pockets_from_plan(domain_plan),
            state_variables=domain_plan.get("state_variables") or [],
            scenario_outputs=cls.empty_scenario_outputs(domain_plan),
        )
        return cls.save(state)

    @classmethod
    def load(cls, simulation_id: str) -> Optional[StructuredSimulationState]:
        path = cls._state_path(simulation_id)
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as handle:
            return StructuredSimulationState.from_dict(json.load(handle))

    @classmethod
    def save(cls, state: StructuredSimulationState) -> StructuredSimulationState:
        os.makedirs(cls._simulation_dir(state.simulation_id), exist_ok=True)
        state.updated_at = datetime.now().isoformat()
        with open(cls._state_path(state.simulation_id), "w", encoding="utf-8") as handle:
            json.dump(state.to_dict(), handle, ensure_ascii=False, indent=2)
        return state

    @classmethod
    def update_validation(cls, simulation_id: str, validation: Dict[str, Any]) -> Optional[StructuredSimulationState]:
        state = cls.load(simulation_id)
        if not state:
            return None
        state.validation = validation or {}
        return cls.save(state)

    @classmethod
    def append_agent_output(cls, simulation_id: str, output: Dict[str, Any]) -> Optional[StructuredSimulationState]:
        state = cls.load(simulation_id)
        if not state:
            return None
        state.agent_outputs.append(output)
        return cls.save(state)

    @classmethod
    def agents_from_plan(cls, domain_plan: Dict[str, Any]) -> List[Dict[str, Any]]:
        agents = []
        domain = domain_plan.get("domain", "other")
        generation_seed = domain_plan.get("generation_seed") or domain_plan.get("_generation_seed") or uuid.uuid4().hex[:12]
        population = domain_plan.get("agent_population") or {}
        allocations = {
            item.get("archetype_name"): item
            for item in population.get("allocations", [])
            if isinstance(item, dict)
        }

        idx = 0
        for archetype_index, archetype in enumerate(domain_plan.get("required_agent_archetypes") or [], start=1):
            name = archetype.get("name") or f"Agent {idx}"
            allocation = allocations.get(name) or {}
            instance_count = int(
                allocation.get("instance_count")
                or archetype.get("instance_count")
                or 1
            )
            instance_count = max(1, min(25, instance_count))
            subtypes = allocation.get("subtypes") or archetype.get("subtypes") or []
            for copy_index in range(1, instance_count + 1):
                idx += 1
                subtype = subtypes[(copy_index - 1) % len(subtypes)] if subtypes else ""
                display_name = name if instance_count == 1 else f"{name} {copy_index}"
                if subtype:
                    display_name = f"{display_name}: {subtype}"
                agents.append({
                    "agent_id": f"agent_{idx:02d}_{uuid.uuid5(uuid.NAMESPACE_DNS, f'{generation_seed}:{idx}:{name}').hex[:8]}",
                    "name": display_name,
                    "domain": domain,
                    "generation_seed": generation_seed,
                    "role": name,
                    "archetype": name,
                    "population_copy_index": copy_index,
                    "population_instance_count": instance_count,
                    "population_share": allocation.get("population_share") or archetype.get("population_share"),
                    "subtype": subtype,
                    "institutional_incentives": archetype.get("likely_bias", ""),
                    "causal_power": archetype.get("causal_role", ""),
                    "information_set": [archetype.get("information_advantage", "")],
                    "trusted_data_sources": [],
                    "ignored_or_underweighted_data": [],
                    "forecasting_method": "Context-specific judgment constrained by structured numeric outputs.",
                    "heuristics": [],
                    "biases": [archetype.get("likely_bias", "")] if archetype.get("likely_bias") else [],
                    "blind_spots": [],
                    "memory": {
                        "prior_beliefs": [],
                        "past_revisions": [],
                        "important_events_seen": [],
                    },
                    "numeric_capabilities": {
                        "must_output_numbers": bool(archetype.get("numeric_output_required", True)),
                        "allowed_units": [
                            variable.get("unit", "unit")
                            for variable in domain_plan.get("target_variables", [])
                        ],
                        "confidence_required": True,
                        "scenario_outputs_required": True,
                    },
                    "interaction_style": "Structured forecast update with concise reasoning.",
                    "revision_rules": [
                        "Update forecasts only when new evidence changes the state variables or scenario probabilities."
                    ],
                    "cognitive_profile": cls._cognitive_profile_from_role(
                        role=name,
                        display_name=display_name,
                        subtype=subtype,
                        generation_seed=generation_seed,
                        index=idx,
                    ),
                    "stakes_profile": cls._stakes_profile_from_role(
                        role=name,
                        display_name=display_name,
                        subtype=subtype,
                        archetype=archetype,
                        generation_seed=generation_seed,
                        index=idx,
                    ),
                    "persona": cls._persona_from_role(
                        role=name,
                        display_name=display_name,
                        subtype=subtype,
                        archetype=archetype,
                        generation_seed=generation_seed,
                        index=idx,
                    ),
                })
        for special_group, agent_kind in [
            ("orchestration_agents", "orchestrator"),
            ("research_agents", "research"),
        ]:
            for template in population.get(special_group, []) or []:
                for copy_index in range(1, int(template.get("count", 1)) + 1):
                    idx += 1
                    name = template.get("name") or f"{agent_kind.title()} Agent"
                    agents.append({
                        "agent_id": f"agent_{idx:02d}_{uuid.uuid5(uuid.NAMESPACE_DNS, f'{generation_seed}:{idx}:{name}:{agent_kind}').hex[:8]}",
                        "name": name if copy_index == 1 else f"{name} {copy_index}",
                        "domain": domain,
                        "generation_seed": generation_seed,
                        "role": name,
                        "archetype": name,
                        "agent_kind": agent_kind,
                        "institutional_incentives": "Maintain simulation quality, focus, and evidence discipline.",
                        "causal_power": template.get("role", ""),
                        "information_set": [template.get("role", "")],
                        "trusted_data_sources": ["simulation_state", "graph_evidence", "approved_external_research"],
                        "ignored_or_underweighted_data": [],
                        "forecasting_method": "Process-control and evidence-validation role.",
                        "heuristics": ["Keep discussion grounded in target variables and validated evidence."],
                        "biases": [],
                        "blind_spots": ["Does not represent a causal stakeholder unless explicitly assigned."],
                        "memory": {
                            "prior_beliefs": [],
                            "past_revisions": [],
                            "important_events_seen": [],
                        },
                        "numeric_capabilities": {
                            "must_output_numbers": bool(template.get("numeric_output_required", False)),
                            "allowed_units": [
                                variable.get("unit", "unit")
                                for variable in domain_plan.get("target_variables", [])
                            ],
                            "confidence_required": bool(template.get("numeric_output_required", False)),
                            "scenario_outputs_required": bool(template.get("numeric_output_required", False)),
                        },
                        "interaction_style": "Moderate, audit, research, or synthesize without inventing unsupported facts.",
                        "revision_rules": [
                            "Escalate missing evidence, numerical gaps, or off-topic drift before the next simulation pocket."
                        ],
                        "cognitive_profile": cls._cognitive_profile_from_role(
                            role=name,
                            display_name=name if copy_index == 1 else f"{name} {copy_index}",
                            subtype=agent_kind,
                            generation_seed=generation_seed,
                            index=idx,
                        ),
                        "stakes_profile": cls._stakes_profile_from_role(
                            role=name,
                            display_name=name if copy_index == 1 else f"{name} {copy_index}",
                            subtype=agent_kind,
                            archetype={"causal_role": template.get("role", "")},
                            generation_seed=generation_seed,
                            index=idx,
                        ),
                        "persona": cls._persona_from_role(
                            role=name,
                            display_name=name if copy_index == 1 else f"{name} {copy_index}",
                            subtype=agent_kind,
                            archetype={"causal_role": template.get("role", "")},
                            generation_seed=generation_seed,
                            index=idx,
                        ),
                    })
        return agents

    @classmethod
    def _persona_from_role(
        cls,
        role: str,
        display_name: str,
        subtype: str,
        archetype: Dict[str, Any],
        generation_seed: str,
        index: int,
    ) -> Dict[str, Any]:
        """Create a lightweight character card from prompt-derived role text.

        This intentionally does not use domain-specific hardcoded actors. It
        turns whatever role the planner discovered into a simulated participant
        with a stance, pressure, speaking style, and revision trigger.
        """
        role_text = " ".join(part for part in [display_name, role, subtype] if part)
        role_l = role_text.lower()
        voice_bank = [
            "plain-spoken and impatient with abstractions",
            "careful, numbers-first, and allergic to overconfidence",
            "strategic, combative, and focused on incentives",
            "ground-level, anecdotal, and sensitive to trust signals",
            "institutional, cautious, and reputation-aware",
            "skeptical, evidence-demanding, and willing to challenge consensus",
        ]
        stake_bank = [
            "being blamed if the forecast misses the lived reality",
            "missing a late swing hidden inside aggregate numbers",
            "overweighting noisy signals because they sound dramatic",
            "underestimating people who do not appear in elite data channels",
            "confusing public messaging with private behavior",
            "letting one clean story erase regional or subgroup variation",
        ]
        seed = uuid.uuid5(uuid.NAMESPACE_DNS, f"{generation_seed}:{index}:{role_text}").int
        voice = voice_bank[seed % len(voice_bank)]
        private_concern = stake_bank[(seed // 7) % len(stake_bank)]

        if any(term in role_l for term in ["strategist", "campaign", "party"]):
            vantage = "a campaign room where turnout, candidate quality, message discipline, and opponent mistakes are converted into seats"
            objective = "protect the side's route to victory while identifying where the map can break"
            default_tension = "public confidence may hide private vulnerability"
            background = "Has access to booth reports, campaign message testing, candidate weakness notes, and opponent attack lines; tends to convert every signal into seat math."
            knowledge_boundary = "Does not directly know private voter sincerity or whether turnout enthusiasm survives local candidate anger."
        elif any(term in role_l for term in ["voter", "beneficiary", "rural", "urban", "worker", "youth", "poor", "middle", "cohort"]):
            vantage = "the everyday layer of the system where policy promises, local trust, identity, prices, jobs, and safety become behavior"
            objective = "force the model to respect lived experience instead of treating people as a uniform average"
            default_tension = "what elites count as a small issue may be decisive locally"
            background = "Lives inside the affected population layer; sees household tradeoffs, local credibility, fear, fatigue, social pressure, and whether benefits are felt as real."
            knowledge_boundary = "Does not see statewide war rooms, hidden polling, or the full regional seat map."
        elif any(term in role_l for term in ["region", "regional", "north", "south", "east", "west", "belt", "hills", "border", "coastal"]):
            vantage = "a regional ground map where identity, local economy, candidate networks, and turnout machinery can diverge from the statewide story"
            objective = "stop the simulation from flattening local variation into one statewide swing"
            default_tension = "the statewide average can be right while the seat map is wrong"
            background = "Tracks local alliances, identity blocs, border or industry issues, candidate recognition, and whether statewide narratives travel into the region."
            knowledge_boundary = "May overread its region and underweight countervailing statewide momentum."
        elif any(term in role_l for term in ["pollster", "scientist", "quant", "data"]):
            vantage = "the measurement desk where noisy samples, historical baselines, missing data, and uncertainty bands are translated into numbers"
            objective = "keep the forecast numerically coherent and punish unsupported confidence"
            default_tension = "clean numbers can still be wrong if the frame is wrong"
            background = "Works from historical baselines, swing models, sample-quality checks, turnout assumptions, and scenario bands; treats anecdotes as hypotheses, not proof."
            knowledge_boundary = "Can miss local emotional shifts that are real but not yet measurable."
        elif any(term in role_l for term in ["journalist", "media", "narrative"]):
            vantage = "the public narrative layer where repeated stories shape what voters and institutions think is plausible"
            objective = "track which claims become salient enough to change behavior"
            default_tension = "attention is not the same thing as persuasion"
            background = "Sees what is repeated in public, what scandals stick, which leaders dominate airtime, and which claims become common sense."
            knowledge_boundary = "May confuse elite attention with actual voter conversion."
        elif any(term in role_l for term in ["watchdog", "auditor", "governance"]):
            vantage = "the accountability desk checking whether claims are sourced, dated, and free of future leakage"
            objective = "block invented certainty and expose missing evidence"
            default_tension = "the most polished claim may be the least audited one"
            background = "Checks dates, source quality, internal contradictions, missing denominators, and whether claims exceed the evidence."
            knowledge_boundary = "Does not forecast persuasion well; it mainly grades evidence integrity."
        elif any(term in role_l for term in ["business", "industry", "market", "firm"]):
            vantage = "the economic decision layer where investment, hiring, demand, and confidence turn politics or policy into material outcomes"
            objective = "translate high-level claims into ground-level economic behavior"
            default_tension = "headline confidence can coexist with private risk aversion"
            background = "Hears from employers, traders, contractors, suppliers, and households where jobs, investment, demand, and uncertainty bite."
            knowledge_boundary = "May overstate economic issues relative to identity, welfare, or organization."
        elif any(term in role_l for term in ["mediator", "negotiator", "alliance"]):
            vantage = "the bargaining table where coordination, trust, and fragmentation decide whether separate signals combine or cancel"
            objective = "surface the tradeoffs hidden behind public alignment"
            default_tension = "agreement on paper may not transfer into action"
            background = "Understands coordination failure, bargaining incentives, vote transfer friction, and when nominal allies quietly compete."
            knowledge_boundary = "May overfocus on elite deals and underweight voter-level behavior."
        elif "moderator" in role_l:
            vantage = "the process-control desk that evaluates contributions, assigns follow-up work, prevents drift, and decides whether the room can advance"
            objective = "turn a messy debate into an accountable sequence without copying the last speaker"
            default_tension = "the debate can sound productive while leaving weak assumptions untouched"
            background = "Trained in facilitation, adversarial questioning, evidence triage, agenda design, turn-taking, and synthesis under uncertainty."
            knowledge_boundary = "Does not own domain facts directly; it must rely on evidence, specialist agents, and explicit uncertainty."
        elif "quantitative" in role_l or "synthesizer" in role_l or "quant" in role_l:
            vantage = "the modeling desk that converts validated assumptions into numeric paths, ranges, confidence bands, and sensitivity checks"
            objective = "make numbers coherent without pretending weak assumptions are measured facts"
            default_tension = "a precise table can create fake certainty"
            background = "Trained in forecasting, uncertainty ranges, aggregation, sensitivity analysis, calibration, denominator checks, and scenario math."
            knowledge_boundary = "Can quantify only what evidence and agent inputs support; it should refuse unsupported precision."
        elif "external research" in role_l or "research scout" in role_l:
            vantage = "the source-discovery desk that looks outside graph memory for source pointers, contrary evidence, and missing context"
            objective = "expand the evidence frontier while keeping sources auditable"
            default_tension = "search results can be noisy, stale, biased, or post-cutoff"
            background = "Trained in search query design, source diversity, public web triage, literature/news/blog/social-source discovery, and source caveats."
            knowledge_boundary = "Does not turn search snippets into truth; every source must be checked by the auditor and interpreted by relevant agents."
        elif "data retrieval" in role_l:
            vantage = "the extraction desk that turns messy text into dates, units, denominators, tables, missing values, and numeric anchors"
            objective = "make sure numbers are comparable before any model uses them"
            default_tension = "similar-looking numbers may have different units, dates, or denominators"
            background = "Trained in table extraction, unit normalization, denominator checks, historical baselines, missing-data flags, and data provenance."
            knowledge_boundary = "Does not explain causality alone; it prepares numbers for agents and quant synthesis."
        elif "evidence auditor" in role_l or "auditor" in role_l:
            vantage = "the audit desk that checks source quality, cutoff compliance, unsupported claims, contradictions, and leakage"
            objective = "stop the simulation from becoming confident when evidence is weak"
            default_tension = "the most fluent explanation may be least supported"
            background = "Trained in fact-checking, source hierarchy, leakage detection, contradiction logging, evidence grading, and claim-to-source traceability."
            knowledge_boundary = "Does not decide strategy; it decides whether evidence is strong enough to support a claim."
        else:
            vantage = "a specialized vantage point created from the prompt's causal map"
            objective = "stress-test the simulation from this role's information advantage"
            default_tension = "the role may see one channel clearly while missing the wider system"
            background = "Has a narrow causal window from the prompt-derived role and uses it to stress-test the common forecast."
            knowledge_boundary = "May lack enough context outside its assigned causal channel."

        opening_position = archetype.get("causal_role") or objective
        if str(opening_position).lower().startswith("represents or moves part of the simulation outcome through"):
            opening_position = objective
        if str(opening_position).lower().startswith("represents the actual affected people implied by"):
            opening_position = "bring the ground reaction into the room before analysts smooth it into an average"

        return {
            "character_name": display_name,
            "vantage_point": vantage,
            "objective": objective,
            "voice": voice,
            "private_concern": private_concern,
            "default_tension": default_tension,
            "opening_position": opening_position,
            "background": background,
            "knowledge_boundary": knowledge_boundary,
            "speaks_strongly_about": cls._speaks_strongly_about(role_l),
        }

    @classmethod
    def _cognitive_profile_from_role(
        cls,
        role: str,
        display_name: str,
        subtype: str,
        generation_seed: str,
        index: int,
    ) -> Dict[str, Any]:
        """Give each agent distinct reasoning, social, and strategic behavior."""
        role_text = " ".join(part for part in [display_name, role, subtype] if part)
        role_l = role_text.lower()
        seed = uuid.uuid5(uuid.NAMESPACE_DNS, f"cognition:{generation_seed}:{index}:{role_text}").int
        iq = 45 + (seed % 51)
        eq = 42 + ((seed // 11) % 54)
        game = 38 + ((seed // 29) % 58)
        numeracy = 35 + ((seed // 41) % 61)
        domain_expertise = 40 + ((seed // 53) % 56)
        local_knowledge = 35 + ((seed // 67) % 61)

        reasoning_styles = [
            "Bayesian updater",
            "case-based pattern matcher",
            "incentive-first strategist",
            "ground-truth ethnographer",
            "skeptical falsifier",
            "systems thinker",
        ]
        social_styles = [
            "reads status and coalition pressure quickly",
            "notices fear, pride, and resentment before they show up in numbers",
            "overweights institutional signals and underweights informal emotion",
            "detects when public statements are strategic rather than sincere",
            "pushes quiet participants to reveal hidden constraints",
        ]
        game_theory_styles = [
            "models actors as optimizing under constraints",
            "tracks coordination failure and free-rider incentives",
            "looks for signaling, bluffing, and cheap talk",
            "tests whether threats are credible",
            "looks for principal-agent problems and hidden payoffs",
            "models repeated-game reputation effects",
        ]
        if any(term in role_l for term in ["moderator", "mediator", "negotiator"]):
            game += 10
            eq += 8
        if "moderator" in role_l:
            eq += 14
            game += 8
            domain_expertise += 6
        if any(term in role_l for term in ["quant", "data", "scientist", "research", "auditor"]):
            iq += 8
            numeracy += 18
        if "quantitative" in role_l or "synthesizer" in role_l or "quant" in role_l:
            numeracy += 22
            iq += 10
            local_knowledge -= 8
        if "external research" in role_l or "research scout" in role_l:
            domain_expertise += 12
            iq += 6
        if "data retrieval" in role_l:
            numeracy += 18
            domain_expertise += 10
        if "evidence auditor" in role_l or "auditor" in role_l:
            iq += 10
            game += 4
            eq -= 4
        if any(term in role_l for term in ["voter", "consumer", "worker", "household", "community", "beneficiary"]):
            eq += 10
            local_knowledge += 18
        if any(term in role_l for term in ["strategist", "campaign", "trader", "executive", "party", "operator"]):
            game += 12
            domain_expertise += 10

        return {
            "iq_style": reasoning_styles[(seed // 3) % len(reasoning_styles)],
            "iq_score": min(99, iq),
            "eq_style": social_styles[(seed // 5) % len(social_styles)],
            "eq_score": min(99, eq),
            "game_theory_style": game_theory_styles[(seed // 7) % len(game_theory_styles)],
            "game_theory_score": min(99, game),
            "numeracy_score": min(99, numeracy),
            "domain_expertise_score": min(99, domain_expertise),
            "local_knowledge_score": min(99, local_knowledge),
            "risk_tolerance": [
                "risk-averse",
                "risk-balanced",
                "risk-seeking",
                "tail-risk-sensitive",
            ][(seed // 19) % 4],
            "incentive_susceptibility": [
                "low; resists pressure unless evidence changes",
                "medium; reacts to reputation and peer pressure",
                "high; strongly shaped by institutional incentives",
                "asymmetric; resists public pressure but reacts to private payoff changes",
            ][(seed // 23) % 4],
            "belief_update_temperament": [
                "slow unless evidence is numerically strong",
                "moves quickly when incentives change",
                "moves after a credible opponent challenges its assumptions",
                "requires both numbers and lived-behavior evidence",
            ][(seed // 13) % 4],
            "debate_behavior": [
                "asks for missing denominators",
                "challenges causal leaps",
                "spots strategic silence",
                "forces tradeoffs into numbers",
                "defends local nuance against averages",
            ][(seed // 17) % 5],
        }

    @classmethod
    def _stakes_profile_from_role(
        cls,
        role: str,
        display_name: str,
        subtype: str,
        archetype: Dict[str, Any],
        generation_seed: str,
        index: int,
    ) -> Dict[str, Any]:
        role_text = " ".join(part for part in [display_name, role, subtype] if part)
        role_l = role_text.lower()
        seed = uuid.uuid5(uuid.NAMESPACE_DNS, f"stakes:{generation_seed}:{index}:{role_text}").int
        if any(term in role_l for term in ["voter", "consumer", "worker", "household", "community", "beneficiary", "patient", "student"]):
            direct_stake = "personal material outcome, local dignity, safety, access, household budget, or lived future"
            downside_exposure = "bears consequences directly but has limited control over institutions"
        elif any(term in role_l for term in ["strategist", "campaign", "party", "executive", "operator", "trader"]):
            direct_stake = "career, reputation, power, budget control, market position, or organizational mandate"
            downside_exposure = "penalized if strategy fails or if private weakness becomes public"
        elif any(term in role_l for term in ["pollster", "scientist", "quant", "research", "auditor", "watchdog"]):
            direct_stake = "method credibility, source discipline, forecast accuracy, and professional trust"
            downside_exposure = "loses authority when claims are wrong, unsupported, or contaminated by leakage"
        elif any(term in role_l for term in ["media", "journalist", "narrative", "influencer"]):
            direct_stake = "attention, credibility, access, audience trust, and narrative ownership"
            downside_exposure = "may amplify a salient but false story and damage trust"
        elif any(term in role_l for term in ["mediator", "negotiator", "moderator"]):
            direct_stake = "quality of coordination, clarity of disagreement, and preventing drift"
            downside_exposure = "fails if the room performs agreement instead of revealing conflict"
        elif "quantitative" in role_l or "synthesizer" in role_l or "quant" in role_l:
            direct_stake = "numeric coherence, calibration quality, uncertainty honesty, and avoiding fake precision"
            downside_exposure = "creates false confidence if unsupported numbers pass through"
        elif "external research" in role_l or "research scout" in role_l:
            direct_stake = "source coverage, source diversity, and finding missing or contrary evidence before debate locks in"
            downside_exposure = "debate becomes graph-bound or stale if research gaps are missed"
        elif "data retrieval" in role_l:
            direct_stake = "correct extraction of dates, units, denominators, and comparable numeric anchors"
            downside_exposure = "bad units or missing denominators contaminate every downstream forecast"
        elif "evidence auditor" in role_l or "auditor" in role_l:
            direct_stake = "evidence integrity, cutoff compliance, and claim-to-source discipline"
            downside_exposure = "future leakage or unsupported claims enter the official simulation state"
        else:
            direct_stake = archetype.get("causal_role") or "outcome exposure through its assigned causal channel"
            downside_exposure = "may misread the wider system outside its information lane"

        return {
            "skin_in_the_game": direct_stake,
            "what_they_gain_if_right": [
                "credibility",
                "material benefit",
                "strategic advantage",
                "social trust",
                "better allocation of resources",
            ][(seed // 5) % 5],
            "what_they_lose_if_wrong": downside_exposure,
            "payoff_horizon": ["immediate", "short-term", "medium-term", "long-term"][(seed // 7) % 4],
            "public_position_pressure": ["low", "medium", "high", "very high"][(seed // 11) % 4],
            "private_information_pressure": ["low", "medium", "high", "conflicted"][(seed // 13) % 4],
            "strategic_posture": [
                "truth-seeking",
                "defensive",
                "opportunistic",
                "coalition-building",
                "risk-warning",
                "status-quo-protecting",
            ][(seed // 17) % 6],
        }

    @classmethod
    def _speaks_strongly_about(cls, role_l: str) -> List[str]:
        topics = []
        mapping = [
            (["strategist", "campaign", "party", "operator", "executive"], ["conversion mechanics", "incentives", "opponent strategy", "execution capacity"]),
            (["voter", "consumer", "beneficiary", "rural", "urban", "poor", "middle", "cohort", "household", "community"], ["lived experience", "participation motivation", "local trust", "benefit credibility"]),
            (["minority", "women", "youth", "worker", "student", "patient", "user"], ["subgroup behavior", "identity pressure", "household decision-making", "ground-truth response"]),
            (["north", "south", "east", "west", "region", "belt", "hills", "border", "coastal", "local"], ["regional variation", "local actor effects", "place-specific constraints"]),
            (["pollster", "scientist", "quant", "data"], ["numeric uncertainty", "baseline comparison", "confidence bands", "measurement error"]),
            (["journalist", "media", "narrative"], ["public salience", "message repetition", "scandal visibility"]),
            (["watchdog", "auditor", "governance"], ["evidence quality", "leakage control", "claim discipline"]),
            (["business", "industry", "market", "producer", "supplier", "trader"], ["material incentives", "investment", "demand confidence"]),
            (["mediator", "negotiator", "alliance", "coalition"], ["coordination", "fragmentation", "bargaining incentives"]),
        ]
        for terms, values in mapping:
            if any(term in role_l for term in terms):
                topics.extend(values)
        return list(dict.fromkeys(topics)) or ["assigned causal channel"]

    @classmethod
    def empty_scenario_outputs(cls, domain_plan: Dict[str, Any]) -> Dict[str, Any]:
        scenarios = domain_plan.get("scenario_structure") or {}
        scenario_paths = scenarios.get("scenarios") if isinstance(scenarios, dict) else []
        if isinstance(scenario_paths, list) and scenario_paths:
            return {
                str(item.get("id") or item.get("name") or f"scenario_{idx}").strip(): {}
                for idx, item in enumerate(scenario_paths, start=1)
                if isinstance(item, dict) and (item.get("required", True))
            }
        scenario_names = {
            "base_case": "base",
            "upside_case": "upside",
            "downside_case": "downside",
            "tail_case": "tail",
        }
        return {name: {} for flag, name in scenario_names.items() if scenarios.get(flag, True)}

    @classmethod
    def time_pockets_from_plan(cls, domain_plan: Dict[str, Any]) -> List[Dict[str, Any]]:
        explicit_pockets = domain_plan.get("time_pockets") or []
        if isinstance(explicit_pockets, list) and explicit_pockets:
            pockets = []
            for idx, item in enumerate(explicit_pockets, start=1):
                item = item if isinstance(item, dict) else {"label": str(item)}
                pockets.append({
                    "pocket_id": item.get("pocket_id") or f"pocket_{idx:03d}",
                    "label": item.get("label") or item.get("name") or f"Pocket {idx}",
                    "start": item.get("start") or "auto",
                    "end": item.get("end") or "auto",
                    "events": item.get("events") if isinstance(item.get("events"), list) else [],
                    "state_before": item.get("state_before") if isinstance(item.get("state_before"), dict) else {},
                    "agent_actions": item.get("agent_actions") if isinstance(item.get("agent_actions"), list) else [],
                    "agent_forecasts": item.get("agent_forecasts") if isinstance(item.get("agent_forecasts"), list) else [],
                    "cross_agent_interactions": item.get("cross_agent_interactions") if isinstance(item.get("cross_agent_interactions"), list) else [],
                    "state_after": item.get("state_after") if isinstance(item.get("state_after"), dict) else {},
                    "triggered_revisions": item.get("triggered_revisions") if isinstance(item.get("triggered_revisions"), list) else [],
                })
            return pockets

        horizon = domain_plan.get("forecast_horizon") or {}
        granularity = horizon.get("granularity") or "event_triggered"
        start = horizon.get("start") or "auto"
        end = horizon.get("end") or "auto"
        label = f"{granularity} simulation pocket"
        if start == "auto" or end == "auto":
            return [{
                "pocket_id": "pocket_001",
                "label": label,
                "start": start,
                "end": end,
                "events": [],
                "state_before": {},
                "agent_actions": [],
                "agent_forecasts": [],
                "cross_agent_interactions": [],
                "state_after": {},
                "triggered_revisions": [],
            }]

        return [{
            "pocket_id": "pocket_001",
            "label": label,
            "start": start,
            "end": end,
            "events": [],
            "state_before": {},
            "agent_actions": [],
            "agent_forecasts": [],
            "cross_agent_interactions": [],
            "state_after": {},
            "triggered_revisions": [],
        }]
