from pathlib import Path
import json
import re

from app.collection_telemetry import SCHEMA as TELEMETRY_SCHEMA
from app.database import SCHEMA as IP_SCHEMA
from app.storage import SCHEMA as RANK_SCHEMA


ROOT = Path(__file__).resolve().parents[1]
DOC_ROOT = ROOT / "docs"


def markdown_files():
    return [ROOT / "README_CN.md", *DOC_ROOT.rglob("*.md")]


def test_all_local_markdown_links_resolve():
    broken = []
    pattern = re.compile(r"\[[^]]+\]\(([^)]+)\)")
    for document in markdown_files():
        for target in pattern.findall(document.read_text(encoding="utf-8")):
            path_text = target.split("#", 1)[0]
            if not path_text or "://" in path_text or path_text.startswith("mailto:"):
                continue
            target_path = (document.parent / path_text).resolve()
            if not target_path.exists():
                broken.append(f"{document.relative_to(ROOT)} -> {target}")
    assert broken == []


def test_private_data_reference_tracks_all_sqlite_tables_and_public_paths():
    reference = (
        DOC_ROOT / "reference" / "PRIVATE_DATA_FORMATS_CN.md"
    ).read_text(encoding="utf-8")
    table_pattern = re.compile(r"CREATE TABLE IF NOT EXISTS\s+(\w+)", re.IGNORECASE)
    expected_tables = set(table_pattern.findall(IP_SCHEMA + TELEMETRY_SCHEMA + RANK_SCHEMA))
    assert expected_tables
    assert all(f"`{name}`" in reference for name in expected_tables)

    expected_paths = {
        "private/dashboard_secrets.json",
        "private/dashboard_chromes.json",
        "private/dedicated_chrome_profiles.json",
        "private/google_state.json",
        "private/ip_history.json",
        "private/node_score.json",
        "private/ip_intelligence.sqlite3",
        "logs/collection_telemetry.sqlite3",
        "rank_tracker.sqlite3",
    }
    assert all(f"`{path}`" in reference for path in expected_paths)


def test_root_has_no_scattered_version_or_fix_notes():
    forbidden = [
        path.name for path in ROOT.iterdir()
        if path.is_file()
        and (
            path.name.endswith("_CHANGELOG.md")
            or "FIX_NOTE" in path.name
            or path.name.startswith("VERSION_IP")
        )
    ]
    assert forbidden == []


def test_published_clash_node_list_is_valid_direct_output():
    path = DOC_ROOT / "reference" / "CLASH_REAL_NODE_LIST_CURRENT.json"
    nodes = json.loads(path.read_text(encoding="utf-8"))
    assert len(nodes) == 58
    assert all(set(item) == {"clash_name", "type"} for item in nodes)
    assert len({item["clash_name"] for item in nodes}) == len(nodes)
    assert {item["type"] for item in nodes} == {"AnyTLS", "Vless"}


def test_published_clash_selector_list_contains_nested_categories():
    path = DOC_ROOT / "reference" / "CLASH_SELECTOR_LIST_CURRENT.json"
    selectors = json.loads(path.read_text(encoding="utf-8"))
    assert [item["group"] for item in selectors] == ["GLOBAL", "XFLTD"]
    assert all(
        set(item) == {"group", "current", "current_leaf", "choices"}
        for item in selectors
    )
    global_choices = selectors[0]["choices"]
    xfltd_choices = selectors[1]["choices"]
    assert {"DIRECT", "REJECT", "XFLTD", "自动选择", "故障转移"} <= set(global_choices)
    assert xfltd_choices[:2] == ["自动选择", "故障转移"]
