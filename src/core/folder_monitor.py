"""
文件夹监控模块
使用 watchdog 监控指定文件夹中的新增图片文件
"""

import os
import logging
from PyQt6.QtCore import QObject, pyqtSignal
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from src.core.ocr_processor import OCRProcessor

logger = logging.getLogger(__name__)


class FolderMonitor(QObject):
    result_signal = pyqtSignal(str, object)  # 添加信号：图片路径和OCR结果
    
    def __init__(self, path, modelpath, ocr_processor=None):
        """初始化文件夹监控器

        Args:
            path: 要监控的文件夹路径
            modelpath: OCR模型路径
            ocr_processor: 可选的预加载 OCRProcessor 实例
        """
        super().__init__()
        self.path = path
        self.ocr_processor = ocr_processor or OCRProcessor(modelpath)
        self.event_handler = FileSystemEventHandler()
        self.event_handler.on_created = self.on_created
        self.observer = Observer()
        self.observer.schedule(self.event_handler, path, recursive=False)
        self.processed_files = set()
        self.running = True
        
        # 确保目录存在并可访问
        if not os.path.exists(path):
            os.makedirs(path, exist_ok=True)
        if not os.access(path, os.R_OK):
            raise PermissionError(f"No read permission for directory: {path}")
            
        # 初始化已存在的文件列表
        self.processed_files = set(os.listdir(path))
        logger.debug(f"Initial files in directory: {self.processed_files}")
        
        self.observer.start()
        logger.info(f"Started monitoring directory: {path}")

    def on_created(self, event):
        if not event.is_directory:
            file = os.path.basename(event.src_path)
            if file.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp')):
                full_path = event.src_path.replace('\\', '/')
                logger.info(f"Processing new file: {full_path}")
                try:
                    result = self.ocr_processor.process_image(full_path)
                    self.result_signal.emit(full_path, result)
                    logger.info(f"Successfully processed file: {full_path}")
                except Exception as e:
                    logger.error(f"Error processing file {full_path}: {str(e)}")

    def stop(self):
        """停止文件监控"""
        logger.info("Stopping folder monitor")
        self.running = False
        try:
            self.observer.stop()
            self.observer.join(timeout=1.0)
        except Exception as e:
            logger.warning(f"Error stopping observer: {e}")
        logger.info("Folder monitor stopped successfully")