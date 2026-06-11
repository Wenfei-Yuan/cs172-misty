from __future__ import annotations

from dataclasses import dataclass
import json
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


def _clean_spoken_prompt(text: str, fallback: str) -> str:
    if not isinstance(text, str):
        return fallback
    cleaned = re.sub(r"\s+", " ", text).strip().strip('"')
    if not cleaned:
        return fallback
    parts = [p.strip() for p in re.split(r"(?<=[。！？.!?])\s*", cleaned) if p.strip()]
    if not parts:
        return fallback
    return " ".join(parts[:4])


def _reading_context(reading_text: str) -> dict:
    normalized = (reading_text or "").strip()
    if not normalized:
        return {}
    try:
        payload = json.loads(normalized)
    except json.JSONDecodeError:
        return {"text": normalized, "currentSentence": normalized}
    if not isinstance(payload, dict):
        return {"text": normalized, "currentSentence": normalized}
    return {key: value.strip() for key, value in payload.items() if isinstance(value, str) and value.strip()}


def _fallback_reminder() -> str:
    return "You are doing fine, let's gently hop back into the sentence you were reading. We can take it one small bit at a time together."


def generate_focus_reminder_from_text(reading_text: str, cfg) -> FocusPromptResult:
    context = _reading_context(reading_text)
    if not context:
        reminder = _fallback_reminder()
        return FocusPromptResult(
            summary="",
            reminder=_clean_spoken_prompt(reminder, _fallback_reminder()),
            used_fallback=True,
            reason="missing_text",
        )

    full_article = context.get("fullArticleText") or context.get("text") or ""
    read_so_far = context.get("readSoFarText") or context.get("text") or ""
    current_sentence = context.get("currentSentence") or context.get("text") or ""
    upcoming_text = context.get("upcomingText") or ""

    try:
        client = get_openai_client(cfg)
        reminder_response = client.chat.completions.create(
            model=_text_model(cfg),
            temperature=0.65,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a warm reading buddy helping a user return to an article. "
                        "Before writing, silently use the full article text, as if you are viewing the extension in full mode, "
                        "to understand what the whole article is about. "
                        "Then write a spoken prompt in English that is 3 to 4 short sentences total. "
                        "Sentence 1 should gently acknowledge the user's current sentence and encourage them. "
                        "Sentence 2 should summarize what they have read so far in one friendly sentence. "
                        "The last 1 to 2 sentences should make the upcoming content sound interesting and invite them to keep reading; "
                        "you may use a question if it feels natural, and it must include a concrete hint from the upcoming content. "
                        "Use casual, cute, companion-like wording, with no scolding, no pressure, no preface, and no labels."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Full article text from extension full mode:\n"
                        f"{full_article}\n\n"
                        "User's current sentence or current reading target:\n"
                        f"{current_sentence}\n\n"
                        "Text the user has already read or likely reached:\n"
                        f"{read_so_far}\n\n"
                        "Text coming next:\n"
                        f"{upcoming_text}"
                    ),
                },
            ],
        )
        reminder_raw = reminder_response.choices[0].message.content
        reminder = _clean_spoken_prompt(
            reminder_raw if isinstance(reminder_raw, str) else "",
            _fallback_reminder(),
        )
        return FocusPromptResult(summary=read_so_far[:500], reminder=reminder)
    except Exception as exc:
        reminder = _clean_spoken_prompt(_fallback_reminder(), _fallback_reminder())
        return FocusPromptResult(
            summary="",
            reminder=reminder,
            used_fallback=True,
            reason=str(exc),
        )
