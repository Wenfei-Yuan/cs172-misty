from __future__ import annotations

from functools import lru_cache

import openai


def _openai_timeout_s(cfg) -> float:
    return max(1.0, float(getattr(cfg, "openai_timeout_s", 20.0)))


def _openai_max_retries(cfg) -> int:
    return max(0, int(getattr(cfg, "openai_max_retries", 0)))


@lru_cache(maxsize=8)
def _cached_openai_client(api_key: str, timeout_s: float, max_retries: int):
    return openai.OpenAI(api_key=api_key, timeout=timeout_s, max_retries=max_retries)


def get_openai_client(cfg):
    return _cached_openai_client(
        cfg.openai_api_key,
        _openai_timeout_s(cfg),
        _openai_max_retries(cfg),
    )
