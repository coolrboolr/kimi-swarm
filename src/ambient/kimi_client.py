"""Kimi K2.5 client with async support, streaming, and retry logic.

Provides robust communication with Kimi K2.5 via OpenAI-compatible API.
"""

from __future__ import annotations

import asyncio
import os
import random
from typing import Any, AsyncIterator

import httpx

from .config import KimiConfig


class KimiClient:
    """
    Async HTTP client for Kimi K2.5 with retry/backoff logic.

    Features:
    - Exponential backoff with jitter for rate limits
    - Concurrency limiting via semaphore
    - Streaming support for progressive responses
    - Automatic retry on transient failures
    """

    def __init__(self, config: KimiConfig):
        self.config = config
        self.semaphore = asyncio.Semaphore(config.max_concurrency)
        self.retry_max = int(os.getenv("AMBIENT_RETRY_MAX", "6"))

    async def chat_completion(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
    ) -> dict[str, Any]:
        """
        Send chat completion request with retry/backoff logic.

        Args:
            messages: List of message dicts with "role" and "content"
            temperature: Sampling temperature (default: from config)

        Returns:
            Response dict with "choices" containing the completion

        Retry strategy:
        - 429 (rate limit): Exponential backoff with jitter
        - 503/504 (server error): Exponential backoff
        - 400/401/403: No retry (client error)
        - Network errors: Retry with backoff

        Raises:
            Exception: After max retries exceeded or on client errors
        """
        if temperature is None:
            temperature = self.config.temperature

        async with self.semaphore:  # Limit concurrency
            for attempt in range(self.retry_max):
                try:
                    async with httpx.AsyncClient(
                        timeout=self.config.timeout_seconds
                    ) as client:
                        response = await client.post(
                            f"{self.config.base_url}/chat/completions",
                            json={
                                "model": self.config.model_id,
                                "messages": messages,
                                "temperature": temperature,
                            },
                        )

                        if response.status_code == 200:
                            return response.json()

                        # Retry on transient errors
                        if response.status_code in [429, 503, 504]:
                            sleep_time = (2**attempt) * 0.5  # Exponential backoff
                            jitter = random.uniform(0, 0.1 * sleep_time)
                            await asyncio.sleep(sleep_time + jitter)
                            continue

                        # Don't retry on client errors
                        response.raise_for_status()

                except (httpx.NetworkError, httpx.TimeoutException) as e:
                    if attempt < self.retry_max - 1:
                        sleep_time = (2**attempt) * 0.5
                        await asyncio.sleep(sleep_time)
                        continue
                    raise Exception(
                        f"Network error after {self.retry_max} attempts: {e}"
                    ) from e

            raise Exception(f"Max retries ({self.retry_max}) exceeded")

    async def chat_completion_stream(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """
        Send streaming chat completion request.

        Args:
            messages: List of message dicts with "role" and "content"
            temperature: Sampling temperature (default: from config)

        Yields:
            Chunk dicts with delta content

        Example:
            async for chunk in client.chat_completion_stream(messages):
                delta = chunk["choices"][0]["delta"]
                if "content" in delta:
                    print(delta["content"], end="")
        """
        if temperature is None:
            temperature = self.config.temperature

        async with self.semaphore:
            async with httpx.AsyncClient(
                timeout=self.config.timeout_seconds
            ) as client:
                async with client.stream(
                    "POST",
                    f"{self.config.base_url}/chat/completions",
                    json={
                        "model": self.config.model_id,
                        "messages": messages,
                        "temperature": temperature,
                        "stream": True,
                    },
                ) as response:
                    response.raise_for_status()

                    async for line in response.aiter_lines():
                        if not line.strip():
                            continue
                        if line.startswith("data: "):
                            line = line[6:]  # Remove "data: " prefix
                        if line == "[DONE]":
                            break

                        try:
                            import json

                            chunk = json.loads(line)
                            yield chunk
                        except json.JSONDecodeError:
                            # Skip malformed lines
                            continue

    async def health_check(self) -> bool:
        """
        Check if Kimi API is accessible.

        Returns:
            True if API responds, False otherwise
        """
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.config.base_url}/models")
                return response.status_code == 200
        except Exception:
            return False

    async def list_models(self) -> list[str]:
        """
        List available models.

        Returns:
            List of model IDs
        """
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{self.config.base_url}/models")
                if response.status_code == 200:
                    data = response.json()
                    return [model["id"] for model in data.get("data", [])]
        except Exception:
            pass
        return []
