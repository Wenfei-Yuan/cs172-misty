from __future__ import annotations

import openai


def capture_frame(misty) -> str:
    try:
        response = misty.get_info("picture_rgb")
        payload = response.parse_to_dict()
        return payload.get("result", {}).get("base64", "")
    except Exception:
        return ""


def vlm_is_facing_screen(b64: str, cfg) -> bool:
    if not b64:
        return False
    try:
        client = openai.OpenAI(api_key=cfg.openai_api_key, timeout=20)
        result = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Check this camera frame from a robot. "
                                "Is a computer screen clearly visible and centered, "
                                "with the camera facing the screen directly? "
                                "Answer only: yes or no."
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                        },
                    ],
                }
            ],
        )
        message = result.choices[0].message.content
        if not isinstance(message, str):
            return False
        return message.strip().lower().startswith("yes")
    except Exception:
        return False


def vlm_is_gazing(b64: str, cfg) -> bool:
    if not b64:
        return False
    try:
        client = openai.OpenAI(api_key=cfg.openai_api_key, timeout=20)
        result = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Is the person in this image looking directly at the camera "
                                "(i.e., making eye contact with the camera)? "
                                "Answer only: yes or no."
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                        },
                    ],
                }
            ],
        )
        message = result.choices[0].message.content
        if not isinstance(message, str):
            return False
        return message.strip().lower().startswith("yes")
    except Exception:
        return False
