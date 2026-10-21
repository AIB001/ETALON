"""Run the product skill's portable synthetic SDK example from a source checkout."""
from pathlib import Path
import runpy

if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).resolve().parents[1] / ".claude" / "skills" /
                      "etalon-research" / "scripts" / "research_methods.py"), run_name="__main__")
