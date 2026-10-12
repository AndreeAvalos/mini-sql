"""Confirmación antes de guardar los cambios de la grilla: muestra las sentencias que se van a ejecutar."""

from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
)

from .highlighter import SqlHighlighter
from .style import WARN_COLOR, is_dark, mono_font


class SavePreviewDialog(QDialog):
    """target: "OWNER.TABLA"; statements: textos de db.grid.preview_sql;
    summary: dict de EditableResultModel.summary."""

    def __init__(self, target, statements, summary, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Guardar cambios en {target}")
        self.resize(760, 480)
        self.statements = statements
        layout = QVBoxLayout(self)

        counts = ", ".join(f"{n} {k}" for k, n in summary.items() if n) or "sin cambios"
        head = QLabel(f"Se ejecutarán <b>{len(statements)}</b> sentencia(s) en <b>{target}</b> ({counts}).")
        layout.addWidget(head)
        if summary.get("borradas"):
            warn = QLabel(f"⚠ Se borrarán {summary['borradas']} fila(s).")
            warn.setStyleSheet(f"color: {WARN_COLOR}; font-weight: bold;")
            layout.addWidget(warn)

        self.text = QPlainTextEdit("\n\n".join(statements))
        self.text.setReadOnly(True)
        self.text.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.text.setFont(mono_font())
        self.highlighter = SqlHighlighter(self.text.document(), dark=is_dark(self))
        layout.addWidget(self.text, 1)

        note = QLabel("Cada fila se identifica por su ROWID y los valores viajan como bind variables. "
                      "Si una sentencia falla no se guarda ninguna. No se hace Commit: después decides "
                      "Commit o Rollback.")
        note.setWordWrap(True)
        note.setStyleSheet("color: gray;")
        layout.addWidget(note)

        buttons = QDialogButtonBox()
        self.save_btn = buttons.addButton("✔ Guardar", QDialogButtonBox.AcceptRole)
        buttons.addButton("Copiar SQL", QDialogButtonBox.ActionRole).clicked.connect(self.copy)
        buttons.addButton("Cancelar", QDialogButtonBox.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.save_btn.setDefault(True)

    def copy(self):
        QApplication.clipboard().setText(self.text.toPlainText())
