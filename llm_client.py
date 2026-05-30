"""
LLM API 客户端 - 支持 OpenAI / Anthropic / 自定义兼容接口
"""
import base64
import json
import io
from PIL import Image


class LLMClient:
    """统一的大模型 API 客户端"""

    def __init__(self, config: dict):
        self.provider = config.get("provider", "openai")
        self.api_key = config.get("api_key", "")
        self.model = config.get("model", "gpt-4o")
        self.base_url = config.get("base_url", "")
        self.max_tokens = config.get("max_tokens", 2000)
        self.temperature = config.get("temperature", 0.1)

    def ask_text(self, question_text: str) -> dict:
        """
        纯文本问答：发送题目文本，返回结构化答案
        返回: {"answer": "A", "explanation": "...", "question_type": "single_choice"}
        """
        prompt = self._build_text_prompt(question_text)

        if self.provider == "openai" or self.provider == "custom":
            return self._call_openai_text(prompt)
        elif self.provider == "anthropic":
            return self._call_anthropic_text(prompt)
        else:
            raise ValueError(f"不支持的 provider: {self.provider}")

    def ask_vision(self, question_text: str, image: Image.Image) -> dict:
        """
        视觉问答：发送截图 + OCR文字，返回结构化答案（含坐标）
        返回: {"answer": "A", "explanation": "...", "click_x": 100, "click_y": 200}
        """
        prompt = self._build_vision_prompt(question_text)

        if self.provider == "openai" or self.provider == "custom":
            return self._call_openai_vision(prompt, image)
        elif self.provider == "anthropic":
            return self._call_anthropic_vision(prompt, image)
        else:
            raise ValueError(f"不支持的 provider: {self.provider}")

    def _build_text_prompt(self, question_text: str) -> str:
        return f"""你是一个考试答题助手。请根据以下题目内容，给出正确答案。

{question_text}

请以 JSON 格式返回答案（只返回 JSON，不要其他内容）：
{{"answer": "选项字母或答案内容", "explanation": "简要解析", "question_type": "single_choice/multiple_choice/true_false/fill_blank"}}

注意：
- 单选题 answer 为单个字母，如 "A"
- 多选题 answer 为多个字母，如 "AB" 或 "ACD"
- 判断题 answer 为 "正确" 或 "错误"（或 "A"/"B"，视题目格式而定）
- 填空题 answer 为填空内容"""

    def _build_vision_prompt(self, ocr_text: str) -> str:
        return f"""你是一个考试答题助手。这是从考试页面截图中 OCR 识别出的文字内容：

{ocr_text}

请根据以上内容判断正确答案。请以 JSON 格式返回（只返回 JSON）：
{{"answer": "选项字母或答案内容", "explanation": "简要解析", "question_type": "single_choice/multiple_choice/true_false/fill_blank"}}

注意：
- 单选题 answer 为单个字母，如 "A"
- 多选题 answer 为多个字母，如 "AB" 或 "ACD"
- 判断题 answer 为 "正确" 或 "错误"
- 填空题 answer 为填空内容"""

    def _call_openai_text(self, prompt: str) -> dict:
        from openai import OpenAI

        kwargs = {"api_key": self.api_key}
        if self.base_url:
            kwargs["base_url"] = self.base_url

        client = OpenAI(**kwargs)
        response = client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": "你是一个准确的考试答题助手，始终以JSON格式返回答案。"},
                {"role": "user", "content": prompt},
            ],
            max_tokens=self.max_tokens,
            temperature=self.temperature,
        )
        return self._parse_json_response(response.choices[0].message.content)

    def _call_openai_vision(self, prompt: str, image: Image.Image) -> dict:
        from openai import OpenAI

        # 将图片转为 base64
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        img_b64 = base64.b64encode(buf.getvalue()).decode()

        kwargs = {"api_key": self.api_key}
        if self.base_url:
            kwargs["base_url"] = self.base_url

        client = OpenAI(**kwargs)
        response = client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": "你是一个准确的考试答题助手，始终以JSON格式返回答案。"},
                {"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
                ]},
            ],
            max_tokens=self.max_tokens,
            temperature=self.temperature,
        )
        return self._parse_json_response(response.choices[0].message.content)

    def _call_anthropic_text(self, prompt: str) -> dict:
        import anthropic

        client = anthropic.Anthropic(api_key=self.api_key)
        response = client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system="你是一个准确的考试答题助手，始终以JSON格式返回答案。",
            messages=[{"role": "user", "content": prompt}],
        )
        return self._parse_json_response(response.content[0].text)

    def _call_anthropic_vision(self, prompt: str, image: Image.Image) -> dict:
        import anthropic

        buf = io.BytesIO()
        image.save(buf, format="PNG")
        img_b64 = base64.b64encode(buf.getvalue()).decode()

        client = anthropic.Anthropic(api_key=self.api_key)
        response = client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system="你是一个准确的考试答题助手，始终以JSON格式返回答案。",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": img_b64}},
                    {"type": "text", "text": prompt},
                ],
            }],
        )
        return self._parse_json_response(response.content[0].text)

    def _parse_json_response(self, text: str) -> dict:
        """从 LLM 回复中提取 JSON"""
        text = text.strip()
        # 去掉可能的 markdown 代码块标记
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:]) if len(lines) > 1 else text
            if text.endswith("```"):
                text = text[:-3]
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # 尝试提取 JSON 片段
            import re
            match = re.search(r'\{[^}]+\}', text, re.DOTALL)
            if match:
                return json.loads(match.group())
            return {"answer": "", "explanation": text, "question_type": "unknown", "raw": text}
