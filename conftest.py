import os
import sys

# Ensure the project root is importable so tests can `from tools import ...`
# regardless of where pytest is invoked from.
sys.path.insert(0, os.path.dirname(__file__))
