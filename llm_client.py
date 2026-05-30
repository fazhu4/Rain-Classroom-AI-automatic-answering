"""
LLM API 客户端 - 统一封装 OpenAI / Anthropic / 自定义兼容接口，所有 provider 返回统一 JSON 格式
"""
import base64  # 图片→base64编码，用于视觉模式发送图片
import json    # 解析LLM返回的JSON格式答案
import io      # 内存字节流，图片编码时避免写磁盘
from PIL import Image  # 图像处理，截图模式下的图片对象


class LLMClient:
    """统一的大模型 API 客户端"""

    # 初始化LLM客户端，从配置字典读取provider/api_key/model等参数。config: YAML中llm段的配置字典
    def __init__(self, config: dict):
        self.provider = config.get("provider", "openai")      # API提供商：openai/anthropic/custom
        self.api_key = config.get("api_key", "")              # API密钥
        self.model = config.get("model", "gpt-4o")            # 模型名称
        self.base_url = config.get("base_url", "")            # 自定义API地址（仅custom模式生效）
        self.max_tokens = config.get("max_tokens", 2000)      # 最大生成token数
        self.temperature = config.get("temperature", 0.1)     # 温度参数（越低答案越确定）

    # 纯文本问答：发送题面文本给LLM，返回{answer, explanation, question_type}。
    # question_text: 拼接好的题面字符串（题干+选项+题型）
    def ask_text(self, question_text: str) -> dict:
        prompt = self._build_text_prompt(question_text)  # 构建包含system提示和答题格式要求的完整prompt

        if self.provider == "openai" or self.provider == "custom":  # OpenAI及兼容API（DeepSeek/Ollama/vLLM等）
            return self._call_openai_text(prompt)
        elif self.provider == "anthropic":
            return self._call_anthropic_text(prompt)    # Anthropic Claude API
        else:
            raise ValueError(f"不支持的 provider: {self.provider}")

    # 视觉问答：发送截图+OCR文字给多模态LLM，返回{answer, explanation, question_type}。
    # question_text: OCR识别出的页面文字, image: 截图的PIL Image对象
    def ask_vision(self, question_text: str, image: Image.Image) -> dict:
        prompt = self._build_vision_prompt(question_text)  # 构建视觉模式prompt（强调OCR文字为参考上下文）

        if self.provider == "openai" or self.provider == "custom":
            return self._call_openai_vision(prompt, image)  # OpenAI视觉API（图片base64嵌入）
        elif self.provider == "anthropic":
            return self._call_anthropic_vision(prompt, image)  # Anthropic视觉API（图片base64+文字）
        else:
            raise ValueError(f"不支持的 provider: {self.provider}")

    # 构建文本模式的system prompt，引导LLM只返回JSON。question_text: 拼接好的完整题面文本
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

    # 构建视觉模式prompt，告知LLM OCR文字作为上下文参考。ocr_text: EasyOCR识别出的页面所有文字
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

    # 调用OpenAI（或兼容）文本API，发送题面→解析返回JSON。prompt: 完整的prompt字符串
    def _call_openai_text(self, prompt: str) -> dict:
        from openai import OpenAI

        # 构建客户端参数：API Key必须，base_url可选（custom模式指定自定义地址）
        kwargs = {"api_key": self.api_key}
        if self.base_url:
            kwargs["base_url"] = self.base_url    # 设置自定义API地址（DeepSeek/Ollama等）

        client = OpenAI(**kwargs)                 # 创建OpenAI客户端实例
        response = client.chat.completions.create(  # 调用聊天补全API
            model=self.model,
            messages=[
                {"role": "system", "content": "你是一个准确的考试答题助手，始终以JSON格式返回答案。"},
                {"role": "user", "content": prompt},
            ],
            max_tokens=self.max_tokens,
            temperature=self.temperature,
        )
        return self._parse_json_response(response.choices[0].message.content)  # 提取LLM回复中的JSON

    # 调用OpenAI视觉API：将图片转base64嵌入请求，OCR文字作为辅助文本。prompt: 文本prompt, image: 截图PIL对象
    def _call_openai_vision(self, prompt: str, image: Image.Image) -> dict:
        from openai import OpenAI

        # 将PIL图片编码为base64字符串（data:image/png;base64,xxx格式）
        buf = io.BytesIO()                              # 创建内存字节缓冲区
        image.save(buf, format="PNG")                   # 图片以PNG格式保存到内存
        img_b64 = base64.b64encode(buf.getvalue()).decode()  # 编码为base64字符串

        kwargs = {"api_key": self.api_key}
        if self.base_url:
            kwargs["base_url"] = self.base_url

        client = OpenAI(**kwargs)
        response = client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": "你是一个准确的考试答题助手，始终以JSON格式返回答案。"},
                {"role": "user", "content": [                        # 多模态消息：文本+图片
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
                ]},
            ],
            max_tokens=self.max_tokens,
            temperature=self.temperature,
        )
        return self._parse_json_response(response.choices[0].message.content)

    # 调用Anthropic Claude文本API，使用Messages API发送题面。prompt: 完整prompt字符串
    def _call_anthropic_text(self, prompt: str) -> dict:
        import anthropic

        client = anthropic.Anthropic(api_key=self.api_key)  # 创建Anthropic客户端
        response = client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system="你是一个准确的考试答题助手，始终以JSON格式返回答案。",  # Anthropic的system独立于messages
            messages=[{"role": "user", "content": prompt}],
        )
        return self._parse_json_response(response.content[0].text)  # Anthropic返回content列表，取第一个block

    # 调用Anthropic Claude视觉API，图片base64+文字一同发送。prompt: 文本prompt, image: 截图PIL对象
    def _call_anthropic_vision(self, prompt: str, image: Image.Image) -> dict:
        import anthropic

        # 图片编码为base64
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
                "content": [                                                  # Anthropic多模态格式
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": img_b64}},
                    {"type": "text", "text": prompt},
                ],
            }],
        )
        return self._parse_json_response(response.content[0].text)

    # 从LLM回复文本中提取JSON对象，兼容markdown代码块、JSON数组等多种格式。
    # text: LLM返回的原始文本内容
    def _parse_json_response(self, text: str) -> dict:
        text = text.strip()  # 去除首尾空白字符

        # 去掉可能的markdown代码块标记（```json ... ``` 或 ``` ... ```）
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:]) if len(lines) > 1 else text  # 去掉第一行```标记
            if text.endswith("```"):
                text = text[:-3]  # 去掉结尾```标记

        try:
            result = json.loads(text)          # 尝试直接解析JSON字符串
            if isinstance(result, list):       # LLM返回JSON数组时取第一个元素兜底
                result = result[0] if result else {}
            return result
        except json.JSONDecodeError:
            # 直接解析失败时用正则从文本中提取第一个{...}花括号块
            import re
            match = re.search(r'\{[^}]+\}', text, re.DOTALL)
            if match:
                return json.loads(match.group())  # 解析提取到的JSON片段
            # 完全无法解析时返回原始文本作为explanation
            return {"answer": "", "explanation": text, "question_type": "unknown", "raw": text}
