"""
浏览器控制模块 - Playwright CDP 连接已有浏览器，注入JS提取DOM题目、点击选项
"""
import re
import asyncio


class BrowserController:
    """Playwright 浏览器控制器：连接→提取→点击"""

    def __init__(self, config: dict):
        self.cdp_url = config.get("cdp_url", "http://localhost:9222")
        self.exam_url_pattern = config.get("exam_url_pattern", "yuketang.cn/exam")
        self._playwright = None
        self._browser = None
        self._page = None
        self._all_questions = []
        self._current_index = 0

    async def connect(self):
        """通过CDP连接到已有浏览器实例"""
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        try:
            self._browser = await self._playwright.chromium.connect_over_cdp(self.cdp_url)
            print(f"[+] 已连接到浏览器 (CDP: {self.cdp_url})")
        except Exception:
            raise

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

    # ==================== 题目提取 ====================

    async def extract_all_questions(self) -> list:
        """从.subject-item容器逐个解析所有题目，缓存并返回"""
        if self._all_questions:
            return self._all_questions
        if not self._page:
            return []

        self._all_questions = await self._page.evaluate(self._EXTRACT_SCRIPT)

        if self._all_questions:
            print(f"[+] 从页面提取到 {len(self._all_questions)} 道题目")
            type_counts = {}
            for q in self._all_questions:
                t = q.get("type", "unknown")
                type_counts[t] = type_counts.get(t, 0) + 1
            print(f"    题型分布: {type_counts}")
        else:
            print("[!] 未能从页面提取到任何题目")

        return self._all_questions

    async def scroll_to_question(self, index: int) -> dict:
        """滚动到第index题，返回缓存中的题目dict"""
        if index >= len(self._all_questions):
            return {}

        await self._page.evaluate(f"""
            () => {{
                const items = document.querySelectorAll('.exam-main--content .subject-item');
                if (items[{index}]) {{
                    items[{index}].scrollIntoView({{block: 'center'}});
                }}
            }}
        """)

        q = self._all_questions[index]
        print(f"  -> 题目: {q['text'][:100]}...")
        for opt in q.get("options", []):
            print(f"     {opt['label']}. {opt['text'][:60]}")
        return q

    # ==================== 点击操作 ====================

    async def click_option(self, label: str, question_data: dict) -> bool:
        """在浏览器中点击指定选项（5策略逐级兜底）"""
        if not self._page:
            return False

        options = question_data.get("options", [])
        qtype = question_data.get("type", "single_choice")
        qindex = question_data.get("index", self._current_index)

        # 找到label对应的选项在options列表中的索引
        opt_index = -1
        label_stripped = label.strip()
        for i, opt in enumerate(options):
            if opt["label"].strip().upper() == label_stripped.upper():
                opt_index = i
                break
        # 判断题：把文字答案映射到选项索引
        if opt_index < 0 and qtype == "true_false":
            true_vals = {"正确", "对", "TRUE", "T", "Y", "A", "是"}
            false_vals = {"错误", "错", "FALSE", "F", "N", "B", "否"}
            if label_stripped in true_vals or label_stripped.upper() in true_vals:
                for i, opt in enumerate(options):
                    if opt.get("label", "") in true_vals or opt.get("value", "") == "true":
                        opt_index = i
                        break
            elif label_stripped in false_vals or label_stripped.upper() in false_vals:
                for i, opt in enumerate(options):
                    if opt.get("label", "") in false_vals or opt.get("value", "") == "false":
                        opt_index = i
                        break

        if opt_index < 0:
            print(f"[!] 未找到选项 '{label}' 对应的DOM元素")
            return False

        # 策略1: JS点击.exam-font > li[opt_index]内的radio input
        clicked = await self._page.evaluate(f"""
            () => {{
                const items = document.querySelectorAll('.exam-main--content .subject-item');
                const item = items[{qindex}];
                if (!item) return false;

                const lis = item.querySelectorAll('.exam-font li');
                const li = lis[{opt_index}];
                if (!li) return false;

                // 点击li内的radio/checkbox input
                const input = li.querySelector('input[type="radio"], input[type="checkbox"]');
                if (input) {{
                    input.click();
                    input.checked = true;
                    input.dispatchEvent(new Event('change', {{bubbles: true}}));
                    input.dispatchEvent(new Event('input', {{bubbles: true}}));
                    return true;
                }}
                return false;
            }}
        """)

        if clicked:
            print(f"  -> 点击选项 {label}")
            return True

        # 策略2: JS点击li本身
        clicked = await self._page.evaluate(f"""
            () => {{
                const items = document.querySelectorAll('.exam-main--content .subject-item');
                const item = items[{qindex}];
                if (!item) return false;
                const lis = item.querySelectorAll('.exam-font li');
                const li = lis[{opt_index}];
                if (!li) return false;
                li.click();
                return true;
            }}
        """)

        if clicked:
            print(f"  -> 点击li选项 {label}")
            return True

        # 策略3: JS点击li内的.custom_ueditor_cn_body
        clicked = await self._page.evaluate(f"""
            () => {{
                const items = document.querySelectorAll('.exam-main--content .subject-item');
                const item = items[{qindex}];
                if (!item) return false;
                const lis = item.querySelectorAll('.exam-font li');
                const li = lis[{opt_index}];
                if (!li) return false;
                const body = li.querySelector('.custom_ueditor_cn_body');
                if (body) {{ body.click(); return true; }}
                return false;
            }}
        """)

        if clicked:
            print(f"  -> 点击custom_ueditor_cn_body选项 {label}")
            return True

        # 策略4: 判断题兜底——搜"正确"/"错误"文字点击（适用于无.exam-font的判断题）
        if qtype == "true_false":
            try:
                item_el = self._page.locator('.exam-main--content .subject-item').nth(qindex)
                search_text = "正确" if label_stripped in {"正确", "对", "TRUE", "T", "Y", "A", "是"} else "错误"
                btn = item_el.locator('label, li, span', has_text=search_text).first
                if await btn.count() > 0:
                    await btn.click(timeout=3000)
                    print(f"  -> 判断题兜底点击: {search_text}")
                    return True
            except Exception:
                pass

        # 策略5: Playwright文本匹配点击
        target_text = options[opt_index].get("text", "")
        if target_text:
            try:
                item_el = self._page.locator('.exam-main--content .subject-item').nth(qindex)
                btn = item_el.locator('li', has_text=target_text).first
                if await btn.count() > 0:
                    await btn.click(timeout=3000)
                    print(f"  -> 文本匹配点击选项 {label}")
                    return True
            except Exception:
                pass

        return False

    async def click_next(self) -> bool:
        """移动到下一题：递增索引并滚动"""
        self._current_index += 1
        if self._current_index >= len(self._all_questions):
            print("[*] 已是最后一题")
            return False

        await self._page.evaluate(f"""
            () => {{
                const items = document.querySelectorAll('.exam-main--content .subject-item');
                if (items[{self._current_index}]) {{
                    items[{self._current_index}].scrollIntoView({{block: 'center'}});
                }}
            }}
        """)
        return True

    async def close(self):
        """释放Playwright连接资源"""
        if self._playwright:
            await self._playwright.stop()

    # ---- DOM提取JS：逐.subject-item解析 ----
    _EXTRACT_SCRIPT = """
    () => {
        const questions = [];
        const container = document.querySelector('.exam-main--content');
        if (!container) {
            console.log('[extract] 未找到 .exam-main--content');
            return questions;
        }

        const items = container.querySelectorAll('.subject-item');
        console.log('[extract] 找到', items.length, '个 .subject-item');

        const LABELS = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H'];

        items.forEach((item, itemIdx) => {
            // ---- 题型检测：从 .item-type 读取 ----
            let qtype = 'single_choice';
            const typeEl = item.querySelector('.item-type');
            const typeText = typeEl ? (typeEl.textContent || '').trim() : '';
            if (typeText) {
                if (typeText.includes('判断')) {
                    qtype = 'true_false';
                } else if (typeText.includes('多选')) {
                    qtype = 'multiple_choice';
                } else if (typeText.includes('填空')) {
                    qtype = 'fill_blank';
                } else if (typeText.includes('单选')) {
                    qtype = 'single_choice';
                }
            }

            // ---- 题面：从 h4 读取 ----
            const h4 = item.querySelector('h4');
            let questionText = h4 ? h4.textContent.trim() : '';

            // ---- 选项：从 .exam-font > li 读取 ----
            const options = [];
            const examFont = item.querySelector('.exam-font');
            if (examFont) {
                const lis = examFont.querySelectorAll('li');
                if (lis.length) {
                    console.log('[extract] 第' + (itemIdx+1) + '题 找到.exam-font, ' + lis.length + '个li');
                }
                lis.forEach((li, liIdx) => {
                    // 优先读.custom_ueditor_cn_body，兜底读li全文
                    const body = li.querySelector('.custom_ueditor_cn_body');
                    const text = body
                        ? body.textContent.trim()
                        : li.textContent.trim();

                    // 查radio/checkbox input的value
                    const input = li.querySelector('input[type="radio"], input[type="checkbox"]');
                    const val = input ? (input.value || '') : '';

                    const label = liIdx < LABELS.length ? LABELS[liIdx] : String(liIdx);

                    if (text) {
                        options.push({
                            label: label,
                            text: text,
                            value: val
                        });
                    }
                });
            } else {
                console.log('[extract] 第' + (itemIdx+1) + '题 未找到.exam-font');
            }

            // ---- 兜底：无.exam-font时通过radio/checkbox推断选项 ----
            if (options.length === 0) {
                const radios = item.querySelectorAll('input[type="radio"], input[type="checkbox"]');
                if (radios.length >= 2) {
                    console.log('[extract] 第' + (itemIdx+1) + '题 兜底: 找到' + radios.length + '个radio/checkbox');
                    radios.forEach((r, ri) => {
                        const label = ri < LABELS.length ? LABELS[ri] : String(ri);
                        const parentLi = r.closest('li');
                        const parentText = parentLi ? parentLi.textContent.trim() : '';
                        // 从父元素文本中去掉input本身
                        const text = parentText || (r.value === 'true' ? '正确' : r.value === 'false' ? '错误' : r.value);
                        options.push({
                            label: label,
                            text: text,
                            value: r.value || ''
                        });
                    });
                } else if (qtype === 'true_false') {
                    // 判断题完全无选项元素，构造虚拟选项
                    options.push(
                        { label: 'A', text: '正确', value: 'true' },
                        { label: 'B', text: '错误', value: 'false' }
                    );
                }
            }

            if (questionText && options.length > 0) {
                console.log('[extract] 第' + (itemIdx+1) + '题 type=' + qtype + ' opts=' + options.length);
                questions.push({
                    text: questionText,
                    type: qtype,
                    options: options,
                    index: itemIdx
                });
            } else if (questionText && qtype === 'fill_blank') {
                // 填空题无选项是正常的
                console.log('[extract] 第' + (itemIdx+1) + '题 type=fill_blank (无选项)');
                questions.push({
                    text: questionText,
                    type: qtype,
                    options: [],
                    index: itemIdx
                });
            } else {
                console.log('[extract] 跳过第' + (itemIdx+1) + '题 text=' + !!questionText + ' opts=' + options.length);
            }
        });

        console.log('[extract] 共提取', questions.length, '道题目');
        return questions;
    }
    """
