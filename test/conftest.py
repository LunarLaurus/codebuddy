# test/conftest.py
import sys
from pathlib import Path

# Add project root so all internal imports work
sys.path.append(str(Path(__file__).resolve().parent.parent))
