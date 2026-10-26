"""FreeCAD entry point; installation paths are resolved locally."""
import sys
from pathlib import Path
from PySide import QtCore

_sidebar_root = str(Path(__file__).resolve().parent)
if _sidebar_root not in sys.path:
    sys.path.insert(0, _sidebar_root)

def _start_codex_companion():
    try:
        import arthur_supervisor
        arthur_supervisor.start()
    except Exception as exc:
        import FreeCAD
        FreeCAD.Console.PrintError('FreeCAD Codex companion: '+str(exc)+'\n')

QtCore.QTimer.singleShot(1800, _start_codex_companion)
