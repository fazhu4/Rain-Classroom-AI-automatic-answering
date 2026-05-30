#!/usr/bin/env python
"""
网课自动答题脚本
- 浏览器模式（推荐）：直连 Chrome 读取 DOM → 大模型作答 → 浏览器内点击
- 截图模式：屏幕截图 → OCR 识别 → 大模型作答 → 模拟鼠标点击

使用方法:
  浏览器模式：
    1. 关闭所有 Chrome 窗口
    2. 用调试模式启动 Chrome:
       chrome.exe --remote-debugging-port=9222
    3. 在 Chrome 中打开考试页面并登录
    4. python main.py
    5. 按 Ctrl+Shift+A 触发答题

  截图模式：
    1. 编辑 config.yaml，设置 mode: screenshot
    2. python main.py
    3. 首次运行用鼠标选择截图区域
"""
import sys
import time
import threading
import asyncio
import yaml
from pathlib import Path

from llm_client import LLMClient
from pynput import keyboard


class AutoAnswer:
    """自动答题主控制器"""

    def __init__(self, config_path: str = None):
        if config_path is None:
            config_path = Path(__file__).parent / "config.yaml"
        self.config = self._load_config(config_path)
        self.config_path = config_path
        self.llm = LLMClient(self.config["llm"])
        self.mode = self.config.get("mode", "screenshot")
        self.hotkey = self.config["automation"].get("hotkey", "ctrl+shift+x")
        self.click_delay = self.config["automation"].get("click_delay", 0.5)
        self.auto_next = self.config["automation"].get("auto_next", False)
        self.running = True
        self._hotkey_triggered = False
        self._loop = None

    def _load_config(self, path: str) -> dict:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    def run(self):
        api_key = self.config["llm"].get("api_key", "")
        if not api_key or "your-api-key" in api_key:
            print("[!] 请先在 config.yaml 中填入你的 API Key")
            sys.exit(1)

        if self.mode == "browser":
            self._run_browser_mode()
        else:
            self._run_screenshot_mode()

    # ==================== 浏览器模式 ====================

    def _run_browser_mode(self):
        """浏览器模式入口：连接浏览器后自动循环答题"""
        from browser import BrowserController

        browser_cfg = self.config.get("browser", {})
        self.browser = BrowserController(browser_cfg)

        print(f"""
╔══════════════════════════════════════╗
║       网课自动答题 v2.0 浏览器模式  ║
╠══════════════════════════════════════╣
║  LLM: {self.llm.provider:12s} model: {self.llm.model:18s} ║
║  CDP: {browser_cfg.get('cdp_url', 'http://localhost:9222'):32s} ║
╠══════════════════════════════════════╣
║  连接浏览器后自动开始答题           ║
║  按 Ctrl+C 终止程序                ║
╚══════════════════════════════════════╝
        """)

        print("[*] 正在连接浏览器...")
        asyncio.run(self._auto_answer_loop())

    async def _auto_answer_loop(self):
        """自动循环答题：连接 → 提取题目 → LLM 作答 → 点击 → 下一题"""
        # 连接浏览器（与答题循环共享同一个事件循环）
        try:
            await self.browser.connect()
        except Exception as e:
            print(f"[!] 连接失败: {e}")
            browser_type = self.config.get("browser", {}).get("browser_type", "edge")
            exe = "msedge.exe" if browser_type == "edge" else "chrome.exe"
            print(f"""
请按以下步骤操作:
  1. 完全关闭所有 {browser_type.upper()} 窗口
  2. 打开任务管理器，确认没有残留进程，有就结束掉
  3. Win+R 运行: {exe} --remote-debugging-port=9222
  4. 在浏览器中打开考试页面并登录
  5. 重新运行: python main.py
""")
            return

        print("[+] 开始自动答题...")
        question_count = 0
        fail_streak = 0

        try:
            while self.running:
                question_count += 1
                print(f"\n{'=' * 50}")
                print(f"[*] 第 {question_count} 题")

                try:
                    # 1. 注入标记
                    await self.browser.inject_answer_markers()

                    # 2. 提取 DOM 内容
                    q = await self.browser.extract_question()
                    if not q or not q.get("text") or not q.get("options"):
                        fail_streak += 1
                        print(f"[!] 未能提取到题目 (连续失败 {fail_streak} 次)")
                        if fail_streak >= 3:
                            print("[*] 可能已答完所有题目，退出。")
                            break
                        # 尝试点击下一题
                        await asyncio.sleep(1)
                        await self.browser.click_next()
                        await asyncio.sleep(2)
                        continue

                    fail_streak = 0

                    # 3. 组装题面发给 LLM
                    prompt = self._build_question_prompt(q)
                    print(f"[*] 大模型思考中...")
                    result = self.llm.ask_text(prompt)

                    answer = result.get("answer", "").strip()
                    explanation = result.get("explanation", "")
                    print(f"  -> 答案: {answer}")
                    print(f"  -> 解析: {explanation[:150]}")

                    if not answer:
                        print("[!] 大模型未能返回有效答案，跳过此题")
                        await self.browser.click_next()
                        await asyncio.sleep(2)
                        continue

                    # 4. 在浏览器中点击答案
                    ok = await self.browser.click_option(answer, q)
                    if not ok:
                        print(f"[!] 未能自动点击选项 '{answer}'，尝试手动点击下一题")
                    else:
                        print(f"[+] 已选择答案: {answer}")

                    # 5. 自动点击下一题
                    await asyncio.sleep(self.click_delay)
                    next_ok = await self.browser.click_next()
                    if not next_ok:
                        print("[!] 未找到'下一题'按钮，可能已是最后一题")
                        break

                    print(f"[+] 已进入下一题")
                    await asyncio.sleep(2)  # 等待页面加载

                except Exception as e:
                    fail_streak += 1
                    print(f"[!] 答题出错: {e}")
                    if fail_streak >= 3:
                        print("[*] 连续出错，退出自动答题。")
                        break
                    await asyncio.sleep(2)
                    continue

                print(f"{'=' * 50}")

        except KeyboardInterrupt:
            print("\n[*] 用户中断")
        finally:
            await self.browser.close()
            print(f"\n[*] 共作答 {question_count} 题，程序退出。")

    def _answer_browser(self):
        """浏览器模式：从 DOM 提取题目 → LLM → 浏览器内点击"""
        print("\n" + "=" * 50)
        print("[*] 浏览器模式答题...")

        try:
            # 1. 注入标记
            self._run_async(self.browser.inject_answer_markers())

            # 2. 提取 DOM 内容
            print("[1/3] 从浏览器提取题目...")
            q = self._run_async(self.browser.extract_question())
            if not q or not q.get("text"):
                print("[!] 未能从页面提取题目，请确认考试页面是当前标签页")
                return

            # 3. 组装题面发给 LLM
            prompt = self._build_question_prompt(q)
            print(f"[2/3] 大模型思考中...")
            result = self.llm.ask_text(prompt)

            answer = result.get("answer", "").strip()
            explanation = result.get("explanation", "")
            print(f"  -> 答案: {answer}")
            print(f"  -> 解析: {explanation[:150]}")

            if not answer:
                print("[!] 大模型未能返回有效答案")
                return

            # 4. 在浏览器中点击答案
            print(f"[3/3] 点击答案: {answer}")
            ok = self._run_async(self.browser.click_option(answer, q))
            if not ok:
                print(f"[!] 未能自动点击选项 '{answer}'，请手动选择")

            # 清理标记
            self._run_async(self.browser.inject_answer_markers())

            if self.auto_next:
                time.sleep(self.click_delay)
                self._run_async(self.browser.click_next())

            print("[+] 答题完成!")
        except Exception as e:
            print(f"[!] 答题出错: {e}")
            import traceback
            traceback.print_exc()

        print("=" * 50 + "\n")

    def _build_question_prompt(self, q: dict) -> str:
        """组装给 LLM 的题面"""
        parts = [f"题目: {q.get('text', '')}"]
        opts = q.get("options", [])
        if opts:
            parts.append("选项:")
            for o in opts:
                parts.append(f"  {o['label']}. {o['text']}")
        qtype = q.get("type", "")
        if qtype:
            parts.append(f"题型: {qtype}")
        return "\n".join(parts)

    def _run_async(self, coro):
        """同步方式运行异步协程"""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)
        # 在已有事件循环中，用 run_coroutine_threadsafe 或新线程
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result(timeout=30)

    # ==================== 截图模式 ====================

    def _run_screenshot_mode(self):
        """截图模式入口"""
        from capture import ScreenCapture

        ocr_lang = self.config["automation"].get("ocr_lang", ["ch_sim", "en"])
        self.capture = ScreenCapture(ocr_lang=ocr_lang)
        self.vision_mode = self.config["automation"].get("vision_mode", False)

        # 初始化截图区域
        region = self.config.get("capture", {}).get("region", [0, 0, 0, 0])
        if region is None or sum(region) == 0:
            self.capture.select_region()
            left, top, w, h = self.capture.region
            # 回写配置
            with open(self.config_path, "r", encoding="utf-8") as f:
                content = f.read()
            import re
            content = re.sub(r"region:\s*\[[^\]]*\]",
                             f"region: [{left}, {top}, {w}, {h}]", content)
            with open(self.config_path, "w", encoding="utf-8") as f:
                f.write(content)
        else:
            self.capture.region = tuple(region)

        print(f"""
╔══════════════════════════════════════╗
║       网课自动答题 v2.0 截图模式    ║
╠══════════════════════════════════════╣
║  LLM: {self.llm.provider:12s} model: {self.llm.model:18s} ║
║  模式: {'视觉模式' if self.vision_mode else 'OCR文本模式':10s}                  ║
║  截图区域: {str(self.capture.region):30s} ║
╠══════════════════════════════════════╣
║  按 {self.hotkey:22s} 触发答题 ║
║  按 Ctrl+Shift+Q        退出       ║
╚══════════════════════════════════════╝
        """)

        print("[*] 正在加载 OCR 引擎...")
        _ = self.capture.ocr
        print("[+] OCR 引擎就绪")

        self._answer_handler = self._answer_screenshot
        self._setup_hotkeys()
        print("[*] 监听中，等待热键...")

    def _answer_screenshot(self):
        """截图模式答题流程"""
        import pyautogui

        print("\n" + "=" * 50)
        print("[*] 截图模式答题...")

        try:
            capture_delay = self.config.get("capture", {}).get("capture_delay", 0.3)
            image = self.capture.capture_delay(capture_delay)
            print(f"  -> 截图: {image.size}")

            ocr_results = self.capture.ocr_detect(image)
            ocr_text = "\n".join([d["text"] for d in ocr_results])
            print(f"  -> OCR 识别 {len(ocr_results)} 段文字")

            if self.vision_mode:
                result = self.llm.ask_vision(ocr_text, image)
            else:
                result = self.llm.ask_text(ocr_text)

            answer = result.get("answer", "").strip()
            explanation = result.get("explanation", "")
            print(f"  -> 答案: {answer}  |  {explanation[:100]}")

            if not answer:
                print("[!] 大模型未能返回有效答案")
                return

            click_pos = self._locate_answer(ocr_results, answer, image)
            if click_pos:
                sx = self.capture.region[0] + click_pos[0]
                sy = self.capture.region[1] + click_pos[1]
                print(f"  -> 点击 @ ({sx}, {sy})")
                pyautogui.click(sx, sy)
                time.sleep(self.click_delay)

                if self.auto_next:
                    self._click_next()
                print("[+] 答题完成!")
            else:
                print(f"[!] 无法定位选项 '{answer}'，请手动选择")

        except Exception as e:
            print(f"[!] 答题出错: {e}")
            import traceback
            traceback.print_exc()

        print("=" * 50 + "\n")

    def _locate_answer(self, ocr_results, answer, image):
        """在 OCR 结果中定位答案位置"""
        pos = self.capture.find_text_position(ocr_results, answer)
        if pos:
            return pos
        if len(answer) == 1 and answer.isalpha():
            for fmt in [f"{answer}.", f"{answer}、", f"({answer})", f"{answer})"]:
                pos = self.capture.find_text_position(ocr_results, fmt)
                if pos:
                    return pos
        if len(answer) > 1:
            for d in ocr_results:
                if answer[:10] in d["text"] or d["text"][:10] in answer:
                    return d["center"]
        try:
            import cv2, numpy as np
            from PIL import ImageDraw, ImageFont
            font = ImageFont.load_default()
            dummy = Image.new("RGB", (1, 1))
            draw = ImageDraw.Draw(dummy)
            bbox = draw.textbbox((0, 0), answer, font=font)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            template = Image.new("RGB", (tw + 10, th + 10), color="white")
            draw = ImageDraw.Draw(template)
            draw.text((5, 5), answer, fill="black", font=font)
            result = cv2.matchTemplate(np.array(image), np.array(template), cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(result)
            if max_val > 0.6:
                return (max_loc[0] + tw // 2, max_loc[1] + th // 2)
        except Exception:
            pass
        return None

    def _click_next(self):
        import pyautogui
        image = self.capture.capture_delay(0.3)
        ocr_results = self.capture.ocr_detect(image)
        for kw in ["下一题", "下一頁", "next", ">"]:
            pos = self.capture.find_text_position(ocr_results, kw)
            if pos:
                pyautogui.click(self.capture.region[0] + pos[0],
                                self.capture.region[1] + pos[1])
                time.sleep(1)
                return

    # ==================== 热键监听 ====================

    def _setup_hotkeys(self):
        def _normalize(parts: list) -> set:
            aliases = {
                "ctrl": {"ctrl", "ctrl_l", "ctrl_r", "control"},
                "shift": {"shift", "shift_l", "shift_r"},
                "alt": {"alt", "alt_l", "alt_r"},
                "cmd": {"cmd", "cmd_l", "cmd_r", "win", "win_l", "win_r", "super"},
            }
            result = set()
            for p in parts:
                p = p.lower().strip()
                found = False
                for master, alts in aliases.items():
                    if p in alts:
                        result.add(master)
                        found = True
                        break
                if not found:
                    result.add(p)
            return result

        hotkey_combo = _normalize(self.hotkey.lower().replace(" ", "").split("+"))
        quit_combo = _normalize(["ctrl", "shift", "q"])
        current = set()
        lock = threading.Lock()

        def _key_name(key):
            try:
                if hasattr(key, 'name') and key.name:
                    return key.name.lower()
                if hasattr(key, 'char') and key.char:
                    return key.char.lower()
            except Exception:
                pass
            return ""

        def on_press(key):
            with lock:
                name = _key_name(key)
                if not name:
                    return
                current.add(name)
                normalized = _normalize(list(current))

                if normalized >= quit_combo:
                    print("\n[*] 退出...")
                    self.running = False
                    self._cleanup()
                    return False

                if normalized >= hotkey_combo:
                    if not self.running:
                        return False
                    if self._hotkey_triggered:
                        return
                    self._hotkey_triggered = True
                    threading.Thread(target=self._safe_dispatch, daemon=True).start()

        def on_release(key):
            with lock:
                name = _key_name(key)
                if not name:
                    return
                current.discard(name)
                if not (_normalize(list(current)) & hotkey_combo):
                    self._hotkey_triggered = False

        listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        listener.start()
        listener.join()

    def _safe_dispatch(self):
        time.sleep(0.3)
        self._answer_handler()

    def _cleanup(self):
        """截图模式退出时清理（浏览器模式在 _auto_answer_loop 的 finally 中清理）"""
        if self.mode == "browser":
            return  # 浏览器模式由 _auto_answer_loop 的 finally 处理


if __name__ == "__main__":
    config_path = sys.argv[1] if len(sys.argv) > 1 else None
    app = AutoAnswer(config_path)
    app.run()
