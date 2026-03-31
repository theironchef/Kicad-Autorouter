"""
KiCad Plugin and Content Manager entry point.

When installed via PCM, KiCad loads this __init__.py from the plugins/ directory.
It imports the kicad_autorouter package which registers the action plugin.
"""

import os
import sys
import logging

logger = logging.getLogger("kicad_autorouter")

# Add parent directory to path so 'kicad_autorouter' package can be found
_plugin_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _plugin_dir not in sys.path:
    sys.path.insert(0, _plugin_dir)

try:
    from kicad_autorouter.plugin import AutorouterPlugin  # noqa: F401
except Exception as e:
    logger.error("Failed to load kicad-autorouter: %s", e)
