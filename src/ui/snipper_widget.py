"""
Snipaste 级全功能屏幕截图框选组件 (SnipperWidget)
提供区域框选、8点缩放调整、位置移动、Snipaste 浮动工具栏（贴图、翻译、复制、保存）
完全基于内存捕获，零文件写入，零后台持续监控
"""

import os
from PyQt6.QtCore import Qt, QRect, QPoint, pyqtSignal
from PyQt6.QtGui import (
    QPainter, QColor, QPen, QBrush, QFont, QCursor, QPixmap, QImage, QAction
)
from PyQt6.QtWidgets import (
    QWidget, QApplication, QHBoxLayout, QPushButton, QFileDialog, QMessageBox
)


class SelectionState:
    IDLE = 0        # 初始未选
    DRAGGING = 1    # 正在拉框拖选
    SELECTED = 2    # 选框确定，显示工具栏和调整手柄
    MOVING = 3      # 正在拖拽移动整个选框
    RESIZING = 4    # 正在拖拽手柄缩放选框


class SnipperToolbar(QWidget):
    """Snipaste 风格的悬浮操作工具栏"""
    translate_clicked = pyqtSignal()
    pin_clicked = pyqtSignal()
    copy_clicked = pyqtSignal()
    save_clicked = pyqtSignal()
    close_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SnipperToolbar")
        self.setStyleSheet("""
            QWidget#SnipperToolbar {
                background-color: rgba(28, 28, 30, 240);
                border: 1px solid rgba(255, 255, 255, 40);
                border-radius: 6px;
            }
            QPushButton {
                background-color: transparent;
                color: #ffffff;
                border: none;
                border-radius: 4px;
                padding: 5px 9px;
                font-size: 12px;
                font-family: "Microsoft YaHei", "Segoe UI", sans-serif;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 40);
            }
            QPushButton#transBtn {
                color: #2ecc71;
                font-weight: bold;
            }
            QPushButton#transBtn:hover {
                background-color: rgba(46, 204, 113, 60);
            }
            QPushButton#pinBtn {
                color: #3498db;
                font-weight: bold;
            }
            QPushButton#pinBtn:hover {
                background-color: rgba(52, 152, 219, 60);
            }
            QPushButton#closeBtn:hover {
                background-color: rgba(231, 76, 60, 90);
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)

        # 1. 翻译贴图
        self.btn_trans = QPushButton("🌐 翻译贴图", self)
        self.btn_trans.setObjectName("transBtn")
        self.btn_trans.setToolTip("在原位生成置顶贴图并自动翻译 (快捷键: T)")
        self.btn_trans.clicked.connect(self.translate_clicked.emit)
        layout.addWidget(self.btn_trans)

        # 2. 原图贴图
        self.btn_pin = QPushButton("📌 贴图 (F3)", self)
        self.btn_pin.setObjectName("pinBtn")
        self.btn_pin.setToolTip("像 Snipaste 一样将截图置顶在屏幕上 (可在贴图上按空格/T手动翻译)")
        self.btn_pin.clicked.connect(self.pin_clicked.emit)
        layout.addWidget(self.btn_pin)

        # 3. 复制
        self.btn_copy = QPushButton("📋 复制", self)
        self.btn_copy.setToolTip("复制截图到剪贴板并退出 (Enter / Ctrl+C)")
        self.btn_copy.clicked.connect(self.copy_clicked.emit)
        layout.addWidget(self.btn_copy)

        # 4. 保存
        self.btn_save = QPushButton("💾 保存", self)
        self.btn_save.setToolTip("保存截图文件 (Ctrl+S)")
        self.btn_save.clicked.connect(self.save_clicked.emit)
        layout.addWidget(self.btn_save)

        # 5. 关闭
        self.btn_close = QPushButton("✕", self)
        self.btn_close.setObjectName("closeBtn")
        self.btn_close.setToolTip("取消截图 (Esc)")
        self.btn_close.clicked.connect(self.close_clicked.emit)
        layout.addWidget(self.btn_close)

        self.adjustSize()


class SnipperWidget(QWidget):
    """
    全功能交互截图组件
    信号：
      action_triggered(pixmap, global_rect, action_type)
      - action_type: 'translate' (翻译贴图), 'pin' (原图贴图), 'copy' (仅复制)
    """
    action_triggered = pyqtSignal(object, QRect, str)
    cancelled = pyqtSignal()

    HANDLE_SIZE = 8

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setMouseTracking(True)

        self.state = SelectionState.IDLE
        self.fast_mode = False
        self.auto_translate = False

        self.start_pos = None
        self.current_pos = None
        self.selected_rect = QRect()
        self.drag_start_point = QPoint()
        self.active_handle = None

        self.fullscreen_pixmap = None
        self.screen_geo = None

        # 创建悬浮工具栏
        self.toolbar = SnipperToolbar(self)
        self.toolbar.translate_clicked.connect(lambda: self.finish_with_action('translate'))
        self.toolbar.pin_clicked.connect(lambda: self.finish_with_action('pin'))
        self.toolbar.copy_clicked.connect(self.copy_to_clipboard)
        self.toolbar.save_clicked.connect(self.save_to_file)
        self.toolbar.close_clicked.connect(self.cancel_snip)
        self.toolbar.hide()

    def start_snip(self, fast_mode=False, auto_translate=False):
        """开始截图"""
        self.fast_mode = fast_mode
        self.auto_translate = auto_translate
        self.state = SelectionState.IDLE
        self.selected_rect = QRect()
        self.start_pos = None
        self.current_pos = None
        self.toolbar.hide()

        # 截取全部虚拟桌面（支持高分屏与多屏）
        primary = QApplication.primaryScreen()
        self.dpr = primary.devicePixelRatio()
        self.screen_geo = primary.virtualGeometry()
        self.fullscreen_pixmap = primary.grabWindow(
            0,
            self.screen_geo.x(),
            self.screen_geo.y(),
            self.screen_geo.width(),
            self.screen_geo.height()
        )
        self.fullscreen_pixmap.setDevicePixelRatio(self.dpr)

        self.setGeometry(self.screen_geo)
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
        self.show()
        self.raise_()
        self.activateWindow()

    def _get_physical_crop(self, logical_rect: QRect) -> QPixmap:
        """根据屏幕 DPI 将逻辑选区精确转换为底层底图物理像素并裁剪"""
        dpr = self.fullscreen_pixmap.devicePixelRatio()
        phys_rect = QRect(
            int(round(logical_rect.x() * dpr)),
            int(round(logical_rect.y() * dpr)),
            int(round(logical_rect.width() * dpr)),
            int(round(logical_rect.height() * dpr))
        )
        cropped = self.fullscreen_pixmap.copy(phys_rect)
        cropped.setDevicePixelRatio(dpr)
        return cropped

    def finish_with_action(self, action_type: str):
        """以指定模式完成截图"""
        if not self.selected_rect.isValid() or self.selected_rect.width() < 6 or self.selected_rect.height() < 6:
            self.cancel_snip()
            return

        cropped = self._get_physical_crop(self.selected_rect)
        global_rect = QRect(
            self.screen_geo.x() + self.selected_rect.x(),
            self.screen_geo.y() + self.selected_rect.y(),
            self.selected_rect.width(),
            self.selected_rect.height()
        )

        self.hide()
        self.action_triggered.emit(cropped, global_rect, action_type)

    def copy_to_clipboard(self):
        """复制截图到剪贴板并退出"""
        if self.selected_rect.isValid() and self.selected_rect.width() > 5:
            cropped = self._get_physical_crop(self.selected_rect)
            QApplication.clipboard().setPixmap(cropped)
        self.cancel_snip()

    def save_to_file(self):
        """保存截图到本地文件"""
        if not self.selected_rect.isValid() or self.selected_rect.width() < 5:
            return

        cropped = self._get_physical_crop(self.selected_rect)
        self.toolbar.hide()
        
        file_path, _ = QFileDialog.getSaveFileName(
            self, "保存截图", "截图.png", "PNG 图像 (*.png);;JPG 图像 (*.jpg);;所有文件 (*.*)"
        )
        if file_path:
            cropped.save(file_path)
            self.cancel_snip()
        else:
            self.toolbar.show()

    def cancel_snip(self):
        """退出截图"""
        self.toolbar.hide()
        self.hide()
        self.state = SelectionState.IDLE
        self.cancelled.emit()

    # ===== 选框与 8 点手柄辅助计算 =====

    def _get_handles(self, rect: QRect):
        """返回 8 个调整控制点的坐标字典"""
        hs = self.HANDLE_SIZE
        half = hs // 2
        l, r, t, b = rect.left(), rect.right(), rect.top(), rect.bottom()
        cx, cy = rect.center().x(), rect.center().y()

        return {
            'top_left': QRect(l - half, t - half, hs, hs),
            'top': QRect(cx - half, t - half, hs, hs),
            'top_right': QRect(r - half, t - half, hs, hs),
            'right': QRect(r - half, cy - half, hs, hs),
            'bottom_right': QRect(r - half, b - half, hs, hs),
            'bottom': QRect(cx - half, b - half, hs, hs),
            'bottom_left': QRect(l - half, b - half, hs, hs),
            'left': QRect(l - half, cy - half, hs, hs),
        }

    def _get_handle_under_pos(self, pos: QPoint):
        if not self.selected_rect.isValid():
            return None
        handles = self._get_handles(self.selected_rect)
        for name, h_rect in handles.items():
            if h_rect.contains(pos):
                return name
        return None

    def _update_cursor_for_pos(self, pos: QPoint):
        if self.state != SelectionState.SELECTED:
            return

        handle = self._get_handle_under_pos(pos)
        if handle in ('top_left', 'bottom_right'):
            self.setCursor(QCursor(Qt.CursorShape.SizeFDiagCursor))
        elif handle in ('top_right', 'bottom_left'):
            self.setCursor(QCursor(Qt.CursorShape.SizeBDiagCursor))
        elif handle in ('top', 'bottom'):
            self.setCursor(QCursor(Qt.CursorShape.SizeVerCursor))
        elif handle in ('left', 'right'):
            self.setCursor(QCursor(Qt.CursorShape.SizeHorCursor))
        elif self.selected_rect.contains(pos):
            self.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))
        else:
            self.setCursor(QCursor(Qt.CursorShape.CrossCursor))

    def _update_toolbar_position(self):
        """计算并更新浮动工具栏位置（贴近选框下方或上方）"""
        if not self.selected_rect.isValid() or self.state != SelectionState.SELECTED:
            self.toolbar.hide()
            return

        tb_w = self.toolbar.width()
        tb_h = self.toolbar.height()

        # 默认放置在选框右下方
        tb_x = self.selected_rect.right() - tb_w
        tb_y = self.selected_rect.bottom() + 8

        # 如果下方超出屏幕底端，则放置在选框上方
        if tb_y + tb_h > self.height() - 10:
            tb_y = self.selected_rect.top() - tb_h - 8

        # 确保不会超出左右边界
        tb_x = max(10, min(tb_x, self.width() - tb_w - 10))
        tb_y = max(10, min(tb_y, self.height() - tb_h - 10))

        self.toolbar.move(tb_x, tb_y)
        self.toolbar.show()
        self.toolbar.raise_()

    # ===== 鼠标与键盘事件处理 =====

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            if self.state == SelectionState.SELECTED:
                # 右键取消当前选框，恢复重新选取
                self.state = SelectionState.IDLE
                self.selected_rect = QRect()
                self.toolbar.hide()
                self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
                self.update()
            else:
                # 退出截图
                self.cancel_snip()
            return

        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.pos()

            if self.state == SelectionState.SELECTED:
                handle = self._get_handle_under_pos(pos)
                if handle:
                    self.state = SelectionState.RESIZING
                    self.active_handle = handle
                    self.drag_start_point = pos
                    self.toolbar.hide()
                elif self.selected_rect.contains(pos):
                    self.state = SelectionState.MOVING
                    self.drag_start_point = pos
                    self.toolbar.hide()
                else:
                    # 点击选框外部，重新拉框
                    self.state = SelectionState.DRAGGING
                    self.start_pos = pos
                    self.current_pos = pos
                    self.selected_rect = QRect()
                    self.toolbar.hide()
                    self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
            else:
                self.state = SelectionState.DRAGGING
                self.start_pos = pos
                self.current_pos = pos
                self.selected_rect = QRect()

            self.update()

    def mouseMoveEvent(self, event):
        pos = event.pos()

        if self.state == SelectionState.DRAGGING:
            self.current_pos = pos
            self.selected_rect = self._normalize_rect(self.start_pos, self.current_pos)
            self.update()

        elif self.state == SelectionState.MOVING:
            delta = pos - self.drag_start_point
            self.drag_start_point = pos
            new_rect = self.selected_rect.translated(delta)
            # 限制在屏幕内
            new_rect = new_rect.intersected(self.rect())
            self.selected_rect = new_rect
            self.update()

        elif self.state == SelectionState.RESIZING:
            self._resize_rect(pos)
            self.update()

        elif self.state == SelectionState.SELECTED:
            self._update_cursor_for_pos(pos)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self.state == SelectionState.DRAGGING:
                self.current_pos = event.pos()
                self.selected_rect = self._normalize_rect(self.start_pos, self.current_pos)

                if self.selected_rect.width() > 8 and self.selected_rect.height() > 8:
                    if self.fast_mode:
                        # 极速模式（一步直达贴图翻译）
                        action = 'translate' if self.auto_translate else 'pin'
                        self.finish_with_action(action)
                        return
                    else:
                        # Snipaste 交互模式：显示工具栏
                        self.state = SelectionState.SELECTED
                        self._update_toolbar_position()
                        self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
                else:
                    self.state = SelectionState.IDLE
                    self.selected_rect = QRect()

            elif self.state in (SelectionState.MOVING, SelectionState.RESIZING):
                self.state = SelectionState.SELECTED
                self._update_toolbar_position()

            self.update()

    def mouseDoubleClickEvent(self, event):
        """双击选框内部：确认截图"""
        if event.button() == Qt.MouseButton.LeftButton and self.selected_rect.contains(event.pos()):
            action = 'translate' if self.auto_translate else 'pin'
            self.finish_with_action(action)

    def keyPressEvent(self, event):
        key = event.key()

        if key == Qt.Key.Key_Escape:
            if self.state == SelectionState.SELECTED:
                self.state = SelectionState.IDLE
                self.selected_rect = QRect()
                self.toolbar.hide()
                self.update()
            else:
                self.cancel_snip()

        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self.selected_rect.isValid():
                self.copy_to_clipboard()

        elif key == Qt.Key.Key_T:
            if self.selected_rect.isValid():
                self.finish_with_action('translate')

        elif key == Qt.Key.Key_F3 or key == Qt.Key.Key_P:
            if self.selected_rect.isValid():
                self.finish_with_action('pin')

        elif event.modifiers() == Qt.KeyboardModifier.ControlModifier and key == Qt.Key.Key_C:
            self.copy_to_clipboard()

        elif event.modifiers() == Qt.KeyboardModifier.ControlModifier and key == Qt.Key.Key_S:
            self.save_to_file()

    def _normalize_rect(self, p1: QPoint, p2: QPoint) -> QRect:
        if not p1 or not p2:
            return QRect()
        return QRect(
            min(p1.x(), p2.x()),
            min(p1.y(), p2.y()),
            abs(p2.x() - p1.x()),
            abs(p2.y() - p1.y())
        )

    def _resize_rect(self, pos: QPoint):
        """根据当前激活的手柄缩放选框"""
        r = self.selected_rect
        l, t, right, b = r.left(), r.top(), r.right(), r.bottom()
        h = self.active_handle

        if h == 'top_left':
            l, t = pos.x(), pos.y()
        elif h == 'top':
            t = pos.y()
        elif h == 'top_right':
            right, t = pos.x(), pos.y()
        elif h == 'right':
            right = pos.x()
        elif h == 'bottom_right':
            right, b = pos.x(), pos.y()
        elif h == 'bottom':
            b = pos.y()
        elif h == 'bottom_left':
            l, b = pos.x(), pos.y()
        elif h == 'left':
            l = pos.x()

        rect = QRect(min(l, right), min(t, b), abs(right - l), abs(b - t))
        if rect.width() > 6 and rect.height() > 6:
            self.selected_rect = rect

    # ===== 绘制界面 =====

    def paintEvent(self, event):
        if not self.fullscreen_pixmap:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 1. 绘制底层屏幕原图
        painter.drawPixmap(0, 0, self.fullscreen_pixmap)

        # 2. 绘制带镂空的半透明遮罩 (选区内部保持 100% 原图像素，杜绝任何坐标错位)
        from PyQt6.QtGui import QRegion
        full_region = QRegion(self.rect())
        if self.selected_rect.isValid() and self.selected_rect.width() > 0:
            mask_region = full_region.subtracted(QRegion(self.selected_rect))
        else:
            mask_region = full_region

        painter.setClipRegion(mask_region)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 110))
        painter.setClipping(False)

        # 3. 选框高亮边框
        if self.selected_rect.isValid() and self.selected_rect.width() > 0:
            rect = self.selected_rect
            border_pen = QPen(QColor(0, 166, 125), 2)
            painter.setPen(border_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(rect)

            # 4. 绘制 8 点调整手柄（处于 SELECTED 状态时）
            if self.state == SelectionState.SELECTED:
                painter.setPen(QPen(QColor(0, 166, 125), 1.5))
                painter.setBrush(QBrush(QColor(255, 255, 255)))
                for h_rect in self._get_handles(rect).values():
                    painter.drawRect(h_rect)

            # 5. 绘制选区尺寸信息标签
            info_text = f"{rect.width()} × {rect.height()}"
            font = QFont("Microsoft YaHei", 9)
            painter.setFont(font)
            fm = painter.fontMetrics()
            tw = fm.horizontalAdvance(info_text)
            th = fm.height()

            info_x = rect.x()
            info_y = rect.y() - th - 8
            if info_y < 5:
                info_y = rect.y() + 6

            info_bg = QRect(info_x, info_y, tw + 10, th + 4)
            painter.fillRect(info_bg, QColor(0, 0, 0, 190))
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(info_x + 5, info_y + th - 2, info_text)
