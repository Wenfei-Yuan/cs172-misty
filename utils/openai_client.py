from __future__ import annotations

from functools import lru_cache

import openai


def _openai_timeout_s(cfg) -> float:
    return max(1.0, float(getattr(cfg, "openai_timeout_s", 20.0)))


@lru_cache(maxsize=8)
def _cached_openai_client(api_key: str, timeout_s: float):
    return openai.OpenAI(api_key=api_key, timeout=timeout_s)


def get_openai_client(cfg):
    return _cached_openai_client(cfg.openai_api_key, _openai_timeout_s(cfg))
