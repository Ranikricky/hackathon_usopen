"""
Horizon XL Backend entry point.
"""

import os
import sys

# Configure UTF-8 output early for Windows consoles.
if sys.platform == 'win32':
    os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# Add project root to path.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app
from app.config import Config


def main():
    """Run the backend server."""
    # Validate configuration without blocking process startup. Render and other
    # hosts need /health to respond even when API secrets are added later.
    errors = Config.validate()
    if errors:
        print("Configuration warnings:")
        for err in errors:
            print(f"  - {err}")
        print("\nThe server will start, but affected API calls will fail until these are configured.")
    
    # Create app.
    app = create_app()
    
    # Runtime configuration.
    host = os.environ.get('FLASK_HOST', '0.0.0.0')
    port = int(os.environ.get('PORT') or os.environ.get('FLASK_PORT', 5001))
    debug = Config.DEBUG
    
    # Start server.
    app.run(host=host, port=port, debug=debug, threaded=True)


if __name__ == '__main__':
    main()
