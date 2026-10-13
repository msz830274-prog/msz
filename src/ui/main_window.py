import os
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QIcon, QCursor, QAction, QPalette
from PyQt6.QtWidgets import (
    QMainWindow, QVBoxLayout, QLabel, QWidget, QCheckBox,
    QMenu, QSystemTrayIcon, QFrame, QMessageBox, QHBoxLayout, 
    QLineEdit, QPushButton, QFileDialog, QDialog, QFormLayout, 
    QDialogButtonBox, QComboBox, QApplication
)
import logging
import yaml
import sys
import platform
import pathlib

from src.core.ocr_thread import OCRThread
from src.core.hotkey_manager import GlobalHotkeyThread
from src.ui.snipper_widget import SnipperWidget
from src.ui.preview_window import PreviewWindow
from src.utils.logging_config import setup_logging
from ..core.resource_path import get_resource_path

# Initialize logger for this module
logger = logging.getLogger(__name__)

APP_NAME = "SnipasteOCR"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        setup_logging()
        
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        
        self.ocrThread = None
        self.preview_window = None
        self.preview_enabled = True
        self.pinned_windows = []
        
        # 快捷键配置 (全面对接 Snipaste 使用习惯)
        self.hotkeys = {
            'snip': 'F1',
            'snip_and_pin': 'F4',
            'clipboard_pin': 'F3'
        }
        self.auto_translate = False
        
        # 翻译设置（默认免配置免费翻译）
        self.translation_settings = {
            'provider': 'free',
            'secret_id': '',
            'secret_key': '',
            'from_lang': 'auto',
            'to_lang': 'zh'
        }
        # 默认关闭所有后台文件监听与剪贴板监听，实现零内存/零CPU空闲开销
        self.monitor_folder_enabled = False
        self.clipboard_monitor_enabled = False
        
        # 屏幕框选截图器 (内置 Snipaste 工具栏与 8 点调整)
        self.snipper_widget = SnipperWidget()
        self.snipper_widget.action_triggered.connect(self.on_snipper_action)
        
        # 全局快捷键线程与活跃工作线程管理
        self.hotkey_thread = None
        self.active_workers = set()
        
        # 剪贴板监听状态变量
        self._last_clipboard_hash = None
        self._is_internal_copy = False
        
        # 检测系统是否为暗色模式
        self.is_dark_mode = self.check_dark_mode()
        
        self.initUI()
        self.initLayout()
        self.loadConfig()
        self.initData()
        self.initHotkeys()
        self.initStyle()
        
        self.create_translation_settings_menu()
        
    def initUI(self):
        self.setObjectName("MainWindow")
        self.setGeometry(0, 0, 520, 420)
        self.setFixedSize(self.size())
        self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        
        icon_path = get_resource_path('assets/icon.png')
        self.setWindowIcon(QIcon(icon_path))
        self.setWindowTitle('SnipasteOCR - 智能截屏贴图翻译器')
        
        screen = QApplication.primaryScreen().geometry()
        size = self.geometry()
        self.move(int((screen.width() - size.width()) / 2), int((screen.height() - size.height()) / 2))
        
        self.setupMainWindow()
        self.setupTrayIcon()
        
    def create_translation_settings_menu(self):
        menubar = self.menuBar()
        settings_menu = menubar.addMenu('设置')
        
        settings_action = QAction('首选项与翻译设置', self)
        settings_action.triggered.connect(self.show_translation_settings)
        settings_menu.addAction(settings_action)

    def setupMainWindow(self):
        pass

    def setupTrayIcon(self):
        self.trayIconMenu = QMenu(self)
        self.trayIconMenu.setObjectName("trayMenu")
        
        titleAction = QAction(APP_NAME, self)
        titleAction.setIcon(QIcon(get_resource_path('assets/icon.png')))
        titleAction.setEnabled(False)
        self.trayIconMenu.addAction(titleAction)
        
        self.trayIconMenu.addSeparator()

        # 快捷操作菜单
        snip_label = f"✂️ 截屏 ({self.hotkeys.get('snip', 'F1')})"
        self.traySnipAction = QAction(snip_label, self)
        self.traySnipAction.triggered.connect(self.start_standard_snip)
        self.trayIconMenu.addAction(self.traySnipAction)

        fast_label = f"⚡ 极速截屏翻译 ({self.hotkeys.get('snip_and_pin', 'F4')})"
        self.trayFastAction = QAction(fast_label, self)
        self.trayFastAction.triggered.connect(self.start_fast_snip_and_pin)
        self.trayIconMenu.addAction(self.trayFastAction)

        clip_label = f"📌 贴图 ({self.hotkeys.get('clipboard_pin', 'F3')})"
        self.trayClipAction = QAction(clip_label, self)
        self.trayClipAction.triggered.connect(self.start_clipboard_pin)
        self.trayIconMenu.addAction(self.trayClipAction)

        self.trayIconMenu.addSeparator()
        
        self.toggleWindowAction = QAction("显示主窗口", self)
        self.toggleWindowAction.setIcon(QIcon(get_resource_path('assets/icon.png')))
        self.toggleWindowAction.triggered.connect(self.toggleWindow)
        self.trayIconMenu.addAction(self.toggleWindowAction)
        
        restartAction = QAction("重启程序", self)
        restartAction.setIcon(QIcon(get_resource_path('assets/icon.png')))
        restartAction.triggered.connect(self.restart)
        self.trayIconMenu.addAction(restartAction)
        
        self.trayIconMenu.addSeparator()
        
        helpMenu = QMenu("帮助", self)
        helpMenu.setIcon(QIcon(get_resource_path('assets/icon.png')))
        
        aboutAction = QAction("关于", self)
        aboutAction.triggered.connect(self.showAbout)
        helpMenu.addAction(aboutAction)
        
        usageAction = QAction("使用说明", self)
        usageAction.triggered.connect(self.showUsage)
        helpMenu.addAction(usageAction)
        
        self.trayIconMenu.addMenu(helpMenu)
        
        self.trayIconMenu.addSeparator()
        
        self.quitAction = QAction("退出", self)
        self.quitAction.setIcon(QIcon(get_resource_path('assets/icon.png')))
        self.quitAction.triggered.connect(self.quit)
        self.trayIconMenu.addAction(self.quitAction)

        self.trayIcon = QSystemTrayIcon(self)
        self.trayIcon.setContextMenu(self.trayIconMenu)
        self.trayIcon.setIcon(QIcon(get_resource_path('assets/icon.png')))
        self.trayIcon.activated.connect(self.iconActivated)
        self.trayIcon.setToolTip(f"{APP_NAME} - 截图定屏翻译")
        self.trayIcon.show()

    def initData(self):
        """初始化 OCR 引擎线程"""
        if self.ocrThread is None or not self.ocrThread.isRunning():
            try:
                self.ocrThread = OCRThread()
                self.ocrThread.preview_signal.connect(self.show_preview)
                self.ocrThread.error_signal.connect(self.show_ocr_error)
                self.ocrThread.start()
                if hasattr(self, 'preview_button'):
                    self.preview_button.setEnabled(True)
                    self.preview_button.setToolTip('')
            except Exception as e:
                logger.error(f"Failed to initialize OCR thread: {e}")
                self.show_ocr_error(f"启动OCR服务出错: {e}\n请检查模型路径。")
                if hasattr(self, 'preview_button'):
                    self.preview_button.setEnabled(False)
                    self.preview_button.setToolTip('OCR服务启动失败')

    def initHotkeys(self):
        """初始化全局快捷键监听"""
        try:
            if self.hotkey_thread and self.hotkey_thread.isRunning():
                self.hotkey_thread.stop()
            self.hotkey_thread = GlobalHotkeyThread(
                snip_hotkey=self.hotkeys.get('snip', 'F1'),
                snip_fast_hotkey=self.hotkeys.get('snip_and_pin', 'F4'),
                clipboard_hotkey=self.hotkeys.get('clipboard_pin', 'F3')
            )
            self.hotkey_thread.snip_triggered.connect(self.start_standard_snip)
            self.hotkey_thread.snip_fast_triggered.connect(self.start_fast_snip_and_pin)
            self.hotkey_thread.clipboard_triggered.connect(self.start_clipboard_pin)
            self.hotkey_thread.error_signal.connect(self.show_ocr_error)
            self.hotkey_thread.start()
            logger.info("Global hotkeys initialized successfully")
        except Exception as e:
            logger.error(f"Failed to init hotkeys: {e}")

    def get_ocr_processor(self):
        """获取共享的 OCR 处理器实例（按需延迟加载）"""
        if self.ocrThread and hasattr(self.ocrThread, 'get_ocr_processor'):
            return self.ocrThread.get_ocr_processor()
        return None

    def start_standard_snip(self):
        """触发 Snipaste 交互截图 (F1)"""
        logger.info("Triggered standard snip")
        self.snipper_widget.start_snip(fast_mode=False, auto_translate=self.auto_translate)

    def start_fast_snip_and_pin(self):
        """触发极速截屏直接翻译贴图 (F4)"""
        logger.info("Triggered fast snip and pin")
        self.snipper_widget.start_snip(fast_mode=True, auto_translate=True)

    def on_snipper_action(self, pixmap, rect, action_type):
        """处理截屏工具栏发出的动作"""
        logger.info(f"Snipper action: {action_type}")
        if action_type == 'translate':
            self.show_preview_image(pixmap, screen_rect=rect, auto_translate=True)
        elif action_type == 'pin':
            self.show_preview_image(pixmap, screen_rect=rect, auto_translate=False)
        elif action_type == 'copy':
            QApplication.clipboard().setPixmap(pixmap)

    def start_clipboard_pin(self):
        """触发贴图 (F3)"""
        logger.info("Triggered clipboard pin")
        clipboard = QApplication.clipboard()
        qimg = clipboard.image()
        if not qimg.isNull():
            self.show_preview_image(qimg, screen_rect=None, auto_translate=self.auto_translate)
        else:
            QMessageBox.information(
                self, '提示', 
                '剪贴板中当前没有图片！\n请先使用截屏 (F1) 或复制图片。'
            )

    def on_clipboard_changed(self):
        """剪贴板内容变化时自动检测图像并贴图翻译 (仅在设置中明确开启时生效)"""
        if not getattr(self, 'clipboard_monitor_enabled', False):
            return
        if getattr(self, '_is_internal_copy', False):
            return

        clipboard = QApplication.clipboard()
        mime = clipboard.mimeData()
        if mime and mime.hasImage():
            qimg = clipboard.image()
            if not qimg.isNull() and qimg.width() > 10 and qimg.height() > 10:
                curr_hash = (
                    qimg.width(), qimg.height(), 
                    qimg.pixelColor(0, 0).rgba(), 
                    qimg.pixelColor(qimg.width()//2, qimg.height()//2).rgba()
                )
                if curr_hash == getattr(self, '_last_clipboard_hash', None):
                    return
                self._last_clipboard_hash = curr_hash
                logger.info(f"Clipboard image auto-detected: {qimg.width()}x{qimg.height()}")
                self.show_preview_image(qimg, screen_rect=None, auto_translate=self.auto_translate)

    def show_preview_image(self, image_data, screen_rect=None, ocr_result=None, auto_translate=None):
        """显示置顶贴图并执行自动/手动翻译"""
        if not self.preview_enabled:
            return

        should_translate = self.auto_translate if auto_translate is None else auto_translate

        win = PreviewWindow(
            parent=self,
            image_data=image_data,
            ocr_result=ocr_result,
            screen_rect=screen_rect,
            ocr_processor=self.get_ocr_processor(),
            auto_translate=should_translate
        )
        self.pinned_windows.append(win)
        win.destroyed.connect(lambda: self.pinned_windows.remove(win) if win in self.pinned_windows else None)
        win.show()
        self.preview_window = win

    def show_preview(self, image_path, result):
        """文件夹监控新增截图触发的回调"""
        self.show_preview_image(image_path, screen_rect=None, ocr_result=result)

    def show_ocr_error(self, error_msg):
        logger.warning(f"OCR Error notice: {error_msg}")
        if hasattr(self, 'trayIcon') and self.trayIcon and self.isHidden():
            self.trayIcon.showMessage(
                "提示", str(error_msg),
                QSystemTrayIcon.MessageIcon.Warning, 3000
            )
        else:
            QMessageBox.warning(self, "OCR 提示", str(error_msg))

    def initStyle(self):
        if self.is_dark_mode:
            dark_stylesheet = """
                QWidget#MainWindow {
                    background-color: #1e1e1e;
                }
                QFrame {
                    background-color: #2d2d2d;
                    border: 1px solid #3a3a3a;
                    border-radius: 4px;
                }
                QFrame#actionFrame {
                    background-color: #252526;
                    border: 1px solid #007d5e;
                    border-radius: 6px;
                }
                QLabel {
                    background: transparent;
                    border: none;
                    color: #e0e0e0;
                    font-size: 13px;
                }
                #titleLabel {
                    color: #ffffff;
                    font-size: 18px;
                    font-weight: bold;
                    padding: 2px;
                }
                #authorLabel, #descLabel {
                    color: #b0b0b0;
                    font-size: 11px;
                }
                QPushButton {
                    background-color: #2d5af7;
                    color: white;
                    border: none;
                    padding: 6px 12px;
                    border-radius: 4px;
                    min-width: 60px;
                }
                QPushButton:hover {
                    background-color: #1e4aef;
                }
                QPushButton#snipActionButton {
                    background-color: #00a67d;
                    font-weight: bold;
                    font-size: 13px;
                    padding: 8px 12px;
                }
                QPushButton#snipActionButton:hover {
                    background-color: #008f6c;
                }
                QPushButton#clipActionButton {
                    background-color: #34495e;
                    font-weight: bold;
                    font-size: 13px;
                    padding: 8px 12px;
                }
                QPushButton#clipActionButton:hover {
                    background-color: #2c3e50;
                }
                QPushButton#previewButton {
                    background-color: #00a67d;
                }
                QPushButton#previewButton[preview_off="true"] {
                    background-color: #666666;
                }
                QPushButton#autostartButton {
                    background-color: #00a67d;
                }
                QPushButton#autostartButton[autostart_off="true"] {
                    background-color: #666666;
                }
                QLineEdit {
                    padding: 5px;
                    border: 1px solid #3a3a3a;
                    border-radius: 4px;
                    background-color: #3a3a3a;
                    color: #e0e0e0;
                }
                QCheckBox {
                    color: #e0e0e0;
                }
            """
            self.setStyleSheet(dark_stylesheet)
        else:
            light_stylesheet = """
                QWidget#MainWindow {
                    background-color: #f5f5f5;
                }
                QFrame {
                    background-color: #ffffff;
                    border: 1px solid #e0e0e0;
                    border-radius: 4px;
                }
                QFrame#actionFrame {
                    background-color: #f0fdf4;
                    border: 1px solid #00a67d;
                    border-radius: 6px;
                }
                QLabel {
                    background: transparent;
                    border: none;
                    color: #333333;
                    font-size: 13px;
                }
                #titleLabel {
                    color: #1a1a1a;
                    font-size: 18px;
                    font-weight: bold;
                    padding: 2px;
                }
                #authorLabel, #descLabel {
                    color: #666666;
                    font-size: 11px;
                }
                QPushButton {
                    background-color: #2d5af7;
                    color: white;
                    border: none;
                    padding: 6px 12px;
                    border-radius: 4px;
                    min-width: 60px;
                }
                QPushButton:hover {
                    background-color: #1e4aef;
                }
                QPushButton#snipActionButton {
                    background-color: #00a67d;
                    font-weight: bold;
                    font-size: 13px;
                    padding: 8px 12px;
                }
                QPushButton#snipActionButton:hover {
                    background-color: #008f6c;
                }
                QPushButton#clipActionButton {
                    background-color: #4a5568;
                    font-weight: bold;
                    font-size: 13px;
                    padding: 8px 12px;
                }
                QPushButton#clipActionButton:hover {
                    background-color: #2d3748;
                }
                QLineEdit {
                    padding: 5px;
                    border: 1px solid #dcdcdc;
                    border-radius: 4px;
                    background-color: #ffffff;
                    color: #333333;
                }
                QCheckBox {
                    color: #333333;
                }
            """
            self.setStyleSheet(light_stylesheet)

    def initLayout(self):
        mainLayout = QVBoxLayout(self.central_widget)
        mainLayout.setContentsMargins(10, 10, 10, 10)
        mainLayout.setSpacing(8)
        
        # 1. 顶部标题部分
        headerFrame = QFrame(self.central_widget)
        headerLayout = QVBoxLayout(headerFrame)
        headerLayout.setContentsMargins(10, 8, 10, 8)
        headerLayout.setSpacing(4)
        
        titleLayout = QHBoxLayout()
        logoLabel = QLabel()
        logoLabel.setPixmap(QIcon(get_resource_path('assets/icon.png')).pixmap(28, 28))
        titleLabel = QLabel("SnipasteOCR 贴图自动翻译")
        titleLabel.setObjectName("titleLabel")
        titleLayout.addWidget(logoLabel)
        titleLayout.addWidget(titleLabel)
        titleLayout.addStretch()
        
        infoLayout = QHBoxLayout()
        authorLabel = QLabel("作者: formero009 | 支持免存盘原地定屏翻译")
        authorLabel.setObjectName("authorLabel")
        infoLayout.addWidget(authorLabel)
        infoLayout.addStretch()
        
        headerLayout.addLayout(titleLayout)
        headerLayout.addLayout(infoLayout)
        mainLayout.addWidget(headerFrame)
        
        # 2. 核心功能快捷操作卡片
        actionFrame = QFrame(self.central_widget)
        actionFrame.setObjectName("actionFrame")
        actionLayout = QVBoxLayout(actionFrame)
        actionLayout.setContentsMargins(12, 10, 12, 10)
        actionLayout.setSpacing(6)
        
        btnLayout = QHBoxLayout()
        self.btn_snip = QPushButton(f"✂️ 截屏 ({self.hotkeys.get('snip', 'F1')})")
        self.btn_snip.setObjectName("snipActionButton")
        self.btn_snip.clicked.connect(self.start_standard_snip)
        btnLayout.addWidget(self.btn_snip)

        self.btn_fast = QPushButton(f"⚡ 一键翻译 ({self.hotkeys.get('snip_and_pin', 'F4')})")
        self.btn_fast.setObjectName("fastActionButton")
        self.btn_fast.clicked.connect(self.start_fast_snip_and_pin)
        btnLayout.addWidget(self.btn_fast)
        
        self.btn_clip = QPushButton(f"📌 贴图 ({self.hotkeys.get('clipboard_pin', 'F3')})")
        self.btn_clip.setObjectName("clipActionButton")
        self.btn_clip.clicked.connect(self.start_clipboard_pin)
        btnLayout.addWidget(self.btn_clip)

        # 模式切换行：手动翻译 vs 自动翻译
        modeLayout = QHBoxLayout()
        self.auto_trans_btn = QPushButton("截图模式：手动翻译 (贴出原图，按空格/T翻译)")
        self.auto_trans_btn.setStyleSheet("background-color: #2c3e50; color: white; border-radius: 4px; padding: 4px 8px; font-size: 12px;")
        self.auto_trans_btn.clicked.connect(self.toggle_auto_translate)
        modeLayout.addWidget(self.auto_trans_btn)
        
        tipLabel = QLabel("💡 零后台监控消耗：按 F1 调出 Snipaste 交互工具栏；按 F4 极速框选并覆盖翻译；贴图支持按【空格/T】切换原文/译文，滚轮调透明度，双击关闭。")
        tipLabel.setWordWrap(True)
        tipLabel.setStyleSheet("font-size: 11px; opacity: 0.85; padding-top: 2px;")
        
        actionLayout.addLayout(btnLayout)
        actionLayout.addLayout(modeLayout)
        actionLayout.addWidget(tipLabel)
        mainLayout.addWidget(actionFrame)
        
        # 3. 基础配置卡片（路径与选项）
        pathFrame = QFrame(self.central_widget)
        pathLayout = QVBoxLayout(pathFrame)
        pathLayout.setContentsMargins(10, 8, 10, 8)
        pathLayout.setSpacing(6)
        
        # 模型路径
        model_layout = QHBoxLayout()
        model_layout.setSpacing(5)
        model_label = QLabel('模型路径:')
        self.model_path = QLineEdit()
        self.model_path.setReadOnly(True)
        model_button = QPushButton('浏览')
        model_button.setFixedWidth(55)
        model_button.clicked.connect(lambda: self.browse_folder(self.model_path))
        model_layout.addWidget(model_label)
        model_layout.addWidget(self.model_path)
        model_layout.addWidget(model_button)
        pathLayout.addLayout(model_layout)
        
        # 按钮布局
        buttonLayout = QHBoxLayout()
        
        self.preview_button = QPushButton('贴图置顶：开启')
        self.preview_button.setObjectName("previewButton")
        self.preview_button.clicked.connect(self.toggle_preview)
        buttonLayout.addWidget(self.preview_button)
        
        self.autostart_button = QPushButton('开机自启：关闭')
        self.autostart_button.setObjectName("autostartButton")
        self.autostart_button.clicked.connect(self.toggle_autostart)
        buttonLayout.addWidget(self.autostart_button)
        
        self.settings_btn = QPushButton('首选项与热键')
        self.settings_btn.clicked.connect(self.show_translation_settings)
        buttonLayout.addWidget(self.settings_btn)
        
        buttonLayout.addStretch()
        
        save_button = QPushButton('保存设置')
        save_button.clicked.connect(self.saveConfig)
        buttonLayout.addWidget(save_button)
        
        pathLayout.addLayout(buttonLayout)
        mainLayout.addWidget(pathFrame)
        
        self.central_widget.setLayout(mainLayout)

    def closeEvent(self, event):
        event.ignore()
        self.hide()

    def quit(self):
        try:
            logger.info("Quitting application")
            for win in list(self.pinned_windows):
                try:
                    win.close()
                except Exception:
                    pass
            self.pinned_windows.clear()

            # 取消所有可能还在运行的后台工作线程
            for worker in list(getattr(self, 'active_workers', [])):
                try:
                    worker.cancel()
                except Exception:
                    pass
            self.active_workers.clear()

            if self.hotkey_thread is not None and self.hotkey_thread.isRunning():
                self.hotkey_thread.stop()
                self.hotkey_thread = None

            if self.ocrThread is not None and self.ocrThread.isRunning():
                self.ocrThread.stop()
                self.ocrThread.quit()
                if not self.ocrThread.wait(1000):
                    self.ocrThread.terminate()
                    self.ocrThread.wait()
                self.ocrThread = None
            
            QApplication.quit()
        except Exception as e:
            logger.error(f"Error during quit: {e}")
            QApplication.quit()

    def iconActivated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            if self.isHidden():
                self.showNormal()
            else:
                self.hide()

    def toggleWindow(self):
        if self.isHidden():
            self.showNormal()
            self.toggleWindowAction.setText("隐藏主窗口")
        else:
            self.hide()
            self.toggleWindowAction.setText("显示主窗口")

    def showAbout(self):
        QMessageBox.about(self, f"关于 {APP_NAME}",
                         f"""<h3>{APP_NAME}</h3>
                         <p>支持 Snipaste 贴图与屏幕原地定屏翻译的 OCR 工具</p>
                         <p>开箱即用免配置翻译 | 飞桨 FastDeploy OCR 高速模型</p>
                         <p>项目主页: <a href='https://github.com/formero009/SnipasteOCR'>GitHub</a></p>""")

    def showUsage(self):
        snip_k = self.hotkeys.get('snip_and_pin', 'F4')
        clip_k = self.hotkeys.get('clipboard_pin', 'Alt+W')
        QMessageBox.information(self, "使用说明",
                              f"""<h3>快捷使用指南：</h3>
                              <p><b>1. 原地定屏翻译（推荐）：</b><br>
                              按快捷键 <b>{snip_k}</b>，框选屏幕上想要翻译的区域（包括 Snipaste 贴图），松开鼠标即可原地生成贴图，自动覆盖翻译！</p>
                              <p><b>2. 剪贴板贴图翻译：</b><br>
                              使用 Snipaste 等截取并复制图片后，按 <b>{clip_k}</b> 即可直接贴在鼠标位置并自动翻译。</p>
                              <p><b>3. 贴图窗口操作：</b><br>
                              - <b>鼠标拖动</b>：任意拖拽贴图位置<br>
                              - <b>滚轮滑动</b>：调整贴图透明度<br>
                              - <b>空格键</b>：快速切换原文/译文<br>
                              - <b>双击或 ESC</b>：关闭贴图</p>""")

    def browse_folder(self, line_edit):
        start_dir = line_edit.text() if os.path.isdir(line_edit.text()) else str(pathlib.Path.home())
        folder = QFileDialog.getExistingDirectory(self, '选择文件夹', start_dir)
        if folder:
            line_edit.setText(folder)
            line_edit.setToolTip(folder)

    def loadConfig(self):
        try:
            current_path = os.path.abspath(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
            config_path = os.path.join(current_path, 'config.yml')
            
            if os.path.exists(config_path):
                with open(config_path, 'r', encoding='utf-8') as f:
                    config = yaml.safe_load(f) or {}
            else:
                config = {}

            snipaste_config = config.get('snipaste') or {}
            default_model_path = os.path.join(current_path, 'models')
            modelpath_text = snipaste_config.get('modelpath') or default_model_path
            self.model_path.setText(modelpath_text)
            self.model_path.setToolTip(modelpath_text)

            self.auto_translate = snipaste_config.get('auto_translate', False)
            self.preview_enabled = snipaste_config.get('preview_enabled', True)
            self.monitor_folder_enabled = snipaste_config.get('monitor_folder_enabled', False)
            self.clipboard_monitor_enabled = snipaste_config.get('clipboard_monitor_enabled', False)

            if hasattr(self, 'auto_trans_btn'):
                self.update_auto_trans_btn_text()

            if hasattr(self, 'preview_button'):
                self.preview_button.setText(f'贴图置顶：{"开启" if self.preview_enabled else "关闭"}')
                self.preview_button.setProperty("preview_off", not self.preview_enabled)
                self.preview_button.style().unpolish(self.preview_button)
                self.preview_button.style().polish(self.preview_button)
                
            if hasattr(self, 'autostart_button'):
                self.update_autostart_button()
            
            if 'hotkeys' in config:
                self.hotkeys.update({
                    'snip': config['hotkeys'].get('snip', 'F1'),
                    'snip_and_pin': config['hotkeys'].get('snip_and_pin', 'F4'),
                    'clipboard_pin': config['hotkeys'].get('clipboard_pin', 'F3')
                })

            if 'translation' in config:
                self.translation_settings.update({
                    'provider': config['translation'].get('provider', 'free'),
                    'secret_id': config['translation'].get('secret_id', ''),
                    'secret_key': config['translation'].get('secret_key', ''),
                    'from_lang': config['translation'].get('from_lang', 'auto'),
                    'to_lang': config['translation'].get('to_lang', 'zh')
                })

            self.update_hotkey_labels()
        except Exception as e:
            logger.error(f"Failed to load configuration: {e}")

    def update_auto_trans_btn_text(self):
        if not hasattr(self, 'auto_trans_btn'):
            return
        if self.auto_translate:
            self.auto_trans_btn.setText("截图模式：自动翻译 (框选后立即识别覆盖)")
            self.auto_trans_btn.setStyleSheet("background-color: #27ae60; color: white; font-weight: bold; border-radius: 4px; padding: 4px 8px; font-size: 12px;")
        else:
            self.auto_trans_btn.setText("截图模式：手动翻译 (贴出原图，按空格/T翻译)")
            self.auto_trans_btn.setStyleSheet("background-color: #2c3e50; color: white; border-radius: 4px; padding: 4px 8px; font-size: 12px;")

    def toggle_auto_translate(self):
        self.auto_translate = not self.auto_translate
        self.update_auto_trans_btn_text()
        self.saveConfig()

    def update_hotkey_labels(self):
        """更新界面上快捷键的显示文案"""
        snip_k = self.hotkeys.get('snip', 'F1')
        fast_k = self.hotkeys.get('snip_and_pin', 'F4')
        clip_k = self.hotkeys.get('clipboard_pin', 'F3')
        if hasattr(self, 'btn_snip'):
            self.btn_snip.setText(f"✂️ 截屏 ({snip_k})")
        if hasattr(self, 'btn_fast'):
            self.btn_fast.setText(f"⚡ 一键翻译 ({fast_k})")
        if hasattr(self, 'btn_clip'):
            self.btn_clip.setText(f"📌 贴图 ({clip_k})")
        if hasattr(self, 'traySnipAction'):
            self.traySnipAction.setText(f"✂️ 截屏 ({snip_k})")
        if hasattr(self, 'trayFastAction'):
            self.trayFastAction.setText(f"⚡ 极速截屏翻译 ({fast_k})")
        if hasattr(self, 'trayClipAction'):
            self.trayClipAction.setText(f"📌 贴图 ({clip_k})")

    def saveConfig(self):
        try:
            current_path = os.path.abspath(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
            config_path = os.path.join(current_path, 'config.yml')
            
            existing_config = {}
            if os.path.exists(config_path):
                with open(config_path, 'r', encoding='utf-8') as f:
                    existing_config = yaml.safe_load(f) or {}
            
            config = {
                'hotkeys': {
                    'snip': self.hotkeys.get('snip', 'F1'),
                    'snip_and_pin': self.hotkeys.get('snip_and_pin', 'F4'),
                    'clipboard_pin': self.hotkeys.get('clipboard_pin', 'F3')
                },
                'snipaste': {
                    'path': os.path.join(current_path, 'screenshots'),
                    'modelpath': self.model_path.text(),
                    'auto_translate': self.auto_translate,
                    'preview_enabled': self.preview_enabled,
                    'monitor_folder_enabled': self.monitor_folder_enabled,
                    'clipboard_monitor_enabled': getattr(self, 'clipboard_monitor_enabled', False)
                },
                'translation': {
                    'provider': self.translation_settings.get('provider', 'free'),
                    'secret_id': self.translation_settings.get('secret_id', ''),
                    'secret_key': self.translation_settings.get('secret_key', ''),
                    'from_lang': self.translation_settings.get('from_lang', 'auto'),
                    'to_lang': self.translation_settings.get('to_lang', 'zh')
                }
            }
            
            existing_config.update(config)
            
            with open(config_path, 'w', encoding='utf-8') as f:
                yaml.dump(existing_config, f, allow_unicode=True)

            self.update_hotkey_labels()
            self.initHotkeys()

            QMessageBox.information(self, '成功', '设置已保存！快捷键与翻译服务已更新生效。')
        except Exception as e:
            logger.error(f"Failed to save configuration: {e}")
            QMessageBox.warning(self, '错误', f'保存配置文件失败: {e}')

    def restart(self):
        try:
            logger.info("Restarting application")
            self.quit()
            program = sys.executable
            args = sys.argv
            import subprocess
            subprocess.Popen([program] + args)
        except Exception as e:
            logger.error(f"Failed to restart application: {e}")

    def toggle_preview(self):
        self.preview_enabled = not self.preview_enabled
        self.preview_button.setText(f'贴图置顶：{"开启" if self.preview_enabled else "关闭"}')
        self.preview_button.setProperty("preview_off", not self.preview_enabled)
        self.preview_button.style().unpolish(self.preview_button)
        self.preview_button.style().polish(self.preview_button)

    def show_translation_settings(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('首选项与快捷键设置')
        dialog.setMinimumSize(420, 320)
        layout = QFormLayout()
        layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)
        
        # 翻译服务选择
        provider_combo = QComboBox()
        provider_combo.addItem("免费翻译 (开箱即用，无需申请 API)", "free")
        provider_combo.addItem("腾讯云机器翻译 (需填密钥)", "tencent")
        curr_provider = self.translation_settings.get('provider', 'free')
        provider_combo.setCurrentIndex(1 if curr_provider == 'tencent' else 0)
        layout.addRow('翻译服务:', provider_combo)
        
        # SecretId输入
        secret_id_input = QLineEdit(self.translation_settings.get('secret_id', ''))
        layout.addRow('SecretId (腾讯云):', secret_id_input)
        
        # SecretKey输入
        secret_key_input = QLineEdit(self.translation_settings.get('secret_key', ''))
        secret_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addRow('SecretKey (腾讯云):', secret_key_input)
        
        def update_secret_fields(idx):
            is_tencent = (idx == 1)
            secret_id_input.setEnabled(is_tencent)
            secret_key_input.setEnabled(is_tencent)
        provider_combo.currentIndexChanged.connect(update_secret_fields)
        update_secret_fields(provider_combo.currentIndex())
        
        # 源语言
        from_lang_input = QComboBox()
        langs = [('auto', '自动检测'), ('zh', '中文'), ('en', '英文'), ('ja', '日文'), ('ko', '韩文')]
        for code, name in langs:
            from_lang_input.addItem(name, code)
        c_from = from_lang_input.findData(self.translation_settings.get('from_lang', 'auto'))
        from_lang_input.setCurrentIndex(max(0, c_from))
        layout.addRow('源语言:', from_lang_input)
        
        # 目标语言
        to_lang_input = QComboBox()
        for code, name in langs[1:]:
            to_lang_input.addItem(name, code)
        c_to = to_lang_input.findData(self.translation_settings.get('to_lang', 'zh'))
        to_lang_input.setCurrentIndex(max(0, c_to))
        layout.addRow('目标语言:', to_lang_input)
        
        # 快捷键设置 (Snipaste 交互式组合键录制)
        from src.ui.hotkey_edit import HotkeyEdit

        snip_layout = QHBoxLayout()
        snip_hotkey_input = HotkeyEdit(self.hotkeys.get('snip', 'F1'))
        btn_test_snip = QPushButton("▶ 试用截图")
        btn_test_snip.setToolTip("点击后立即测试截屏功能")
        btn_test_snip.clicked.connect(lambda: (dialog.hide(), QTimer.singleShot(250, self.start_standard_snip)))
        snip_layout.addWidget(snip_hotkey_input)
        snip_layout.addWidget(btn_test_snip)
        layout.addRow('截屏快捷键 (Snipaste 模式):', snip_layout)

        fast_layout = QHBoxLayout()
        fast_hotkey_input = HotkeyEdit(self.hotkeys.get('snip_and_pin', 'F4'))
        btn_test_fast = QPushButton("▶ 试用翻译")
        btn_test_fast.setToolTip("点击后立即测试极速截屏翻译")
        btn_test_fast.clicked.connect(lambda: (dialog.hide(), QTimer.singleShot(250, self.start_fast_snip_and_pin)))
        fast_layout.addWidget(fast_hotkey_input)
        fast_layout.addWidget(btn_test_fast)
        layout.addRow('一键极速翻译快捷键:', fast_layout)
        
        clip_layout = QHBoxLayout()
        clip_hotkey_input = HotkeyEdit(self.hotkeys.get('clipboard_pin', 'Alt+W'))
        btn_test_clip = QPushButton("▶ 试用贴图")
        btn_test_clip.setToolTip("点击后立即测试贴图功能")
        btn_test_clip.clicked.connect(lambda: (dialog.hide(), QTimer.singleShot(250, self.start_clipboard_pin)))
        clip_layout.addWidget(clip_hotkey_input)
        clip_layout.addWidget(btn_test_clip)
        layout.addRow('贴图快捷键 (Snipaste F3):', clip_layout)

        # 自动翻译模式开关
        auto_translate_cb = QCheckBox('截图后自动翻译 (关闭则仅贴出原图，按空格或T手动翻译)')
        auto_translate_cb.setChecked(self.auto_translate)
        layout.addRow('', auto_translate_cb)

        # 剪贴板自动贴图开关
        clip_monitor_cb = QCheckBox('后台自动监听剪贴板 (默认关闭，零内存/零CPU消耗)')
        clip_monitor_cb.setChecked(self.clipboard_monitor_enabled)
        layout.addRow('', clip_monitor_cb)

        # 文件夹监控开关
        folder_monitor_cb = QCheckBox('后台自动监听截图文件夹 (默认关闭，零内存/零CPU消耗)')
        folder_monitor_cb.setChecked(self.monitor_folder_enabled)
        layout.addRow('', folder_monitor_cb)
        
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)
        
        dialog.setLayout(layout)
        dialog.setModal(True)
        
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.translation_settings.update({
                'provider': provider_combo.currentData(),
                'secret_id': secret_id_input.text().strip(),
                'secret_key': secret_key_input.text().strip(),
                'from_lang': from_lang_input.currentData(),
                'to_lang': to_lang_input.currentData()
            })
            self.hotkeys.update({
                'snip': snip_hotkey_input.text().strip() or 'F1',
                'snip_and_pin': fast_hotkey_input.text().strip() or 'F4',
                'clipboard_pin': clip_hotkey_input.text().strip() or 'F3'
            })
            self.auto_translate = auto_translate_cb.isChecked()
            if hasattr(self, 'auto_trans_btn'):
                self.update_auto_trans_btn_text()
            self.clipboard_monitor_enabled = clip_monitor_cb.isChecked()
            self.monitor_folder_enabled = folder_monitor_cb.isChecked()
            self.saveConfig()

    def get_translation_settings(self):
        return self.translation_settings.copy()

    def toggle_autostart(self):
        current_os = platform.system()
        try:
            if current_os == "Windows":
                is_enabled = self._is_windows_autostart_enabled()
                self._set_windows_autostart(not is_enabled)
                self.update_autostart_button()
            elif current_os == "Linux":
                is_enabled = self._is_linux_autostart_enabled()
                self._set_linux_autostart(not is_enabled)
                self.update_autostart_button()
            else:
                QMessageBox.warning(self, '不支持', f'此操作系统 ({current_os}) 不支持自启动设置。')
        except Exception as e:
            logger.error(f"Failed to toggle autostart: {e}")

    def update_autostart_button(self):
        current_os = platform.system()
        is_enabled = False
        try:
            if current_os == "Windows":
                is_enabled = self._is_windows_autostart_enabled()
            elif current_os == "Linux":
                is_enabled = self._is_linux_autostart_enabled()
        except Exception:
            pass

        if hasattr(self, 'autostart_button'):
            self.autostart_button.setText(f'开机自启：{"开启" if is_enabled else "关闭"}')
            self.autostart_button.setProperty("autostart_off", not is_enabled)
            self.autostart_button.style().unpolish(self.autostart_button)
            self.autostart_button.style().polish(self.autostart_button)

    def check_dark_mode(self):
        if platform.system() == "Windows":
            return self._is_windows_dark_mode()
        return False

    def _is_windows_dark_mode(self):
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
            )
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            winreg.CloseKey(key)
            return value == 0
        except Exception:
            return False

    def _get_executable_path(self):
        if getattr(sys, 'frozen', False):
            path = sys.executable
        else:
            path = os.path.abspath(sys.argv[0])
            if sys.argv[0] == '-m':
                import inspect
                frame = inspect.currentframe()
                while frame.f_back:
                    frame = frame.f_back
                    module_name = frame.f_globals.get('__name__')
                    if module_name == '__main__':
                        path = os.path.abspath(frame.f_code.co_filename)
                        break
                else:
                    path = os.path.abspath(os.getcwd())
            elif not os.path.isabs(path):
                path = os.path.abspath(os.path.join(os.getcwd(), path))

        if platform.system() == "Windows" and ' ' in path:
            return f'"{path}"'
        return path

    def _get_windows_registry_key(self, access):
        import winreg
        try:
            return winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, access)
        except OSError:
            return None

    def _is_windows_autostart_enabled(self):
        import winreg
        key = self._get_windows_registry_key(winreg.KEY_READ)
        if not key:
            return False
        try:
            winreg.QueryValueEx(key, APP_NAME)
            winreg.CloseKey(key)
            return True
        except FileNotFoundError:
            winreg.CloseKey(key)
            return False
        except OSError:
            if key: winreg.CloseKey(key)
            return False

    def _set_windows_autostart(self, enabled: bool):
        import winreg
        key = self._get_windows_registry_key(winreg.KEY_ALL_ACCESS)
        if not key:
            raise OSError("Could not open registry key for writing.")
        
        app_path = self._get_executable_path()
        try:
            if enabled:
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, app_path)
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                except FileNotFoundError:
                    pass
            winreg.CloseKey(key)
            return True
        except OSError:
            if key: winreg.CloseKey(key)
            raise

    def _get_linux_autostart_path(self):
        autostart_dir = pathlib.Path.home() / ".config" / "autostart"
        return autostart_dir / f"{APP_NAME}.desktop"

    def _is_linux_autostart_enabled(self):
        return self._get_linux_autostart_path().exists()

    def _set_linux_autostart(self, enabled: bool):
        desktop_file_path = self._get_linux_autostart_path()
        autostart_dir = desktop_file_path.parent
        try:
            if enabled:
                autostart_dir.mkdir(parents=True, exist_ok=True)
                exec_path = self._get_executable_path()
                icon_path = os.path.abspath(get_resource_path('assets/icon.png'))
                desktop_content = f"""[Desktop Entry]
Type=Application
Name={APP_NAME}
Exec={exec_path}
Icon={icon_path}
Comment=Snipaste OCR Tool with Pin and Auto Translation
Terminal=false
X-GNOME-Autostart-enabled=true
"""
                desktop_file_path.write_text(desktop_content, encoding='utf-8')
            else:
                if desktop_file_path.exists():
                    desktop_file_path.unlink()
            return True
        except Exception as e:
            logger.error(f"Failed to set Linux autostart: {e}")
            raise