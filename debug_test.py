# -*- coding: utf-8 -*-
"""
SnipasteOCR 专项调试诊断工具
用于排查文字识别、网络翻译、剪贴板监听及贴图窗口渲染全流程
"""

import os
import sys
import time
import cv2
import numpy as np

# 确保控制台支持 UTF-8 中文输出
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

print("=" * 60)
print("       SnipasteOCR 专项调试诊断工具")
print("=" * 60)

# 1. 检查 Python 环境与依赖
print("\n[1/5] 正在检查运行环境与核心依赖...")
print(f"  - Python 版本: {sys.version.split()[0]}")
print(f"  - 运行目录: {os.path.abspath('.')}")

try:
    import fastdeploy as fd
    import cv2
    import pyperclip
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtCore import QTimer
    from PyQt6.QtGui import QImage
    print("  [✓] FastDeploy, OpenCV, PyQt6, Pyperclip 导入成功！")
except Exception as e:
    print(f"  [✗] 依赖导入失败: {e}")
    sys.exit(1)

# 2. 检查 OCR 模型文件
print("\n[2/5] 正在检查飞桨 OCR 模型文件...")
model_dir = os.path.abspath('models')
det_path = os.path.join(model_dir, 'ch_PP-OCRv3_det_infer', 'inference.pdmodel')
rec_path = os.path.join(model_dir, 'ch_PP-OCRv3_rec_infer', 'inference.pdmodel')
cls_path = os.path.join(model_dir, 'ch_ppocr_mobile_v2.0_cls_infer', 'inference.pdmodel')
label_path = os.path.join(model_dir, 'labels.txt')

all_exist = all(os.path.exists(p) for p in [det_path, rec_path, cls_path, label_path])
if all_exist:
    print("  [✓] 检测、识别、方向分类模型及 labels.txt 全部完整！")
else:
    print(f"  [✗] 模型文件缺失！请检查目录: {model_dir}")
    sys.exit(1)

# 3. 初始化 OCR 引擎并跑基准测试
print("\n[3/5] 正在初始化 OCR 引擎并测试识别基准...")
from src.core.ocr_processor import OCRProcessor
try:
    t0 = time.time()
    ocr = OCRProcessor(model_dir)
    print(f"  [✓] FastDeploy 模型引擎加载完成，耗时: {round(time.time() - t0, 2)} 秒")
    
    # 构造基准测试图片 (英文文字)
    test_img = np.full((120, 360, 3), 255, dtype=np.uint8)
    cv2.putText(test_img, 'FastDeploy OCR', (20, 70), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 0), 2)
    res, content = ocr.process_cv2_image(test_img, copy_to_clipboard=False)
    print(f"  [✓] 基准图片识别结果: {res.text}")
except Exception as e:
    print(f"  [✗] OCR 初始化或识别异常: {e}")
    sys.exit(1)

# 4. 测试网络翻译服务
print("\n[4/5] 正在测试翻译网络连通性...")
from src.utils.translator import FreeTranslator
ft = FreeTranslator()
test_words = ['Snipaste', 'Screenshot and Pin', 'Auto Translation']
for word in test_words:
    try:
        t0 = time.time()
        trans = ft.translate(word, source_lang='auto', target_lang='zh')
        print(f"  [✓] 翻译测试: '{word}' -> '{trans}' (耗时 {round(time.time() - t0, 2)}s)")
    except Exception as e:
        print(f"  [✗] 翻译失败 '{word}': {e}")

# 5. 交互式剪贴板检测与贴图弹窗测试
print("\n[5/5] 进入实时剪贴板监听与贴图测试模式...")
print("-" * 60)
print("【请操作】现在请使用 Snipaste 进行一次截图：")
print("  1. 打开 Snipaste 框选屏幕上包含英文的区域")
print("  2. 点击 Snipaste 工具栏上的【复制】按钮（或按 Enter / 双击区域）")
print("  程序将实时捕捉该图片、执行 OCR 与自动翻译，并在屏幕上弹出翻译贴图！")
print("-" * 60)

app = QApplication(sys.argv)
from src.ui.preview_window import PreviewWindow, to_cv2_bgr

# 检查当前剪贴板是否已经有图片
clip = QApplication.clipboard()
active_preview = None

def process_clipboard_image(qimg):
    global active_preview
    print(f"\n⚡ 检测到新截图！图片尺寸: {qimg.width()} × {qimg.height()}")
    try:
        bgr = to_cv2_bgr(qimg)
        print("  -> 正在进行飞桨 OCR 文字识别...")
        t0 = time.time()
        res, content = ocr.process_cv2_image(bgr, copy_to_clipboard=True)
        print(f"  -> OCR 识别完成 (耗时 {round(time.time()-t0, 2)}s)！识别到的文本:")
        if res.text:
            for idx, text in enumerate(res.text):
                print(f"     [{idx + 1}] {text}")
        else:
            print("     (未在图片中检测到文字)")

        print("  -> 正在弹出定在屏幕上的翻译贴图窗口...")
        active_preview = PreviewWindow(parent=None, image_data=qimg, ocr_result=res, ocr_processor=ocr)
        active_preview.show()
        print("  [✓] 贴图已置顶显示！请在屏幕上查看覆盖翻译效果。")
        print("      提示：在贴图窗口上按【空格键】切换原文/译文，【滚轮】调节透明度，【双击】或按【Esc】关闭。")
    except Exception as e:
        import traceback
        print(f"  [✗] 处理图片失败: {e}")
        traceback.print_exc()

last_dim = None

# 如果剪贴板已有图片，立即处理
if clip.mimeData().hasImage():
    qimg = clip.image()
    if not qimg.isNull() and qimg.width() > 10:
        print("💡 当前剪贴板中已有图片，正在立即测试该图片...")
        last_dim = (qimg.width(), qimg.height())
        process_clipboard_image(qimg)
else:
    # 剪贴板为空时，若本地有样例截图，先弹出一个样例贴图展示效果
    sample_path = os.path.join('screenshots', 'Snipaste_2026-10-02_21-26-47.jpg')
    if os.path.exists(sample_path):
        print("💡 剪贴板暂无图片，正在加载本地样例截图展示自动贴图翻译效果...")
        sample_qimg = QImage(sample_path)
        if not sample_qimg.isNull():
            process_clipboard_image(sample_qimg)

def on_clipboard_changed():
    global last_dim
    mime = clip.mimeData()
    if mime and mime.hasImage():
        qimg = clip.image()
        if not qimg.isNull() and qimg.width() > 10:
            dim = (qimg.width(), qimg.height())
            if dim != last_dim:
                last_dim = dim
                process_clipboard_image(qimg)

clip.dataChanged.connect(on_clipboard_changed)

print("\n(程序正在后台监听剪贴板，等待您的截图操作... 按 Ctrl+C 或关闭本窗口退出)\n")
try:
    sys.exit(app.exec())
except KeyboardInterrupt:
    print("\n已退出调试工具。")
