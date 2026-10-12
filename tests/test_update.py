"""Actualización automática: versiones, elegir el release, descarga verificada, instalar y volver atrás."""
import hashlib
import io
import json

import pytest
from PySide6.QtWidgets import QMessageBox

from minisql import __version__, update
from minisql.ui import updates as ui_updates
from minisql.ui.main_window import MainWindow
from minisql.ui.updates import UpdateManager

from support import wait_until

EXE = b"MZ nueva version de MiniSQL" * 1000
SHA = hashlib.sha256(EXE).hexdigest()


def release(tag, exe=True, sha=True, draft=False, prerelease=False, size=len(EXE), digest=None):
    assets = []
    if exe:
        assets.append({"name": "MiniSQL.exe", "browser_download_url": f"https://github.com/x/{tag}/MiniSQL.exe",
                       "size": size, "digest": digest})
    if sha:
        assets.append({"name": "MiniSQL.exe.sha256",
                       "browser_download_url": f"https://github.com/x/{tag}/MiniSQL.exe.sha256"})
    return {"tag_name": tag, "draft": draft, "prerelease": prerelease, "body": f"Novedades de {tag}",
            "html_url": f"https://github.com/x/releases/{tag}", "assets": assets}


class FakeResponse(io.BytesIO):
    def __init__(self, data, url):
        super().__init__(data)
        self.url, self.headers = url, {"Content-Length": str(len(data))}

    def geturl(self):
        return self.url

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def opener(files):
    """urlopen simulado: url → bytes."""
    def urlopen(request, timeout=None):
        url = request.full_url
        assert request.headers["User-agent"] == f"MiniSQL/{__version__}"     # no se manda nada más
        if url not in files:
            raise OSError(f"404 {url}")
        return FakeResponse(files[url], url)
    return urlopen


# ---------------------------------------------------------------- versiones y releases

def test_version_order():
    assert update.is_newer("0.10.0", "0.9.1") and update.is_newer("v1.0.0", "0.99.99")
    assert update.is_newer("0.3.0", "0.3.0-beta.2") and update.is_newer("0.3.0-beta.2", "0.3.0-beta.1")
    assert not update.is_newer("0.2.0", "0.2.0") and not update.is_newer("0.1.9", "0.2.0")
    assert not update.is_newer("cualquier-cosa", "0.1.0") and update.version_key("v0.1") is None


def test_latest_release_picks_the_newest_usable_one():
    releases = [release("v9.0.0", draft=True), release("v8.0.0-beta.1", prerelease=True),
                release("v7.0.0", exe=False), release("v0.0.1"), release("v5.0.0"), release("v6.0.0")]
    best = update.latest_release("0.1.0", releases=releases)
    assert best.version == "6.0.0" and best.asset_url.endswith("v6.0.0/MiniSQL.exe") and best.sha256_url
    assert update.latest_release("0.1.0", include_prerelease=True, releases=releases).version == "8.0.0-beta.1"
    assert update.latest_release("6.0.0", releases=releases) is None


def test_fetch_releases_reports_network_errors():
    with pytest.raises(update.UpdateError, match="No se pudo consultar GitHub"):
        update.fetch_releases(opener({}))
    data = json.dumps([release("v1.0.0")]).encode()
    assert update.fetch_releases(opener({update.RELEASES_API: data}))[0]["tag_name"] == "v1.0.0"


def test_should_check_once_a_day_and_only_if_enabled():
    assert update.should_check({}, now=10**9)
    assert not update.should_check({"last_check": 10**9 - 3600}, now=10**9)
    assert update.should_check({"last_check": 10**9 - 25 * 3600}, now=10**9)
    assert not update.should_check({"auto": False}, now=10**9)


# ---------------------------------------------------------------- descarga verificada

def files_for(rel, exe=EXE, sha=SHA):
    return {rel.asset_url: exe, rel.sha256_url: f"{sha}  MiniSQL.exe".encode()}


def test_download_verifies_sha256(tmp_path):
    rel = update.latest_release("0.1.0", releases=[release("v1.0.0")])
    seen = []
    path = update.download(rel, tmp_path, lambda d, t: seen.append((d, t)), opener=opener(files_for(rel)), chunk=4096)
    assert path.name == "MiniSQL-1.0.0.exe" and path.read_bytes() == EXE
    assert seen[-1] == (len(EXE), len(EXE)) and not list(tmp_path.glob("*.part"))


def test_download_with_wrong_hash_is_discarded(tmp_path):
    rel = update.latest_release("0.1.0", releases=[release("v1.0.0")])
    with pytest.raises(update.UpdateError, match="no coincide"):
        update.download(rel, tmp_path, opener=opener(files_for(rel, sha="0" * 64)))
    assert not list(tmp_path.iterdir())                       # nada a medias ni sin verificar


def test_download_without_published_hash_is_refused(tmp_path):
    rel = update.latest_release("0.1.0", releases=[release("v1.0.0", sha=False)])
    with pytest.raises(update.UpdateError, match="no publica la huella"):
        update.download(rel, tmp_path, opener=opener({rel.asset_url: EXE}))
    rel = update.latest_release("0.1.0", releases=[release("v1.0.0", sha=False, digest=f"sha256:{SHA}")])
    assert update.download(rel, tmp_path, opener=opener({rel.asset_url: EXE})).read_bytes() == EXE


def test_download_incomplete_or_cancelled(tmp_path):
    rel = update.latest_release("0.1.0", releases=[release("v1.0.0", size=len(EXE) + 10)])
    with pytest.raises(update.UpdateError, match="incompleta"):
        update.download(rel, tmp_path, opener=opener(files_for(rel)))
    rel = update.latest_release("0.1.0", releases=[release("v1.0.0")])
    with pytest.raises(update.Cancelled):
        update.download(rel, tmp_path, cancelled=lambda: True, opener=opener(files_for(rel)))
    assert not list(tmp_path.iterdir())


def test_only_https():
    with pytest.raises(update.UpdateError, match="no segura"):
        update._request("http://github.com/x")


# ---------------------------------------------------------------- instalar y volver atrás

def test_install_keeps_previous_and_rollback_swaps(tmp_path):
    exe = tmp_path / "MiniSQL.exe"
    exe.write_text("vieja")
    new = tmp_path / "descargas" / "MiniSQL-1.0.0.exe"
    new.parent.mkdir()
    new.write_text("nueva")
    assert update.can_replace(exe)
    old = update.install(new, exe)
    assert exe.read_text() == "nueva" and old.read_text() == "vieja" and old.name == "MiniSQL.old.exe"
    update.rollback(exe)
    assert exe.read_text() == "vieja" and old.read_text() == "nueva"


def test_install_failure_leaves_everything_as_it_was(tmp_path):
    exe = tmp_path / "MiniSQL.exe"
    exe.write_text("vieja")
    with pytest.raises(OSError):
        update.install(tmp_path / "no-existe.exe", exe)
    assert exe.read_text() == "vieja" and not update.previous_exe(exe).exists()


def test_self_test_of_the_new_exe(tmp_path):
    exe = tmp_path / "MiniSQL-1.0.0.exe"

    class Done:
        def __init__(self, code):
            self.returncode, self.stdout, self.stderr = code, "", ""

    def run_ok(args, **kw):
        assert args[1] == "--self-test"
        (tmp_path / "selftest.log").write_text("OK   todo", encoding="utf-8")
        return Done(0)
    assert update.self_test(exe, run=run_ok) == (True, "OK   todo")
    assert not (tmp_path / "selftest.log").exists()
    assert update.self_test(exe, run=lambda *a, **k: Done(1))[0] is False


# ---------------------------------------------------------------- en la ventana

class Settings:
    def __init__(self, **updates):
        self.data = {"updates": updates} if updates else {}

    def load(self):
        return json.loads(json.dumps(self.data))

    def save(self, data):
        self.data = data


def window(tmp_path, settings, releases, frozen=False, exe=None):
    factory = lambda w: UpdateManager(w, load=settings.load, save=settings.save, releases=lambda: releases,  # noqa: E731
                                      frozen=frozen, exe=exe, downloads=tmp_path / "descargas")
    w = MainWindow(session_path=str(tmp_path / "sesion"), restore=False, updates=factory)
    w.keeper.stop()
    return w


def test_manual_check_shows_banner_and_skip_is_remembered(app, tmp_path):
    settings = Settings()
    w = window(tmp_path, settings, [release("v99.0.0")])
    w.show()
    w.updates.check(manual=True)
    assert wait_until(app, lambda: w.updates.banner.isVisible())
    assert "99.0.0" in w.updates.banner.label.text()
    w.updates.skip()
    assert not w.updates.banner.isVisible() and settings.data["updates"]["skip"] == "99.0.0"
    w.updates.check(manual=False)                                  # automática: la omitida no se ofrece
    assert wait_until(app, lambda: not w.updates.checking)
    assert not w.updates.banner.isVisible()
    w.close()


def test_manual_check_without_news_says_so(app, tmp_path, monkeypatch):
    said = []
    monkeypatch.setattr(ui_updates.QMessageBox, "information", staticmethod(lambda *a, **k: said.append(a[2])))
    w = window(tmp_path, Settings(), [release("v0.0.1")])
    w.updates.check(manual=True)
    assert wait_until(app, lambda: said)
    assert "más reciente" in said[0]
    w.close()


def test_source_checkout_never_checks_automatically(app, tmp_path):
    settings = Settings()
    w = window(tmp_path, settings, [release("v99.0.0")])            # python main.py: no es el .exe
    w.updates.start()
    assert not w.updates.checking and settings.data == {}           # ni red ni config.json
    w.close()


def test_update_installs_on_close_and_relaunches(app, tmp_path, monkeypatch):
    exe = tmp_path / "app" / "MiniSQL.exe"
    exe.parent.mkdir()
    exe.write_text("vieja")
    settings = Settings()
    w = window(tmp_path, settings, [release("v99.0.0")], frozen=True, exe=exe)
    rel = update.latest_release("0.1.0", releases=[release("v99.0.0")])
    real_download = update.download
    monkeypatch.setattr(update, "download", lambda r, d, p, c: real_download(r, d, p, c, opener=opener(files_for(rel))))
    monkeypatch.setattr(update, "self_test", lambda path: (True, "OK"))
    monkeypatch.setattr(ui_updates.QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    w.show()
    w.updates.check(manual=True)
    assert wait_until(app, lambda: w.updates.release is not None)
    w.updates.update_now()                                         # descarga, verifica y pide reiniciar
    assert wait_until(app, lambda: w.relaunch_exe is not None, 10)
    assert exe.read_bytes() == EXE and update.previous_exe(exe).read_text() == "vieja"
    assert w.relaunch_exe == exe and settings.data["updates"]["previous_version"] == __version__


def test_restart_is_cancelled_if_the_window_does_not_close(app, tmp_path, monkeypatch):
    exe = tmp_path / "MiniSQL.exe"
    exe.write_text("vieja")
    w = window(tmp_path, Settings(), [], frozen=True, exe=exe)
    w.updates.pending = ("install", tmp_path / "MiniSQL-99.0.0.exe")
    monkeypatch.setattr(w, "close", lambda: False)                 # p. ej. dijo "No" a cerrar con un Commit pendiente
    w.updates.restart_now()
    assert not w.updates.restart and w.updates.pending and exe.read_text() == "vieja"


def test_child_exe_does_not_inherit_pyinstaller_environment(tmp_path):
    """Si el .exe nuevo hereda _PYI_* del viejo, usa sus archivos de Temp/_MEIxxxx, que se borran al cerrarse
    el viejo: "shiboken6/libshiboken does not exist". Se abre siempre con un entorno limpio."""
    base = {"PATH": "x", "_PYI_APPLICATION_HOME_DIR": "C:/Temp/_MEI1", "_PYI_PARENT_PROCESS_LEVEL": "1",
            "_PYI_ARCHIVE_FILE": "viejo.exe", "_MEIPASS2": "C:/Temp/_MEI1"}
    env = update.clean_env(base)
    assert env == {"PATH": "x", "PYINSTALLER_RESET_ENVIRONMENT": "1"}

    launched = []
    update.relaunch(tmp_path / "MiniSQL.exe", popen=lambda args, **kw: launched.append(kw["env"]))
    assert launched[0]["PYINSTALLER_RESET_ENVIRONMENT"] == "1"

    class Done:
        returncode, stdout, stderr = 0, "", ""
    seen = []
    update.self_test(tmp_path / "MiniSQL-9.exe", run=lambda args, **kw: seen.append(kw["env"]) or Done())
    assert seen[0]["PYINSTALLER_RESET_ENVIRONMENT"] == "1" and not any(k.startswith("_PYI_") for k in seen[0])
