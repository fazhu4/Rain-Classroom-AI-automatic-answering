"""
浏览器控制模块
通过 Playwright CDP 协议连接到用户已打开的浏览器，注入 JS 脚本从 DOM 中提取考试题目，
再通过多层兜底策略将大模型返回的答案点击到页面上。
"""
import re


class BrowserController:
    """
    Playwright 浏览器控制器
    负责：CDP连接 → DOM题目提取 → 选项点击 → 题目翻页
    """

    def __init__(self, config: dict):
        """
        从配置字典初始化浏览器控制器的各项参数
        Args:
            config: YAML配置中 browser 段的字典，包含：
                - cdp_url: 浏览器远程调试端口地址
                - exam_url_pattern: 考试页面URL的匹配规则（正则表达式）
        """
        # 浏览器远程调试地址，CDP协议默认端口9222，Edge/Chrome启动时通过 --remote-debugging-port 指定
        self.cdp_url = config.get("cdp_url", "http://localhost:9222")
        # 考试页面URL正则匹配模式，用于在所有标签页中自动识别目标页面
        self.exam_url_pattern = config.get("exam_url_pattern", "yuketang.cn/exam")
        # Playwright 实例句柄，connect() 中启动
        self._playwright = None
        # 通过 CDP 连接到的浏览器对象，包含所有context和page
        self._browser = None
        # 目标考试页面的 Page 对象，所有 DOM 提取和点击操作都通过此对象进行
        self._page = None
        # 从页面一次性提取的所有题目缓存列表，避免重复注入JS
        self._all_questions = []
        # 当前正在处理的题目序号（从0开始），用于滚动和DOM定位
        self._current_index = 0

    # ==================== 浏览器连接 ====================

    # 启动 Playwright 并通过 CDP 协议连接到已有浏览器实例，然后自动定位考试页面
    # Returns: bool — True=成功找到考试页面，False=未找到
    async def connect(self):
        # 动态导入避免模块级别依赖，只有用到浏览器模式才会加载playwright
        from playwright.async_api import async_playwright

        # 启动 Playwright 实例，这一步不连接浏览器，只是初始化Playwright框架
        self._playwright = await async_playwright().start()
        # 通过 CDP 协议连接到用户已经打开的浏览器（需要用户先以 --remote-debugging-port 模式启动浏览器）
        self._browser = await self._playwright.chromium.connect_over_cdp(self.cdp_url)
        print(f"[+] 已连接到浏览器 (CDP: {self.cdp_url})")

        # 在所有标签页中搜索考试页面
        await self._find_exam_page()
        # 返回是否成功定位到考试页面
        return self._page is not None

    # 遍历浏览器所有窗口的所有标签页，用 exam_url_pattern 正则匹配考试页面。
    # 自动匹配成功则直接绑定；失败则列出所有页面供用户手动选择。
    async def _find_exam_page(self):
        # contexts 是浏览器的所有窗口/会话（包括普通窗口和隐私窗口）
        contexts = self._browser.contexts
        for ctx in contexts:
            # 遍历当前窗口内所有打开的标签页
            for page in ctx.pages:
                # 用正则匹配页面URL，找到包含考试关键词的页面
                if re.search(self.exam_url_pattern, page.url):
                    self._page = page
                    # 将该标签页切到前台，方便用户观察答题过程
                    await self._page.bring_to_front()
                    print(f"[+] 找到考试页面: {page.url[:80]}")
                    return  # 匹配到第一个就返回

        # ---- 未自动匹配到考试页面，列出所有标签页供用户手动选择 ----
        print("[!] 未自动匹配到考试页面，当前打开的标签页:")
        all_pages = []
        for ctx in contexts:
            all_pages.extend(ctx.pages)  # 收集所有窗口的全部标签页
        # 打印每个标签页的URL，让用户根据序号选择
        for i, page in enumerate(all_pages):
            print(f"  [{i}] {page.url[:100]}")
        if all_pages:
            # 等待用户输入序号
            idx = int(input("请选择考试页面序号: "))
            self._page = all_pages[idx]  # 选取用户指定的标签页
            await self._page.bring_to_front()  # 切到前台

    # ==================== 题目提取 ====================

    # 一次性注入 JS 脚本到考试页面，从 DOM 中提取所有题目的结构化数据，并缓存。
    # 每个题目 dict 包含：
    #     text:    题面文字（str）
    #     -type:   题型（str） single_choice / multiple_choice / true_false / fill_blank
    #     options: 选项列表（list[dict]），每个选项含 label、text、value
    #     index:   在页面中的序号（int，从0开始）
    # Returns: list[dict] — 所有题目的结构化数据列表
    async def extract_all_questions(self) -> list:
        # 如果已经提取过，直接返回缓存，避免重复JS注入
        if self._all_questions:
            return self._all_questions
        # 如果没有绑定考试页面，返回空列表
        if not self._page:
            return []

        # 注入 _EXTRACT_SCRIPT 到浏览器执行，返回JS解析出的题目数组
        self._all_questions = await self._page.evaluate(self._EXTRACT_SCRIPT)

        # ---- 打印提取结果统计 ----
        if self._all_questions:
            print(f"[+] 从页面提取到 {len(self._all_questions)} 道题目")
            # 统计各题型的数量分布
            type_counts = {}
            for q in self._all_questions:
                t = q.get("type", "unknown")
                type_counts[t] = type_counts.get(t, 0) + 1
            print(f"    题型分布: {type_counts}")
        else:
            print("[!] 未能从页面提取到任何题目")

        return self._all_questions

    # 将页面上第 index 个 .subject-item 容器滚动到可视区中央，并返回该题的缓存数据。
    # 滚动后同时打印题目文字和选项列表供控制台查看。
    # Args:
    #     index: 题目序号，从0开始，对应 .subject-item 在页面中的索引
    # Returns: dict — 该题的缓存数据，索引越界时返回空dict
    async def scroll_to_question(self, index: int) -> dict:
        # 索引越界保护
        if index >= len(self._all_questions):
            return {}

        # 注入JS将指定 .subject-item 滚动到屏幕垂直中央
        await self._page.evaluate(f"""
            () => {{
                const items = document.querySelectorAll('.exam-main--content .subject-item');
                if (items[{index}]) {{
                    items[{index}].scrollIntoView({{block: 'center'}});
                }}
            }}
        """)

        # 从缓存中取出该题数据
        q = self._all_questions[index]
        # 打印题面（截取前100字符避免输出过长）
        print(f"  -> 题目: {q['text'][:100]}...")
        # 逐个打印选项：标签（A/B/C/D）+ 文字（截取前60字符）
        for opt in q.get("options", []):
            print(f"     {opt['label']}. {opt['text'][:60]}")
        return q

    # ==================== 点击操作 ====================

    # 在浏览器中点击指定选项，使用5层兜底策略确保点击成功。
    # 策略1: JS点击 radio/checkbox input 并派发事件（适用于Vue/React组件）
    # 策略2: JS点击整个 li 元素
    # 策略3: JS点击 li 内的 .custom_ueditor_cn_body 文字span
    # 策略4: 判断题专用 — 搜索"正确"/"错误"文字匹配点击（适用于无.exam-font的判断题）
    # 策略5: Playwright定位器文本匹配点击（利用Playwright的has_text能力）
    # Args:
    #     label: 答案标签，选择题为字母如 "A"/"B"/"AB"；判断题为 "正确"/"错误"
    #     question_data: 该题的缓存dict，必须包含 options列表、type题型、index序号
    # Returns: bool — True=成功点击，False=所有策略均未命中
    async def click_option(self, label: str, question_data: dict) -> bool:
        # 检查是否有可用的页面
        if not self._page:
            return False

        # 从题目数据中提取选项列表、题型、页面索引
        options = question_data.get("options", [])
        qtype = question_data.get("type", "single_choice")
        qindex = question_data.get("index", self._current_index)

        # 将答案label翻译为options列表中的索引（例如 "B" → 1）
        opt_index = self._find_option_index(label, options, qtype)
        if opt_index < 0:
            print(f"[!] 未找到选项 '{label}' 对应的DOM元素")
            return False

        # ===== 策略1: JS点击li内的radio/checkbox input =====
        # 原理：找到li中的input元素，click()后手动设置checked=true，
        # 再派发change和input事件以触发Vue/React的响应式更新
        clicked = await self._click_by_js(qindex, opt_index, 'input')
        if clicked:
            print(f"  -> 点击选项 {label}")
            return True

        # ===== 策略2: JS直接点击整个li元素 =====
        # 原理：有些网站监听的是li的click事件而非input的change事件
        clicked = await self._click_by_js(qindex, opt_index, 'li')
        if clicked:
            print(f"  -> 点击li选项 {label}")
            return True

        # ===== 策略3: JS点击li内的.custom_ueditor_cn_body =====
        # 原理：某些考试平台（如雨课堂）选项文字包裹在custom_ueditor_cn_body中，
        # 点击事件可能绑定在这个元素上
        clicked = await self._click_by_js(qindex, opt_index, 'body')
        if clicked:
            print(f"  -> 点击custom_ueditor_cn_body选项 {label}")
            return True

        # ===== 策略4: 判断题文字搜索兜底 =====
        # 原理：判断题的DOM结构可能没有.exam-font和li，
        # 直接在题目容器内搜索"正确"或"错误"文字进行点击
        if qtype == "true_false":
            try:
                # 定位当前题目的.subject-item容器
                item_el = self._page.locator('.exam-main--content .subject-item').nth(qindex)
                # 根据答案选择搜索"正确"还是"错误"
                search_text = "正确" if label.strip() in {"正确", "对", "TRUE", "T", "Y", "A", "是"} else "错误"
                # 在容器内找包含目标文字的label/li/span元素
                btn = item_el.locator('label, li, span', has_text=search_text).first
                if await btn.count() > 0:
                    await btn.click(timeout=3000)
                    print(f"  -> 判断题兜底点击: {search_text}")
                    return True
            except Exception:
                pass  # 策略4失败不报错，继续下一个策略

        # ===== 策略5: Playwright文本匹配点击 =====
        # 原理：用Playwright的has_text定位器，根据选项文字内容定位li元素并点击
        # 这是最后一层兜底，利用Playwright的智能文本匹配
        target_text = options[opt_index].get("text", "")
        if target_text:
            try:
                item_el = self._page.locator('.exam-main--content .subject-item').nth(qindex)
                # 在题目容器内找包含选项文字的li元素
                btn = item_el.locator('li', has_text=target_text).first
                if await btn.count() > 0:
                    await btn.click(timeout=3000)
                    print(f"  -> 文本匹配点击选项 {label}")
                    return True
            except Exception:
                pass  # 策略5失败不报错

        # 所有策略均失败
        return False

    # 将LLM返回的答案标签（如 "A"、"正确"）映射为 options 列表中的索引位置。
    # 选择题：按字母标签匹配（如 "A" → 0, "B" → 1）
    # 判断题：按正确/错误文字映射到对应选项的索引
    # Args:
    #     label: LLM返回的答案，如 "A", "AB", "正确", "错误"
    #     options: 页面提取的选项列表，每项含 label、text、value
    #     qtype: 题型，判断题需要特殊映射
    # Returns: int — 选项在options列表中的索引，未找到返回-1
    def _find_option_index(self, label: str, options: list, qtype: str) -> int:
        label_stripped = label.strip()

        # ---- 选择题：按字母标签匹配 ----
        # 遍历options，找label匹配的选项（大小写不敏感）
        for i, opt in enumerate(options):
            if opt["label"].strip().upper() == label_stripped.upper():
                return i

        # ---- 判断题：将"正确"/"错误"映射到选项索引 ----
        if qtype == "true_false":
            # 定义正确答案的别名集合（中英文、大小写、简写）
            true_vals = {"正确", "对", "TRUE", "T", "Y", "A", "是"}
            false_vals = {"错误", "错", "FALSE", "F", "N", "B", "否"}

            if label_stripped in true_vals or label_stripped.upper() in true_vals:
                # 答案表示"正确"，在options中找label或value为true的选项
                for i, opt in enumerate(options):
                    if opt.get("label", "") in true_vals or opt.get("value", "") == "true":
                        return i
            elif label_stripped in false_vals or label_stripped.upper() in false_vals:
                # 答案表示"错误"，在options中找label或value为false的选项
                for i, opt in enumerate(options):
                    if opt.get("label", "") in false_vals or opt.get("value", "") == "false":
                        return i

        # 未找到匹配的选项
        return -1

    # 通过注入JS代码，在指定题目的第opt_index个选项上执行点击，统一处理三种点击目标。
    # - 'input': 点击li内的 radio/checkbox input，设置checked=true，
    #            并派发change/input事件以触发Vue双向绑定更新
    # - 'li':    直接对li元素调用click()，适用于事件绑定在li上的场景
    # - 'body':  点击li内 .custom_ueditor_cn_body 元素，适用于雨课堂等特定平台
    # Args:
    #     qindex: 题目在 .exam-main--content .subject-item 列表中的索引
    #     opt_index: 选项li在 .exam-font li 列表中的索引
    #     target: 点击目标类型：'input' / 'li' / 'body'
    # Returns: bool — JS端是否成功找到目标并执行了点击
    async def _click_by_js(self, qindex: int, opt_index: int, target: str) -> bool:
        # 根据target类型构建不同的点击逻辑片段
        if target == 'input':
            # 策略1：找li内的radio/checkbox input
            # click()触发原生点击，checked=true确保状态，dispatchEvent派发事件通知框架
            click_logic = """
                const input = li.querySelector('input[type="radio"], input[type="checkbox"]');
                if (input) {
                    input.click();
                    input.checked = true;
                    input.dispatchEvent(new Event('change', {bubbles: true}));
                    input.dispatchEvent(new Event('input', {bubbles: true}));
                    return true;
                }
                return false;
            """
        elif target == 'li':
            # 策略2：直接点击li元素本体，适用于click事件绑定在li上的页面
            click_logic = """
                li.click();
                return true;
            """
        else:  # target == 'body'
            # 策略3：点击li内的 .custom_ueditor_cn_body 文字容器
            # 雨课堂等平台把选项文字放在这个class的span中，点击事件绑定在它上面
            click_logic = """
                const body = li.querySelector('.custom_ueditor_cn_body');
                if (body) { body.click(); return true; }
                return false;
            """

        # 注入完整JS到浏览器执行：先定位题目容器 → 定位选项li → 执行点击逻辑
        return await self._page.evaluate(f"""
            () => {{
                const items = document.querySelectorAll('.exam-main--content .subject-item');
                const item = items[{qindex}];
                if (!item) return false;
                const lis = item.querySelectorAll('.exam-font li');
                const li = lis[{opt_index}];
                if (!li) return false;
                {click_logic}
            }}
        """)

    # 将内部题目索引 +1，并滚动到下一题的 .subject-item 容器。
    # 如果当前已是最后一题，打印提示并返回False。
    # Returns: bool — True=成功切到下一题，False=已是最后一题
    async def click_next(self) -> bool:
        # 递增当前题目序号
        self._current_index += 1
        # 检查是否超出题目总数
        if self._current_index >= len(self._all_questions):
            print("[*] 已是最后一题")
            return False

        # 注入JS滚动下一题到可视区中央
        await self._page.evaluate(f"""
            () => {{
                const items = document.querySelectorAll('.exam-main--content .subject-item');
                if (items[{self._current_index}]) {{
                    items[{self._current_index}].scrollIntoView({{block: 'center'}});
                }}
            }}
        """)
        return True

    # ==================== 资源释放 ====================

    # 释放 Playwright 连接资源，停止内部事件循环
    async def close(self):
        if self._playwright:
            # stop() 会关闭浏览器连接并清理Playwright自身的资源
            await self._playwright.stop()

    # ==================== DOM提取JS脚本 ====================
    # 此脚本会被注入到浏览器页面中执行，负责从DOM中解析所有题目。
    # 解析流程：
    #   1. 找到 .exam-main--content 主容器
    #   2. 遍历容器内所有 .subject-item 子元素（每个代表一道题）
    #   3. 对每个 .subject-item：
    #      a. 从 .item-type 元素读取题型（判断/单选/多选/填空）
    #      b. 从 h4 元素读取题面文字
    #      c. 从 .exam-font > li 读取选项，优先取 .custom_ueditor_cn_body 内的文本
    #      d. 兜底：无 .exam-font 时扫描页面上的 radio/checkbox input 推断选项
    #   4. 返回 question dict 数组
    _EXTRACT_SCRIPT = """
    () => {
        const questions = [];
        // 定位考试主内容区容器
        const container = document.querySelector('.exam-main--content');
        if (!container) return questions;  // 页面没有考试内容则返回空

        // 获取所有题目容器（每个.subject-item是一道题）
        const items = container.querySelectorAll('.subject-item');
        // 选项标签字母表，A~H共8个，覆盖绝大多数考试
        const LABELS = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H'];

        items.forEach((item, itemIdx) => {
            // ==== 步骤1: 题型检测 ====
            // 从 .item-type 元素读取文字，判断题目类型
            let qtype = 'single_choice';  // 默认当作单选题
            const typeEl = item.querySelector('.item-type');
            const typeText = typeEl ? (typeEl.textContent || '').trim() : '';
            if (typeText) {
                if (typeText.includes('判断')) qtype = 'true_false';
                else if (typeText.includes('多选')) qtype = 'multiple_choice';
                else if (typeText.includes('填空')) qtype = 'fill_blank';
                else if (typeText.includes('单选')) qtype = 'single_choice';
            }

            // ==== 步骤2: 题面提取 ====
            // 从 h4 元素读取题目文字
            const h4 = item.querySelector('h4');
            let questionText = h4 ? h4.textContent.trim() : '';

            // ==== 步骤3: 选项提取（主路径：.exam-font > li） ====
            const options = [];
            const examFont = item.querySelector('.exam-font');
            if (examFont) {
                const lis = examFont.querySelectorAll('li');  // 获取所有选项li元素
                lis.forEach((li, liIdx) => {
                    // 优先读取 .custom_ueditor_cn_body 内的文字（雨课堂风格），
                    // 如果不存在则读取整个li的文本内容
                    const body = li.querySelector('.custom_ueditor_cn_body');
                    const text = body ? body.textContent.trim() : li.textContent.trim();

                    // 尝试读取 li 内 radio/checkbox input 的 value 属性
                    // value可能为 "true"/"false" 用于判断题
                    const input = li.querySelector('input[type="radio"], input[type="checkbox"]');
                    const val = input ? (input.value || '') : '';

                    // 按序分配字母标签（A/B/C/D...）
                    const label = liIdx < LABELS.length ? LABELS[liIdx] : String(liIdx);
                    if (text) {
                        options.push({ label: label, text: text, value: val });
                    }
                });
            }

            // ==== 步骤4: 选项提取（兜底路径：无.exam-font时通过input推断） ====
            if (options.length === 0) {
                // 扫描题目容器内所有 radio/checkbox input，推断选项结构
                const radios = item.querySelectorAll('input[type="radio"], input[type="checkbox"]');
                if (radios.length >= 2) {
                    // 有多个input控件，每个input对应一个选项
                    radios.forEach((r, ri) => {
                        const label = ri < LABELS.length ? LABELS[ri] : String(ri);
                        // 尝试从父级li获取文字，或使用input的value作为选项文本
                        const parentLi = r.closest('li');
                        const parentText = parentLi ? parentLi.textContent.trim() : '';
                        const text = parentText || (r.value === 'true' ? '正确' : r.value === 'false' ? '错误' : r.value);
                        options.push({ label: label, text: text, value: r.value || '' });
                    });
                } else if (qtype === 'true_false') {
                    // 判断题且页面上没有任何选项元素，构造虚拟选项（A=正确, B=错误）
                    options.push(
                        { label: 'A', text: '正确', value: 'true' },
                        { label: 'B', text: '错误', value: 'false' }
                    );
                }
            }

            // ==== 步骤5: 组装题目dict并加入结果数组 ====
            if (questionText && options.length > 0) {
                // 有题面且有选项：选择题或判断题
                questions.push({ text: questionText, type: qtype, options: options, index: itemIdx });
            } else if (questionText && qtype === 'fill_blank') {
                // 填空题没有选项是正常的，options为空列表
                questions.push({ text: questionText, type: qtype, options: [], index: itemIdx });
            }
            // 其他情况（无题面or无选项且非填空题）则跳过，不加入结果
        });

        return questions;
    }
    """
