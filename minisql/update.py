"""Actualización automática desde GitHub Releases, sin nada de interfaz.

Flujo: latest_release() consulta si hay una versión mayor → download() la baja y verifica su SHA-256 →
self_test() corre el .exe nuevo con --self-test → install() reemplaza el .exe al cerrar la app (Windows no
deja sobrescribir un .exe abierto, pero sí renombrarlo). El .exe anterior queda como MiniSQL.old.exe para
volver atrás (rollback()).

Seguridad: solo HTTPS, nunca se ejecuta un archivo sin verificar su huella SHA-256 publicada en el release,
y la consulta no envía ningún dato de la app ni de las conexiones (solo el User-Agent con la versión).
"""
import contextlib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import __version__

REPO = "AndreeAvalos/mini-sql"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases?per_page=15"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
ASSET = "MiniSQL.exe"
CHECK_EVERY_S = 24 * 3600          # revisión automática: como máximo una vez al día
TIMEOUT_S = 15

_VERSION = re.compile(r"v?(\d+)\.(\d+)\.(\d+)(?:[-.]?(a|b|rc|alpha|beta|pre|dev)[.-]?(\d*))?", re.I)


class UpdateError(Exception):
    """Algo impidió actualizar; el mensaje es para el usuario."""


class Cancelled(UpdateError):
    """El usuario canceló la descarga."""


def version_key(text):
    """Clave para comparar versiones: "v0.10.0" > "0.9.1" y "0.3.0-beta.1" < "0.3.0". None si no es una versión."""
    m = _VERSION.fullmatch((text or "").strip())
    if not m:
        return None
    major, minor, patch, pre, pre_n = m.groups()
    return int(major), int(minor), int(patch), 0 if pre else 1, int(pre_n or 0)


def is_newer(candidate, current=__version__):
    a, b = version_key(candidate), version_key(current)
    return a is not None and b is not None and a > b


def is_frozen():
    """True en el .exe de PyInstaller (solo ahí tiene sentido reemplazar el ejecutable)."""
    return bool(getattr(sys, "frozen", False))


def current_exe():
    return Path(sys.executable).resolve()


@dataclass
class Release:
    version: str
    notes: str
    page: str
    asset_url: str
    asset_size: int
    sha256_url: str | None = None
    digest: str | None = None          # "sha256:…" que GitHub calcula para cada archivo del release
    prerelease: bool = False


def _request(url, accept="application/vnd.github+json"):
    if not url.startswith("https://"):
        raise UpdateError(f"Dirección no segura: {url}")
    return urllib.request.Request(url, headers={"Accept": accept, "User-Agent": f"MiniSQL/{__version__}"})


def fetch_releases(opener=urllib.request.urlopen):
    """Lista de releases de GitHub (dicts de su API). Usa el proxy de Windows o HTTPS_PROXY y los
    certificados de Windows (sirve con la inspección HTTPS de una red corporativa)."""
    try:
        with opener(_request(RELEASES_API), timeout=TIMEOUT_S) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except UpdateError:
        raise
    except Exception as e:
        raise UpdateError(f"No se pudo consultar GitHub: {e}") from e


def latest_release(current=__version__, include_prerelease=False, releases=None):
    """La versión publicada más nueva que current y que trae MiniSQL.exe, o None."""
    best = None
    for r in releases if releases is not None else fetch_releases():
        if r.get("draft") or (r.get("prerelease") and not include_prerelease):
            continue
        version = (r.get("tag_name") or "").lstrip("vV")
        if not is_newer(version, current):
            continue
        assets = {a.get("name"): a for a in r.get("assets") or []}
        exe = assets.get(ASSET)
        if not exe:
            continue
        sha = assets.get(ASSET + ".sha256")
        candidate = Release(version=version, notes=r.get("body") or "", page=r.get("html_url") or RELEASES_PAGE,
                            asset_url=exe["browser_download_url"], asset_size=int(exe.get("size") or 0),
                            sha256_url=sha["browser_download_url"] if sha else None, digest=exe.get("digest"),
                            prerelease=bool(r.get("prerelease")))
        if best is None or version_key(candidate.version) > version_key(best.version):
            best = candidate
    return best


def should_check(settings, now=None):
    """¿Toca la revisión automática? settings: el dict "updates" de config.json."""
    if not settings.get("auto", True):
        return False
    return (now or time.time()) - float(settings.get("last_check") or 0) >= CHECK_EVERY_S


def expected_sha256(release, opener=urllib.request.urlopen):
    """La huella publicada del .exe: el archivo MiniSQL.exe.sha256 del release o, si no está, la que
    calcula GitHub. Sin huella no se instala nada."""
    if release.sha256_url:
        try:
            with opener(_request(release.sha256_url, "application/octet-stream"), timeout=TIMEOUT_S) as resp:
                text = resp.read(4096).decode("ascii", "replace")
        except Exception as e:
            raise UpdateError(f"No se pudo descargar la huella SHA-256: {e}") from e
        m = re.search(r"\b[0-9a-fA-F]{64}\b", text)
        if m:
            return m.group().lower()
    if release.digest and release.digest.lower().startswith("sha256:"):
        return release.digest.split(":", 1)[1].lower()
    raise UpdateError("El release no publica la huella SHA-256 del .exe; por seguridad no se instala. "
                      "Descárgalo a mano desde la página de versiones.")


def download(release, dest_dir, progress=None, cancelled=lambda: False, opener=urllib.request.urlopen,
             chunk=256 * 1024):
    """Descarga el .exe a dest_dir/MiniSQL-<versión>.exe y verifica su SHA-256. progress(bajados, total).
    Mientras baja se llama .part: un corte nunca deja un .exe a medias que parezca bueno."""
    expected = expected_sha256(release, opener)
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    final = dest_dir / f"MiniSQL-{release.version}.exe"
    part = final.with_name(final.name + ".part")
    digest, done = hashlib.sha256(), 0
    try:
        with opener(_request(release.asset_url, "application/octet-stream"), timeout=TIMEOUT_S) as resp, \
                open(part, "wb") as out:
            if not resp.geturl().startswith("https://"):
                raise UpdateError("La descarga fue redirigida a una dirección no segura")
            total = int(resp.headers.get("Content-Length") or release.asset_size or 0)
            while True:
                if cancelled():
                    raise Cancelled("Descarga cancelada")
                block = resp.read(chunk)
                if not block:
                    break
                out.write(block)
                digest.update(block)
                done += len(block)
                if progress:
                    progress(done, total)
        if release.asset_size and done != release.asset_size:
            raise UpdateError(f"La descarga quedó incompleta ({done} de {release.asset_size} bytes)")
        if digest.hexdigest() != expected:
            raise UpdateError("La huella SHA-256 del archivo descargado no coincide con la publicada; "
                              "no se instala.")
        os.replace(part, final)
        return final
    except UpdateError:
        raise
    except Exception as e:
        raise UpdateError(f"No se pudo descargar la versión {release.version}: {e}") from e
    finally:
        with contextlib.suppress(OSError):
            part.unlink()


def self_test(exe, timeout=180, run=subprocess.run):
    """Corre el .exe nuevo con --self-test antes de instalarlo. Devuelve (ok, informe)."""
    exe = Path(exe)
    try:
        result = run([str(exe), "--self-test"], cwd=str(exe.parent), timeout=timeout,
                     capture_output=True, text=True)
    except Exception as e:
        return False, str(e)
    log = exe.parent / "selftest.log"
    report = ""
    with contextlib.suppress(OSError):
        report = log.read_text(encoding="utf-8")
        log.unlink()
    return result.returncode == 0, report or (result.stdout or "") + (result.stderr or "")


def previous_exe(exe=None):
    exe = Path(exe or current_exe())
    return exe.with_name(exe.stem + ".old" + exe.suffix)


def can_replace(exe=None):
    """¿Se puede escribir en la carpeta del .exe? (En Program Files no, sin permisos de administrador.)"""
    folder = Path(exe or current_exe()).parent
    try:
        with tempfile.NamedTemporaryFile(dir=folder, prefix=".minisql-", delete=True):
            return True
    except OSError:
        return False


def install(new_exe, exe=None):
    """Pone new_exe en lugar del .exe actual (que puede estar abierto: se renombra, no se sobrescribe).
    El anterior queda como MiniSQL.old.exe. Si algo falla, deja todo como estaba."""
    exe = Path(exe or current_exe())
    old = previous_exe(exe)
    if old.exists():
        old.unlink()                       # el respaldo de la actualización anterior
    exe.rename(old)
    try:
        shutil.move(str(new_exe), str(exe))
    except Exception:
        with contextlib.suppress(OSError):
            exe.unlink()
        old.rename(exe)
        raise
    return old


def rollback(exe=None):
    """Vuelve a la versión anterior: intercambia MiniSQL.exe y MiniSQL.old.exe."""
    exe = Path(exe or current_exe())
    old = previous_exe(exe)
    if not old.exists():
        raise UpdateError("No hay una versión anterior guardada")
    swap = exe.with_name(exe.stem + ".swap" + exe.suffix)
    exe.rename(swap)
    try:
        old.rename(exe)
    except Exception:
        swap.rename(exe)
        raise
    swap.rename(old)


def clean_downloads(dest_dir, keep=None):
    """Borra descargas viejas o a medias (keep: el archivo que todavía se va a instalar)."""
    for f in Path(dest_dir).glob("MiniSQL-*.exe*"):
        if keep is None or f.resolve() != Path(keep).resolve():
            with contextlib.suppress(OSError):
                f.unlink()


def relaunch(exe, popen=subprocess.Popen):
    """Abre el .exe (después de soltar el candado de instancia única), separado de este proceso."""
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    popen([str(exe)], cwd=str(Path(exe).parent), close_fds=True, creationflags=flags)
