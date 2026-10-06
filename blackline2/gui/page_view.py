"""Seitenansicht mit farbig markierten Funden bzw. Vorschau der fertigen Schwärzung."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QGraphicsItem, QGraphicsPixmapItem, QGraphicsRectItem, QGraphicsScene,
                               QGraphicsSimpleTextItem, QGraphicsView)

from blackline2.labels import short_label
from blackline2.model import Hit

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


def color_for(category: str) -> QColor:
    return CATEGORY_COLORS.get(category, CATEGORY_COLORS["sonstiges"])


class HitItem(QGraphicsRectItem):
    def __init__(self, hit: Hit, rect: QRectF, preview: bool, highlight: bool):
        super().__init__(rect)
        self.hit = hit
        self.setAcceptHoverEvents(True)
        self.setToolTip(f"{hit.label}\n„{hit.text}“\nQuelle: {hit.source}\n"
                        + ("Klicken = nicht schwärzen" if hit.enabled else "Klicken = doch schwärzen"))
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
        if highlight:
            pen = QPen(QColor(255, 200, 0), 2.5)
        pen.setCosmetic(False)
        self.setPen(pen)


class PageView(QGraphicsView):
    hit_toggled = Signal(int)
    rect_drawn = Signal(QRectF)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
                            | QPainter.RenderHint.TextAntialiasing)
        self.setBackgroundBrush(QBrush(QColor(90, 90, 95)))
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.manual_mode = False
        self._drag_start: QPointF | None = None
        self._rubber: QGraphicsRectItem | None = None
        self._page_rect = QRectF()
        self._fit_pending = True
        self._zoom_fit = True

    # ------------------------------------------------------------ Anzeige
    def clear_page(self, message: str = "") -> None:
        self.scene().clear()
        if message:
            t = self.scene().addSimpleText(message)
            t.setBrush(QBrush(Qt.GlobalColor.white))
        self._page_rect = QRectF()

    def show_page(self, pixmap: QPixmap | None, width: float, height: float, hits: list[Hit],
                  preview: bool, highlight_id: int | None = None) -> None:
        scene = self.scene()
        scene.clear()
        self._rubber = None
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
                scene.addItem(it)
                if preview:
                    self._add_label(r, h.label)
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

    # ------------------------------------------------------------ Maus
    def set_manual_mode(self, on: bool) -> None:
        self.manual_mode = on
        self.setDragMode(QGraphicsView.DragMode.NoDrag if on else QGraphicsView.DragMode.ScrollHandDrag)
        self.viewport().setCursor(Qt.CursorShape.CrossCursor if on else Qt.CursorShape.OpenHandCursor)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if self.manual_mode:
                self._drag_start = self.mapToScene(event.position().toPoint())
                self._rubber = self.scene().addRect(QRectF(self._drag_start, self._drag_start),
                                                    QPen(QColor(0, 0, 0), 1, Qt.PenStyle.DashLine),
                                                    QBrush(QColor(0, 0, 0, 60)))
                self._rubber.setZValue(10)
                event.accept()
                return
            item = self.itemAt(event.position().toPoint())
            while item is not None and not isinstance(item, HitItem):
                item = item.parentItem() if isinstance(item, QGraphicsItem) else None
            if isinstance(item, HitItem):
                self.hit_toggled.emit(item.hit.id)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self.manual_mode and self._drag_start is not None and self._rubber is not None:
            p = self.mapToScene(event.position().toPoint())
            self._rubber.setRect(QRectF(self._drag_start, p).normalized())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self.manual_mode and self._drag_start is not None:
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
