"""La autoprueba del ejecutable también debe pasar desde el código (la usa el workflow de publicación)."""
from minisql import selftest


def test_selftest_passes_from_source(app, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)                    # escribe selftest.log en la carpeta actual
    assert selftest.run() == 0
    out = capsys.readouterr().out
    assert "FALLA" not in out and "red Oracle" in out and "cryptography" in out
    assert (tmp_path / "selftest.log").read_text(encoding="utf-8").startswith("OK")


def test_keyring_is_required_only_on_windows(app, tmp_path, monkeypatch, capsys):
    """En Linux sin escritorio (GitHub Actions) no hay llavero: es un aviso. En Windows, un error."""
    import keyring
    from keyring.backends import fail

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(keyring, "get_keyring", lambda: fail.Keyring())
    monkeypatch.setattr(selftest.sys, "platform", "linux")
    assert selftest.run() == 0
    assert "sin llavero del sistema" in capsys.readouterr().out
    monkeypatch.setattr(selftest.sys, "platform", "win32")
    assert selftest.run() == 1
    assert "el llavero no tiene un backend útil" in capsys.readouterr().out
