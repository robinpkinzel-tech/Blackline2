"""Seitenansicht mit farbig markierten Funden bzw. Vorschau der fertigen Schwärzung.

Bedienung:
  * Klick auf eine Markierung      -> überall (alle Dokumente) aus-/einschalten
  * Strg + Klick                   -> nur diese eine Stelle
  * Rechtsklick auf Wort/Markierung -> Menü (überall schwärzen, Kürzel ändern …)
  * Gelb gestrichelt               -> ungelesener Bereich (Handschrift, Stempel); Klick = schwärzen
  * Ziehen in der Vorschau (oder Shift+Ziehen, oder im Modus "Bereich manuell schwärzen")
                                   -> Bereich wird weiß, direkt darunter erscheint das Eingabefeld
                                      für das Kürzel; Enter übernimmt, Esc bricht ab
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QCheckBox, QCompleter, QGraphicsItem, QGraphicsPixmapItem, QGraphicsRectItem,
                               QGraphicsScene, QGraphicsSimpleTextItem, QGraphicsView, QHBoxLayout, QLineEdit,
                               QWidget)

from blackline2.labels import short_label
from blackline2.model import Hit, Rect

RENDER_DPI = 144

CATEGORY_COLORS = {
    "name": QColor(220, 40, 40),
    "adresse": QColor(235, 130, 20),
    "email": QColor(30, 110, 220),
    "telefon": QColor(140, 60, 200),
    "iban": QColor(0, 150, 140),
    "versicherung": QColor(150, 90, 40),
    "geburtsdatum": QColor(210, 40, 160),
    "benutzer": QColor(30, 150, 50),
    "sonstiges": QColor(110, 110, 110),
    "manuell": QColor(20, 20, 20),
}
UNREAD_COLOR = QColor(255, 170, 0)


def color_for(category: str) -> QColor:
    return CATEGORY_COLORS.get(category, CATEGORY_COLORS["sonstiges"])


class HitItem(QGraphicsRectItem):
    def __init__(self, hit: Hit, rect: QRectF, preview: bool, highlight: bool):
        super().__init__(rect)
        self.hit = hit
        self.setAcceptHoverEvents(True)
        tip = f"{hit.label}\n„{hit.text}“\nQuelle: {hit.source}"
        if hit.question:
            tip += f"\n❓ Rückfrage: {hit.question}"
        tip += ("\n\nKlick = überall nicht schwärzen" if hit.enabled else "\n\nKlick = überall doch schwärzen")
        tip += "\nStrg+Klick = nur diese Stelle · Rechtsklick = Menü"
        self.setToolTip(tip)
        if preview:
            self.setPen(QPen(Qt.PenStyle.NoPen))
            self.setBrush(QBrush(Qt.GlobalColor.white))
            return
        c = color_for(hit.category)
        if hit.enabled:
            fill = QColor(c)
            fill.setAlpha(70)
            pen = QPen(c, 1.0)
            self.setBrush(QBrush(fill))
        else:
            pen = QPen(QColor(150, 150, 150), 0.8, Qt.PenStyle.DashLine)
            self.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        if hit.question:
            pen = QPen(QColor(230, 120, 0), 1.6, Qt.PenStyle.DotLine)
        if highlight:
            pen = QPen(QColor(255, 200, 0), 2.5)
        pen.setCosmetic(False)
        self.setPen(pen)


class UnreadItem(QGraphicsRectItem):
    def __init__(self, index: int, rect: QRectF):
        super().__init__(rect)
        self.index = index
        self.setToolTip("Ungelesener Bereich (Handschrift, Unterschrift, Stempel?)\n"
                        "Texterkennung und KI können hier nichts lesen.\nKlick = Bereich schwärzen")
        pen = QPen(UNREAD_COLOR, 1.6, Qt.PenStyle.DashLine)
        pen.setCosmetic(False)
        self.setPen(pen)
        fill = QColor(UNREAD_COLOR)
        fill.setAlpha(28)
        self.setBrush(QBrush(fill))
        self.setZValue(1)


class _LabelEdit(QLineEdit):
    """Eingabefeld für das Kürzel: Enter übernimmt, Esc bricht ab."""

    confirmed = Signal()
    cancelled = Signal()

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.confirmed.emit()
            return
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
            return
        super().keyPressEvent(event)


class PageView(QGraphicsView):
    hit_toggled = Signal(int, bool)          # hit_id, überall?
    rect_drawn = Signal(QRectF)
    rect_labeled = Signal(QRectF, str, bool)  # Bereich, Kürzel, Text überall suchen?
    unread_clicked = Signal(int)
    context_requested = Signal(float, float, int, object)  # x, y (Seite), hit_id oder -1, globale Position

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
                            | QPainter.RenderHint.TextAntialiasing)
        self.setBackgroundBrush(QBrush(QColor(90, 90, 95)))
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.manual_mode = False
        self.preview_mode = False
        self._drag_start: QPointF | None = None
        self._rubber: QGraphicsRectItem | None = None
        self._page_rect = QRectF()
        self._zoom_fit = True
        # laufende Kürzel-Eingabe
        self._edit_proxy = None
        self._edit_panel: QWidget | None = None
        self._edit_box: QGraphicsRectItem | None = None
        self._edit_rect: QRectF | None = None
        self.active_editor: _LabelEdit | None = None
        self.active_everywhere: QCheckBox | None = None

    # ------------------------------------------------------------ Anzeige
    def clear_page(self, message: str = "") -> None:
        self.scene().clear()
        if message:
            t = self.scene().addSimpleText(message)
            t.setBrush(QBrush(Qt.GlobalColor.white))
        self._page_rect = QRectF()

    def show_page(self, pixmap: QPixmap | None, width: float, height: float, hits: list[Hit],
                  preview: bool, highlight_id: int | None = None, unread: list[Rect] | None = None) -> None:
        scene = self.scene()
        scene.clear()
        self._rubber = None
        self._edit_proxy = self._edit_box = self._edit_rect = None
        self.active_editor = self.active_everywhere = None
        self.set_preview_mode(preview)
        self._page_rect = QRectF(0, 0, width, height)
        scene.setSceneRect(self._page_rect.adjusted(-20, -20, 20, 20))
        bg = scene.addRect(self._page_rect, QPen(Qt.PenStyle.NoPen), QBrush(Qt.GlobalColor.white))
        bg.setZValue(-2)
        if pixmap is not None:
            item = QGraphicsPixmapItem(pixmap)
            item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
            item.setScale(width / pixmap.width())
            item.setZValue(-1)
            scene.addItem(item)
        for h in hits:
            if preview and not h.enabled:
                continue
            for x0, y0, x1, y1 in h.rects:
                r = QRectF(x0, y0, x1 - x0, y1 - y0)
                it = HitItem(h, r, preview, h.id == highlight_id)
                it.setZValue(2)
                scene.addItem(it)
                if preview:
                    self._add_label(r, h.label)
        if unread and not preview:
            for i, (x0, y0, x1, y1) in enumerate(unread):
                scene.addItem(UnreadItem(i, QRectF(x0, y0, x1 - x0, y1 - y0)))
        if self._zoom_fit:
            self.fit_width()

    def _add_label(self, r: QRectF, label: str) -> None:
        if r.width() < 3 or r.height() < 3:
            return
        for text in (label, short_label(label)):
            t = QGraphicsSimpleTextItem(text)
            f = QFont("Helvetica")
            f.setPointSizeF(10)
            t.setFont(f)
            br = t.boundingRect()
            s = min(r.height() * 0.8 / br.height(), (r.width() - 2) / max(br.width(), 1))
            if s * 10 >= 4.5 or text != label:
                break
        s = max(s, 0.05)
        t.setScale(s)
        t.setBrush(QBrush(Qt.GlobalColor.black))
        w, h = br.width() * s, br.height() * s
        t.setPos(r.x() + (r.width() - w) / 2, r.y() + (r.height() - h) / 2)
        t.setZValue(5)
        self.scene().addItem(t)

    def center_on_hit(self, hit: Hit) -> None:
        if hit.rects:
            x0, y0, x1, y1 = hit.rects[0]
            self.centerOn(QPointF((x0 + x1) / 2, (y0 + y1) / 2))

    def center_on_rect(self, rect: Rect) -> None:
        x0, y0, x1, y1 = rect
        self.centerOn(QPointF((x0 + x1) / 2, (y0 + y1) / 2))

    # ------------------------------------------------------------ Zoom
    def fit_width(self) -> None:
        if self._page_rect.isEmpty():
            return
        self._zoom_fit = True
        vw = self.viewport().width() - 30
        if vw <= 0:
            return
        s = vw / self._page_rect.width()
        self.resetTransform()
        self.scale(s, s)

    def zoom(self, factor: float) -> None:
        self._zoom_fit = False
        self.scale(factor, factor)

    def wheelEvent(self, event) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoom(1.15 if event.angleDelta().y() > 0 else 1 / 1.15)
            event.accept()
            return
        super().wheelEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._zoom_fit:
            self.fit_width()

    # ------------------------------------------------------------ Kürzel direkt am Bereich eingeben
    def begin_label_edit(self, rect: QRectF, labels: list[str], phrase: str = "",
                         default: str = "geschwärzt") -> None:
        """Bereich weiß zeigen und sofort das Eingabefeld für das Kürzel öffnen."""
        self.cancel_label_edit()
        scene = self.scene()
        self._edit_rect = QRectF(rect)
        box = scene.addRect(rect, QPen(QColor(0, 0, 0), 0.6, Qt.PenStyle.DashLine), QBrush(Qt.GlobalColor.white))
        box.setZValue(6)
        self._edit_box = box

        panel = QWidget()
        panel.setStyleSheet("QWidget { background: #fffbe6; border: 1px solid #c90; border-radius: 4px; }"
                            "QLineEdit { background: white; border: 1px solid #999; padding: 2px 4px; }"
                            "QCheckBox { border: none; }")
        lay = QHBoxLayout(panel)
        lay.setContentsMargins(6, 4, 6, 4)
        edit = _LabelEdit()
        edit.setPlaceholderText("Kürzel tippen, Enter")
        edit.setText(default)
        edit.selectAll()
        edit.setMinimumWidth(190)
        completer = QCompleter(sorted(set(labels)))
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setCompletionMode(QCompleter.CompletionMode.InlineCompletion)
        edit.setCompleter(completer)
        lay.addWidget(edit)
        every = QCheckBox("auch überall")
        every.setToolTip(f"„{phrase[:60]}“ zusätzlich in allen geladenen Dokumenten suchen und schwärzen"
                         if phrase else "Kein erkannter Text im Bereich")
        every.setChecked(bool(phrase) and len(phrase) >= 3)
        every.setVisible(bool(phrase))
        lay.addWidget(every)
        proxy = scene.addWidget(panel)
        proxy.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
        proxy.setZValue(30)
        proxy.setPos(rect.left(), rect.bottom() + 2)
        self._edit_proxy = proxy
        self._edit_panel = panel  # Referenz halten: der Proxy besitzt das Widget, Python darf es nicht freigeben
        self.active_editor = edit
        self.active_everywhere = every

        # Aufräumen erst im nächsten Ereignisdurchlauf: das Eingabefeld darf nicht gelöscht
        # werden, während es noch sein eigenes Enter/Esc-Signal verarbeitet
        edit.confirmed.connect(lambda: QTimer.singleShot(0, self._confirm_label_edit))
        edit.cancelled.connect(lambda: QTimer.singleShot(0, self.cancel_label_edit))
        self.setFocus()
        proxy.setFocus()
        edit.setFocus()
        self.ensureVisible(proxy)

    def _confirm_label_edit(self) -> None:
        if self._edit_rect is None or self.active_editor is None:
            return
        label = self.active_editor.text().strip() or "geschwärzt"
        everywhere = bool(self.active_everywhere and self.active_everywhere.isVisible()
                          and self.active_everywhere.isChecked())
        rect = QRectF(self._edit_rect)
        self.cancel_label_edit()
        self.rect_labeled.emit(rect, label, everywhere)

    def cancel_label_edit(self) -> None:
        """Eingabe beenden. Die Elemente werden nur ausgeblendet; gelöscht werden sie beim
        nächsten Seitenaufbau (scene.clear) – das vermeidet Abstürze durch doppelte Freigabe."""
        if self.active_editor is not None:
            self.active_editor.clearFocus()
        for item in (self._edit_proxy, self._edit_box):
            if item is not None:
                item.setVisible(False)
                item.setEnabled(False)
        self._edit_proxy = self._edit_box = self._edit_rect = None
        self.active_editor = self.active_everywhere = None
        self.setFocus()

    # ------------------------------------------------------------ Maus
    def set_preview_mode(self, on: bool) -> None:
        self.preview_mode = on
        self._update_drag_mode()

    def set_manual_mode(self, on: bool) -> None:
        self.manual_mode = on
        self._update_drag_mode()

    def _update_drag_mode(self) -> None:
        draw = self.manual_mode or self.preview_mode
        self.setDragMode(QGraphicsView.DragMode.NoDrag if draw else QGraphicsView.DragMode.ScrollHandDrag)
        self.viewport().setCursor(Qt.CursorShape.CrossCursor if draw else Qt.CursorShape.OpenHandCursor)

    def _item_at(self, pos, cls):
        for item in self.items(pos):
            while item is not None and not isinstance(item, cls):
                item = item.parentItem() if isinstance(item, QGraphicsItem) else None
            if isinstance(item, cls):
                return item
        return None

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            if self._edit_proxy is not None and self._edit_proxy.isUnderMouse():
                super().mousePressEvent(event)
                return
            if self._edit_proxy is not None:
                QTimer.singleShot(0, self.cancel_label_edit)  # Klick daneben = Eingabe abbrechen
            on_item = self._item_at(pos, HitItem) is not None or self._item_at(pos, UnreadItem) is not None
            if self.manual_mode or shift or (self.preview_mode and not on_item):
                self._drag_start = self.mapToScene(pos)
                self._rubber = self.scene().addRect(QRectF(self._drag_start, self._drag_start),
                                                    QPen(QColor(0, 0, 0), 1, Qt.PenStyle.DashLine),
                                                    QBrush(QColor(0, 0, 0, 60)))
                self._rubber.setZValue(10)
                event.accept()
                return
            hit_item = self._item_at(pos, HitItem)
            if hit_item is not None:
                everywhere = not (event.modifiers() & Qt.KeyboardModifier.ControlModifier)
                self.hit_toggled.emit(hit_item.hit.id, everywhere)
                event.accept()
                return
            unread_item = self._item_at(pos, UnreadItem)
            if unread_item is not None:
                self.unread_clicked.emit(unread_item.index)
                event.accept()
                return
        super().mousePressEvent(event)

    def contextMenuEvent(self, event) -> None:
        if self._page_rect.isEmpty():
            return
        pos = event.pos()
        scene_pos = self.mapToScene(pos)
        hit_item = self._item_at(pos, HitItem)
        self.context_requested.emit(scene_pos.x(), scene_pos.y(),
                                    hit_item.hit.id if hit_item else -1, event.globalPos())
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_start is not None and self._rubber is not None:
            p = self.mapToScene(event.position().toPoint())
            self._rubber.setRect(QRectF(self._drag_start, p).normalized())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._drag_start is not None:
            p = self.mapToScene(event.position().toPoint())
            rect = QRectF(self._drag_start, p).normalized().intersected(self._page_rect)
            self._drag_start = None
            if self._rubber is not None:
                self.scene().removeItem(self._rubber)
                self._rubber = None
            if rect.width() >= 3 and rect.height() >= 3:
                self.rect_drawn.emit(rect)
            event.accept()
            return
        super().mouseReleaseEvent(event)
