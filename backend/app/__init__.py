"""
Horizon XL Backend - Flask app factory.
"""

import os
import warnings

# Suppress multiprocessing resource_tracker warnings from third-party libraries.
# This must be configured before other imports.
warnings.filterwarnings("ignore", message=".*resource_tracker.*")

from flask import Flask, request, send_from_directory
from flask_cors import CORS

from .config import Config
from .services.durable_store import DurableStore
from .utils.logger import setup_logger, get_logger


def create_app(config_class=Config):
    """Create and configure the Flask app."""
    frontend_dist = os.path.abspath(os.path.join(
        os.path.dirname(__file__), '../../frontend/dist'
    ))
    app = Flask(
        __name__,
        static_folder=frontend_dist if os.path.isdir(frontend_dist) else None,
        static_url_path=''
    )
    app.config.from_object(config_class)
    
    # Preserve UTF-8 characters in JSON responses.
    # Flask >= 2.3 uses app.json.ensure_ascii; older versions use JSON_AS_ASCII.
    if hasattr(app, 'json') and hasattr(app.json, 'ensure_ascii'):
        app.json.ensure_ascii = False
    
    # Set up logging.
    logger = setup_logger('horizonxl')
    
    # Log startup only in the reloader child process to avoid duplicate messages.
    is_reloader_process = os.environ.get('WERKZEUG_RUN_MAIN') == 'true'
    debug_mode = app.config.get('DEBUG', False)
    should_log_startup = not debug_mode or is_reloader_process
    
    if should_log_startup:
        logger.info("=" * 50)
        logger.info("Horizon XL Backend starting...")
        logger.info("=" * 50)
    
    # Enable CORS.
    CORS(app, resources={r"/api/*": {"origins": "*"}})
    
    # Register simulation cleanup so child processes are stopped on server shutdown.
    from .services.simulation_runner import SimulationRunner
    SimulationRunner.register_cleanup()
    if should_log_startup:
        logger.info("Registered simulation process cleanup")
    
    # Request logging middleware.
    @app.before_request
    def log_request():
        logger = get_logger('horizonxl.request')
        logger.debug(f"Request: {request.method} {request.path}")
        if request.content_type and 'json' in request.content_type:
            logger.debug(f"Request body: {request.get_json(silent=True)}")
    
    @app.after_request
    def log_response(response):
        logger = get_logger('horizonxl.request')
        logger.debug(f"Response: {response.status_code}")
        return response
    
    # Register blueprints.
    from .api import graph_bp, simulation_bp, report_bp
    app.register_blueprint(graph_bp, url_prefix='/api/graph')
    app.register_blueprint(simulation_bp, url_prefix='/api/simulation')
    app.register_blueprint(report_bp, url_prefix='/api/report')
    
    # Health check.
    @app.route('/health')
    def health():
        config_warnings = Config.validate()
        durable_status = DurableStore.provider_status()
        return {
            'status': 'ok',
            'service': 'Horizon XL Backend',
            'build_marker': 'ontology_prompt_anchor_v3',
            'configuration': {
                'ready': not config_warnings,
                'warnings': config_warnings,
                'storage': {
                    'zep_configured': bool(Config.ZEP_API_KEY),
                    'git_configured': durable_status.get('git', False),
                    'durable_fallback': DurableStore.enabled(),
                    'active_provider': (
                        'git' if durable_status.get('git')
                        else 'local_ephemeral'
                    )
                }
            }
        }

    @app.route('/', defaults={'path': ''})
    @app.route('/<path:path>')
    def serve_frontend(path):
        """Serve the built Vue app when running as a single deployed service."""
        if not app.static_folder:
            return {'status': 'ok', 'service': 'Horizon XL Backend', 'frontend': 'not_built'}

        candidate = os.path.join(app.static_folder, path)
        if path and os.path.exists(candidate) and os.path.isfile(candidate):
            return send_from_directory(app.static_folder, path)
        return send_from_directory(app.static_folder, 'index.html')
    
    if should_log_startup:
        logger.info("Horizon XL Backend started")
    
    return app
