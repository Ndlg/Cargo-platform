from types import SimpleNamespace

from app.services.woda_template_matcher import (
    find_woda_template_config,
    unique_printxml_fallback_config,
    woda_template_source_key,
)


class _ScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeDb:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self, _statement):
        return _ScalarResult(self._rows)


def _config(label: str, structure_kind: str, *, top_level_source: bool = True):
    config = {
        "match_structure_kind": structure_kind,
        "template_match": {
            "source_template_key": "printxml:c863897829c2",
            "structure_kind": structure_kind,
        },
    }
    if top_level_source:
        config["source_template_key"] = "printxml:c863897829c2"
    return SimpleNamespace(
        template_key=f"custom:{label}",
        template_source="printxml_layout",
        config=config,
    )


def test_woda_template_config_uses_fingerprint_for_repeated_item_templates() -> None:
    single_config = _config("single", "single_line_standard")
    multi_config = _config("multi", "multi_item")
    db = _FakeDb([single_config, multi_config])

    selected = find_woda_template_config(
        db,
        1,
        "printxml:c863897829c2",
    )

    assert selected is single_config


def test_woda_template_source_key_reads_nested_template_match() -> None:
    config = _config("nested", "multi_item", top_level_source=False)

    assert woda_template_source_key(config) == "printxml:c863897829c2"


def test_woda_template_config_falls_back_to_only_enabled_printxml_config() -> None:
    config = _config("only", "multi_item")
    db = _FakeDb([config])

    selected = find_woda_template_config(
        db,
        1,
        "printxml:cb9c8469df0b",
    )

    assert selected is config


def test_woda_template_config_does_not_guess_when_multiple_printxml_configs_exist() -> None:
    first = _config("first", "multi_item")
    second = _config("second", "multi_item")

    assert unique_printxml_fallback_config([first, second]) is None
