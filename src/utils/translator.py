# -*- coding: utf-8 -*-
"""
翻译模块：支持免费免密钥翻译（Google/MyMemory）与腾讯云机器翻译
"""

import hashlib
import hmac
import json
import time
import logging
import urllib.request
import urllib.parse
from datetime import datetime
from http.client import HTTPSConnection
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)


class FreeTranslator:
    """免费免密钥翻译器，优先使用 Google Translate Web RPC，备用 MyMemory"""

    def __init__(self):
        self._lang_map_google = {
            'zh': 'zh-CN',
            'auto': 'auto',
            'en': 'en',
            'ja': 'ja',
            'ko': 'ko',
            'fr': 'fr',
            'de': 'de',
            'ru': 'ru',
            'es': 'es',
        }
        self._lang_map_mymemory = {
            'zh': 'zh-CN',
            'auto': 'en',
            'en': 'en',
            'ja': 'ja',
            'ko': 'ko',
        }

    def _translate_google_single(self, text, source_lang='auto', target_lang='zh'):
        sl = self._lang_map_google.get(source_lang, source_lang)
        tl = self._lang_map_google.get(target_lang, target_lang)
        url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl={sl}&tl={tl}&dt=t&q={urllib.parse.quote(text)}"
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        with urllib.request.urlopen(req, timeout=2.0) as res:
            data = json.loads(res.read().decode('utf-8'))
            if data and data[0]:
                return "".join(part[0] for part in data[0] if part and part[0])
        return text

    def _translate_mymemory(self, text, source_lang='auto', target_lang='zh'):
        sl = self._lang_map_mymemory.get(source_lang, 'en')
        tl = self._lang_map_mymemory.get(target_lang, 'zh-CN')
        url = f"https://api.mymemory.translated.net/get?q={urllib.parse.quote(text)}&langpair={sl}|{tl}"
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0'
        })
        with urllib.request.urlopen(req, timeout=3.0) as res:
            data = json.loads(res.read().decode('utf-8'))
            trans = data.get('responseData', {}).get('translatedText', '')
            if trans and trans != text:
                return trans
        return text

    def translate(self, text, source_lang='auto', target_lang='zh'):
        """翻译单段文本"""
        if not text or not text.strip():
            return text
        try:
            return self._translate_google_single(text, source_lang, target_lang)
        except Exception as e:
            logger.warning(f"Google translate failed: {e}, falling back to MyMemory")
            try:
                return self._translate_mymemory(text, source_lang, target_lang)
            except Exception as e2:
                logger.error(f"MyMemory fallback failed: {e2}")
                return text

    def translate_list(self, texts, source_lang='auto', target_lang='zh'):
        """批量翻译文本列表"""
        if not texts:
            return []

        # 尝试快速批处理（用换行符合并）
        try:
            combined = "\n".join(texts)
            translated_combined = self.translate(combined, source_lang, target_lang)
            lines = translated_combined.split("\n")
            if len(lines) == len(texts):
                return lines
        except Exception as e:
            logger.debug(f"Combined batch translation failed or line count mismatch: {e}")

        # 回退为并行逐行翻译
        def _worker(t):
            return self.translate(t, source_lang, target_lang)

        with ThreadPoolExecutor(max_workers=min(6, len(texts))) as pool:
            return list(pool.map(_worker, texts))


class TencentTranslator:
    def __init__(self, secret_id, secret_key):
        self.secret_id = secret_id
        self.secret_key = secret_key
        self.service = "tmt"
        self.host = "tmt.tencentcloudapi.com"
        self.version = "2018-03-21"
        self.region = "ap-beijing"
        self.action = "TextTranslate"
        self.algorithm = "TC3-HMAC-SHA256"

    def _sign(self, key, msg):
        return hmac.new(key.encode("utf-8") if isinstance(key, str) else key,
                        msg.encode("utf-8"), hashlib.sha256).digest()

    def translate(self, text, source_lang='auto', target_lang='zh'):
        if not text or not text.strip():
            return text
        timestamp = int(time.time())
        date = datetime.utcfromtimestamp(timestamp).strftime("%Y-%m-%d")

        # 准备请求体
        payload = json.dumps({
            "SourceText": text,
            "Source": source_lang,
            "Target": target_lang,
            "ProjectId": 0
        })

        # 构建签名所需信息
        canonical_headers = (
            f"content-type:application/json; charset=utf-8\n"
            f"host:{self.host}\n"
            f"x-tc-action:{self.action.lower()}\n"
        )
        signed_headers = "content-type;host;x-tc-action"

        # 构建规范请求串
        canonical_request = (
            "POST\n/\n\n" +
            canonical_headers + "\n" +
            signed_headers + "\n" +
            hashlib.sha256(payload.encode("utf-8")).hexdigest()
        )

        # 构建待签名字符串
        credential_scope = f"{date}/{self.service}/tc3_request"
        string_to_sign = (
            f"{self.algorithm}\n{timestamp}\n{credential_scope}\n" +
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()
        )

        # 计算签名
        secret_date = self._sign(f"TC3{self.secret_key}", date)
        secret_service = self._sign(secret_date, self.service)
        secret_signing = self._sign(secret_service, "tc3_request")
        signature = hmac.new(secret_signing,
                            string_to_sign.encode("utf-8"),
                            hashlib.sha256).hexdigest()

        # 构建授权信息
        authorization = (
            f"{self.algorithm} Credential={self.secret_id}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )

        # 发送请求
        headers = {
            "Authorization": authorization,
            "Content-Type": "application/json; charset=utf-8",
            "Host": self.host,
            "X-TC-Action": self.action,
            "X-TC-Timestamp": str(timestamp),
            "X-TC-Version": self.version,
            "X-TC-Region": self.region
        }

        conn = None
        try:
            conn = HTTPSConnection(self.host, timeout=5)
            conn.request("POST", "/", payload, headers)
            response = conn.getresponse()
            result = json.loads(response.read().decode())

            if "Response" in result and "TargetText" in result["Response"]:
                return result["Response"]["TargetText"]
            else:
                raise Exception(f"Translation failed: {result}")
        except Exception as e:
            raise Exception(f"Translation request failed: {str(e)}")
        finally:
            if conn:
                conn.close()

    def translate_list(self, texts, source_lang='auto', target_lang='zh'):
        results = []
        for text in texts:
            results.append(self.translate(text, source_lang, target_lang))
        return results


def get_translator(settings: dict):
    """根据设置返回合适的翻译器实例"""
    provider = settings.get('provider', 'free')
    secret_id = settings.get('secret_id', '').strip()
    secret_key = settings.get('secret_key', '').strip()

    if provider == 'tencent' and secret_id and secret_key:
        return TencentTranslator(secret_id, secret_key)
    # 默认或密钥为空时使用免费翻译
    return FreeTranslator()