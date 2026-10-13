import os
import yaml
import logging
from PyQt6.QtCore import QThread, pyqtSignal
from src.core.folder_monitor import FolderMonitor
from src.core.ocr_processor import OCRProcessor
from src.utils.logging_config import setup_logging

logger = logging.getLogger(__name__)


class OCRThread(QThread):
    preview_signal = pyqtSignal(str, object)
    error_signal = pyqtSignal(str)
    ready_signal = pyqtSignal()
    
    def __init__(self):
        super().__init__()
        setup_logging()
        self.running = True
        self.ocr_processor = None
        
        # Load configuration
        try:
            current_path = os.path.abspath(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
            config_path = os.path.join(current_path, 'config.yml')
            
            with open(config_path, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f) or {}
                snipaste_cfg = config.get('snipaste') or {}
                self.path = snipaste_cfg.get('path') or os.path.join(current_path, 'screenshots')
                self.modelpath = snipaste_cfg.get('modelpath') or os.path.join(current_path, 'models')
                self.monitor_folder_enabled = snipaste_cfg.get('monitor_folder_enabled', False)
                
                # Ensure screenshot path exists
                if not os.path.exists(self.path):
                    os.makedirs(self.path, exist_ok=True)

                # Validate model path
                if not self.modelpath or not os.path.exists(self.modelpath):
                    error_msg = "模型路径无效或为空，请在设置中指定正确的模型路径。"
                    logger.error(error_msg)
                    self.error_signal.emit(error_msg)
                    return
                    
                # 采用按需延迟加载模式，不在启动时强占几百兆内存
                self.ocr_processor = None
                logger.info("OCRThread initialized with lazy loading (0MB startup RAM)")
                    
        except Exception as e:
            logger.error(f"Failed to load OCR configuration: {str(e)}")
            raise

    def get_ocr_processor(self):
        """按需延迟初始化 OCR 处理器"""
        if self.ocr_processor is None:
            if not self.modelpath or not os.path.exists(self.modelpath):
                raise ValueError(f"模型路径无效或不存在: {self.modelpath}")
            logger.info("Initializing OCRProcessor on first demand...")
            self.ocr_processor = OCRProcessor(self.modelpath)
        return self.ocr_processor

    def run(self):
        logger.info("Starting OCR thread")
        try:
            if self.monitor_folder_enabled:
                self.FolderMonitor = FolderMonitor(self.path, self.modelpath, ocr_processor=self.ocr_processor)
                self.FolderMonitor.result_signal.connect(self.handle_result)
                logger.info(f"Folder monitor active for: {self.path}")

            self.ready_signal.emit()
            self.exec()
        except Exception as e:
            error_msg = f"启动OCR服务失败: {str(e)}"
            logger.error(error_msg)
            self.error_signal.emit(error_msg)
        
    def handle_result(self, image_path, result):
        if self.running:
            logger.info(f"Received folder monitor result: {image_path}")
            self.preview_signal.emit(image_path, result)
            
    def stop(self):
        """停止OCR线程并清理资源"""
        logger.info("Stopping OCR thread")
        self.running = False
        if hasattr(self, 'FolderMonitor'):
            try:
                self.FolderMonitor.result_signal.disconnect()
            except Exception:
                pass
            self.FolderMonitor.stop()
            delattr(self, 'FolderMonitor')
        self.quit()
        self.wait(1000)