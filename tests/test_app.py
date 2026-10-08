"""Una sola instancia: dos MiniSQL abiertos se pisarían el autoguardado de las hojas."""
import subprocess
import sys
from pathlib import Path

from minisql.app import single_instance_lock

HOLD_LOCK = r"""
import sys
sys.path.insert(0, sys.argv[2])
from PySide6.QtCore import QCoreApplication
app = QCoreApplication([])
from pathlib import Path
from minisql.app import single_instance_lock
lock = single_instance_lock(Path(sys.argv[1]))
print("tomado" if lock else "ocupado", flush=True)
sys.stdin.readline()              # mantiene el candado hasta que la prueba le escriba
"""


def test_only_one_instance_at_a_time(app, tmp_path):
    path = tmp_path / "minisql.lock"
    root = str(Path(__file__).resolve().parents[1])
    other = subprocess.Popen([sys.executable, "-c", HOLD_LOCK, str(path), root],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert other.stdout.readline().strip() == "tomado"           # otra instancia abierta
        assert single_instance_lock(path) is None                     # esta no puede abrirse
    finally:
        other.stdin.write("\n")
        other.stdin.flush()
        other.wait(10)
    lock = single_instance_lock(path)                                 # la otra se cerró: ya se puede
    assert lock is not None
    lock.unlock()


def test_lock_is_released_if_the_other_instance_crashed(app, tmp_path):
    path = tmp_path / "minisql.lock"
    root = str(Path(__file__).resolve().parents[1])
    other = subprocess.Popen([sys.executable, "-c", HOLD_LOCK, str(path), root],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    assert other.stdout.readline().strip() == "tomado"
    other.kill()                                                      # cierre inesperado: no suelta el candado
    other.wait(10)
    assert path.exists()                                              # el archivo quedó en disco…
    lock = single_instance_lock(path)                                 # …pero se detecta que su proceso murió
    assert lock is not None
    lock.unlock()
