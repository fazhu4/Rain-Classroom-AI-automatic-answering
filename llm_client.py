"""
LLM API 客户端
统一封装 OpenAI / Anthropic / 自定义兼容接口，所有 provider 返回统一的 dict 格式：
    {"answer": "A", "explanation": "简要解析", "question_type": "single_choice"}
"""
import json
import re


class LLMClient:
    """统一的大模型 API 客户端，支持 OpenAI 和 Anthropic 两种 SDK，以及 OpenAI 兼容的自定义 API"""

    def __init__(self, config: dict):
        """
        从配置字典的 llm 段读取所有参数
        Args:
            config: YAML配置中 llm 段的字典，包含：
                - provider: 模型提供商，可选 "openai" / "anthropic" / "custom"
                - api_key:  API 密钥
                - model:    模型名称，如 "gpt-4o" / "deepseek-v4-flash"
                - base_url: 自定义API地址（仅 provider=custom 时生效）
                - max_tokens: 最大输出 token 数
                - temperature: 生成随机性参数（0=确定性，1=随机）
        """
        # 模型提供商标识，决定走哪个SDK路径
        self.provider = config.get("provider", "openai")
        # API密钥，必填
        self.api_key = config.get("api_key", "")
        # 模型名称，传给SDK的model参数
        self.model = config.get("model", "gpt-4o")
        # 自定义API地址（如 DeepSeek、Ollama 等 OpenAI 兼容服务）
        self.base_url = config.get("base_url", "")
        # 限制LLM单次回复的最大token数
        self.max_tokens = config.get("max_tokens", 2000)
        # 温度参数：0.1 接近确定性输出，适合考试答题场景
        self.temperature = config.get("temperature", 0.1)

    # ==================== 公开接口 ====================

    # 文本问答入口：接收 system 和 user 提示词，按 provider 分发给对应SDK，
    # 返回统一格式的答题结果dict。
    # Args:
    #     system_prompt: 系统角色提示词（设定AI行为和回答格式）
    #     user_message:  用户问题内容（题目文字+选项）
    # Returns: dict — {"answer": "A", "explanation": "...", "question_type": "..."}
    def ask_text(self, system_prompt: str, user_message: str) -> dict:
        if self.provider == "openai" or self.provider == "custom":
            # OpenAI 和 custom 都走 OpenAI 兼容接口
            # custom 模式通过 base_url 区分，如 https://api.deepseek.com
            return self._call_openai(system_prompt, user_message)
        elif self.provider == "anthropic":
            return self._call_anthropic(system_prompt, user_message)
        else:
            # 不支持的 provider 抛出异常而非静默失败
            raise ValueError(f"不支持的 provider: {self.provider}")

    # ==================== OpenAI 调用路径 ====================

    # 调用 OpenAI 兼容API（包括 OpenAI 官方和 DeepSeek 等第三方）
    # Args:
    #     system_prompt: 系统提示词
    #     user_message:  用户消息
    # Returns: dict — 解析后的答题结果
    def _call_openai(self, system_prompt: str, user_message: str) -> dict:
        from openai import OpenAI

        # 构造OpenAI客户端参数
        kwargs = {"api_key": self.api_key}
        if self.base_url:
            # 如果配置了自定义地址则传入（DeepSeek、Ollama等）
            kwargs["base_url"] = self.base_url

        # 创建客户端实例
        client = OpenAI(**kwargs)
        # 发起聊天补全请求
        response = client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},  # 系统角色设定
                {"role": "user", "content": user_message},      # 用户问题
            ],
            max_tokens=self.max_tokens,      # 限制输出长度
            temperature=self.temperature,    # 控制输出随机性
        )
        # 从响应中提取文本内容并解析为JSON
        raw_text = response.choices[0].message.content
        return self._parse_json_response(raw_text)

    # ==================== Anthropic 调用路径 ====================

    # 调用 Anthropic Claude API
    # Args:
    #     system_prompt: 系统提示词
    #     user_message:  用户消息
    # Returns: dict — 解析后的答题结果
    def _call_anthropic(self, system_prompt: str, user_message: str) -> dict:
        import anthropic

        # 创建Anthropic客户端
        client = anthropic.Anthropic(api_key=self.api_key)
        # 发起消息请求（Anthropic的system参数是顶层字段，不同于OpenAI放在messages里）
        response = client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system_prompt,  # Claude的system prompt是独立参数
            messages=[{"role": "user", "content": user_message}],
        )
        # 从响应中提取文本（Anthropic返回格式与OpenAI不同）
        raw_text = response.content[0].text
        return self._parse_json_response(raw_text)

    # ==================== 响应解析 ====================

    # 从LLM回复中提取JSON，使用3层解析策略逐级兜底：
    #   策略1: 直接 json.loads() 解析（最理想的情况）
    #   策略2: 去掉markdown代码块标记(```json ... ```)后再解析
    #   策略3: 用正则匹配第一个 {...} 结构（LLM输出格式不规范时的兜底）
    # Args:
    #     text: LLM返回的原始文本
    # Returns: dict — 解析出的JSON字典，失败时返回 answer 为空的dict
    def _parse_json_response(self, text: str) -> dict:
        text = text.strip()

        # ---- 策略1: 直接解析JSON ----
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass  # 解析失败不报错，继续下一层策略

        # ---- 策略2: 去掉markdown代码块标记后解析 ----
        if text.startswith("```"):
            # 去掉开头的 ```json 或 ```
            lines = text.split("\n")
            text = "\n".join(lines[1:]) if len(lines) > 1 else text
            # 去掉结尾的 ```
            if text.endswith("```"):
                text = text[:-3]
            try:
                return json.loads(text.strip())
            except json.JSONDecodeError:
                pass  # 仍然失败，继续兜底

        # ---- 策略3: 正则匹配第一个 {...} JSON对象 ----
        # 用于LLM输出中夹杂额外文字的情况（如 "答案是：{...}"）
        match = re.search(r'\{[^}]+\}', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass  # 所有策略都失败

        # 所有解析策略均失败，返回空答案并保留原始文本在explanation中供排查
        return {"answer": "", "explanation": text, "question_type": "unknown"}
