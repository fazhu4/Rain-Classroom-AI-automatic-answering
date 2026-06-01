# 网课自动答题脚本

通过 Playwright CDP 协议连接本地浏览器，从考试页面 DOM 中提取题目，发送给大模型 API 作答，再将答案自动点击回页面。

有无法解决的问题联系我，主页有联系方式

## 环境要求

- Python 3.9+ （没有在）
- Edge 浏览器
- 大模型 API Key （我使用的是deepseek，只支持文本输入，所以没做图片传输，也太麻烦了，有图片的题目自己做一下就好了）

## 快速开始

### 1. 安装依赖

```powershell
pip install -r requirements.txt
```

### 2. 配置 API

编辑 `config.yaml`，填入大模型 API Key 和模型信息：
使用deepseek的话直接将项目里的api_key换成你的就可以了
```yaml
llm:
  provider: openai              # 模型提供商：openai / anthropic / custom
  api_key: "sk-你的API-Key"     # API 密钥（必填）
  model: "deepseek-v4-flash"     # 模型名称
  base_url: "https://api.deepseek.com"  # 自定义API地址（使用DeepSeek等第三方时填写）
```

### 3. 启动浏览器调试模式

> Edge 有后台常驻进程，端口被占用会导致连接失败，必须彻底关闭后再启动。

**步骤：**
1. 关闭所有 Edge 窗口
2. `Ctrl+Shift+Esc` 打开任务管理器 → 搜索 `msedge` → 结束所有残留进程
3. `Win+R` 运行：`msedge.exe --remote-debugging-port=9222`
4. 验证端口是否监听：浏览器打开 `http://localhost:9222/json`，能看到页面列表的 JSON 数据说明成功
5. 在新启动的浏览器中打开考试页面并登录

### 4. 运行脚本

```powershell
python main.py
```

脚本启动后会自动：
- 连接到浏览器的 CDP 调试端口
- 在所有标签页中匹配考试页面
- 提取所有题目并开始逐题作答

按 `Ctrl+C` 可随时终止程序。

## 配置详解

### LLM 配置 (`llm` 段)

| 字段 | 说明 | 示例值 |
|------|------|--------|
| `provider` | 模型提供商 | `openai` / `anthropic` / `custom` |
| `api_key` | API 密钥 | `sk-xxx` |
| `model` | 模型名称 | `gpt-4o` / `deepseek-v4-flash` / `claude-sonnet-4-6` |
| `base_url` | 自定义API地址（仅 `custom` 或使用第三方OpenAI兼容服务时填写） | `https://api.deepseek.com` |
| `max_tokens` | 单次回复最大 token 数 | `2000` |
| `temperature` | 生成温度（0=确定，1=随机） | `0.1` |


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

脚本会自动从页面的 `.item-type` 元素检测题型，针对不同题型使用不同的 LLM 提示词策略：

| 题型 | 检测关键词 | LLM提示词策略 | 答案格式 |
|------|-----------|-------------|---------|
| 单选题 | 单选 | 发送题目文字 + 全部选项 | 单个字母，如 `A` |
| 多选题 | 多选 | 发送题目文字 + 全部选项 | 多个字母连写，如 `ABD` |
| 判断题 | 判断 | 仅发送题目文字，告知 A=正确 B=错误 | 单个字母 `A` 或 `B` |
| 填空题 | 填空 | 仅发送题目文字 | 填空答案文字 |

## 选项点击策略

为防止页面DOM结构差异导致点击失败，脚本使用5层兜底策略依次尝试点击答案选项：

| 策略 | 方式 | 适用场景 |
|------|------|---------|
| 1 | JS点击 radio/checkbox input + 派发事件 | Vue/React等框架的响应式组件 |
| 2 | JS直接点击 li 元素 | 事件绑定在li上的页面 |
| 3 | JS点击 .custom_ueditor_cn_body | 雨课堂等使用富文本编辑器的平台 |
| 4 | 判断题文字搜索 | 判断题DOM结构不规范的页面 |
| 5 | Playwright文本匹配 | 以上策略均失败时的通用兜底 |

## 文件结构

```
auto/
├── config.yaml       # 用户配置文件（API Key、浏览器类型等）
├── main.py           # 主入口：加载配置 → 校验API Key → 启动答题循环
├── browser.py        # 浏览器控制：CDP连接 → DOM提取 → 选项点击 → 翻题
├── llm_client.py     # LLM客户端：统一封装 OpenAI / Anthropic / 自定义API
├── requirements.txt  # Python 依赖
└── README.md         # 本文件
```

### 各模块职责

- **`main.py`** — 程序入口和答题流程编排。加载配置、校验API Key、创建浏览器控制器和LLM客户端，运行异步答题主循环（连接→提取→逐题作答→清理）。
- **`browser.py`** — 浏览器自动化层。通过 Playwright CDP 连接到用户浏览器，注入JS脚本从 `.subject-item` 容器提取题目结构化数据，提供5层兜底点击策略将答案填回页面。
- **`llm_client.py`** — 大模型API客户端。统一 OpenAI 和 Anthropic 两种SDK的调用接口，自动解析LLM返回的JSON（支持3层解析兜底：直接解析→去markdown标记→正则匹配）。
- **`config.yaml`** — 用户配置文件。包含LLM API密钥、模型选择、浏览器类型、自动化参数等所有可配置项。
- **`requirements.txt`** — Python依赖清单。包含 openai、anthropic、pyyaml、playwright 四个核心依赖。

## 常见问题

### Edge 连接失败 `ECONNREFUSED`

- 确认 Edge 已彻底关闭（任务管理器搜索 `msedge`，结束所有残留进程）
- 确认启动命令带了 `--remote-debugging-port=9222` 参数
- 用浏览器打开 `http://localhost:9222/json` 验证端口是否在监听
- 如果返回 JSON 数据说明端口正常；如果无法访问说明端口未开启

### 未能自动匹配到考试页面

- 确认考试页面已经在调试模式启动的浏览器中打开
- 确认 `exam_url_pattern` 配置正确（默认识别 `yuketang.cn/exam`）
- 程序会列出所有打开的标签页，可以手动输入序号选择目标页面

### 题目提取不完整

- 脚本通过 `.exam-main--content .subject-item` 选择器提取题目
- 如果发现某些题目未被提取，可能是页面结构不匹配
- 可修改 `browser.py` 中 `_EXTRACT_SCRIPT` 的 CSS 选择器适配特定平台

### DeepSeek API 报错

- `provider` 设为 `openai` 或 `custom` 均可
- `base_url` 填 `https://api.deepseek.com`（不需要带 `/v1`，SDK会自动拼接）
- `model` 填正确的模型名，如 `deepseek-v4-flash`、`deepseek-chat` 等

### 点击不生效

脚本内置5层点击兜底策略，正常情况下至少有一种能生效。如果全部失败：
- 检查浏览器页面是否在前台可见
- 检查页面DOM结构是否与预期差异较大
- 查看控制台输出的错误信息定位具体原因
