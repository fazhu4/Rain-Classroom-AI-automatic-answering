# 开发者文档

## 工作原理

```
浏览器(CDP) → 注入JS提取DOM题目 → 分题型构建Prompt → LLM作答 → 浏览器内点击选项 → 下一题
```

1. 通过 Playwright CDP 协议连接到用户已打开的浏览器（端口 9222）
2. 在所有标签页中自动匹配考试页面（URL匹配 `yuketang.cn/exam`）
3. 注入 JS 脚本一次性提取页面上所有题目（题面 + 题型 + 选项），缓存到内存
4. 逐题循环：滚动到题目 → 根据题型（单选/多选/判断/填空）构建不同的 LLM 提示词 → 调用大模型 API → 解析返回的 JSON 答案 → 在浏览器中点击对应选项 → 翻到下一题

## 技术架构

```
auto/
├── config.yaml       # 用户配置文件
├── main.py           # 主入口：加载配置 → 校验API Key → 启动答题循环
├── browser.py        # 浏览器控制：CDP连接 → DOM提取 → 选项点击 → 翻题
├── llm_client.py     # LLM客户端：统一封装 OpenAI / Anthropic / 自定义API
├── auto_exam_solver.spec  # PyInstaller 打包配置
├── start_edge.bat    # Edge 调试模式启动脚本
├── requirements.txt  # Python 依赖
└── README.md         # 用户使用文档
```

### 各模块职责

- **`main.py`** — 程序入口和答题流程编排。加载配置、校验 API Key（首次使用自动提示输入并保存）、创建浏览器控制器和 LLM 客户端，运行异步答题主循环。
- **`browser.py`** — 浏览器自动化层。通过 Playwright CDP 连接到用户浏览器，注入 JS 脚本从 `.subject-item` 容器提取题目结构化数据，提供 5 层兜底点击策略将答案填回页面。
- **`llm_client.py`** — 大模型 API 客户端。统一 OpenAI 和 Anthropic 两种 SDK 的调用接口，自动解析 LLM 返回的 JSON（支持 3 层解析兜底）。
- **`config.yaml`** — 用户配置文件。包含 LLM API 密钥、模型选择、浏览器类型、自动化参数等。
- **`auto_exam_solver.spec`** — PyInstaller 打包配置。定义 hidden imports、excludes、console 模式等。
- **`requirements.txt`** — Python 依赖清单。`openai`、`anthropic`、`pyyaml`、`playwright` 四个核心依赖。

## 环境要求

- Python 3.9+
- Edge 浏览器
- 大模型 API Key

## 开发环境搭建

```powershell
pip install -r requirements.txt
```

## 配置详解

### LLM 配置 (`llm` 段)

| 字段 | 说明 | 示例值 |
|------|------|--------|
| `provider` | 模型提供商 | `openai` / `anthropic` / `custom` |
| `api_key` | API 密钥 | `sk-xxx` |
| `model` | 模型名称 | `gpt-4o` / `deepseek-v4-flash` / `claude-sonnet-4-6` |
| `base_url` | 自定义API地址 | `https://api.deepseek.com` |
| `max_tokens` | 单次回复最大 token 数 | `2000` |
| `temperature` | 生成温度（0=确定，1=随机） | `0.1` |

> `base_url` 不需要带 `/v1` 后缀，OpenAI SDK 会自动拼接 `/v1/chat/completions`。

**各 provider 配置示例：**

- **OpenAI 官方：**
  ```yaml
  provider: openai
  api_key: "sk-xxx"
  model: "gpt-4o"
  base_url: ""
  ```

- **DeepSeek：**
  ```yaml
  provider: openai
  api_key: "sk-xxx"
  model: "deepseek-v4-flash"
  base_url: "https://api.deepseek.com"
  ```

- **Anthropic Claude：**
  ```yaml
  provider: anthropic
  api_key: "sk-ant-xxx"
  model: "claude-sonnet-4-6"
  base_url: ""
  ```

- **其他 OpenAI 兼容服务（Ollama / vLLM 等）：**
  ```yaml
  provider: custom
  api_key: "not-needed"
  model: "llama3"
  base_url: "http://localhost:11434"
  ```

### 浏览器配置 (`browser` 段)

| 字段 | 说明 | 默认值 |
|------|------|--------|
| `browser_type` | 浏览器类型 | `edge` |
| `cdp_url` | CDP 远程调试地址 | `http://localhost:9222` |
| `exam_url_pattern` | 考试页面URL匹配规则（正则） | `yuketang.cn/exam` |

### 自动化配置 (`automation` 段)

| 字段 | 说明 | 默认值 |
|------|------|--------|
| `click_delay` | 点击选项前等待时间（秒） | `0.5` |

## 支持的题型

脚本从页面的 `.item-type` 元素检测题型，针对不同题型使用不同的 LLM 提示词策略：

| 题型 | 检测关键词 | LLM提示词策略 | 答案格式 |
|------|-----------|-------------|---------|
| 单选题 | 单选 | 发送题目文字 + 全部选项 | 单个字母，如 `A` |
| 多选题 | 多选 | 发送题目文字 + 全部选项 | 多个字母连写，如 `ABD` |
| 判断题 | 判断 | 仅发送题目文字，告知 A=正确 B=错误 | 单个字母 `A` 或 `B` |
| 填空题 | 填空 | 仅发送题目文字 | 填空答案文字 |

## 选项点击策略

5 层兜底策略依次尝试点击答案选项：

| 策略 | 方式 | 适用场景 |
|------|------|---------|
| 1 | JS点击 radio/checkbox input + 派发事件 | Vue/React等框架的响应式组件 |
| 2 | JS直接点击 li 元素 | 事件绑定在li上的页面 |
| 3 | JS点击 .custom_ueditor_cn_body | 雨课堂等使用富文本编辑器的平台 |
| 4 | 判断题文字搜索 | 判断题DOM结构不规范的页面 |
| 5 | Playwright文本匹配 | 以上策略均失败时的通用兜底 |

## LLM 响应解析策略

3 层兜底解析 LLM 返回的 JSON：

1. 直接 `json.loads()` 解析
2. 去掉 markdown 代码块标记（```json ... ```）后解析
3. 正则匹配第一个 `{...}` 结构（LLM 输出格式不规范时兜底）

## 打包为 exe

```powershell
# 建议在干净 venv 中构建，避免自动检测到全局包
python -m venv .build_venv
.build_venv\Scripts\activate
pip install -r requirements.txt
pip install pyinstaller

# 构建
pyinstaller auto_exam_solver.spec

# 输出在 dist/AutoExamSolver.exe
```
