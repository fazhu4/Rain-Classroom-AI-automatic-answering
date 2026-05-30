"""
LLM API 客户端 - 支持 OpenAI / Anthropic / 自定义兼容接口
"""
import json


class LLMClient:
    """统一的大模型 API 客户端"""

    def __init__(self, config: dict):
        self.provider = config.get("provider", "openai")
        self.api_key = config.get("api_key", "")
        self.model = config.get("model", "gpt-4o")
        self.base_url = config.get("base_url", "")
        self.max_tokens = config.get("max_tokens", 2000)
        self.temperature = config.get("temperature", 0.1)

    def ask_text(self, system_prompt: str, user_message: str) -> dict:
        """
        文本问答，返回结构化答案。
        system_prompt: 系统角色提示，user_message: 用户问题内容
        返回: {"answer": "A", "explanation": "...", "question_type": "single_choice"}
        """
        if self.provider == "openai" or self.provider == "custom":
            return self._call_openai(system_prompt, user_message)
        elif self.provider == "anthropic":
            return self._call_anthropic(system_prompt, user_message)
        else:
            raise ValueError(f"不支持的 provider: {self.provider}")

    def _call_openai(self, system_prompt: str, user_message: str) -> dict:
        from openai import OpenAI

        kwargs = {"api_key": self.api_key}
        if self.base_url:
            kwargs["base_url"] = self.base_url

        client = OpenAI(**kwargs)
        response = client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            max_tokens=self.max_tokens,
            temperature=self.temperature,
        )
        return self._parse_json_response(response.choices[0].message.content)

    def _call_anthropic(self, system_prompt: str, user_message: str) -> dict:
        import anthropic

        client = anthropic.Anthropic(api_key=self.api_key)
        response = client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        return self._parse_json_response(response.content[0].text)

    def _parse_json_response(self, text: str) -> dict:
        """从 LLM 回复中提取 JSON"""
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:]) if len(lines) > 1 else text
            if text.endswith("```"):
                text = text[:-3]
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            import re
            match = re.search(r'\{[^}]+\}', text, re.DOTALL)
            if match:
                return json.loads(match.group())
            return {"answer": "", "explanation": text, "question_type": "unknown", "raw": text}
