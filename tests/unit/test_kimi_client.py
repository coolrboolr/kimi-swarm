"""Unit tests for KimiClient networking behavior."""

from __future__ import annotations

import pytest

from ambient.config import KimiConfig
from ambient.kimi_client import KimiClient


@pytest.mark.asyncio
async def test_disable_network_blocks_requests(monkeypatch):
    monkeypatch.setenv("AMBIENT_DISABLE_NETWORK", "1")

    client = KimiClient(KimiConfig())

    with pytest.raises(RuntimeError, match="AMBIENT_DISABLE_NETWORK"):
        await client.chat_completion(messages=[{"role": "user", "content": "hi"}])

