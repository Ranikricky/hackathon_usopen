"""Local graph reconstruction helpers.

Render's free instances use ephemeral storage. A project can survive in one
data file while its local graph JSON is missing or stale after a restart. These
helpers rebuild a lightweight local graph from the saved ontology so graph
visualization and report-chat tools can keep working instead of returning 404.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..models.project import ProjectManager


CONTROL_NODES = [
    ("SimulationModerator", "Keeps the discussion focused and manages turn-taking."),
    ("EvidenceAuditor", "Checks claims against graph evidence and approved external research."),
    ("ExternalResearchScout", "Fetches or summarizes approved external web/source pointers outside graph memory."),
    ("DataRetrievalAnalyst", "Extracts numbers, units, dates, and missing-data warnings."),
    ("QuantitativeSynthesizer", "Turns agent positions into numeric scenario tables and confidence bands."),
    ("NegotiationMediator", "Surfaces disagreement, pressure points, tradeoffs, and possible compromise paths."),
]


def build_local_graph_from_ontology(
    graph_id: str,
    ontology: Dict[str, Any],
    simulation_requirement: str = "",
    generation_seed: str = "",
) -> Dict[str, Any]:
    """Build a small graph-compatible JSON payload from ontology definitions."""
    entity_types = ontology.get("entity_types", []) or []
    edge_types = ontology.get("edge_types", []) or []

    nodes: List[Dict[str, Any]] = []
    entity_uuid_map: Dict[str, List[str]] = {}

    for idx, entity in enumerate(entity_types, start=1):
        entity_name = (entity or {}).get("name") or f"EntityType{idx}"
        node_uuid = f"{graph_id}_node_{idx:03d}"
        entity_uuid_map[entity_name] = [node_uuid]
        attr_defs = (entity or {}).get("attributes", []) or []
        attributes = {str(a.get("name")): "" for a in attr_defs if a.get("name")}
        attributes.update({
            "schema_type": True,
            "agent_instance": True,
            "repaired_from_ontology": True,
            "generation_seed": generation_seed,
        })
        nodes.append({
            "uuid": node_uuid,
            "name": entity_name,
            "labels": ["Entity", entity_name],
            "summary": (entity or {}).get("description", ""),
            "attributes": attributes,
            "created_at": None,
        })

    for entity_name, description in CONTROL_NODES:
        if entity_name in entity_uuid_map:
            continue
        node_uuid = f"{graph_id}_node_{len(nodes) + 1:03d}"
        entity_uuid_map[entity_name] = [node_uuid]
        nodes.append({
            "uuid": node_uuid,
            "name": entity_name,
            "labels": ["Entity", entity_name],
            "summary": description,
            "attributes": {
                "agent_instance": True,
                "orchestration_agent": True,
                "repaired_from_ontology": True,
                "generation_seed": generation_seed,
            },
            "created_at": None,
        })

    edges: List[Dict[str, Any]] = []
    for edge_def in edge_types:
        edge_name = (edge_def or {}).get("name") or "RELATED_TO"
        source_targets = (edge_def or {}).get("source_targets", []) or []
        for st in source_targets:
            source_name = (st or {}).get("source")
            target_name = (st or {}).get("target")
            source_uuids = entity_uuid_map.get(source_name) or []
            target_uuids = entity_uuid_map.get(target_name) or []
            if not source_uuids or not target_uuids:
                continue
            edges.append({
                "uuid": f"{graph_id}_edge_{len(edges) + 1:04d}",
                "name": edge_name,
                "fact": (edge_def or {}).get("description", ""),
                "fact_type": edge_name,
                "source_node_uuid": source_uuids[0],
                "target_node_uuid": target_uuids[0],
                "source_node_name": source_name,
                "target_node_name": target_name,
                "attributes": {"schema_relation": True, "repaired_from_ontology": True},
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
        "mode": "local_ontology_repair",
        "repaired": True,
        "simulation_requirement": simulation_requirement,
    }


def get_or_repair_local_graph(graph_id: str) -> Optional[Dict[str, Any]]:
    """Return a local graph or rebuild it from the owning project's ontology."""
    graph_data = ProjectManager.get_local_graph_by_graph_id(graph_id)
    if graph_data:
        return graph_data

    project = ProjectManager.get_project_by_graph_id(graph_id)
    if not project or not project.ontology:
        return None

    repaired = build_local_graph_from_ontology(
        graph_id=graph_id,
        ontology=project.ontology,
        simulation_requirement=project.simulation_requirement or "",
        generation_seed=project.generation_seed or "",
    )
    ProjectManager.save_local_graph(project.project_id, repaired)
    return repaired
