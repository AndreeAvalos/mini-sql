"""Novedades: notas de cada versión (desde los commits) y la ventana que las muestra al actualizar."""
import importlib.util
from pathlib import Path

from test_update import Settings, release, window

from minisql import __version__, update
from minisql.ui import updates as ui_updates

from support import wait_until

SCRIPT = Path(__file__).resolve().parents[1] / "packaging" / "release_notes.py"
spec = importlib.util.spec_from_file_location("release_notes", SCRIPT)
release_notes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_notes)


def with_notes(tag, body, date="2026-10-15T10:00:00Z", **kw):
    r = release(tag, **kw)
    r.update(body=body, published_at=date)
    return r


# ---------------------------------------------------------------- notas desde los commits

def test_notes_from_commit_messages():
    subjects = ["[CI minor] Interfaz rediseñada", "Versión 0.2.5", "Merge branch 'x'", "arreglo del filtro",
                "[CI] Arreglo del filtro", ""]
    assert release_notes.notes(subjects) == "- Interfaz rediseñada\n- Arreglo del filtro"
    assert release_notes.notes(["Versión 0.3.0"]) == "- Mejoras y correcciones menores."


def test_notes_use_commits_since_the_previous_version():
    calls = []

    class Done:
        def __init__(self, out):
            self.stdout = out

    def run(args, **_kw):
        calls.append(args[1:])
        if args[1] == "describe":
            return Done("v0.2.5\n")
        return Done("[CI] Zoom con Ctrl+rueda\nVersión 0.2.6\n")

    text = release_notes.build("abc123", run=run)
    assert calls[0][-1] == "abc123^" and calls[1][-1] == "v0.2.5..abc123"
    assert text == "## Cambios\n\n- Zoom con Ctrl+rueda\n"


# ---------------------------------------------------------------- juntar varias versiones

RELEASES = [with_notes("v0.4.0", "## Cambios\n\n- Cuatro"), with_notes("v0.3.0", "- Tres"),
            with_notes("v0.2.0", "- Dos"), with_notes("v0.5.0-beta.1", "- Prueba", prerelease=True),
            with_notes("v0.9.0", "- Borrador", draft=True)]


def test_notes_between_versions():
    entries = update.notes_between(RELEASES, "0.2.0", "0.4.0")
    assert [e[0] for e in entries] == ["0.4.0", "0.3.0"]                 # de la más nueva a la más vieja
    assert entries[0][1] == "2026-10-15"
    assert [e[0] for e in update.notes_between(RELEASES, "0.2.0", "0.5.0-beta.1")] == ["0.5.0-beta.1", "0.4.0",
                                                                                     "0.3.0"]
    text = update.changelog_markdown(entries)
    assert text.startswith("## MiniSQL 0.4.0  ·  2026-10-15\n\n- Cuatro") and "## Cambios" not in text
    assert update.changelog_markdown([]) == "Sin notas de versión."


# ---------------------------------------------------------------- en la ventana

def test_after_updating_shows_whats_new(app, tmp_path, monkeypatch):
    shown = []
    monkeypatch.setattr(ui_updates.WhatsNewDialog, "exec", lambda self: shown.append(self) or 0)
    previous = "0.0.1"
    settings = Settings(last_run_version=previous)
    releases = [with_notes(f"v{__version__}", "- Lo último"), with_notes("v0.0.2", "- Lo de antes"),
                with_notes("v0.0.1", "- Lo que ya tenía")]
    w = window(tmp_path, settings, releases, frozen=True, exe=tmp_path / "MiniSQL.exe")
    w.updates.start()
    assert wait_until(app, lambda: shown)
    dialog = shown[0]
    text = dialog.text.toPlainText()
    assert __version__ in dialog.windowTitle() and "Lo último" in text and "Lo de antes" in text
    assert "Lo que ya tenía" not in text                                 # solo lo nuevo desde tu versión
    assert settings.data["updates"]["last_run_version"] == __version__   # una sola vez
    w.close()


def test_banner_notes_include_the_versions_you_would_skip(app, tmp_path, monkeypatch):
    shown = []
    monkeypatch.setattr(ui_updates.WhatsNewDialog, "exec", lambda self: shown.append(self) or 0)
    releases = [with_notes("v98.0.0", "- Novena"), with_notes("v99.0.0", "- Décima")]
    w = window(tmp_path, Settings(), releases)
    w.updates.check(manual=True)
    assert wait_until(app, lambda: w.updates.release is not None)
    w.updates.show_notes()
    text = shown[0].text.toPlainText()
    assert "Décima" in text and "Novena" in text and "2 versiones" in shown[0].findChild(ui_updates.QLabel).text()
    w.close()
