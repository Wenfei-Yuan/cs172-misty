from __future__ import annotations

from dataclasses import dataclass
import re

from utils.openai_client import get_openai_client


@dataclass(frozen=True)
class FocusPromptResult:
    summary: str
    reminder: str
    used_fallback: bool = False
    reason: str | None = None


def _text_model(cfg) -> str:
    return str(getattr(cfg, "text_model", "gpt-4o-mini"))


def _clean_single_sentence(text: str, fallback: str) -> str:
    if not isinstance(text, str):
        return fallback
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return fallback
    parts = re.split(r"(?<=[。！？.!?])\s*", cleaned)
    first = next((p.strip() for p in parts if p.strip()), "")
    return first or fallback


def _fallback_reminder() -> str:
    return "Let's gently return to what you were reading and keep going."


def generate_focus_reminder_from_text(reading_text: str, cfg) -> FocusPromptResult:
    normalized_text = (reading_text or "").strip()
    if not normalized_text:
        reminder = _fallback_reminder()
        return FocusPromptResult(
            summary="",
            reminder=_clean_single_sentence(reminder, "Let's gently return to what you were reading and keep going."),
            used_fallback=True,
            reason="missing_text",
        )

    try:
        client = get_openai_client(cfg)
        reminder_response = client.chat.completions.create(
            model=_text_model(cfg),
            temperature=0.5,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a study assistant helping users with ADHD stay focused while reading. "
                        "First, silently identify the main idea of the passage for yourself. "
                        "Then generate one gentle, encouraging spoken reminder that helps the user resume reading without pressure. "
                        "The output must be in English and exactly one sentence, with no preface, no explanation, and no critical or commanding tone."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Here is the passage the user was reading:\n{normalized_text}",
                },
            ],
        )
        reminder_raw = reminder_response.choices[0].message.content
        reminder = _clean_single_sentence(
            reminder_raw if isinstance(reminder_raw, str) else "",
            _fallback_reminder(),
        )
        return FocusPromptResult(summary="", reminder=reminder)
    except Exception as exc:
        reminder = _clean_single_sentence(_fallback_reminder(), _fallback_reminder())
        return FocusPromptResult(
            summary="",
            reminder=reminder,
            used_fallback=True,
            reason=str(exc),
        )
