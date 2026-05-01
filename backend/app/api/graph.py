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
from ..services.graph_builder import GraphBuilderService
from ..services.text_processor import TextProcessor
from ..utils.file_parser import FileParser
from ..utils.logger import get_logger
from ..utils.locale import t, get_locale, set_locale
from ..models.task import TaskManager, TaskStatus
from ..models.project import ProjectManager, ProjectStatus

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


def _build_local_graph_from_ontology(graph_id: str, ontology: dict) -> dict:
    """
    Build a local graph from the ontology when Zep is not configured.
    This keeps the local demo and workflow usable.
    """
    entity_types = ontology.get("entity_types", []) or []
    edge_types = ontology.get("edge_types", []) or []

    nodes = []
    entity_uuid_map: dict[str, str] = {}

    for idx, entity in enumerate(entity_types, start=1):
        entity_name = (entity or {}).get("name") or f"EntityType{idx}"
        node_uuid = f"{graph_id}_node_{idx:03d}"
        entity_uuid_map[entity_name] = node_uuid

        attr_defs = (entity or {}).get("attributes", []) or []
        attributes = {str(a.get("name")): "" for a in attr_defs if a.get("name")}
        attributes["schema_type"] = True

        nodes.append({
            "uuid": node_uuid,
            "name": entity_name,
            "labels": ["Entity", entity_name],
            "summary": (entity or {}).get("description", ""),
            "attributes": attributes,
            "created_at": None,
        })

    edges = []
    edge_idx = 0
    for edge_def in edge_types:
        edge_name = (edge_def or {}).get("name") or "RELATED_TO"
        edge_fact = (edge_def or {}).get("description", "")
        source_targets = (edge_def or {}).get("source_targets", []) or []
        for st in source_targets:
            source_name = (st or {}).get("source")
            target_name = (st or {}).get("target")
            source_uuid = entity_uuid_map.get(source_name)
            target_uuid = entity_uuid_map.get(target_name)
            if not source_uuid or not target_uuid:
                continue
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
                "attributes": {"schema_relation": True},
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
        project_name = request.form.get('project_name', 'Unnamed Project')
        additional_context = request.form.get('additional_context', '')
        
        logger.debug(f"Project name: {project_name}")
        logger.debug(f"Simulation requirement: {simulation_requirement[:100]}...")
        
        if not simulation_requirement or not simulation_requirement.strip():
            return jsonify({
                "success": False,
                "error": t('api.requireSimulationRequirement')
            }), 400
        
        if _is_placeholder_secret(Config.LLM_API_KEY):
            return jsonify({
                "success": False,
                "error": "LLM_API_KEY is not configured. Please update the project .env file with a valid key."
            }), 400
        
        # Uploaded files are optional; prompt-only projects are supported.
        uploaded_files = request.files.getlist('files')
        
        # Create project.
        project = ProjectManager.create_project(name=project_name)
        project.simulation_requirement = simulation_requirement
        project.generation_seed = f"{project.project_id}:{uuid.uuid4().hex[:12]}"
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
        
        # Generate ontology fresh for every project. We do not reuse previous
        # ontology/agent outputs; the project generation seed nudges the LLM and
        # fallback path to vary secondary actors on repeat prompts.
        logger.info("Generating ontology definition...")
        generator = OntologyGenerator()
        ontology = generator.generate(
            document_texts=document_texts,
            simulation_requirement=simulation_requirement,
            additional_context=additional_context if additional_context else None,
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
        use_local_graph = _is_placeholder_secret(Config.ZEP_API_KEY)
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
            graph_data = _build_local_graph_from_ontology(graph_id, ontology)
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
        "data": [t.to_dict() for t in tasks],
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
            local_graph = ProjectManager.get_local_graph_by_graph_id(graph_id)
            if not local_graph:
                return jsonify({
                    "success": False,
                    "error": f"Graph does not exist: {graph_id}"
                }), 404
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
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


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
