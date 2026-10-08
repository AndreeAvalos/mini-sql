"""La autoprueba del ejecutable también debe pasar desde el código (la usa el workflow de publicación)."""
from minisql import selftest


def test_selftest_passes_from_source(app, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)                    # escribe selftest.log en la carpeta actual
    assert selftest.run() == 0
    out = capsys.readouterr().out
    assert "FALLA" not in out and "red Oracle" in out and "cryptography" in out
    assert (tmp_path / "selftest.log").read_text(encoding="utf-8").startswith("OK")
