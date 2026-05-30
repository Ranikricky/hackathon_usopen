"""Domain Contract helpers for Horizon XL.

The Domain Contract is the user-approved handoff between raw prompt text and
the rest of the system. It deliberately stays domain-general: the contract
stores reusable buckets such as evidence, instructions, targets, actors, output
requirements, rejected prompt fragments, engine mode, and report template.
"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Dict, List, Optional


STRUCTURED_ENGINE = "structured_simulation"
SOCIAL_DISCOURSE_ENGINE = "social_discourse"


REPORT_TEMPLATE_BY_DOMAIN = {
    "election": "election_forecast",
    "oil": "commodity_market_note",
    "commodity": "commodity_market_note",
    "market": "commodity_market_note",
    "ai_future": "ai_adoption_whitepaper",
    "housing": "housing_policy_brief",
    "policy": "housing_policy_brief",
    "geopolitics": "geopolitical_risk_memo",
    "narrative": "narrative_fiction_forecast",
    "fiction": "narrative_fiction_forecast",
    "business": "business_strategy_memo",
}


INSTRUCTION_PATTERNS = [
    r"\b(do not|don't|avoid|must|should|required|output|return|produce|generate|create|run|simulate|forecast)\b",
    r"\b(rules?|instructions?|required outputs?|final output|acceptance tests?)\b",
    r"\b(confidence bands?|scenario comparison|agent disagreement|missing[- ]data warnings?)\b",
]


EVIDENCE_PATTERNS = [
    r"\b(?:19|20)\d{2}\b",
    r"\b\d[\d,]*(?:\.\d+)?\s*(?:%|percent|bps|basis points|million|billion|trillion|seats?|votes?|months?|years?|usd|dollars?|tons?|barrels?)\b",
    r"\b(historical|baseline|current context|data|poll|survey|index|price|turnout|growth|rate|share|source|reported|according)\b",
]


OUTPUT_PATTERNS = [
    r"\b(table|chart|report|whitepaper|memo|article|dashboard|visuali[sz]ation|timeline|appendix|csv)\b",
    r"\b(agent forecast|scenario path|confidence band|sensitivity matrix|numeric forecast)\b",
]


def _compact(text: Any, limit: int = 600) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    return cleaned[:limit]


def _split_prompt_fragments(text: str) -> List[str]:
    raw = text or ""
    fragments: List[str] = []
    for line in raw.splitlines():
        cleaned = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", line).strip()
        if cleaned:
            fragments.append(cleaned)
    if len(fragments) < 4:
        fragments = [
            item.strip()
            for item in re.split(r"(?<=[.!?])\s+", raw)
            if item.strip()
        ]
    seen = set()
    out = []
    for item in fragments:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(_compact(item, 900))
    return out[:160]


def _matches_any(text: str, patterns: List[str]) -> bool:
    return any(re.search(pattern, text or "", flags=re.IGNORECASE) for pattern in patterns)


def _extract_fragment_buckets(prompt: str, plan: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]] | List[str]]:
    evidence: List[Dict[str, Any]] = []
    instructions: List[str] = []
    output_requirements: List[str] = []
    rejected_prompt_fragments: List[Dict[str, Any]] = []

    target_names = {
        str(item.get("name") or "").lower()
        for item in (plan.get("target_variables") or [])
        if isinstance(item, dict)
    }
    actor_names = {
        str(item.get("name") or "").lower()
        for item in (plan.get("required_agent_archetypes") or [])
        if isinstance(item, dict)
    }

    for fragment in _split_prompt_fragments(prompt):
        lowered = fragment.lower()
        is_output = _matches_any(fragment, OUTPUT_PATTERNS)
        is_instruction = _matches_any(fragment, INSTRUCTION_PATTERNS)
        is_evidence = _matches_any(fragment, EVIDENCE_PATTERNS)
        mentions_target = any(name and name.replace("_", " ") in lowered for name in target_names)
        mentions_actor = any(name and name.replace("_", " ") in lowered for name in actor_names)

        if is_output:
            output_requirements.append(fragment)
        if is_instruction:
            instructions.append(fragment)
        if is_evidence and not is_output:
            evidence.append({
                "text": fragment,
                "source": "user_prompt",
                "evidence_type": "numeric_or_contextual",
                "status": "approved_user_context",
            })
        if (is_instruction or is_output) and not is_evidence and not mentions_target and not mentions_actor:
            rejected_prompt_fragments.append({
                "text": fragment,
                "reason": "instruction_or_output_format_not_actor_target_or_evidence",
            })

    return {
        "evidence": evidence[:80],
        "instructions": instructions[:80],
        "output_requirements": output_requirements[:60],
        "rejected_prompt_fragments": rejected_prompt_fragments[:80],
    }


def select_engine_mode(plan: Dict[str, Any], prompt: str = "") -> str:
    """Default to the structured engine unless the user asks for social discourse."""
    domain = str((plan or {}).get("domain") or "").lower()
    lowered = (prompt or "").lower()
    social_signals = [
        "twitter simulation",
        "reddit simulation",
        "social media discourse",
        "public discourse",
        "simulate posts",
        "simulate comments",
        "online discourse",
        "social_discourse",
    ]
    if domain == "social" and any(signal in lowered for signal in social_signals):
        return SOCIAL_DISCOURSE_ENGINE
    if any(signal in lowered for signal in social_signals):
        return SOCIAL_DISCOURSE_ENGINE
    return STRUCTURED_ENGINE


def select_report_template(plan: Dict[str, Any], prompt: str = "") -> str:
    domain = str((plan or {}).get("domain") or "other").lower()
    if domain in REPORT_TEMPLATE_BY_DOMAIN:
        return REPORT_TEMPLATE_BY_DOMAIN[domain]
    lowered = (prompt or "").lower()
    if re.search(r"\b(story|novel|fiction|character|canon|battle|throne|survive|die)\b", lowered):
        return "narrative_fiction_forecast"
    if re.search(r"\b(oil|gas|commodity|copper|gold|brent|wti|price)\b", lowered):
        return "commodity_market_note"
    if re.search(r"\b(election|vote|seat|turnout|poll)\b", lowered):
        return "election_forecast"
    if re.search(r"\b(ai|adoption|model|frontier lab|open source)\b", lowered):
        return "ai_adoption_whitepaper"
    if re.search(r"\b(strategy|business|company|market entry|consumer trend)\b", lowered):
        return "business_strategy_memo"
    return "generic_forecast_memo"


def build_domain_contract(
    plan: Dict[str, Any],
    prompt: str,
    *,
    project_id: Optional[str] = None,
    approved: bool = False,
    overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a persistable Domain Contract from a normalized domain plan."""
    plan = deepcopy(plan or {})
    overrides = deepcopy(overrides or {})
    prompt = prompt or plan.get("user_question") or ""
    buckets = _extract_fragment_buckets(prompt, plan)

    contract = {
        "version": "domain_contract_v1",
        "project_id": project_id,
        "approved": bool(approved or overrides.get("approved")),
        "domain": overrides.get("domain") or plan.get("domain") or "other",
        "engine_mode": overrides.get("engine_mode") or select_engine_mode(plan, prompt),
        "report_template": overrides.get("report_template") or select_report_template(plan, prompt),
        "user_question": overrides.get("user_question") or plan.get("user_question") or prompt,
        "domain_plan": plan,
        "evidence": overrides.get("evidence") or buckets["evidence"],
        "instructions": overrides.get("instructions") or buckets["instructions"],
        "targets": overrides.get("targets") or plan.get("target_variables") or [],
        "actors": overrides.get("actors") or plan.get("required_agent_archetypes") or [],
        "output_requirements": overrides.get("output_requirements") or buckets["output_requirements"] or plan.get("required_outputs") or [],
        "time_pockets": overrides.get("time_pockets") or plan.get("time_pockets") or [],
        "rejected_prompt_fragments": overrides.get("rejected_prompt_fragments") or buckets["rejected_prompt_fragments"],
        "future_leakage_policy": plan.get("future_leakage_policy") or {},
        "semantic_validation": {
            "status": "pending_approval" if not bool(approved or overrides.get("approved")) else "approved",
            "warnings": [],
            "errors": [],
        },
    }
    contract["domain_plan"]["domain_contract"] = {
        "engine_mode": contract["engine_mode"],
        "report_template": contract["report_template"],
        "approved": contract["approved"],
    }
    return contract


def domain_plan_from_contract(contract: Optional[Dict[str, Any]], fallback_plan: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if isinstance(contract, dict):
        plan = deepcopy(contract.get("domain_plan") or {})
        if plan:
            plan["domain"] = contract.get("domain") or plan.get("domain") or "other"
            plan["target_variables"] = contract.get("targets") or plan.get("target_variables") or []
            plan["required_agent_archetypes"] = contract.get("actors") or plan.get("required_agent_archetypes") or []
            plan["time_pockets"] = contract.get("time_pockets") or plan.get("time_pockets") or []
            plan["required_outputs"] = contract.get("output_requirements") or plan.get("required_outputs") or []
            plan["domain_contract"] = {
                "engine_mode": contract.get("engine_mode") or STRUCTURED_ENGINE,
                "report_template": contract.get("report_template") or "generic_forecast_memo",
                "approved": bool(contract.get("approved")),
            }
            return plan
    return deepcopy(fallback_plan or {})
