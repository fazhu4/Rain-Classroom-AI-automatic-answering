#!/usr/bin/env python
"""
网课自动答题脚本 - CDP 直连浏览器读取 DOM → 大模型作答 → 浏览器内点击
"""
import sys             # 系统交互：退出程序
import asyncio         # 异步IO：Playwright 浏览器操作用 asyncio 事件循环
import yaml            # YAML 解析：读取 config.yaml 配置文件
from pathlib import Path  # 路径处理：定位项目目录下的默认配置文件

from llm_client import LLMClient  # 大模型 API 客户端


class AutoAnswer:
    """自动答题主控制器：配置加载 → 连接浏览器 → 循环答题"""

    # 初始化控制器，加载配置文件、创建LLM客户端。
    # config_path: YAML配置文件路径（可选，默认项目目录下config.yaml）
    def __init__(self, config_path: str = None):
        if config_path is None:
            config_path = Path(__file__).parent / "config.yaml"  # 默认取脚本同目录下的config.yaml
        self.config = self._load_config(config_path)              # 加载YAML配置为字典
        self.llm = LLMClient(self.config["llm"])                  # 创建LLM客户端（传入llm配置段）
        self.click_delay = self.config["automation"].get("click_delay", 0.5)  # 点击后延时（秒）
        self.running = True  # 运行状态标志

    # 读取YAML配置文件返回字典。path: 配置文件绝对路径
    def _load_config(self, path: str) -> dict:
        with open(path, "r", encoding="utf-8") as f:  # 以UTF-8编码打开（支持中文内容）
            return yaml.safe_load(f)                    # safe_load避免YAML代码注入风险

    # 程序入口：校验 API Key → 启动浏览器模式
    def run(self):
        api_key = self.config["llm"].get("api_key", "")
        if not api_key or "your-api-key" in api_key:      # 检查是否已配置真实 API Key
            print("[!] 请先在 config.yaml 中填入你的 API Key")
            sys.exit(1)

        self._run_browser_mode()  # 连接浏览器 → 自动循环答题

    # 浏览器模式入口：初始化 BrowserController → 连接浏览器 → 进入自动答题循环
    def _run_browser_mode(self):
        from browser import BrowserController  # 延迟导入，避免未安装playwright时导入报错

        browser_cfg = self.config.get("browser", {})          # 读取browser配置段
        self.browser = BrowserController(browser_cfg)         # 创建浏览器控制器实例

        # 打印启动横幅
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
        asyncio.run(self._auto_answer_loop())  # 启动唯一的事件循环：连接→答题→退出

    # 自动答题主循环（async协程）：连接浏览器 → 预提取所有题目 → 逐题LLM作答 → 点击选项 → 下一题。
    # 所有Playwright操作在同一事件循环中执行，避免跨循环对象失效
    async def _auto_answer_loop(self):
        # 连接浏览器（在同一事件循环中，Playwright对象不会失效）
        try:
            await self.browser.connect()  # CDP连接已有浏览器→自动定位考试标签页
        except Exception as e:
            print(f"[!] 连接失败: {e}")
            browser_type = self.config.get("browser", {}).get("browser_type", "edge")  # 读取浏览器类型
            exe = "msedge.exe" if browser_type == "edge" else "chrome.exe"              # 根据类型选exe名
            print(f"""
请按以下步骤操作:
  1. 完全关闭所有 {browser_type.upper()} 窗口
  2. 打开任务管理器，确认没有残留进程，有就结束掉
  3. Win+R 运行: {exe} --remote-debugging-port=9222
  4. 在浏览器中打开考试页面并登录
  5. 重新运行: python main.py
""")
            return  # 连接失败直接退出

        # 步骤1: 预提取页面中所有题目（从.subject-item容器逐个解析）
        all_questions = await self.browser.extract_all_questions()
        if not all_questions:
            print("[!] 未能从页面提取到任何题目，请确认考试页面已加载完毕")
            await self.browser.close()
            return

        total = len(all_questions)
        print(f"\n[+] 共发现 {total} 道题目，开始逐题作答...")
        answered_count = 0  # 已答题数计数器

        try:
            for i in range(total):
                print(f"\n{'=' * 50}")
                print(f"[*] 第 {i + 1}/{total} 题")

                try:
                    # 步骤2: 滚动到当前题目、注入答案标记、获取题目数据
                    q = await self.browser.prepare_current_question()
                    if not q or not q.get("text") or not q.get("options"):
                        print("[!] 当前题目数据异常，跳过")
                        await self.browser.click_next()  # 移动到下一题
                        await asyncio.sleep(1)
                        continue

                    # 步骤3: 将题目dict拼接为LLM可读的prompt文本，发送给大模型
                    prompt = self._build_question_prompt(q)  # 组装题面+选项+题型为文本
                    print(f"[*] 大模型思考中...")
                    result = self.llm.ask_text(prompt)       # 调用LLM API获取答案（同步阻塞）

                    answer = result.get("answer", "").strip()       # 提取答案选项字母
                    explanation = result.get("explanation", "")     # 提取解析文字
                    print(f"  -> 答案: {answer}")
                    print(f"  -> 解析: {explanation[:150]}")

                    if not answer:
                        print("[!] 大模型未能返回有效答案，跳过此题")
                        await self.browser.click_next()    # 没答案也跳下一题
                        await asyncio.sleep(1)
                        continue

                    # 步骤4: 在浏览器页面中点击LLM返回的选项
                    await asyncio.sleep(self.click_delay)           # 点击前短暂延时
                    ok = await self.browser.click_option(answer, q)  # 传入答案字母和题目数据
                    if ok:
                        print(f"[+] 已选择答案: {answer}")
                        answered_count += 1
                    else:
                        print(f"[!] 未能自动点击选项 '{answer}'")

                    # 步骤5: 移动到下一题
                    await asyncio.sleep(1)
                    next_ok = await self.browser.click_next()       # 滚动到下一题
                    if not next_ok:
                        print("[*] 已是最后一题")
                        break

                    await asyncio.sleep(1.5)  # 题目间短暂间隔

                except Exception as e:
                    print(f"[!] 答题出错: {e}")
                    # 单题异常不中断，继续下一题
                    await self.browser.click_next()
                    await asyncio.sleep(1)
                    continue

                print(f"{'=' * 50}")

        except KeyboardInterrupt:
            print("\n[*] 用户中断")  # Ctrl+C手动终止
        finally:
            await self.browser.close()  # 释放Playwright连接资源
            print(f"\n[*] 共 {total} 题，成功作答 {answered_count} 题，程序退出。")

    # 将题目dict拼接为LLM可读的prompt文本（题干+选项列表+题型标注）
    def _build_question_prompt(self, q: dict) -> str:
        parts = [f"题目: {q.get('text', '')}"]     # 题干行
        opts = q.get("options", [])
        if opts:
            parts.append("选项:")
            for o in opts:
                parts.append(f"  {o['label']}. {o['text']}")  # 拼为"  A. 选项内容"格式
        qtype = q.get("type", "")
        if qtype:
            parts.append(f"题型: {qtype}")          # 附加题型信息帮助LLM判断
        return "\n".join(parts)                      # 用换行符拼接所有行


if __name__ == "__main__":
    config_path = sys.argv[1] if len(sys.argv) > 1 else None  # 命令行可传入自定义配置文件路径
    app = AutoAnswer(config_path)                               # 创建主控制器实例
    app.run()                                                   # 启动程序
