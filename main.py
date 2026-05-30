#!/usr/bin/env python
"""
网课自动答题脚本 - CDP 直连浏览器提取 DOM → 分题型提示词 → 大模型作答 → 浏览器内点击
"""
import sys
import asyncio
import yaml
from pathlib import Path

from llm_client import LLMClient


class AutoAnswer:
    """自动答题主控制器"""

    def __init__(self, config_path: str = None):
        if config_path is None:
            config_path = Path(__file__).parent / "config.yaml"
        self.config = self._load_config(config_path)
        self.llm = LLMClient(self.config["llm"])
        self.click_delay = self.config["automation"].get("click_delay", 0.5)

    def _load_config(self, path: str) -> dict:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    def run(self):
        api_key = self.config["llm"].get("api_key", "")
        if not api_key or "your-api-key" in api_key:
            print("[!] 请先在 config.yaml 中填入你的 API Key")
            sys.exit(1)

        self._run_browser_mode()

    def _run_browser_mode(self):
        from browser import BrowserController

        browser_cfg = self.config.get("browser", {})
        self.browser = BrowserController(browser_cfg)

        print(f"""
╔══════════════════════════════════════╗
║         网课自动答题 浏览器模式     ║
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

    # ==================== 主循环 ====================

    async def _auto_answer_loop(self):
        """预提取所有题目 → 逐题分类型作答 → 点击 → 下一题"""
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

        all_questions = await self.browser.extract_all_questions()
        if not all_questions:
            print("[!] 未能从页面提取到任何题目")
            await self.browser.close()
            return

        total = len(all_questions)
        print(f"\n[+] 共发现 {total} 道题目，开始逐题作答...")
        answered_count = 0

        try:
            for i in range(total):
                print(f"\n{'=' * 50}")
                print(f"[*] 第 {i + 1}/{total} 题")

                try:
                    q = await self.browser.scroll_to_question(i)
                    if not q or not q.get("text"):
                        print("[!] 当前题目数据异常，跳过")
                        await self.browser.click_next()
                        await asyncio.sleep(1)
                        continue

                    qtype = q.get("type", "single_choice")

                    # 分题型构建提示词
                    if qtype == "true_false":
                        system_prompt, user_message = self._build_true_false_prompt(q)
                    elif qtype in ("single_choice", "multiple_choice"):
                        system_prompt, user_message = self._build_choice_prompt(q)
                    elif qtype == "fill_blank":
                        system_prompt, user_message = self._build_fill_blank_prompt(q)
                    else:
                        system_prompt, user_message = self._build_choice_prompt(q)

                    print(f"[*] 题型: {qtype} | 大模型思考中...")
                    result = self.llm.ask_text(system_prompt, user_message)

                    answer = result.get("answer", "").strip()
                    explanation = result.get("explanation", "")
                    print(f"  -> 答案: {answer}")
                    print(f"  -> 解析: {explanation[:150]}")

                    if not answer:
                        print("[!] 大模型未能返回有效答案，跳过此题")
                        await self.browser.click_next()
                        await asyncio.sleep(1)
                        continue

                    await asyncio.sleep(self.click_delay)
                    ok = await self.browser.click_option(answer, q)
                    if ok:
                        print(f"[+] 已选择答案: {answer}")
                        answered_count += 1
                    else:
                        print(f"[!] 未能自动点击选项 '{answer}'")

                    await asyncio.sleep(1)
                    next_ok = await self.browser.click_next()
                    if not next_ok:
                        print("[*] 已是最后一题")
                        break

                    await asyncio.sleep(1.5)

                except Exception as e:
                    print(f"[!] 答题出错: {e}")
                    await self.browser.click_next()
                    await asyncio.sleep(1)
                    continue

        except KeyboardInterrupt:
            print("\n[*] 用户中断")
        finally:
            await self.browser.close()
            print(f"\n[*] 共 {total} 题，成功作答 {answered_count} 题，程序退出。")

    # ==================== 分题型提示词 ====================

    def _build_true_false_prompt(self, q: dict) -> tuple:
        """
        判断题提示词：只发送题目文字，告知 LLM "A=对 B=错"
        返回 (system_prompt, user_message)
        """
        system_prompt = (
            "你是一个考试答题助手。对于判断题：\n"
            "- A 表示正确（对）\n"
            "- B 表示错误（错）\n"
            "请仔细分析题目，只返回正确答案的字母（A 或 B），并附简要解析。"
        )
        user_message = (
            f"题目: {q.get('text', '')}\n\n"
            "请以 JSON 格式返回答案（只返回 JSON，不要其他内容）：\n"
            '{"answer": "A", "explanation": "简要解析", "question_type": "true_false"}'
        )
        return system_prompt, user_message

    def _build_choice_prompt(self, q: dict) -> tuple:
        """
        单选/多选题提示词：发送题目文字 + 全部选项
        返回 (system_prompt, user_message)
        """
        qtype = q.get("type", "single_choice")
        system_prompt = (
            "你是一个考试答题助手。请根据题目内容和所有选项，选出正确答案。"
        )

        parts = [f"题目: {q.get('text', '')}"]
        opts = q.get("options", [])
        if opts:
            parts.append("选项:")
            for o in opts:
                parts.append(f"  {o['label']}. {o['text']}")

        type_label = "单选题" if qtype == "single_choice" else "多选题"
        parts.append(f"\n题型: {type_label}")

        # 输出格式说明
        if qtype == "single_choice":
            parts.append("\n请以 JSON 格式返回答案（只返回 JSON，不要其他内容）：")
            parts.append('{"answer": "A", "explanation": "简要解析", "question_type": "single_choice"}')
            parts.append("注意：单选题 answer 为单个字母，如 A")
        else:
            parts.append("\n请以 JSON 格式返回答案（只返回 JSON，不要其他内容）：")
            parts.append('{"answer": "AB", "explanation": "简要解析", "question_type": "multiple_choice"}')
            parts.append("注意：多选题 answer 为多个字母连在一起，如 AB 或 ACD")

        return system_prompt, "\n".join(parts)

    def _build_fill_blank_prompt(self, q: dict) -> tuple:
        """
        填空题提示词：发送题目文字，要求填空答案
        返回 (system_prompt, user_message)
        """
        system_prompt = "你是一个考试答题助手。请根据题目内容，给出填空题的正确答案。"
        user_message = (
            f"题目: {q.get('text', '')}\n\n"
            "请以 JSON 格式返回答案（只返回 JSON，不要其他内容）：\n"
            '{"answer": "填空答案", "explanation": "简要解析", "question_type": "fill_blank"}'
        )
        return system_prompt, user_message


if __name__ == "__main__":
    config_path = sys.argv[1] if len(sys.argv) > 1 else None
    app = AutoAnswer(config_path)
    app.run()
