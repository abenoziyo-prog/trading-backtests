"""Strategy lab: test new trading ideas against strict gates before they can reach the live (paper) bot."""
import sys
from pathlib import Path

_SRC = str(Path(__file__).resolve().parents[1] / "src")
if _SRC not in sys.path:  # CI runs from a checkout without installing the quant package
    sys.path.insert(0, _SRC)
