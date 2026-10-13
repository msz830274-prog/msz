# -*- coding: utf-8 -*-
"""
智能段落与跨行断句合并模块 (Smart Paragraph & Sentence Merging)
解决 OCR 在面对排版折行、长句跨行、连字符断词时的断句破碎问题
实现连贯的段落合并与高质量上下文机器翻译
"""

import re
import logging

logger = logging.getLogger(__name__)


def is_cjk(char: str) -> bool:
    """判断字符是否属于中日韩文字"""
    if not char:
        return False
    cp = ord(char)
    return (
        0x4E00 <= cp <= 0x9FFF or   # CJK Unified Ideographs
        0x3400 <= cp <= 0x4DBF or   # CJK Unified Ideographs Extension A
        0x20000 <= cp <= 0x2A6DF or # Extension B
        0x3040 <= cp <= 0x309F or   # Hiragana
        0x30A0 <= cp <= 0x30FF or   # Katakana
        0xAC00 <= cp <= 0xD7AF      # Hangul Syllables
    )


def is_sentence_ender(text: str) -> bool:
    """判断一行文本末尾是否为终结标点（句号、问号、叹号等）"""
    text = text.rstrip()
    if not text:
        return False

    # 中文/日文终结标点
    if text[-1] in ('。', '！', '？', '；', '…'):
        return True

    # 英文终结标点
    if text[-1] in ('.', '!', '?', ';'):
        # 排除常见英文缩写（如 e.g., i.e., Dr., Fig. 等）和纯数字小数点
        if re.search(r'\b(e\.g|i\.e|mr|mrs|ms|dr|prof|vs|fig|no|inc|ltd|approx|dept|univ)\.$', text, re.IGNORECASE):
            return False
        if re.search(r'\d+\.$', text):
            return False
        return True

    return False


def is_list_item(text: str) -> bool:
    """判断一行文本是否为列表项/序号开头（如 1. / 1) / (1) / - / • 等）"""
    t = text.strip()
    pattern = r'^(\d+[\.\)、:：]|[-•*◆■▶]\s+|\([0-9a-zA-Z]+\)\s+|\[[0-9a-zA-Z]+\]\s+|[A-Za-z][\.\)]\s+)'
    return bool(re.match(pattern, t))


def merge_text_pair(t1: str, t2: str) -> str:
    """智能拼接两行文本，自动处理连字符与中英文空隙"""
    t1 = t1.strip()
    t2 = t2.strip()
    if not t1:
        return t2
    if not t2:
        return t1

    # 1. 处理英文连字符断词（如 "infor-" + "mation" -> "information"）
    if t1.endswith('-') and len(t1) > 1 and t1[-2].isalpha() and t2[0].isalpha():
        return t1[:-1] + t2

    # 2. 中日韩文字边界无缝拼接（不插入空格），西方字母语言插入空格
    last_char = t1[-1]
    first_char = t2[0]
    if is_cjk(last_char) or is_cjk(first_char):
        return t1 + t2
    else:
        return t1 + ' ' + t2


def cluster_and_merge_lines(boxes, texts):
    """
    基于版面空间几何特征与语言学断句规则，将 OCR 识别的碎行聚类合并为完整段落
    
    Args:
        boxes: 列表，每个元素为 [x0, y0, x1, y1, x2, y2, x3, y3] 的 8 点坐标
        texts: 列表，对应的单行文字列表
        
    Returns:
        (merged_boxes, merged_texts, clusters)
        - merged_boxes: 合并后的段落外接矩形框（8点格式）
        - merged_texts: 合并后的完整语句/段落文本
        - clusters: 包含各聚合段落中原始子行信息的列表字典
    """
    if not boxes or not texts:
        return boxes, texts, []

    # 1. 结构化构建行对象
    lines = []
    for b, t in zip(boxes, texts):
        if not t or not t.strip():
            continue
        min_x = min(b[0], b[6])
        min_y = min(b[1], b[3])
        max_x = max(b[2], b[4])
        max_y = max(b[5], b[7])
        lines.append({
            'min_x': min_x,
            'min_y': min_y,
            'max_x': max_x,
            'max_y': max_y,
            'h': max(1, max_y - min_y),
            'w': max(1, max_x - min_x),
            'text': t.strip(),
            'raw_box': b
        })

    if not lines:
        return [], [], []

    # 2. 按照自然阅读顺序排序（由上至下、由左至右）
    # 使用动态行高量化桶避免轻微倾斜造成交错
    avg_h = sum(l['h'] for l in lines) / max(1, len(lines))
    bucket_size = max(10, int(avg_h * 0.6))
    lines.sort(key=lambda item: (item['min_y'] // bucket_size, item['min_x']))

    clusters = []
    curr = lines[0].copy()
    curr['children'] = [lines[0]]
    last_single_line = lines[0]

    # 3. 逐行判断是否属于同一段落并合并
    for next_line in lines[1:]:
        gap_y = next_line['min_y'] - curr['max_y']
        ref_h = last_single_line['h']
        max_h = max(ref_h, next_line['h'])
        h_ratio = next_line['h'] / max(1, ref_h)

        # 水平方向重叠与对齐检测
        inter_x = max(0, min(curr['max_x'], next_line['max_x']) - max(curr['min_x'], next_line['min_x']))
        min_w = min(curr['w'], next_line['w'])
        overlap_x = inter_x / max(1, min_w)
        left_diff = abs(next_line['min_x'] - curr['min_x'])

        # 几何条件：
        # - 行间距在正常排版范围 (-0.4h ~ 1.5h)
        # - 字体高度比例相近 (0.45 ~ 2.2，避免把标题与正文强行合并)
        # - 存在水平重叠或左边距相近（同列对齐）
        geom_match = (
            -0.4 * max_h <= gap_y <= 1.5 * max_h and
            0.45 <= h_ratio <= 2.2 and
            (overlap_x > 0.2 or left_diff <= 3.0 * max_h)
        )

        # 语义条件：
        # - 下一行不是独立列表序号（如 1. / 2) ）
        # - 当前行未完结，或虽有句号但紧密衔接在同一紧凑段落内
        t1 = curr['text']
        t2 = next_line['text']
        is_bullet = is_list_item(t2)
        has_ended = is_sentence_ender(t1)

        # 只要当前行未完结，在几何匹配下必合并；若已有句号，仅在极紧凑且重叠度高时才归入同一段落
        should_merge = geom_match and not is_bullet and (
            not has_ended or (overlap_x > 0.5 and gap_y <= 0.8 * max_h)
        )

        if should_merge:
            curr['text'] = merge_text_pair(curr['text'], next_line['text'])
            curr['min_x'] = min(curr['min_x'], next_line['min_x'])
            curr['min_y'] = min(curr['min_y'], next_line['min_y'])
            curr['max_x'] = max(curr['max_x'], next_line['max_x'])
            curr['max_y'] = max(curr['max_y'], next_line['max_y'])
            curr['h'] = curr['max_y'] - curr['min_y']
            curr['w'] = curr['max_x'] - curr['min_x']
            curr['children'].append(next_line)
            last_single_line = next_line
        else:
            clusters.append(curr)
            curr = next_line.copy()
            curr['children'] = [next_line]
            last_single_line = next_line

    clusters.append(curr)

    # 4. 组装合并后的 8 点坐标框与文本
    merged_boxes = []
    merged_texts = []
    for c in clusters:
        box8 = [
            c['min_x'], c['min_y'],
            c['max_x'], c['min_y'],
            c['max_x'], c['max_y'],
            c['min_x'], c['max_y']
        ]
        merged_boxes.append(box8)
        merged_texts.append(c['text'])

    return merged_boxes, merged_texts, clusters
