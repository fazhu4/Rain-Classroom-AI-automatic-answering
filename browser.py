"""
浏览器控制模块 - 通过 Playwright 直连 Chrome，读取 DOM 并自动答题
"""
import re
import time
import asyncio


class BrowserController:
    """Playwright 浏览器控制器"""

    def __init__(self, config: dict):
        self.cdp_url = config.get("cdp_url", "http://localhost:9222")
        self.exam_url_pattern = config.get("exam_url_pattern", "yuketang.cn/exam")
        self.question_selector = config.get("question_selector", "")
        self.option_selector = config.get("option_selector", "")
        self.next_selector = config.get("next_selector", "")
        self._playwright = None
        self._browser = None
        self._page = None

    async def connect(self):
        """连接到已有浏览器（需先用 --remote-debugging-port=9222 启动浏览器）"""
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        try:
            self._browser = await self._playwright.chromium.connect_over_cdp(self.cdp_url)
            print(f"[+] 已连接到浏览器 (CDP: {self.cdp_url})")
        except Exception as e:
            # 不要在这里 stop playwright，外层会处理
            raise

        # 找到考试页面
        await self._find_exam_page()
        return self._page is not None

    async def _find_exam_page(self):
        """在所有标签页中定位考试页面"""
        contexts = self._browser.contexts
        for ctx in contexts:
            for page in ctx.pages:
                if re.search(self.exam_url_pattern, page.url):
                    self._page = page
                    await self._page.bring_to_front()
                    print(f"[+] 找到考试页面: {page.url[:80]}")
                    return

        # 没找到，列出所有页面让用户选
        print("[!] 未自动匹配到考试页面，当前打开的标签页:")
        all_pages = []
        for ctx in contexts:
            all_pages.extend(ctx.pages)
        for i, page in enumerate(all_pages):
            print(f"  [{i}] {page.url[:100]}")
        if all_pages:
            idx = int(input("请选择考试页面序号: "))
            self._page = all_pages[idx]
            await self._page.bring_to_front()

    async def extract_question(self) -> dict:
        """
        从 DOM 中提取题目内容
        返回: {"text": "完整题面", "options": [{"label": "A", "text": "...", "element": ...}], "type": "..."}
        """
        if not self._page:
            return {}

        content = await self._page.evaluate(self._EXTRACT_SCRIPT)
        if not content or not content.get("text"):
            return {}

        print(f"  -> 提取到题目: {content['text'][:100]}...")
        for opt in content.get("options", []):
            print(f"     {opt['label']}. {opt['text'][:60]}")
        return content

    async def click_option(self, label: str, question_data: dict):
        """在浏览器中点击指定选项"""
        options = question_data.get("options", [])

        # 方法1: 用存储的 DOM 路径点击
        for opt in options:
            if opt["label"].strip().upper() == label.strip().upper():
                elem = await self._page.evaluate_handle(
                    f'document.querySelector("[data-answer-index=\'{opt["index"]}\']")'
                )
                if await elem.as_element():
                    await elem.as_element().click()
                    print(f"  -> 点击选项 {label}")
                    return True

        # 方法2: 用文本匹配点击（兜底）
        for opt in options:
            if opt["label"].strip().upper() == label.strip().upper():
                text = opt.get("text", "")
                try:
                    # 用文本内容定位可点击元素
                    elem = self._page.get_by_text(text, exact=False).first
                    if elem:
                        await elem.click()
                        print(f"  -> 文本匹配点击选项 {label}")
                        return True
                except Exception:
                    pass

        # 方法3: 用 aria-label 或 title 匹配
        try:
            await self._page.click(f'[aria-label*="{label}"]')
            return True
        except Exception:
            pass

        return False

    async def click_next(self):
        """点击下一题按钮"""
        if self.next_selector:
            try:
                await self._page.click(self.next_selector, timeout=3000)
                return True
            except Exception:
                pass

        # 通用匹配
        next_texts = ["下一题", "下一頁", "next", ">", "提交"]
        for t in next_texts:
            try:
                btn = self._page.get_by_text(t, exact=True).first
                if btn:
                    await btn.click(timeout=2000)
                    time.sleep(1)
                    return True
            except Exception:
                continue
        return False

    async def inject_answer_markers(self):
        """
        在页面中注入标记，给每个选项加上 data-answer-index 属性，
        方便后续直接通过索引点击
        """
        await self._page.evaluate("""
        () => {
            // 清除旧标记
            document.querySelectorAll('[data-answer-index]').forEach(el => {
                el.removeAttribute('data-answer-index');
            });

            // 查找所有选项容器并标记
            const labels = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H',
                           '正确', '错误', '对', '错', 'True', 'False', 'Y', 'N'];

            // 遍历所有可点击元素，寻找选项
            const allEls = document.querySelectorAll('label, li, div[class*="option"], div[class*="choice"], div[class*="answer"], span[class*="option"]');
            allEls.forEach(el => {
                const text = el.textContent?.trim() || '';
                for (const label of labels) {
                    if (text.startsWith(label + '.') || text.startsWith(label + '、') ||
                        text.startsWith(label + ')') || text.startsWith(label + '）') ||
                        text.startsWith('(' + label + ')') || text === label) {
                        el.setAttribute('data-answer-index', label);
                        break;
                    }
                }
            });

            // 如果没找到，尝试找 radio/checkbox 的 label
            if (document.querySelectorAll('[data-answer-index]').length === 0) {
                document.querySelectorAll('input[type="radio"], input[type="checkbox"]').forEach(input => {
                    const label = input.closest('label') || input.parentElement;
                    const text = label?.textContent?.trim() || '';
                    for (const l of labels) {
                        if (text.includes(l + '.') || text.includes('(' + l + ')') || text.startsWith(l)) {
                            label?.setAttribute('data-answer-index', l);
                            break;
                        }
                    }
                });
            }

            return document.querySelectorAll('[data-answer-index]').length;
        }
        """)

    async def close(self):
        if self._playwright:
            await self._playwright.stop()

    # ---- DOM 提取脚本 ----
    _EXTRACT_SCRIPT = """
    () => {
        const result = { text: '', options: [], type: 'single_choice' };

        // ---- 检测题型 ----
        const body = document.body.innerText || '';
        if (body.includes('多选题') || body.includes('多选')) result.type = 'multiple_choice';
        if (body.includes('判断题') || body.includes('判断')) result.type = 'true_false';
        if (body.includes('填空题') || body.includes('填空')) result.type = 'fill_blank';

        // ---- 提取题目正文 ----
        // 策略: 找包含题目编号的元素（如 "1."、"第1题"）
        const allText = document.body.innerText || '';

        // 尝试找最可能包含题目的容器
        let contentArea = document.querySelector('.exam-main--content')
            || document.querySelector('[class*="exam-content"]')
            || document.querySelector('[class*="question"]')
            || document.querySelector('main')
            || document.querySelector('article')
            || document.body;

        result.text = (contentArea.innerText || allText).substring(0, 2000);

        // ---- 提取选项 ----
        const labels = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H'];
        const optionPatterns = [];
        for (const label of labels) {
            optionPatterns.push(
                label + '.', label + '、', label + ')', label + '）',
                '(' + label + ')', ' ' + label + ' ', label + '．'
            );
        }

        // 在正文中按模式拆分选项
        let lastIdx = -1;
        let lastLabel = '';
        for (const pattern of optionPatterns) {
            const idx = result.text.indexOf(pattern);
            if (idx > 0 && (lastIdx < 0 || idx < lastIdx)) {
                lastIdx = idx;
                lastLabel = pattern[0];
            }
        }

        if (lastIdx > 0) {
            // 题目正文在第一个选项之前
            const questionText = result.text.substring(0, lastIdx).trim();

            // 如果是判断题
            if (/[是正][确確]|[错误]|[对錯]|[TF]|true|false/i.test(questionText)) {
                result.type = 'true_false';
            }

            // 拆分选项
            const optionsText = result.text.substring(lastIdx);
            const parts = optionsText.split(/([A-H][.、)）．])/);
            let currentLabel = '';
            for (const part of parts) {
                const stripped = part.trim();
                if (labels.includes(stripped[0]) && stripped.length <= 2) {
                    currentLabel = stripped[0];
                } else if (currentLabel && stripped) {
                    result.options.push({
                        label: currentLabel,
                        text: stripped.substring(0, 200)
                    });
                    currentLabel = '';
                }
            }
        }

        // 如果没找到字母选项，尝试判断题格式
        if (result.options.length === 0) {
            if (result.text.includes('正确') || result.text.includes('错误') || result.text.includes('对') || result.text.includes('错')) {
                result.type = 'true_false';
                result.options = [
                    { label: 'A', text: '正确' },
                    { label: 'B', text: '错误' }
                ];
            }
        }

        return result;
    }
    """
