"""Registro de errores: lo que tumba la app queda en errores.log (el .exe no tiene consola)."""
import sys
import threading

from minisql import errorlog


def test_unhandled_errors_are_written_to_the_log(tmp_path, monkeypatch):
    monkeypatch.setattr(errorlog.faulthandler, "enable", lambda **_k: None)   # no tocar el de pytest
    monkeypatch.setattr(sys, "excepthook", lambda *_a: None)
    monkeypatch.setattr(threading, "excepthook", threading.excepthook)
    monkeypatch.setattr(sys, "stderr", None)                                  # como el .exe sin consola
    path = errorlog.start(tmp_path / "errores.log")
    try:
        try:
            raise RuntimeError("DPY-5002: internal error")
        except RuntimeError as e:
            sys.excepthook(type(e), e, e.__traceback__)
        worker = threading.Thread(target=lambda: 1 / 0, name="consulta")
        worker.start()
        worker.join()
    finally:
        errorlog._stream.close()
    text = path.read_text(encoding="utf-8")
    assert "=== MiniSQL" in text and "RuntimeError: DPY-5002: internal error" in text
    assert "en el hilo consulta" in text and "ZeroDivisionError" in text


def test_big_log_is_rotated(tmp_path, monkeypatch):
    monkeypatch.setattr(errorlog.faulthandler, "enable", lambda **_k: None)
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    monkeypatch.setattr(threading, "excepthook", threading.excepthook)
    log = tmp_path / "errores.log"
    log.write_text("x" * (errorlog.MAX_BYTES + 1), encoding="utf-8")
    errorlog.start(log)
    errorlog._stream.close()
    assert (tmp_path / "errores.old.log").stat().st_size > errorlog.MAX_BYTES
    assert log.stat().st_size < 1000
