"""Notas de una versión a partir de los mensajes de commit desde la versión anterior (lo usa release.yml).

    python packaging/release_notes.py <commit> notas.md

GitHub arma sus notas automáticas con Pull Requests; como aquí se sube directo a main, saldrían vacías. Estas se
leen en la app (ventana "Novedades"): sin las marcas [CI], sin los commits "Versión X.Y.Z" del bot ni merges.
"""
import re
import subprocess
import sys
from pathlib import Path

SKIP = re.compile(r"^(Versión \d+\.\d+\.\d+\S*|Merge\b)", re.I)
CI_MARK = re.compile(r"\s*\[CI(?:\s+(?:minor|major|patch))?\]\s*", re.I)


def clean(subject):
    """El mensaje sin la marca [CI] y con mayúscula inicial."""
    text = " ".join(CI_MARK.sub(" ", subject).split())
    return text[:1].upper() + text[1:]


def notes(subjects):
    """Lista en Markdown, sin repetidos ni commits que no le dicen nada al usuario."""
    items, seen = [], set()
    for subject in subjects:
        subject = subject.strip()
        if not subject or SKIP.match(subject):
            continue
        text = clean(subject)
        if text and text.lower() not in seen:
            seen.add(text.lower())
            items.append(f"- {text}")
    return "\n".join(items) if items else "- Mejoras y correcciones menores."


def git(*args, run=subprocess.run):
    return run(["git", *args], capture_output=True, text=True, encoding="utf-8").stdout.strip()


def previous_tag(ref, run=subprocess.run):
    """La versión publicada anterior a ref (None si es la primera)."""
    return git("describe", "--tags", "--abbrev=0", "--match", "v*", f"{ref}^", run=run) or None


def build(ref, run=subprocess.run):
    prev = previous_tag(ref, run=run)
    log = git("log", "--no-merges", "--pretty=%s", f"{prev}..{ref}" if prev else ref, run=run)
    return "## Cambios\n\n" + notes(log.splitlines()) + "\n"


if __name__ == "__main__":
    Path(sys.argv[2]).write_text(build(sys.argv[1]), encoding="utf-8")
