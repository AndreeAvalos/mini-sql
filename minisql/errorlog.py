"""Registro de errores en ~/.minisql/errores.log: si la app se cierra de golpe (el .exe no tiene consola), ahí queda
el motivo. Guarda las excepciones que nadie atrapó (también las de hilos y slots de Qt) y, con faulthandler, las
caídas nativas (segfault / access violation) con la pila de cada hilo. Nada sale de la máquina."""
import datetime
import faulthandler
import sys
import threading
import traceback

from . import __version__
from .config import APP_DIR

LOG_FILE = APP_DIR / "errores.log"
MAX_BYTES = 1_000_000           # al pasar de ~1 MB se guarda como errores.old.log y empieza otro

_stream = None                  # faulthandler necesita el archivo abierto mientras la app viva


def _write(text):
    try:
        _stream.write(text)
        _stream.flush()
    except Exception:
        pass


def _log_exception(kind, exc_type, exc, tb):
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _write(f"\n[{stamp}] {kind}\n" + "".join(traceback.format_exception(exc_type, exc, tb)))


def start(path=None):
    """Abre el registro y engancha los avisos de error. Devuelve la ruta del archivo (None si no se pudo abrir:
    la app sigue igual, sin registro)."""
    global _stream
    path = path or LOG_FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > MAX_BYTES:
            path.replace(path.with_name("errores.old.log"))
        _stream = open(path, "a", encoding="utf-8")      # noqa: SIM115 (queda abierto: lo usa faulthandler)
    except OSError:
        return None
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _write(f"\n=== MiniSQL {__version__} · {stamp} · Python {sys.version.split()[0]} ===\n")
    faulthandler.enable(file=_stream, all_threads=True)

    previous = sys.excepthook

    def excepthook(exc_type, exc, tb):
        _log_exception("Error no controlado", exc_type, exc, tb)
        if sys.stderr is not None:                      # el .exe sin consola no tiene stderr
            previous(exc_type, exc, tb)

    def thread_hook(args):
        _log_exception(f"Error no controlado en el hilo {args.thread.name if args.thread else '?'}",
                       args.exc_type, args.exc_value, args.exc_traceback)

    sys.excepthook = excepthook
    threading.excepthook = thread_hook
    return path
