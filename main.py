#!/usr/bin/env python
"""
网课自动答题脚本
通过 CDP 直连浏览器 → 注入JS提取DOM题目 → 分题型构建LLM提示词 → 大模型作答 → 浏览器内点击
"""
import sys
import re
import asyncio
from asyncio.windows_events import NULL

import yaml
from pathlib import Path
from typing import Optional

from llm_client import LLMClient


class AutoAnswer:
    """
    自动答题主控制器
    负责：加载配置 → 校验API Key → 启动浏览器模式 → 逐题循环作答
    """

    # 所有题型共用的大模型返回格式提示，避免在三个builder中重复
    _JSON_FORMAT_HINT = '请以 JSON 格式返回答案（只返回 JSON，不要其他内容）：'

    def __init__(self ):
        self.config_path = None
        # 未指定路径时自动推断：PyInstaller frozen 模式取 exe 同目录，开发模式取脚本同目录
        if self.config_path is None:
            if getattr(sys, 'frozen', False):
                # PyInstaller 打包后 sys.executable 是 exe 路径，config.yaml 应放在同目录
                config_path = Path(sys.executable).parent / "config.yaml"
            else:
                # 开发模式：脚本同目录
                config_path = Path(__file__).parent / "config.yaml"
        # 保存配置路径，后续写回 API Key 时需要
        self._config_path = config_path
        # 加载并解析YAML配置
        self.config = self._load_config(config_path)
        # 创建大模型客户端，传入 llm 配置段
        self.llm = LLMClient(self.config["llm"])
        # 从 automation 段读取点击后延迟秒数，默认0.5秒
        self.click_delay = self.config["automation"].get("click_delay", 0.5)

    # 安全加载YAML配置文件，如果文件不存在则打印友好提示
    # Args:
    #     path: 配置文件路径
    # Returns: 解析后的配置字典
    def _load_config(self, path: str) -> dict:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f)
        except FileNotFoundError:
            # PyInstaller 打包后文件可能被移走，打印当前所需路径供排查
            print(f"[!] 找不到配置文件: {path}")
            print(f"    请确保 config.yaml 与 {Path(sys.executable).name} 放在同一个文件夹中")
            sys.exit(1)

    # ==================== 程序入口 ====================

    # 主入口：校验 API Key，首次使用自动提示输入，然后启动浏览器模式
    def run(self):
        # 读取 api_key 字段，检查是否填写了有效的 Key
        api_key = self.config["llm"].get("api_key", "")
        if not api_key:
            # 首次使用：无需用户手动编辑配置文件，直接在控制台粘贴 Key
            print("=" * 50)
            print("  首次使用：请先获取 API Key")
            print("  推荐 DeepSeek：https://platform.deepseek.com")
            print("  注册后在 API Keys 页面创建 Key 并复制")
            print("=" * 50)
            # 等待用户粘贴 API Key
            api_key = input("\n请输入你的 API Key（右键粘贴后按回车）: ").strip()
            if not api_key:
                # 用户直接回车跳过，视为放弃
                print("[!] 未输入 API Key，程序退出")
                sys.exit(1)
            # 将 Key 写回 config.yaml，下次启动无需再输
            self._save_api_key(api_key)
            # 更新内存中的配置
            self.config["llm"]["api_key"] = api_key
            # 用新 Key 重建 LLM 客户端
            self.llm = LLMClient(self.config["llm"])
            print("[+] API Key 已保存，下次启动无需再输\n")

        # 当前仅支持浏览器模式，直接启动
        self._run_browser_mode()

    # 将用户输入的 API Key 写回 config.yaml 文件（用正则替换避免破坏注释和格式）
    # Args:
    #     api_key: 用户粘贴的 API Key 字符串
    def _save_api_key(self, api_key: str):
        with open(self._config_path, "r", encoding="utf-8") as f:
            content = f.read()
        # 匹配 api_key: "任意内容" 或 api_key: ''，替换为新的 Key
        content = re.sub(r'(api_key:\s*")[^"]*(")', f'\\1{api_key}\\2', content)
        content = re.sub(r"(api_key:\s*')[^']*(')", f'\\1{api_key}\\2', content)
        with open(self._config_path, "w", encoding="utf-8") as f:
            f.write(content)

    # 初始化 BrowserController，打印启动横幅，并通过 asyncio.run() 启动异步答题循环
    def _run_browser_mode(self):
        from browser import BrowserController

        # 从配置中读取 browser 段（CDP地址、URL匹配规则等）
        browser_cfg = self.config.get("browser", {})
        # 创建浏览器控制器实例
        self.browser = BrowserController(browser_cfg)

        # 打印启动横幅，显示LLM和CDP连接信息
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
        # asyncio.run() 启动异步事件循环，执行主答题流程
        asyncio.run(self._auto_answer_loop())

    # ==================== 答题主循环 ====================

    # 异步主循环：连接浏览器 → 提取所有题 → 逐题作答 → 点击下一题 → 清理
    async def _auto_answer_loop(self):
        # ---- 阶段1: 连接浏览器 ----
        try:
            await self.browser.connect()
        except Exception as e:
            # 连接失败时打印错误和恢复指引
            print(f"[!] 连接失败: {e}")
            browser_type = self.config.get("browser", {}).get("browser_type", "edge")
            exe = "msedge.exe" if browser_type == "edge" else "chrome.exe"
            # frozen 模式下显示 exe 文件名，开发模式下显示 python 命令
            if getattr(sys, 'frozen', False):
                rerun_cmd = Path(sys.executable).name
            else:
                rerun_cmd = f"python {Path(__file__).name}"
            print(f"""
请按以下步骤操作:
  1. 完全关闭所有 {browser_type.upper()} 窗口
  2. 打开任务管理器，确认没有残留进程，有就结束掉
  3. Win+R 运行: {exe} --remote-debugging-port=9222
  4. 在浏览器中打开考试页面并登录
  5. 重新运行: {rerun_cmd}
""")
            return  # 连接失败直接退出

        # ---- 阶段2: 一次性从页面提取所有题目 ----
        all_questions = await self.browser.extract_all_questions()
        if not all_questions:
            print("[!] 未能从页面提取到任何题目")
            await self.browser.close()  # 释放资源后退出
            return

        total = len(all_questions)
        print(f"\n[+] 共发现 {total} 道题目，开始逐题作答...")
        answered_count = 0  # 成功作答计数器

        # ---- 阶段3: 逐题循环（滚动→识别→LLM作答→点击→下一题） ----
        try:
            for i in range(total):
                print(f"\n{'=' * 50}")
                print(f"[*] 第 {i + 1}/{total} 题")

                try:
                    # 滚动到第i题并获取缓存数据
                    q = await self.browser.scroll_to_question(i)
                    # 检查题目数据完整性（至少要有题面文字）
                    if not q or not q.get("text"):
                        print("[!] 当前题目数据异常，跳过")
                        await self.browser.click_next()  # 直接跳到下一题
                        await asyncio.sleep(1)
                        continue

                    # 获取题型，用于选择对应的提示词构建方法
                    qtype = q.get("type", "single_choice")

                    # 根据题型分派到不同的提示词构建器：
                    #   判断题 → 只发题目文字，告知LLM "A=对 B=错"
                    #   填空题 → 只发题目文字，要求返回填空答案
                    #   单选/多选题 → 发题目文字 + 全部选项列表
                    if qtype == "true_false":
                        system_prompt, user_message = self._build_true_false_prompt(q)
                    elif qtype == "fill_blank":
                        system_prompt, user_message = self._build_fill_blank_prompt(q)
                    else:
                        # 单选/多选/未知类型统一走选择题提示词
                        # 该方法内部会根据qtype区分单选格式(A)和多选格式(AB)
                        system_prompt, user_message = self._build_choice_prompt(q, qtype)

                    # 调用大模型获取答案
                    print(f"[*] 题型: {qtype} | 大模型思考中...")
                    result = self.llm.ask_text(system_prompt, user_message)

                    # 从LLM返回的JSON中取出答案字母和解析文字
                    answer = result.get("answer", "").strip()
                    explanation = result.get("explanation", "")
                    print(f"  -> 答案: {answer}")
                    print(f"  -> 解析: {explanation[:150]}")  # 截取前150字符避免刷屏

                    # LLM未能返回有效答案，跳过此题
                    if not answer:
                        print("[!] 大模型未能返回有效答案，跳过此题")
                        await self.browser.click_next()
                        await asyncio.sleep(1)
                        continue

                    # 等待click_delay秒后点击选项（避免操作过快被网站识别）
                    await asyncio.sleep(self.click_delay)
                    ok = await self.browser.click_option(answer, q)
                    if ok:
                        print(f"[+] 已选择答案: {answer}")
                        answered_count += 1  # 成功计数+1
                    else:
                        print(f"[!] 未能自动点击选项 '{answer}'")

                    # 点击后等待1秒，然后切到下一题
                    await asyncio.sleep(1)
                    next_ok = await self.browser.click_next()
                    if not next_ok:
                        print("[*] 已是最后一题")
                        break  # 没有下一题了，退出循环

                    # 题间暂停1.5秒，避免操作过快
                    await asyncio.sleep(1.5)

                except Exception as e:
                    # 单题出错不崩溃，跳过继续
                    print(f"[!] 答题出错: {e}")
                    await self.browser.click_next()
                    await asyncio.sleep(1)
                    continue

        except KeyboardInterrupt:
            # 用户按 Ctrl+C 手动终止
            print("\n[*] 用户中断")
        finally:
            # 释放Playwright资源并打印统计
            await self.browser.close()
            print(f"\n[*] 共 {total} 题，成功作答 {answered_count} 题，程序退出。")

    # ==================== 分题型提示词构建 ====================

    # 构建判断题提示词：仅发送题目文字，告知LLM "A=对 B=错"
    # 判断题不发送选项列表，因为选项结构是固定的（只有正确/错误两项）
    # Args:
    #     q: 题目dict，至少包含 'text' 字段
    # Returns: (system_prompt, user_message) 元组
    def _build_true_false_prompt(self, q: dict) -> tuple:
        # 系统提示词：明确告知判断题规则（A=正确，B=错误）
        system_prompt = (
            "你是一个考试答题助手。对于判断题：\n"
            "- A 表示正确（对）\n"
            "- B 表示错误（错）\n"
            "请仔细分析题目，只返回正确答案的字母（A 或 B），并附简要解析。"
        )
        # 用户消息：仅包含题目文字，不附带选项（判断题选项固定）
        user_message = (
            f"题目: {q.get('text', '')}\n\n"
            f"{self._JSON_FORMAT_HINT}\n"
            '{"answer": "A", "explanation": "简要解析", "question_type": "true_false"}'
        )
        return system_prompt, user_message

    # 构建选择题提示词：发送题目文字 + 全部选项，单选返回单个字母，多选返回多个字母连写
    # Args:
    #     q: 题目dict，包含 'text' 和 'options' 字段
    #     qtype: 题型字符串，"single_choice" 或 "multiple_choice"
    # Returns: (system_prompt, user_message) 元组
    def _build_choice_prompt(self, q: dict, qtype: str) -> tuple:
        # 系统提示词：通用答题角色
        system_prompt = "你是一个考试答题助手。请根据题目内容和所有选项，选出正确答案。"

        # ---- 组装用户消息：题面 + 选项列表 + 题型说明 + 格式要求 ----
        parts = [f"题目: {q.get('text', '')}"]

        # 添加选项列表（A/B/C/D）
        opts = q.get("options", [])
        if opts:
            parts.append("选项:")
            for o in opts:
                parts.append(f"  {o['label']}. {o['text']}")

        # 题型标签（用于LLM理解题目要求）
        type_label = "单选题" if qtype == "single_choice" else "多选题"
        parts.append(f"\n题型: {type_label}")

        # 通用JSON格式提示
        parts.append(f"\n{self._JSON_FORMAT_HINT}")

        # 根据单选/多选给出不同的JSON模板和说明
        if qtype == "single_choice":
            # 单选题：answer字段为单个字母，如 "A"
            parts.append('{"answer": "A", "explanation": "简要解析", "question_type": "single_choice"}')
            parts.append("注意：单选题 answer 为单个字母，如 A")
        else:
            # 多选题：answer字段为多个字母连在一起，如 "AB" 或 "ACD"
            parts.append('{"answer": "AB", "explanation": "简要解析", "question_type": "multiple_choice"}')
            parts.append("注意：多选题 answer 为多个字母连在一起，如 AB 或 ACD")

        # 将所有片段用换行符连接成完整消息
        return system_prompt, "\n".join(parts)

    # 构建填空题提示词：仅发送题目文字，要求LLM直接给出填空答案
    # Args:
    #     q: 题目dict，至少包含 'text' 字段
    # Returns: (system_prompt, user_message) 元组
    def _build_fill_blank_prompt(self, q: dict) -> tuple:
        system_prompt = "你是一个考试答题助手。请根据题目内容，给出填空题的正确答案。"
        user_message = (
            f"题目: {q.get('text', '')}\n\n"
            f"{self._JSON_FORMAT_HINT}\n"
            '{"answer": "填空答案", "explanation": "简要解析", "question_type": "fill_blank"}'
        )
        return system_prompt, user_message


if __name__ == "__main__":
    # 从命令行参数读取配置文件路径（可选）

    app = AutoAnswer()
    app.run()
