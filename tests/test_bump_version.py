"""Versión automática con [CI] en el mensaje del commit (packaging/bump_version.py, usado por version.yml)."""
import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "packaging" / "bump_version.py"
spec = importlib.util.spec_from_file_location("bump_version", SCRIPT)
bump_version = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bump_version)


@pytest.mark.parametrize("messages, expected", [
    ("Arreglo del filtro", None),
    ("[CI] Arreglo del filtro", "patch"),
    ("[ci] minúsculas también", "patch"),
    ("Grilla editable [CI minor]", "minor"),
    ("[CI] uno\n[CI major] otro\n[CI minor] y otro", "major"),       # gana la subida más grande del push
    ("CI sin corchetes", None),
])
def test_level_from_commit_messages(messages, expected):
    assert bump_version.level(messages) == expected


def test_next_version():
    tags = {"v0.1.0", "v0.2.0"}
    assert bump_version.next_version("0.2.0", "patch", tags) == "0.2.1"
    assert bump_version.next_version("0.2.0", "minor", tags) == "0.3.0"
    assert bump_version.next_version("0.2.0", "major", tags) == "1.0.0"
    assert bump_version.next_version("0.3.0", "patch", tags) == "0.3.0"         # nunca publicada: tal cual
    assert bump_version.next_version("0.1.0", "patch", tags | {"v0.1.1"}) == "0.1.2"   # salta la ya publicada
    assert bump_version.next_version("0.3.0-beta.1", "patch", {"v0.3.0-beta.1"}) == "0.3.0"


def test_main_writes_version_and_outputs(tmp_path, monkeypatch):
    init = tmp_path / "__init__.py"
    init.write_text('"""doc"""\n__version__ = "0.2.0"\n', encoding="utf-8")
    out = tmp_path / "github_output"
    monkeypatch.setattr(bump_version, "INIT", init)
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setenv("TAGS", "v0.1.0 v0.2.0")
    monkeypatch.setenv("MESSAGES", "[CI minor] Paginación")
    bump_version.main()
    assert '__version__ = "0.3.0"' in init.read_text(encoding="utf-8")
    assert out.read_text(encoding="utf-8") == "publish=true\nversion=0.3.0\ntag=v0.3.0\nchanged=true\n"

    out.unlink()
    monkeypatch.setenv("MESSAGES", "sin marca")
    bump_version.main()
    assert out.read_text(encoding="utf-8") == "publish=false\n"


def test_workflows_are_valid_yaml():
    yaml = pytest.importorskip("yaml")
    root = Path(__file__).resolve().parents[1] / ".github" / "workflows"
    version = yaml.safe_load((root / "version.yml").read_text(encoding="utf-8"))
    release = yaml.safe_load((root / "release.yml").read_text(encoding="utf-8"))
    triggers = release[True]                                   # PyYAML lee la clave "on" como True
    assert set(triggers["workflow_call"]["inputs"]) == {"tag", "ref"}
    assert version["jobs"]["publish"]["uses"] == "./.github/workflows/release.yml"
    assert set(version["jobs"]["publish"]["with"]) == {"tag", "ref"}
