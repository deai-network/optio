import sys
from pathlib import Path

# Make scripts/release/ importable without installation.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
