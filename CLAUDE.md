# Auto Exam Solver - 项目概要

## 项目定位

网课考试自动答题工具。通过浏览器自动化或屏幕截图获取题目，发送给大模型 API 作答，再自动将答案填回页面。

## 技术架构

```
auto/
├── config.yaml      # 用户配置（API Key、模式、热键等）
├── main.py          # 主入口：模式分发、热键监听、答题流程编排
├── browser.py       # 浏览器模式：Playwright CDP 连接 → DOM 提取 → 元素点击
├── capture.py       # 截图模式：mss 截图 → EasyOCR 识别 → 文字坐标定位
├── llm_client.py    # LLM 客户端：统一封装 OpenAI / Anthropic / 自定义 API
├── requirements.txt # Python 依赖
└── README.md        # 用户使用文档
```

## 核心流程

### 浏览器模式（mode: browser）

```
用户按热键 → main.py 分发 → browser.py
  1. browser.inject_answer_markers()  → 给页面选项注入 data-answer-index 属性
  2. browser.extract_question()      → page.evaluate() 执行 JS 从 DOM 提取题面和选项
  3. llm_client.ask_text()           → 发送题面文本给 LLM，返回 {answer, explanation}
  4. browser.click_option()          → 通过 data-answer-index 或文本匹配定位并点击选项
  5. [可选] browser.click_next()     → 自动点击下一题
```

- 浏览器连接方式：`playwright.chromium.connect_over_cdp("http://localhost:9222")`
- 支持 Edge 和 Chrome（Chromium CDP 协议通用）
- DOM 提取用 JS 注入，不依赖特定网站结构，通过文本特征自动识别题型和选项

### 截图模式（mode: screenshot）

```
用户按热键 → main.py 分发 → capture.py
  1. capture.capture_delay()         → mss 截取指定屏幕区域
  2. capture.ocr_detect()            → EasyOCR 识别文字 + 像素坐标
  3. llm_client.ask_text/ask_vision  → 文本或图片发给 LLM
  4. _locate_answer()                → 4 层策略定位答案位置（精确匹配→格式匹配→模糊匹配→OpenCV模板匹配）
  5. pyautogui.click()               → 换算屏幕坐标并点击
```

## LLM 客户端设计

`llm_client.py` 支持三种 provider：
- **openai**：使用 `openai` 官方 SDK，自动拼接 `/v1/chat/completions`
- **anthropic**：使用 `anthropic` 官方 SDK
- **custom**：OpenAI 兼容的自定义 API（DeepSeek、Ollama、vLLM 等），通过 `base_url` 指定

所有 provider 返回统一格式：`{"answer": "A", "explanation": "...", "question_type": "single_choice"}`

文本模式和视觉模式共用同一个客户端，视觉模式额外将 PIL Image 转 base64 发送。

## 关键技术点

1. **热键监听**：`pynput` 全局键盘监听，带防抖机制（所有热键释放后才允许再次触发）
2. **异步处理**：浏览器操作用 `asyncio.run()` 包装，通过 `ThreadPoolExecutor` 在热键线程中同步调用
3. **DOM 提取**：JS 脚本注入页面，通过文本模式匹配识别题型（单选/多选/判断/填空）和选项（A/B/C/D），不依赖特定 CSS 选择器
4. **答案定位**：浏览器模式用 `data-answer-index` 属性直连 DOM 元素；截图模式用 OCR 坐标 + 多种匹配策略
5. **答案格式**：LLM prompt 要求返回结构化 JSON，含 `answer`（选项字母）、`explanation`（解析）、`question_type`（题型）

## 外部依赖

- **playwright**：浏览器 CDP 连接和 DOM 操作
- **mss**：高性能屏幕截图
- **easyocr**：OCR 文字识别和定位
- **pyautogui**：模拟鼠标点击
- **pynput**：全局键盘热键监听
- **pillow / opencv-python**：图像处理

## 已知限制

- Edge/Chrome 必须用 `--remote-debugging-port` 启动，浏览器后台进程需彻底关闭
- 热键监听需要终端窗口有焦点
- 某些网站的 DOM 结构特殊可能导致题目提取不完整，需手动补充 CSS 选择器
- EasyOCR 首次加载需要下载模型（~200MB）
