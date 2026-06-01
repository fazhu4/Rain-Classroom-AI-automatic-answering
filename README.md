# 网课自动答题工具

一键式网课考试自动答题。你只需要启动浏览器、打开考试页面、双击 exe，剩下的交给程序。

## 你需要准备

- 一台 Windows 电脑
- Edge 浏览器（Windows 自带）
- 一个大模型 API Key（推荐 DeepSeek，注册即送免费额度）

## 使用步骤

### 第一步：获取 API Key（只需做一次）

访问 [DeepSeek 开放平台](https://platform.deepseek.com)，注册账号后在 API Keys 页面创建一个 Key 并复制下来。

> 其他支持的 API：OpenAI、Anthropic Claude，或任何 OpenAI 兼容接口。

### 第二步：启动浏览器

双击 `start_edge.bat`，等待 Edge 浏览器自动启动。

> 这个脚本会先关闭所有已打开的 Edge 窗口，然后以调试模式重新启动。不用担心丢失标签页，关闭前保存重要内容即可。

### 第三步：进入考试页面

在新打开的 Edge 浏览器中，登录你的网课平台，进入考试页面。

### 第四步：运行答题工具

双击 `AutoExamSolver.exe`。

- **首次使用**：会提示你粘贴 API Key，右键粘贴后按回车即可。Key 自动保存到配置文件，下次不用再输。
- **之后使用**：直接开始自动答题，控制台会显示每道题的答案和大模型解析。

按 `Ctrl+C` 可随时停止程序。

## 常见问题

### 双击 exe 提示"连接失败"

说明 Edge 没有以调试模式启动。请双击 `start_edge.bat` 重新启动浏览器后重试。

### 启动 start_edge.bat 后浏览器没反应

可能是 Edge 安装路径不同。右键 `start_edge.bat` 选"编辑"，修改 `msedge.exe` 的路径为你电脑上的实际位置。

### 未能自动匹配到考试页面

程序会列出浏览器所有打开的标签页，在控制台中输入考试页面对应的数字序号，按回车即可手动选择。

### 想要更换 API Key

用记事本打开 `config.yaml`，找到 `api_key: "xxx"` 这一行，把引号里的内容改成新的 Key 即可。

也可以用记事本打开后把 `api_key: "xxx"` 改成 `api_key: ""` 保存，重新启动 exe 会再次提示你输入。

### 想要用其他模型

用记事本打开 `config.yaml`，修改以下字段：
- `provider`：`openai`（OpenAI 或 DeepSeek）、`anthropic`（Claude）、`custom`（其他兼容接口）
- `model`：模型名称
- `base_url`：API 地址（用 DeepSeek 填 `https://api.deepseek.com`，用 OpenAI 留空）

## 技术文档

如需了解题目提取原理、点击策略、模块架构等技术细节，请查看 [DEVELOPER.md](DEVELOPER.md)。
