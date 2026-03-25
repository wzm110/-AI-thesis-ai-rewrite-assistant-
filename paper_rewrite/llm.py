"""调用各厂商 LLM HTTP API（解析响应做防御性检查）。"""
from __future__ import annotations

from typing import Any

import httpx

from paper_rewrite.paths import server_logger


async def request_llm_rewrite(
    client: httpx.AsyncClient,
    content: str,
    llm_cfg: dict[str, Any],
) -> str:
    provider = llm_cfg["provider"]
    timeout = llm_cfg["request_timeout_sec"]
    temperature = llm_cfg["temperature"]

    if provider == "openai_compatible":
        body: dict[str, Any] = {
            "model": llm_cfg["model"],
            "messages": [{"role": "user", "content": content}],
            "temperature": temperature,
        }
        mt = llm_cfg.get("max_tokens")
        if mt is not None:
            body["max_tokens"] = mt

        resp = await client.post(
            f'{llm_cfg["api_base"]}/chat/completions',
            headers={
                "Authorization": f'Bearer {llm_cfg["api_key"]}',
                "Content-Type": "application/json",
            },
            json=body,
            timeout=timeout,
        )
        resp.raise_for_status()
        try:
            data = resp.json()
        except Exception as e:
            raise RuntimeError(f"LLM 返回非 JSON: {resp.text[:800]}") from e

        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise RuntimeError(f"LLM 返回无 choices: {resp.text[:800]}")
        msg = choices[0].get("message")
        if not isinstance(msg, dict):
            raise RuntimeError(f"LLM 返回 message 异常: {resp.text[:800]}")
        content_out = msg.get("content")
        if content_out is None:
            raise RuntimeError(f"LLM 返回无 content: {resp.text[:800]}")
        return str(content_out).strip()

    if provider == "anthropic":
        resp = await client.post(
            f'{llm_cfg["api_base"]}/messages',
            headers={
                "x-api-key": llm_cfg["api_key"],
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json={
                "model": llm_cfg["model"],
                "max_tokens": llm_cfg.get("anthropic_max_tokens", 2048),
                "temperature": temperature,
                "messages": [{"role": "user", "content": content}],
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        try:
            data = resp.json()
        except Exception as e:
            raise RuntimeError(f"LLM 返回非 JSON: {resp.text[:800]}") from e
        text_parts = [p.get("text", "") for p in data.get("content", []) if p.get("type") == "text"]
        out = "".join(text_parts).strip()
        if not out and not text_parts:
            server_logger.warning("anthropic_empty_text | body_snip=%s", resp.text[:500])
        return out

    if provider == "gemini":
        gen_cfg: dict[str, Any] = {"temperature": temperature}
        mt = llm_cfg.get("max_tokens")
        if mt is not None:
            gen_cfg["maxOutputTokens"] = mt

        resp = await client.post(
            f'{llm_cfg["api_base"]}/models/{llm_cfg["model"]}:generateContent?key={llm_cfg["api_key"]}',
            headers={"Content-Type": "application/json"},
            json={
                "contents": [{"parts": [{"text": content}]}],
                "generationConfig": gen_cfg,
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        try:
            data = resp.json()
        except Exception as e:
            raise RuntimeError(f"LLM 返回非 JSON: {resp.text[:800]}") from e
        candidates = data.get("candidates", [])
        if not candidates:
            raise RuntimeError(f"Gemini 返回为空 candidates: {resp.text[:800]}")
        parts = candidates[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts)
        return text.strip()

    raise RuntimeError(f"Unsupported provider: {provider}")
