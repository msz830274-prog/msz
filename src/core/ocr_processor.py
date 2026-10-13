"""
OCR处理模块
使用 FastDeploy / PaddleOCR 进行快速文字识别
"""

import os
import cv2
import numpy as np
import logging
import fastdeploy as fd
import pyperclip
from src.utils.logging_config import setup_logging

logger = logging.getLogger(__name__)


class OCRProcessor:
    def __init__(self, modelpath, config=None):
        """初始化OCR处理器

        Args:
            modelpath: 模型文件路径
            config: 可选的增强配置字典
        """
        setup_logging()
        logger.info(f"Initializing OCR processor with model path: {modelpath}")
        
        self.modelpath = modelpath
        self.det_model = os.path.join(modelpath, 'ch_PP-OCRv3_det_infer')
        self.rec_model = os.path.join(modelpath, 'ch_PP-OCRv3_rec_infer')
        self.cls_model = os.path.join(modelpath, 'ch_ppocr_mobile_v2.0_cls_infer')
        self.label_file = os.path.join(modelpath, 'labels.txt')
        self.ppocr_v3 = None

        # 加载或初始化 OCR 深度画质与精度增强配置
        self.config = config or self._load_ocr_config()
        self.super_resolution = self.config.get('super_resolution', True)
        self.enhance_contrast = self.config.get('enhance_contrast', True)
        self.max_side_len = self.config.get('max_side_len', 2560)
        self.det_db_thresh = self.config.get('det_db_thresh', 0.2)
        self.det_db_box_thresh = self.config.get('det_db_box_thresh', 0.5)
        self.det_db_unclip_ratio = self.config.get('det_db_unclip_ratio', 1.8)
        self.use_dilation = self.config.get('use_dilation', True)

        self.init_model()

    def _load_ocr_config(self):
        """从 config.yml 加载 ocr 增强配置"""
        default_cfg = {
            'super_resolution': True,
            'enhance_contrast': True,
            'max_side_len': 2560,
            'det_db_thresh': 0.2,
            'det_db_box_thresh': 0.5,
            'det_db_unclip_ratio': 1.8,
            'use_dilation': True
        }
        config_path = os.path.abspath('config.yml')
        if os.path.exists(config_path):
            try:
                import yaml
                with open(config_path, 'r', encoding='utf-8') as f:
                    data = yaml.safe_load(f) or {}
                if 'ocr' in data and isinstance(data['ocr'], dict):
                    default_cfg.update(data['ocr'])
            except Exception as e:
                logger.warning(f"Failed to read OCR config from config.yml: {e}")
        return default_cfg

    def init_model(self):
        """初始化模型文件并以优化参数加载 FastDeploy PPOCRv3 系统"""
        # Detection模型, 检测文字框
        self.det_model_file = os.path.join(self.det_model, "inference.pdmodel")
        self.det_params_file = os.path.join(self.det_model, "inference.pdiparams")
        # Classification模型，方向分类
        self.cls_model_file = os.path.join(self.cls_model, "inference.pdmodel")
        self.cls_params_file = os.path.join(self.cls_model, "inference.pdiparams")
        # Recognition模型，文字识别模型
        self.rec_model_file = os.path.join(self.rec_model, "inference.pdmodel")
        self.rec_params_file = os.path.join(self.rec_model, "inference.pdiparams")
        self.rec_label_file = self.label_file

        # 校验模型文件完整性
        for file_path in [self.det_model_file, self.det_params_file, 
                         self.cls_model_file, self.cls_params_file,
                         self.rec_model_file, self.rec_params_file,
                         self.rec_label_file]:
            if not os.path.exists(file_path):
                logger.error(f"Model file not found: {file_path}")
                raise FileNotFoundError(f"Model file not found: {file_path}")

        try:
            option = self.build_option()

            # 初始化检测模型并持久化引用，防止 pybind11 C++ 指针被提前析构
            self.det_model_instance = fd.vision.ocr.DBDetector(
                self.det_model_file, self.det_params_file, runtime_option=option)

            # 注入专业级检测优化参数：高分辨率上限、高召回灵敏度与扩充边框
            self.det_model_instance.max_side_len = int(self.max_side_len)
            self.det_model_instance.det_db_thresh = float(self.det_db_thresh)
            self.det_model_instance.det_db_box_thresh = float(self.det_db_box_thresh)
            self.det_model_instance.det_db_unclip_ratio = float(self.det_db_unclip_ratio)
            self.det_model_instance.use_dilation = bool(self.use_dilation)

            # 初始化分类模型
            self.cls_model_instance = fd.vision.ocr.Classifier(
                self.cls_model_file, self.cls_params_file, runtime_option=option)

            # 初始化识别模型
            self.rec_model_instance = fd.vision.ocr.Recognizer(
                self.rec_model_file, self.rec_params_file, self.rec_label_file, runtime_option=option)

            # 组合成完整的OCR系统并持久保留在内存中
            self.ppocr_v3 = fd.vision.ocr.PPOCRv3(
                det_model=self.det_model_instance, 
                cls_model=self.cls_model_instance, 
                rec_model=self.rec_model_instance
            )
            logger.info("PPOCRv3 enhanced system loaded successfully (max_side_len=%s, unclip=%s).",
                        self.max_side_len, self.det_db_unclip_ratio)
        except Exception as e:
            logger.error(f"Failed to initialize PPOCRv3 engine: {e}")
            raise

    def build_option(self):
        """构建高性能运行时选项"""
        option = fd.RuntimeOption()
        threads = min(8, max(4, os.cpu_count() or 6))
        option.set_cpu_thread_num(threads)
        option.use_cpu()
        return option

    def enhance_image_for_ocr(self, img_bgr):
        """对输入截图执行智能自适应超分放大与边缘/对比度锐化增强
        
        Args:
            img_bgr: 原始 BGR 图像
            
        Returns:
            (enhanced_image, scale_factor)
        """
        if img_bgr is None or img_bgr.size == 0:
            return img_bgr, 1.0

        h, w = img_bgr.shape[:2]
        scale = 1.0

        # 1. 动态自适应高保真超分辨率放大（针对细小文字、小尺寸选区大幅提升识别率）
        if self.super_resolution:
            if h < 120 or w < 300:
                scale = 2.5
            elif h < 450 or w < 700:
                scale = 2.0
            elif h < 900 and w < 1200:
                scale = 1.4

            if scale > 1.0:
                target_w = int(round(w * scale))
                target_h = int(round(h * scale))
                processed = cv2.resize(img_bgr, (target_w, target_h), interpolation=cv2.INTER_LANCZOS4)
            else:
                processed = img_bgr.copy()
        else:
            processed = img_bgr.copy()

        # 2. 图像对比度与笔画锐化（消除 ClearType 彩色抗锯齿与模糊）
        if self.enhance_contrast:
            # 边缘反锐化掩模 (Unsharp Mask)，强化字符笔画边界
            gaussian = cv2.GaussianBlur(processed, (0, 0), sigmaX=2.0)
            sharpened = cv2.addWeighted(processed, 1.35, gaussian, -0.35, 0)

            # 评估图像对比度（灰度标准差）
            gray = cv2.cvtColor(sharpened, cv2.COLOR_BGR2GRAY)
            std_val = float(np.std(gray))

            # 低对比度图像（暗色背景淡字或浅灰浅底）启动自适应局部直方图均衡 (CLAHE)
            if std_val < 48.0:
                lab = cv2.cvtColor(sharpened, cv2.COLOR_BGR2LAB)
                l, a, b = cv2.split(lab)
                clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
                l = clahe.apply(l)
                processed = cv2.cvtColor(cv2.merge((l, a, b)), cv2.COLOR_LAB2BGR)
            else:
                processed = sharpened

        return processed, scale

    def process_cv2_image(self, image_bgr, save_txt_path=None, copy_to_clipboard=True):
        """直接在内存中处理 cv2 图像（NumPy 数组），无需文件读写

        Args:
            image_bgr: OpenCV BGR 格式的 numpy ndarray
            save_txt_path: 可选的 txt 保存路径
            copy_to_clipboard: 是否将识别文字复制到剪贴板

        Returns:
            (result, content)
        """
        if image_bgr is None or image_bgr.size == 0:
            raise ValueError("Input image is empty or invalid")

        if self.ppocr_v3 is None:
            self.init_model()

        # 动态自适应图像增强与高保真超分预处理
        enhanced_image, scale = self.enhance_image_for_ocr(image_bgr)

        # 执行推理
        result = self.ppocr_v3.predict(enhanced_image)

        # 后备重试机制：若首次识别未检测到任何文字，尝试反色处理（应对反白/深色背景 UI 按钮）
        if (not result or not getattr(result, 'boxes', None) or len(result.boxes) == 0) and image_bgr.size > 0:
            inverted = cv2.bitwise_not(enhanced_image)
            retry_res = self.ppocr_v3.predict(inverted)
            if retry_res and getattr(retry_res, 'boxes', None) and len(retry_res.boxes) > 0:
                result = retry_res

        # 坐标精确回射：将超分坐标系按原比例还原到原始图像物理像素坐标系
        if scale != 1.0 and result and hasattr(result, 'boxes') and result.boxes:
            result.boxes = [[int(round(coord / scale)) for coord in box] for box in result.boxes]

        content = self._parse_result(result)

        if save_txt_path:
            self._save_to_file(save_txt_path, content)

        if copy_to_clipboard and content:
            try:
                pyperclip.copy(content)
            except Exception as e:
                logger.warning(f"Failed to copy OCR text to clipboard: {e}")

        return result, content

    def process_image(self, image_path):
        """处理文件路径图片（保持向后兼容）

        Args:
            image_path: 图片路径

        Returns:
            OCR识别结果
        """
        logger.info(f"Processing image file: {image_path}")
        image = cv2.imread(image_path)
        if image is None:
            raise ValueError(f"Failed to read image: {image_path}")

        txt_path = image_path.replace('.png', '.txt') if image_path.endswith('.png') else image_path + '.txt'
        result, _ = self.process_cv2_image(image, save_txt_path=txt_path, copy_to_clipboard=True)
        return result

    def _parse_result(self, result):
        """解析OCR结果并智能重组跨行段落语句

        Args:
            result: OCR原始结果

        Returns:
            格式化后连贯自然的文本内容
        """
        if not result or not hasattr(result, 'boxes') or len(result.boxes) == 0:
            return ''

        try:
            from src.utils.text_merger import cluster_and_merge_lines
            _, merged_texts, _ = cluster_and_merge_lines(result.boxes, result.text)
            
            # 智能规范化排版空隙
            cleaned_texts = [self._normalize_text_spacing(t) for t in merged_texts]
            return "\n\n".join(cleaned_texts)
        except Exception as e:
            logger.warning(f"Failed to smart merge OCR text: {e}")
            return "\n".join(getattr(result, 'text', []))

    @staticmethod
    def _normalize_text_spacing(text: str) -> str:
        """规范化 OCR 识别结果的空格排版"""
        if not text:
            return ''
        import re
        # 英文标点紧跟字母或数字时补充空格
        text = re.sub(r'([,;:\)\!\?])([A-Za-z0-9])', r'\1 \2', text)
        return text

    def _save_to_file(self, file_path, content):
        """保存结果到文件"""
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(str(content))
        except Exception as e:
            logger.error(f"Error saving results to file {file_path}: {str(e)}")