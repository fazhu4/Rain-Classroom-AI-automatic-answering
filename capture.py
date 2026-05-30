"""
屏幕捕捉模块 - 截图 + OCR 文字识别与定位
"""
import time
import numpy as np
from PIL import Image
import mss


class ScreenCapture:
    """屏幕区域截取"""

    def __init__(self, region: tuple = None, ocr_lang: list = None):
        """
        region: (left, top, width, height)，全为0则需调用 select_region()
        ocr_lang: easyocr 语言列表，默认 ["ch_sim", "en"]
        """
        self.region = region  # (left, top, width, height)
        self.sct = mss.mss()
        self._ocr = None
        self._ocr_lang = ocr_lang or ["ch_sim", "en"]

    def select_region(self):
        """交互式选择截图区域：在屏幕上拖拽选择"""
        import pyautogui
        print("[*] 请将鼠标移到截图区域左上角，然后按 Enter...")
        input()
        x1, y1 = pyautogui.position()
        print(f"  左上角: ({x1}, {y1})")

        print("[*] 请将鼠标移到截图区域右下角，然后按 Enter...")
        input()
        x2, y2 = pyautogui.position()
        print(f"  右下角: ({x2}, {y2})")

        left = min(x1, x2)
        top = min(y1, y2)
        width = abs(x2 - x1)
        height = abs(y2 - y1)
        self.region = (left, top, width, height)
        print(f"[+] 截图区域: left={left}, top={top}, width={width}, height={height}")
        return self.region

    def capture(self) -> Image.Image:
        """截取指定区域，返回 PIL Image"""
        if self.region is None or sum(self.region) == 0:
            raise ValueError("请先配置截图区域或调用 select_region()")

        left, top, width, height = self.region
        monitor = {"left": left, "top": top, "width": width, "height": height}
        img = self.sct.grab(monitor)
        return Image.frombytes("RGB", img.size, img.bgra, "raw", "BGRX")

    def capture_delay(self, delay: float = 0.3) -> Image.Image:
        """延迟后截图（给页面渲染缓冲）"""
        time.sleep(delay)
        return self.capture()

    @property
    def ocr(self):
        """lazy init OCR reader"""
        if self._ocr is None:
            import easyocr
            self._ocr = easyocr.Reader(self._ocr_lang, gpu=True)
        return self._ocr

    def ocr_detect(self, image: Image.Image) -> list[dict]:
        """
        OCR 识别图片中的文字及位置
        返回: [{"text": "...", "bbox": [[x1,y1],[x2,y2],[x3,y3],[x4,y4]], "center": (cx,cy)}, ...]
        """
        img_array = np.array(image)
        results = self.ocr.readtext(img_array)

        detections = []
        for bbox, text, confidence in results:
            if confidence < 0.4:  # 过滤低置信度
                continue
            # bbox: [[x1,y1],[x2,y2],[x3,y3],[x4,y4]] 四个角
            pts = np.array(bbox)
            cx = int(pts[:, 0].mean())
            cy = int(pts[:, 1].mean())
            detections.append({
                "text": text.strip(),
                "bbox": bbox,
                "center": (cx, cy),
                "confidence": confidence,
            })
        return detections

    def find_text_position(self, detections: list[dict], target: str,
                           prefer_lower: bool = True) -> tuple:
        """
        在 OCR 结果中查找目标文字位置
        prefer_lower: 优先选择画面下方的匹配（选项通常在题目下面）
        返回: (x, y) 或 None
        """
        candidates = []
        target_clean = target.strip().rstrip(".)）").lower()

        for d in detections:
            text_clean = d["text"].strip().rstrip(".)）").lower()
            # 精确匹配或包含匹配
            if text_clean == target_clean or target_clean in text_clean:
                candidates.append(d)

        if not candidates:
            return None

        if prefer_lower:
            # 按 y 坐标降序（下方的优先）
            candidates.sort(key=lambda d: d["center"][1], reverse=True)

        return candidates[0]["center"]
