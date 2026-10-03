"""INFRA-048：README.md 的本機環境說明與 compose.yaml、justfile 一致。"""

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
README = REPO_ROOT / "README.md"


def _readme() -> str:
    return README.read_text(encoding="utf-8")


def _compose_host_ports() -> set[int]:
    compose = yaml.safe_load((REPO_ROOT / "compose.yaml").read_text(encoding="utf-8"))
    return {
        int(str(port).split(":")[1])
        for spec in compose["services"].values()
        for port in spec.get("ports", [])
    }


def test_readme_ports_match_compose() -> None:
    text = _readme()

    ports = _compose_host_ports()
    assert ports
    for port in ports:
        assert str(port) in text, port
    assert "8341" in text
    assert "5341" in text
    # README 提到的 543xx port 都必須是 compose.yaml 實際發佈的（不得殘留舊的 Supabase port）
    mentioned = {int(p) for p in re.findall(r"(?<!\d)543\d\d(?!\d)", text)}
    assert mentioned == ports, sorted(mentioned)


def test_readme_quickstart_order() -> None:
    text = _readme()

    assert text.index("just db-start") < text.index("just db-reset --yes") < text.index("just api")


def test_readme_migration_section() -> None:
    text = _readme()

    assert "just db-migrate" in text
    assert "just db-new-migration" in text
    assert "forward-only" in text


def test_readme_tools() -> None:
    text = _readme()

    assert "Docker" in text
    assert "compose" in text
    assert "supabase" not in text.lower()
