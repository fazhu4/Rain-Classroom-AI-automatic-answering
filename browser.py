"""
浏览器控制模块 - 通过 Playwright CDP 连接已有浏览器实例，注入JS提取DOM题目、点击选项
"""
import re        # 正则匹配，用于URL模式匹配定位考试页面
import asyncio   # 异步IO，Playwright所有操作均为async


class BrowserController:
    """Playwright 浏览器控制器：连接→提取→点击"""

    # 初始化控制器，从配置字典读取连接参数和选择器。config: config.yaml的browser配置段
    def __init__(self, config: dict):
        self.cdp_url = config.get("cdp_url", "http://localhost:9222")        # CDP调试端口地址
        self.exam_url_pattern = config.get("exam_url_pattern", "yuketang.cn/exam")  # 考试页面URL匹配特征
        self.question_selector = config.get("question_selector", "")          # 自定义题目选择器（留空则自动检测）
        self.option_selector = config.get("option_selector", "")              # 自定义选项选择器
        self.next_selector = config.get("next_selector", "")                  # 自定义下一题按钮选择器
        self._playwright = None   # Playwright实例（管理整个浏览器生命周期）
        self._browser = None      # 浏览器连接对象
        self._page = None         # 考试页面对象（所有DOM操作的目标页）
        self._all_questions = []  # 预提取的所有题目列表
        self._current_index = 0   # 当前答题索引

    # 通过CDP协议连接已有浏览器实例（需先用--remote-debugging-port启动），自动定位考试标签页
    async def connect(self):
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()  # 启动Playwright实例
        try:
            self._browser = await self._playwright.chromium.connect_over_cdp(self.cdp_url)  # 通过CDP连接已有浏览器
            print(f"[+] 已连接到浏览器 (CDP: {self.cdp_url})")
        except Exception as e:
            raise  # 异常抛出由外层（main.py）统一处理并给出排查指引

        await self._find_exam_page()  # 在所有标签页中定位考试页面
        return self._page is not None

    # 遍历浏览器所有标签页，用URL模式匹配定位考试页面，匹配失败则列出所有页面让用户手动选择
    async def _find_exam_page(self):
        contexts = self._browser.contexts  # 获取所有浏览器上下文（对应浏览器窗口/隐身窗口）
        # 遍历所有上下文和页面，用正则匹配考试URL特征
        for ctx in contexts:
            for page in ctx.pages:
                if re.search(self.exam_url_pattern, page.url):  # URL包含考试特征字符串
                    self._page = page
                    await self._page.bring_to_front()           # 将标签页切换到前台
                    print(f"[+] 找到考试页面: {page.url[:80]}")
                    return

        # 未自动匹配到，列出所有标签页供用户手动选择
        print("[!] 未自动匹配到考试页面，当前打开的标签页:")
        all_pages = []
        for ctx in contexts:
            all_pages.extend(ctx.pages)  # 收集所有上下文的所有页面
        for i, page in enumerate(all_pages):
            print(f"  [{i}] {page.url[:100]}")
        if all_pages:
            idx = int(input("请选择考试页面序号: "))  # 用户输入序号
            self._page = all_pages[idx]
            await self._page.bring_to_front()

    # 预提取页面中所有题目（从.subject-item容器逐个解析），缓存到_all_questions列表。
    # 首次调用时执行JS提取，后续调用直接返回缓存结果
    async def extract_all_questions(self) -> list:
        if self._all_questions:  # 已缓存则直接返回
            return self._all_questions
        if not self._page:
            return []

        self._all_questions = await self._page.evaluate(self._EXTRACT_SCRIPT)  # 在浏览器中执行JS提取脚本

        if self._all_questions:
            print(f"[+] 从页面提取到 {len(self._all_questions)} 道题目")
            # 打印题型分布概览
            type_counts = {}
            for q in self._all_questions:
                t = q.get("type", "unknown")
                type_counts[t] = type_counts.get(t, 0) + 1
            print(f"    题型分布: {type_counts}")
        else:
            print("[!] 未能从页面提取到任何题目")

        return self._all_questions

    # 获取当前要作答的题目（从预提取列表中按索引取）
    def get_current_question(self) -> dict:
        if self._current_index < len(self._all_questions):
            return self._all_questions[self._current_index]
        return {}

    # 滚动到当前题目并注入答案标记，返回当前题目dict供LLM处理
    async def prepare_current_question(self) -> dict:
        if not self._all_questions:
            await self.extract_all_questions()  # 首次调用：预提取所有题目

        q = self.get_current_question()
        if not q:
            return {}

        # 滚动当前题目的.subject-item到可视区域
        await self._page.evaluate(f"""
            () => {{
                const items = document.querySelectorAll('.exam-main--content .subject-item');
                if (items[{self._current_index}]) {{
                    items[{self._current_index}].scrollIntoView({{block: 'center'}});
                }}
            }}
        """)

        # 为当前题目的选项注入data-answer-index标记
        await self._page.evaluate(f"""
            () => {{
                const items = document.querySelectorAll('.exam-main--content .subject-item');
                const item = items[{self._current_index}];
                if (!item) return;

                // 清除当前item中的旧标记
                item.querySelectorAll('[data-answer-index]').forEach(el => {{
                    el.removeAttribute('data-answer-index');
                }});

                // 先查el-radio，无则查所有含radio input的label
                let labels = item.querySelectorAll('label.el-radio');
                if (!labels.length) {{
                    labels = item.querySelectorAll('label:has(input[type="radio"])');
                }}

                labels.forEach(label => {{
                    const input = label.querySelector('input[type="radio"]');
                    const val = input ? input.value.toLowerCase() : '';
                    const radioInput = label.querySelector('.radioInput');

                    if (radioInput) {{
                        // 单选题：A/B/C/D 标签来自radioInput span
                        label.setAttribute('data-answer-index', radioInput.textContent.trim());
                    }} else if (val === 'true' || val === 'false') {{
                        // 判断题：使用input的value作为标记（已转小写）
                        label.setAttribute('data-answer-index', val);
                    }} else if (val) {{
                        // 其他值（如数字、字母等）：直接用作标记
                        label.setAttribute('data-answer-index', val);
                    }}
                }});
            }}
        """)

        # 打印题目预览
        print(f"  -> 题目: {q['text'][:100]}...")
        for opt in q.get("options", []):
            print(f"     {opt['label']}. {opt['text'][:60]}")
        return q

    # 在浏览器中点击指定选项，三策略逐级兜底。
    # label: 选项字母（如"A"、"正确"），question_data: 当前题目的dict
    async def click_option(self, label: str, question_data: dict) -> bool:
        if not self._page:
            return False

        options = question_data.get("options", [])
        qtype = question_data.get("type", "single_choice")
        qindex = question_data.get("index", self._current_index)

        # 确定要查找的data-answer-index值
        target_index = label.strip().upper()  # 默认直接使用label

        # 判断题：映射LLM返回的答案文字到true/false值
        if qtype == "true_false":
            true_vals = {"正确", "对", "TRUE", "T", "Y", "A", "是", "√", "CORRECT", "YES"}
            false_vals = {"错误", "错", "FALSE", "F", "N", "B", "否", "×", "X", "INCORRECT", "NO"}
            label_stripped = label.strip()
            if label_stripped in true_vals or label_stripped.upper() in true_vals:
                target_index = "true"
            elif label_stripped in false_vals or label_stripped.upper() in false_vals:
                target_index = "false"
            # 选项索引也参与匹配：用选项的value字段
            for opt in options:
                if opt["label"].strip() == label_stripped:
                    target_index = opt.get("index", opt.get("value", target_index))
                    break

        # 在选项中查找匹配label的选项，获取其index
        for opt in options:
            if opt["label"].strip().upper() == label.strip().upper():
                target_index = opt.get("index", target_index)
                break

        # 策略1: 用data-answer-index属性在当前题目内定位label元素并点击
        clicked = await self._page.evaluate(f"""
            () => {{
                const items = document.querySelectorAll('.exam-main--content .subject-item');
                const item = items[{qindex}];
                if (!item) return false;

                // 查找el-radio或其他含radio的label
                let label = item.querySelector('label.el-radio[data-answer-index="{target_index}"]');
                if (!label) {{
                    label = item.querySelector('label[data-answer-index="{target_index}"]');
                }}

                if (!label) return false;

                // 点击label触发Vue的change事件
                label.click();

                // 同时设置radio input的checked状态并派发change事件（兜底Vue未响应click）
                const input = label.querySelector('input[type="radio"]');
                if (input) {{
                    input.checked = true;
                    input.dispatchEvent(new Event('change', {{bubbles: true}}));
                    input.dispatchEvent(new Event('input', {{bubbles: true}}));
                }}

                return true;
            }}
        """)

        if clicked:
            print(f"  -> 点击选项 {label}")
            return True

        # 策略2: 用Playwright文本匹配在当前题目内查找并点击
        for opt in options:
            if opt["label"].strip().upper() == label.strip().upper():
                text = opt.get("text", "")
                if text:
                    try:
                        # 限定在当前subject-item内查找el-radio或任意label
                        item_el = self._page.locator('.exam-main--content .subject-item').nth(qindex)
                        btn = item_el.locator('label.el-radio', has_text=text).first
                        if await btn.count() > 0:
                            await btn.click(timeout=3000)
                            print(f"  -> 文本匹配(el-radio)点击选项 {label}")
                            return True
                        # 兜底：查找任意含该文字的label
                        btn = item_el.locator('label', has_text=text).first
                        if await btn.count() > 0:
                            await btn.click(timeout=3000)
                            print(f"  -> 文本匹配(label)点击选项 {label}")
                            return True
                    except Exception:
                        pass

        # 策略3: 判断题兜底——直接在当前item内找含"正确"/"错误"文字的label点击
        if qtype == "true_false":
            try:
                item_el = self._page.locator('.exam-main--content .subject-item').nth(qindex)
                search_text = "正确" if target_index == "true" else "错误"
                btn = item_el.locator('label', has_text=search_text).first
                if await btn.count() > 0:
                    await btn.click(timeout=3000)
                    print(f"  -> 判断题兜底点击: {search_text}")
                    return True
            except Exception:
                pass

        # 策略4: 用aria-label属性匹配
        try:
            item_el = self._page.locator('.exam-main--content .subject-item').nth(qindex)
            await item_el.locator(f'[aria-label*="{label}"]').click(timeout=2000)
            return True
        except Exception:
            pass

        return False  # 四策略均失败

    # 移动到下一题：递增索引并滚动到下一题
    async def click_next(self) -> bool:
        self._current_index += 1
        if self._current_index >= len(self._all_questions):
            print("[*] 已是最后一题")
            return False

        # 滚动下一题到可视区域
        await self._page.evaluate(f"""
            () => {{
                const items = document.querySelectorAll('.exam-main--content .subject-item');
                if (items[{self._current_index}]) {{
                    items[{self._current_index}].scrollIntoView({{block: 'center'}});
                }}
            }}
        """)
        return True

    # 关闭Playwright实例，释放浏览器连接和所有资源
    async def close(self):
        if self._playwright:
            await self._playwright.stop()  # 停止Playwright（清理CDP连接和内部状态）

    # ---- DOM提取JS脚本：从.subject-item容器逐个解析题目、选项、题型 ----
    _EXTRACT_SCRIPT = """
    () => {
        const questions = [];
        const container = document.querySelector('.exam-main--content');
        if (!container) {
            console.log('[extract] 未找到.exam-main--content');
            return questions;
        }

        const items = container.querySelectorAll('.subject-item');
        console.log('[extract] 找到', items.length, '个.subject-item');

        items.forEach((item, itemIdx) => {
            // 获取题型信息：优先从.item-type元素读取，其次从h4/全文关键词兜底
            let qtype = 'single_choice';
            const itemText = (item.textContent || '').replace(/\\s+/g, ' ').trim();

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
            } else {
                // 兜底：从h4标签或全文关键词推断
                const h4 = item.querySelector('h4');
                const h4Text = h4 ? (h4.textContent || '').trim() : '';
                if (h4Text.includes('判断') || itemText.includes('判断题')) {
                    qtype = 'true_false';
                } else if (h4Text.includes('多选') || itemText.includes('多选题')) {
                    qtype = 'multiple_choice';
                } else if (h4Text.includes('填空') || itemText.includes('填空题')) {
                    qtype = 'fill_blank';
                }
            }

            // 提取选项：从label.el-radio或任何含radio input的label元素中获取
            let radioLabels = item.querySelectorAll('label.el-radio');
            // 兜底：查找所有包含radio input的label
            if (!radioLabels.length) {
                radioLabels = item.querySelectorAll('label:has(input[type="radio"])');
            }

            const options = [];

            radioLabels.forEach((label) => {
                const input = label.querySelector('input[type="radio"]');
                const val = input ? (input.value || '').toLowerCase() : '';

                const radioInput = label.querySelector('.radioInput');
                const radioText = label.querySelector('.radioText');
                const optionText = label.querySelector('.optionText');
                const labelSpan = label.querySelector('.el-radio__label');

                let letter = '';
                let displayText = '';

                if (radioInput) {
                    // 单选题：radioInput span含字母(A/B/C/D)，radioText span含选项文字
                    letter = radioInput.textContent.trim();
                    displayText = radioText ? radioText.textContent.trim() : '';
                } else if (optionText) {
                    // 判断题：optionText span含"正确"/"错误"文字
                    displayText = optionText.textContent.trim();
                    letter = displayText;
                } else if (labelSpan) {
                    // 兜底：取el-radio__label的全部文字
                    displayText = labelSpan.textContent.trim();
                    // 判断题：从文本推断label
                    if (displayText.includes('正确') || displayText.includes('对')) {
                        letter = '正确';
                    } else if (displayText.includes('错误') || displayText.includes('错')) {
                        letter = '错误';
                    } else {
                        letter = displayText;
                    }
                }

                // data-answer-index：单选题已预设，判断题用input value
                let ansIdx = label.getAttribute('data-answer-index') || '';
                if (!ansIdx) {
                    if (val === 'true' || val === 'false') {
                        ansIdx = val;
                    } else if (radioInput) {
                        ansIdx = radioInput.textContent.trim();
                    } else if (letter) {
                        ansIdx = letter;
                    }
                }

                if (letter || displayText) {
                    options.push({
                        label: letter || ansIdx,
                        text: displayText || letter,
                        index: ansIdx,
                        value: val
                    });
                }
            });

            // 提取题目正文：取item内文本，剥离选项文本
            let questionText = itemText;
            if (options.length > 0) {
                // 逐段移除选项label文本（如"正确"、"错误"、"A."等）
                for (const opt of options) {
                    const txt = opt.text;
                    if (txt && questionText.includes(txt)) {
                        questionText = questionText.replace(txt, '');
                    }
                    const lbl = opt.label;
                    if (lbl && lbl !== txt && questionText.includes(lbl)) {
                        questionText = questionText.replace(lbl, '');
                    }
                }
                questionText = questionText.replace(/\\s+/g, ' ').trim();
                // 清理尾部残留符号
                questionText = questionText.replace(/[\\s()（）.、]+$/g, '').trim();
            }

            // 兜底：如果没提取到选项但item文本含"正确/错误"关键词，构造判断题选项
            if (options.length === 0) {
                const hasTrue = itemText.includes('正确') || itemText.includes('对');
                const hasFalse = itemText.includes('错误') || itemText.includes('错');
                if (hasTrue && hasFalse) {
                    qtype = 'true_false';
                    options.push(
                        { label: '正确', text: '正确', index: 'true', value: 'true' },
                        { label: '错误', text: '错误', index: 'false', value: 'false' }
                    );
                }
            }

            if (questionText && options.length > 0) {
                console.log('[extract] 第' + (itemIdx+1) + '题 type=' + qtype + ' options=' + options.length);
                questions.push({
                    text: questionText,
                    options: options,
                    type: qtype,
                    index: itemIdx
                });
            } else {
                console.log('[extract] 跳过第' + (itemIdx+1) + '题: text=' + !!questionText + ' opts=' + options.length);
            }
        });

        console.log('[extract] 共提取', questions.length, '道题目');
        return questions;
    }
    """
