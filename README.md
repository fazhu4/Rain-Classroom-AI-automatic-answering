# 网课自动答题脚本

浏览器直连 + 大模型 API，自动读取考试页面题目并作答。

## 快速开始

### 1. 安装依赖

```powershell
pip install -r requirements.txt
```

### 2. 配置 API

编辑 `config.yaml`，填入大模型 API Key：

```yaml
llm:
  provider: openai          # openai / anthropic / custom
  api_key: "sk-xxx"         # 你的 API Key
  model: "deepseek-chat"    # 模型名
  base_url: ""              # 自定义 API 地址（DeepSeek 填 https://api.deepseek.com/v1）
```

### 3. 启动浏览器调试模式

> Edge 有后台常驻进程，必须彻底关闭后再启动，否则端口不会监听。

**Windows：**
1. 关闭所有 Edge 窗口
2. `Ctrl+Shift+Esc` → 搜索 `msedge` → 结束所有残留进程
3. `Win+R` → 输入：`msedge.exe --remote-debugging-port=9222`
4. 验证：浏览器打开 `http://localhost:9222/json`，能看到 JSON 说明成功

**Chrome 同理：** `chrome.exe --remote-debugging-port=9222`

### 4. 运行

```powershell
# 激活 venv（如果有）
.\.venv\Scripts\Activate.ps1

# 启动脚本
python main.py
```

切到考试页面，按 **`Ctrl+Shift+A`** 触发答题，**`Ctrl+Shift+Q`** 退出。

## 两种模式

| 模式 | 配置 | 原理 | 优点 | 缺点 |
|------|------|------|------|------|
| **浏览器模式**（推荐） | `mode: browser` | Playwright 连浏览器 CDP，读 DOM 文本 → LLM → 浏览器内点击 | 精准、便宜、无 OCR 误差 | 需启动浏览器调试端口 |
| 截图模式 | `mode: screenshot` | mss 截图 → EasyOCR 识别 → LLM → pyautogui 模拟点击 | 无需浏览器配置 | OCR 可能识别错误、贵 |

## 常见问题

### Edge 连接失败 `ECONNREFUSED`

- 确认 Edge 已彻底关闭（任务管理器杀残留进程）
- 确认启动命令带了 `--remote-debugging-port=9222`
- 用 `http://localhost:9222/json` 验证端口是否监听

### 热键没反应

- 终端窗口必须在最前面才能捕获热键
- 如果用了管理员权限运行终端，脚本也需要管理员权限

### DeepSeek API 报错

- `provider` 设为 `openai` 或 `custom` 都可以
- `base_url` 必须带 `/v1`：`https://api.deepseek.com/v1`
- `model` 填 `deepseek-chat`

### OCR 加载很慢

首次运行 EasyOCR 会自动下载模型文件（~200MB），等待一次即可。
