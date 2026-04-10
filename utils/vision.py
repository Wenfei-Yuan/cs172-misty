from __future__ import annotations

import base64
import binascii
import io
import re
from dataclasses import dataclass

import requests
from PIL import Image, ImageOps

from utils.openai_client import get_openai_client


@dataclass(frozen=True)
class FrameCaptureResult:
    ok: bool
    base64: str = ""
    reason: str | None = None
    source: str | None = None
    mime_type: str | None = None


@dataclass(frozen=True)
class VisionCheckResult:
    ok: bool
    status: str
    reason: str | None = None
    model_output: str | None = None


def _vision_model(cfg) -> str:
    return str(getattr(cfg, "vision_model", "gpt-4o"))


def _vision_max_image_dim_px(cfg) -> int:
    return max(0, int(getattr(cfg, "vision_max_image_dim_px", 640)))


def _vision_jpeg_quality(cfg) -> int:
    quality = int(getattr(cfg, "vision_jpeg_quality", 60))
    return max(1, min(95, quality))


def _camera_url(misty) -> str:
    protocol = getattr(getattr(misty, "infos", None), "protocol", "http")
    return f"{protocol}://{misty.ip}/api/cameras/rgb"


def _coerce_base64_text(value) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (bytes, bytearray)):
        return base64.b64encode(value).decode("utf-8")
    return ""


def _extract_base64_from_payload(payload: dict) -> tuple[str, str | None]:
    legacy_result = payload.get("result")
    if isinstance(legacy_result, dict):
        base64_value = _coerce_base64_text(legacy_result.get("base64"))
        if base64_value:
            return base64_value, "result.base64"

    rest_response = payload.get("rest_response")
    if isinstance(rest_response, dict):
        for key in ("base64", "content"):
            value = rest_response.get(key)
            if isinstance(value, dict):
                nested = _coerce_base64_text(value.get("base64"))
                if nested:
                    return nested, f"rest_response.{key}.base64"
            else:
                base64_value = _coerce_base64_text(value)
                if base64_value:
                    return base64_value, f"rest_response.{key}"

    base64_value = _coerce_base64_text(rest_response)
    if base64_value:
        return base64_value, "rest_response"

    return "", None


def capture_frame_result(misty) -> FrameCaptureResult:
    try:
        response = requests.get(_camera_url(misty), timeout=5)
        if response.status_code == 409:
            return FrameCaptureResult(ok=False, reason="camera_busy")
        response.raise_for_status()

        content_type = (response.headers.get("content-type") or "").lower()
        mime_type = content_type.split(";", 1)[0].strip() or None
        if "application/json" in content_type:
            payload = {
                "rest_response": response.json(),
                "overall_success": True,
                "misty2py_response": {"success": True},
            }
            base64_value, source = _extract_base64_from_payload(payload)
            if base64_value:
                return FrameCaptureResult(
                    ok=True,
                    base64=base64_value,
                    source=source,
                    mime_type="image/jpeg",
                )
            return FrameCaptureResult(
                ok=False,
                reason="camera_empty_json_payload",
                source="rest_response",
                mime_type=mime_type,
            )

        if response.content:
            return FrameCaptureResult(
                ok=True,
                base64=base64.b64encode(response.content).decode("utf-8"),
                source="raw_bytes",
                mime_type=mime_type or "image/jpeg",
            )

        return FrameCaptureResult(ok=False, reason="camera_empty_response_body", mime_type=mime_type)
    except requests.exceptions.Timeout:
        return FrameCaptureResult(ok=False, reason="camera_connect_timeout")
    except requests.exceptions.ConnectionError as exc:
        return FrameCaptureResult(ok=False, reason=f"camera_connection_error:{exc}")
    except requests.exceptions.RequestException as exc:
        return FrameCaptureResult(ok=False, reason=f"camera_http_error:{exc}")
    except Exception as exc:
        return FrameCaptureResult(ok=False, reason=f"camera_request_failed:{exc}")


def _prepare_vision_frame(frame: FrameCaptureResult, cfg) -> FrameCaptureResult:
    if not frame.ok or not frame.base64:
        return frame

    try:
        raw_bytes = base64.b64decode(frame.base64, validate=True)
        with Image.open(io.BytesIO(raw_bytes)) as image:
            image = ImageOps.exif_transpose(image)
            if image.mode != "RGB":
                image = image.convert("RGB")

            max_dimension = _vision_max_image_dim_px(cfg)
            if max_dimension > 0 and max(image.size) > max_dimension:
                image.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)

            output = io.BytesIO()
            image.save(output, format="JPEG", optimize=True, quality=_vision_jpeg_quality(cfg))
        return FrameCaptureResult(
            ok=True,
            base64=base64.b64encode(output.getvalue()).decode("utf-8"),
            reason=frame.reason,
            source=frame.source,
            mime_type="image/jpeg",
        )
    except (binascii.Error, OSError, ValueError):
        return frame


def _data_uri(frame: FrameCaptureResult) -> str:
    mime_type = frame.mime_type or "image/jpeg"
    return f"data:{mime_type};base64,{frame.base64}"


def analyze_screen_capture(frame: FrameCaptureResult, cfg) -> VisionCheckResult:
    if not frame.ok:
        return VisionCheckResult(ok=False, status="capture_error", reason=frame.reason)
    try:
        client = get_openai_client(cfg)
        prepared_frame = _prepare_vision_frame(frame, cfg)
        result = client.chat.completions.create(
            model=_vision_model(cfg),
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Check this camera frame from a robot. "
                                "Reply with exactly one label: aligned, visible, or none. "
                                "Use aligned when a computer monitor or screen is clearly visible "
                                "and the robot is reasonably facing it. "
                                "Use visible when a monitor or screen is present but not aligned well yet. "
                                "Use none when no monitor or screen is visible."
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": _data_uri(prepared_frame)},
                        },
                    ],
                }
            ],
        )
        message = result.choices[0].message.content
        if not isinstance(message, str):
            return VisionCheckResult(ok=False, status="vlm_error", reason="non_string_response")
        normalized = message.strip().lower()
        print("Screen VLM response:", normalized)
        if re.search(r'\baligned\b', normalized):
            return VisionCheckResult(ok=True, status="aligned", model_output=message)
        if re.search(r'\bvisible\b', normalized):
            return VisionCheckResult(ok=True, status="visible", reason="screen_visible_not_centered", model_output=message)
        if re.search(r'\bnone\b', normalized):
            return VisionCheckResult(ok=True, status="no_screen", model_output=message)
        return VisionCheckResult(ok=False, status="vlm_error", reason=f"unexpected_response:{normalized}", model_output=message)
    except Exception as exc:
        return VisionCheckResult(ok=False, status="vlm_error", reason=str(exc))


def analyze_gaze_capture(frame: FrameCaptureResult, cfg) -> VisionCheckResult:
    if not frame.ok:
        return VisionCheckResult(ok=False, status="capture_error", reason=frame.reason)
    try:
        client = get_openai_client(cfg)
        prepared_frame = _prepare_vision_frame(frame, cfg)
        result = client.chat.completions.create(
            model=_vision_model(cfg),
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Is the person in this image looking at or clearly attending to the robot/camera? "
                                "Count near-eye-contact or a face/head clearly oriented toward the robot as yes, "
                                "even if the eye contact is not perfectly centered. "
                                "Answer no if they are looking away, down at another device, or their face is not directed toward the robot. "
                                "Answer only: yes or no."
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": _data_uri(prepared_frame)},
                        },
                    ],
                }
            ],
        )
        message = result.choices[0].message.content
        if not isinstance(message, str):
            return VisionCheckResult(ok=False, status="vlm_error", reason="non_string_response")
        normalized = message.strip().lower()
        if re.search(r'\byes\b', normalized):
            return VisionCheckResult(ok=True, status="gazing", model_output=message)
        if re.search(r'\bno\b', normalized):
            return VisionCheckResult(ok=True, status="not_gazing", model_output=message)
        return VisionCheckResult(ok=False, status="vlm_error", reason=f"unexpected_response:{normalized}", model_output=message)
    except Exception as exc:
        return VisionCheckResult(ok=False, status="vlm_error", reason=str(exc))
