import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location(
    "verify_ai_recognition_e2e",
    ROOT / "scripts" / "verify_ai_recognition_e2e.py",
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_coverage_requires_every_parent_to_be_normal_or_named_exception():
    assert MODULE.coverage(
        {
            "parents": [
                {"parent_label": "正常", "rows": [{"product": "鞋"}]},
                {"parent_label": "异常", "rows": []},
            ],
            "diagnostics": [{"parent_label": "异常", "reason": "ai_unavailable"}],
        }
    ) == {"prints": 2, "normal": 1, "exceptions": 1}
