"""
贴图与翻译预览窗口 (PreviewWindow)
支持将截图置顶定在屏幕上，自动识别文字并覆盖渲染翻译，支持自由拖拽、透明度调节及原文/译文快速切换
"""

import cv2
import numpy as np
import pyperclip
import logging
from PyQt6.QtCore import Qt, QRect, QPoint, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QPainter, QPixmap, QImage, QColor, QPen, QCursor, QAction
from PyQt6.QtWidgets import (
    QWidget, QApplication, QPushButton, QLabel, QHBoxLayout, 
    QMenu, QGraphicsDropShadowEffect
)
from src.utils.translator import get_translator

logger = logging.getLogger(__name__)


def to_cv2_bgr(image_input):
    """将 QPixmap / QImage / 文件路径转为 OpenCV BGR 格式"""
    if isinstance(image_input, np.ndarray):
        return image_input

    if isinstance(image_input, str):
        img = cv2.imread(image_input)
        if img is None:
            raise ValueError(f"Failed to read image from path: {image_input}")
        return img

    if isinstance(image_input, QPixmap):
        image_input = image_input.toImage()

    if isinstance(image_input, QImage):
        qimg = image_input.convertToFormat(QImage.Format.Format_BGR888)
        w, h = qimg.width(), qimg.height()
        ptr = qimg.bits()
        ptr.setsize(h * w * 3)
        return np.frombuffer(ptr, np.uint8).reshape((h, w, 3)).copy()

    raise TypeError(f"Unsupported image type: {type(image_input)}")


def to_qpixmap(image_input):
    """将各类图像格式统一转为 QPixmap"""
    if isinstance(image_input, QPixmap):
        return image_input

    if isinstance(image_input, QImage):
        return QPixmap.fromImage(image_input)

    if isinstance(image_input, str):
        return QPixmap(image_input)

    if isinstance(image_input, np.ndarray):
        img_rgb = cv2.cvtColor(image_input, cv2.COLOR_BGR2RGB)
        h, w, ch = img_rgb.shape
        bytes_per_line = ch * w
        qimg = QImage(img_rgb.data, w, h, bytes_per_line, QImage.Format.Format_RGB888)
        return QPixmap.fromImage(qimg.copy())

    raise TypeError(f"Unsupported image type for QPixmap: {type(image_input)}")


class OCRAndTranslateWorker(QThread):
    ocr_done = pyqtSignal(object)              # OCR 完成信号 (result)
    translate_done = pyqtSignal(object)        # 翻译完成信号 (dict payload)
    status_signal = pyqtSignal(str)           # 状态更新信号
    error_signal = pyqtSignal(str)

    def __init__(self, ocr_processor, translator, image_bgr, source_lang='auto', target_lang='zh', ocr_result=None):
        super().__init__()
        self.ocr_processor = ocr_processor
        self.translator = translator
        self.image_bgr = image_bgr
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.ocr_result = ocr_result
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        try:
            if self._is_cancelled:
                return

            # 1. 如果尚未识别，则执行 OCR
            if self.ocr_result is None:
                if self.ocr_processor is None:
                    self.status_signal.emit("首次加载AI模型中...")
                    from src.core.ocr_processor import OCRProcessor
                    model_path = os.path.abspath('models')
                    self.ocr_processor = OCRProcessor(model_path)
                
                if self._is_cancelled:
                    return

                self.status_signal.emit("正在识别文字...")
                self.ocr_result, _ = self.ocr_processor.process_cv2_image(
                    self.image_bgr, copy_to_clipboard=True
                )
                if self._is_cancelled:
                    return
                self.ocr_done.emit(self.ocr_result)

            from src.utils.text_merger import cluster_and_merge_lines
            raw_boxes = list(getattr(self.ocr_result, 'boxes', []))
            raw_texts = list(getattr(self.ocr_result, 'text', []))

            if not raw_texts or self._is_cancelled:
                if not raw_texts and not self._is_cancelled:
                    self.status_signal.emit("未检测到文字")
                return

            # 智能重组跨行段落语句
            merged_boxes, merged_texts, clusters = cluster_and_merge_lines(raw_boxes, raw_texts)

            # 2. 执行自动翻译（优先翻译语境连贯完整的段落）
            self.status_signal.emit("正在翻译...")
            translate_targets = merged_texts if merged_texts else raw_texts
            if hasattr(self.translator, 'translate_list'):
                trans_merged = self.translator.translate_list(
                    translate_targets, source_lang=self.source_lang, target_lang=self.target_lang
                )
            else:
                trans_merged = [
                    self.translator.translate(t, source_lang=self.source_lang, target_lang=self.target_lang)
                    for t in translate_targets
                ]

            if self._is_cancelled:
                return

            payload = {
                'trans_merged': trans_merged,
                'merged_boxes': merged_boxes,
                'merged_texts': merged_texts,
                'raw_boxes': raw_boxes,
                'raw_texts': raw_texts
            }
            self.translate_done.emit(payload)
            self.status_signal.emit("")
        except Exception as e:
            if not self._is_cancelled:
                logger.error(f"OCR and Translate worker error: {e}", exc_info=True)
                self.error_signal.emit(str(e))


class PreviewWindow(QWidget):
    """置顶屏幕贴图与翻译窗口（支持全功能 Snipaste 贴图操作及手动/自动翻译）"""

    def __init__(self, parent, image_data, ocr_result=None, screen_rect=None, ocr_processor=None, auto_translate=True):
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.parent = parent
        self.ocr_processor = ocr_processor or getattr(parent, 'get_ocr_processor', lambda: None)()
        self.ocr_result = ocr_result
        self.auto_translate = auto_translate
        self.translated_text = []
        self.is_translated = False
        self.worker_thread = None
        self.screen_rect = screen_rect
        self.opacity = 1.0

        # 智能段落与断句合并相关
        self.merge_paragraphs = True
        self.raw_boxes = []
        self.raw_texts = []
        self.merged_boxes = []
        self.merged_texts = []
        self.translated_merged_texts = []

        # 拖拽相关
        self._is_dragging = False
        self._drag_pos = QPoint()

        # 图像处理
        self.pixmap = to_qpixmap(image_data)
        self.image_bgr = to_cv2_bgr(image_data)

        self.initUI()
        if self.auto_translate:
            self.start_pipeline()
        else:
            self.toggle_btn.setText("🌐 翻译")

    def initUI(self):
        dpr = self.pixmap.devicePixelRatio() if hasattr(self.pixmap, 'devicePixelRatio') else 1.0
        if dpr <= 0:
            dpr = 1.0
        width = int(round(self.pixmap.width() / dpr))
        height = int(round(self.pixmap.height() / dpr))

        if self.screen_rect and isinstance(self.screen_rect, QRect):
            self.setGeometry(self.screen_rect)
        else:
            self.resize(width, height)
            cursor_pos = QCursor.pos()
            screen = QApplication.screenAt(cursor_pos) or QApplication.primaryScreen()
            s_geo = screen.geometry()
            x = max(s_geo.x(), min(cursor_pos.x() - width // 2, s_geo.x() + s_geo.width() - width))
            y = max(s_geo.y(), min(cursor_pos.y() - height // 2, s_geo.y() + s_geo.height() - height))
            self.move(x, y)

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)

        # 顶部微型浮动控制条
        self.control_bar = QWidget(self)
        self.control_bar.setStyleSheet("""
            QWidget {
                background-color: rgba(25, 25, 25, 200);
                border-radius: 4px;
            }
            QPushButton {
                background-color: transparent;
                color: #ffffff;
                border: none;
                border-radius: 3px;
                padding: 3px 8px;
                font-size: 11px;
                font-family: "Microsoft YaHei";
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 50);
            }
            QPushButton#closeBtn:hover {
                background-color: #e74c3c;
            }
        """)

        bar_layout = QHBoxLayout(self.control_bar)
        bar_layout.setContentsMargins(4, 2, 4, 2)
        bar_layout.setSpacing(4)

        # 切换原文/译文按钮
        self.toggle_btn = QPushButton("译文", self.control_bar)
        self.toggle_btn.clicked.connect(self.toggle_translation)
        bar_layout.addWidget(self.toggle_btn)

        # 切换智能段落合并 / 原始单行模式
        self.layout_mode_btn = QPushButton("¶ 段落", self.control_bar)
        self.layout_mode_btn.setToolTip("当前：智能段落合并模式（点击可切换为原始单行）")
        self.layout_mode_btn.clicked.connect(self.toggle_layout_mode)
        bar_layout.addWidget(self.layout_mode_btn)

        # 复制按钮
        self.copy_btn = QPushButton("复制", self.control_bar)
        self.copy_btn.clicked.connect(self.copy_current_text)
        bar_layout.addWidget(self.copy_btn)

        # 关闭按钮
        self.close_btn = QPushButton("✕", self.control_bar)
        self.close_btn.setObjectName("closeBtn")
        self.close_btn.clicked.connect(self.close)
        bar_layout.addWidget(self.close_btn)

        self.control_bar.adjustSize()
        self.control_bar.move(8, 8)
        if width < 220 or height < 80:
            self.control_bar.hide()

        # 状态指示器
        self.status_label = QLabel("", self)
        self.status_label.setStyleSheet("""
            QLabel {
                background-color: rgba(0, 0, 0, 180);
                color: #2ecc71;
                border-radius: 3px;
                padding: 3px 6px;
                font-size: 11px;
                font-family: "Microsoft YaHei";
            }
        """)
        self.status_label.hide()

    def enterEvent(self, event):
        self.control_bar.show()
        super().enterEvent(event)

    def leaveEvent(self, event):
        if self.width() < 220 or self.height() < 80:
            self.control_bar.hide()
        super().leaveEvent(event)

    def start_pipeline(self):
        """启动后台识别与翻译"""
        if self.ocr_processor is None:
            model_path = os.path.abspath('models')
            if os.path.exists(model_path):
                try:
                    from src.core.ocr_processor import OCRProcessor
                    self.ocr_processor = OCRProcessor(model_path)
                except Exception as e:
                    logger.error(f"Fallback OCRProcessor init failed: {e}")

        settings = {}
        if self.parent and hasattr(self.parent, 'get_translation_settings'):
            settings = self.parent.get_translation_settings()

        translator = get_translator(settings)
        source_lang = settings.get('from_lang', 'auto')
        target_lang = settings.get('to_lang', 'zh')

        self.worker_thread = OCRAndTranslateWorker(
            ocr_processor=self.ocr_processor,
            translator=translator,
            image_bgr=self.image_bgr,
            source_lang=source_lang,
            target_lang=target_lang,
            ocr_result=self.ocr_result
        )

        self.worker_thread.ocr_done.connect(self.on_ocr_done)
        self.worker_thread.translate_done.connect(self.on_translate_done)
        self.worker_thread.status_signal.connect(self.on_status_update)
        self.worker_thread.error_signal.connect(self.on_error)
        
        # 挂载到父对象活跃工作线程集合中，防止窗口销毁时 QThread 被 Python 提前 GC 导致 Qt6Core 崩溃 (0xc0000409)
        if self.parent and hasattr(self.parent, 'active_workers'):
            self.parent.active_workers.add(self.worker_thread)
            self.worker_thread.finished.connect(
                lambda w=self.worker_thread: self.parent.active_workers.discard(w) if hasattr(self.parent, 'active_workers') else None
            )

        self.worker_thread.start()

    def on_status_update(self, msg):
        if msg:
            self.status_label.setText(msg)
            self.status_label.adjustSize()
            offset_x = (self.control_bar.x() + self.control_bar.width() + 8) if self.control_bar.isVisible() else 8
            target_x = min(max(4, self.width() - self.status_label.width() - 4), offset_x)
            self.status_label.move(target_x, 8)
            self.status_label.show()
        else:
            self.status_label.hide()

    def on_ocr_done(self, result):
        self.ocr_result = result
        try:
            from src.utils.text_merger import cluster_and_merge_lines
            self.raw_boxes = list(getattr(result, 'boxes', []))
            self.raw_texts = list(getattr(result, 'text', []))
            self.merged_boxes, self.merged_texts, _ = cluster_and_merge_lines(self.raw_boxes, self.raw_texts)
        except Exception as e:
            logger.warning(f"Error merging OCR lines: {e}")
        self.update()

    def on_translate_done(self, data):
        try:
            if isinstance(data, dict):
                self.translated_merged_texts = [str(t) for t in data.get('trans_merged', [])]
                self.merged_boxes = data.get('merged_boxes', [])
                self.merged_texts = [str(t) for t in data.get('merged_texts', [])]
                self.raw_boxes = data.get('raw_boxes', [])
                self.raw_texts = [str(t) for t in data.get('raw_texts', [])]
                self.translated_text = self.translated_merged_texts
            elif isinstance(data, (list, tuple)):
                if data and isinstance(data[0], list):
                    self.translated_merged_texts = [str(t) for t in data[0]]
                    if len(data) >= 2: self.merged_boxes = data[1]
                    if len(data) >= 3: self.merged_texts = [str(t) for t in data[2]]
                    if len(data) >= 4: self.raw_boxes = data[3]
                    if len(data) >= 5: self.raw_texts = [str(t) for t in data[4]]
                    self.translated_text = self.translated_merged_texts
                else:
                    self.translated_text = [str(t) for t in data]
                    self.translated_merged_texts = self.translated_text
            self.is_translated = True
            self.toggle_btn.setText("原文")
        except Exception as e:
            logger.error(f"Error handling translation callback: {e}", exc_info=True)
        finally:
            self.status_label.hide()
            self.update()

    def toggle_layout_mode(self):
        """切换段落智能合并模式与原始单行模式"""
        self.merge_paragraphs = not self.merge_paragraphs
        if self.merge_paragraphs:
            self.layout_mode_btn.setText("¶ 段落")
            self.layout_mode_btn.setToolTip("当前：智能段落合并模式（点击可切换为原始单行）")
        else:
            self.layout_mode_btn.setText("☰ 单行")
            self.layout_mode_btn.setToolTip("当前：原始单行分框模式（点击可切换为段落合并）")
        self.update()

    def on_error(self, err_msg):
        self.status_label.setText(f"错误: {err_msg[:20]}")
        self.status_label.setStyleSheet("color: #e74c3c; background-color: rgba(0,0,0,180); border-radius:3px; padding:3px;")
        self.status_label.adjustSize()
        self.status_label.show()

    def toggle_translation(self):
        if not self.translated_text and not self.is_translated:
            # 重新触发翻译
            self.start_pipeline()
            return

        self.is_translated = not self.is_translated
        self.toggle_btn.setText("原文" if self.is_translated else "译文")
        self.update()

    def copy_current_text(self):
        """复制当前显示的文字到剪贴板（段落模式下保留连贯自然语句，去除多余断行）"""
        if self.is_translated:
            if self.merge_paragraphs and self.translated_merged_texts:
                text = "\n\n".join(self.translated_merged_texts)
            elif self.translated_text:
                text = "\n".join(self.translated_text)
            else:
                text = ""
        else:
            if self.merge_paragraphs and self.merged_texts:
                text = "\n\n".join(self.merged_texts)
            elif self.ocr_result and hasattr(self.ocr_result, 'text'):
                text = "\n".join(self.ocr_result.text)
            else:
                text = ""

        if text:
            try:
                pyperclip.copy(text)
                self.on_status_update("已复制！")
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(1000, lambda: self.status_label.hide())
            except Exception as e:
                logger.error(f"Copy error: {e}")
                self.status_label.hide()
            except Exception as e:
                logger.error(f"Copy error: {e}")

    # ===== 贴图窗口交互 (拖动、滚轮调节透明度、双击关闭) =====

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._is_dragging = True
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
        elif event.button() == Qt.MouseButton.RightButton:
            self.show_context_menu(event.globalPosition().toPoint())

    def mouseMoveEvent(self, event):
        if self._is_dragging and event.buttons() == Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._is_dragging = False

    def mouseDoubleClickEvent(self, event):
        """双击关闭贴图（符合 Snipaste 贴图操作习惯）"""
        if event.button() == Qt.MouseButton.LeftButton:
            self.close()

    def wheelEvent(self, event):
        """滚轮调整贴图透明度 (20% ~ 100%)"""
        delta = event.angleDelta().y()
        step = 0.05 if delta > 0 else -0.05
        self.opacity = max(0.2, min(1.0, self.opacity + step))
        self.setWindowOpacity(self.opacity)
        event.accept()

    def keyPressEvent(self, event):
        key = event.key()
        modifiers = event.modifiers()

        if key == Qt.Key.Key_Escape:
            self.close()
        elif key in (Qt.Key.Key_Space, Qt.Key.Key_T):
            self.toggle_translation()
        elif modifiers == Qt.KeyboardModifier.ControlModifier and key == Qt.Key.Key_C:
            # 复制原图到剪贴板
            QApplication.clipboard().setPixmap(self.pixmap)
            self.on_status_update("已复制图片！")
            QTimer.singleShot(1000, lambda: self.status_label.hide())
        elif key == Qt.Key.Key_C:
            self.copy_current_text()
        elif modifiers == Qt.KeyboardModifier.ControlModifier and key == Qt.Key.Key_S:
            self.save_image_to_file()

    def save_image_to_file(self):
        """保存贴图为本地图像文件"""
        from PyQt6.QtWidgets import QFileDialog
        file_path, _ = QFileDialog.getSaveFileName(
            self, "保存贴图", "贴图.png", "PNG 图像 (*.png);;JPG 图像 (*.jpg);;所有文件 (*.*)"
        )
        if file_path:
            self.pixmap.save(file_path)
            self.on_status_update("已保存！")
            QTimer.singleShot(1000, lambda: self.status_label.hide())

    def show_context_menu(self, global_pos):
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #2b2b2b;
                color: #ffffff;
                border: 1px solid #3c3c3c;
                padding: 4px;
            }
            QMenu::item {
                padding: 5px 20px;
            }
            QMenu::item:selected {
                background-color: #00a67d;
            }
        """)

        trans_label = "切换为原文 (Space/T)" if self.is_translated else "🌐 执行翻译 (Space/T)"
        trans_action = QAction(trans_label, self)
        trans_action.triggered.connect(self.toggle_translation)
        menu.addAction(trans_action)

        copy_text_action = QAction("📋 复制文字内容 (C)", self)
        copy_text_action.triggered.connect(self.copy_current_text)
        menu.addAction(copy_text_action)

        copy_img_action = QAction("🖼️ 复制图片到剪贴板 (Ctrl+C)", self)
        copy_img_action.triggered.connect(lambda: (
            QApplication.clipboard().setPixmap(self.pixmap),
            self.on_status_update("已复制图片！"),
            QTimer.singleShot(1000, lambda: self.status_label.hide())
        ))
        menu.addAction(copy_img_action)

        save_img_action = QAction("💾 保存贴图为图片... (Ctrl+S)", self)
        save_img_action.triggered.connect(self.save_image_to_file)
        menu.addAction(save_img_action)

        menu.addSeparator()

        reset_opacity_action = QAction("🔄 重置透明度为 100%", self)
        reset_opacity_action.triggered.connect(lambda: (setattr(self, 'opacity', 1.0), self.setWindowOpacity(1.0)))
        menu.addAction(reset_opacity_action)

        close_action = QAction("✕ 关闭贴图 (双击/Esc)", self)
        close_action.triggered.connect(self.close)
        menu.addAction(close_action)

        menu.exec(global_pos)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 绘制贴图背景
        painter.drawPixmap(0, 0, self.pixmap)

        # 绘制细微绿色置顶边框
        painter.setPen(QPen(QColor(0, 166, 125, 180), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(0, 0, self.width() - 1, self.height() - 1)

        if not self.ocr_result:
            return

        dpr = self.pixmap.devicePixelRatio() if hasattr(self.pixmap, 'devicePixelRatio') else 1.0
        if dpr <= 0:
            dpr = 1.0

        # 根据当前模式选取对应的坐标框与文本（智能段落合并 vs 原始单行分框）
        if self.merge_paragraphs and self.merged_boxes:
            target_boxes = self.merged_boxes
            if self.is_translated:
                texts = self.translated_merged_texts if self.translated_merged_texts else self.translated_text
            else:
                texts = self.merged_texts
        else:
            target_boxes = self.raw_boxes if self.raw_boxes else getattr(self.ocr_result, 'boxes', [])
            if self.is_translated:
                texts = self.translated_text
            else:
                texts = self.raw_texts if self.raw_texts else getattr(self.ocr_result, 'text', [])

        if not texts:
            return

        # OCR 运行在物理像素上，此处将坐标统一缩放到当前窗口逻辑像素
        boxes = []
        if target_boxes:
            for b in target_boxes:
                boxes.append([coord / dpr for coord in b])

        # 如果检测到文字但没有具体分框，整图按行分割
        if not boxes:
            row_h = max(20, self.height() // max(1, len(texts)))
            boxes = [
                [0, i * row_h, self.width(), i * row_h, self.width(), (i + 1) * row_h, 0, (i + 1) * row_h]
                for i in range(len(texts))
            ]
        elif len(boxes) < len(texts):
            # boxes 数量少于 texts 时，自动按行补足后续行
            last_box = boxes[-1]
            y_base = int(max(last_box[5], last_box[7]))
            rem_h = max(20, (self.height() - y_base) // max(1, len(texts) - len(boxes)))
            for i in range(len(boxes), len(texts)):
                idx = i - len(boxes)
                boxes.append([0, y_base + idx * rem_h, self.width(), y_base + idx * rem_h,
                              self.width(), y_base + (idx + 1) * rem_h, 0, y_base + (idx + 1) * rem_h])

        font = QFont('Microsoft YaHei', 10)
        font.setWeight(QFont.Weight.Medium)
        painter.setFont(font)
        fm = painter.fontMetrics()

        try:
            for box, text in zip(boxes, texts):
                if not text:
                    continue

                if not isinstance(text, str):
                    text = str(text)

                x_min = int(round(min(box[0], box[6])))
                y_min = int(round(min(box[1], box[3])))
                x_max = int(round(max(box[2], box[4])))
                y_max = int(round(max(box[5], box[7])))
                box_w = max(10, x_max - x_min)
                box_h = max(10, y_max - y_min)

                # 自适应换行排版宽度与高度计算
                pad_x = 7
                pad_y = 5
                wrap_w = max(box_w, min(self.width() - 16, 260))
                wrapped_rect = fm.boundingRect(
                    QRect(0, 0, wrap_w - pad_x * 2, 9999),
                    int(Qt.TextFlag.TextWordWrap),
                    text
                )

                target_w = min(self.width() - 4, max(box_w, wrapped_rect.width() + pad_x * 2))
                target_h = min(self.height() - 4, max(box_h, wrapped_rect.height() + pad_y * 2))

                # 确保标签在贴图窗口内部可见
                x = max(2, min(x_min, self.width() - target_w - 2))
                y = max(2, min(y_min, self.height() - target_h - 2))
                bg_rect = QRect(x, y, target_w, target_h)
                text_rect = QRect(x + pad_x, y + pad_y, target_w - pad_x * 2, target_h - pad_y * 2)

                # 绘制半透明圆角背景
                painter.setBrush(QColor(0, 0, 0, 195))
                border_col = QColor(46, 204, 113, 230) if self.is_translated else QColor(52, 152, 219, 210)
                painter.setPen(QPen(border_col, 1.2))
                painter.drawRoundedRect(bg_rect, 4, 4)

                # 绘制多行自适应文本（多行时靠左对齐，单行短文本居中）
                painter.setPen(QColor(255, 255, 255))
                is_multi = wrapped_rect.height() > fm.height() * 1.3 or '\n' in text
                align = (Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter) if is_multi else Qt.AlignmentFlag.AlignCenter
                painter.drawText(text_rect, int(align | Qt.TextFlag.TextWordWrap), text)
        except Exception as e:
            logger.error(f"Error in paintEvent drawing text: {e}", exc_info=True)

    def closeEvent(self, event):
        if self.worker_thread:
            try:
                # 标记取消并断开所有信号，防止向已销毁的窗口投递 UI 信号导致崩溃
                self.worker_thread.cancel()
                self.worker_thread.ocr_done.disconnect()
                self.worker_thread.translate_done.disconnect()
                self.worker_thread.status_signal.disconnect()
                self.worker_thread.error_signal.disconnect()
            except Exception:
                pass
        event.accept()