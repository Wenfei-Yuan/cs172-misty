from __future__ import annotations

import base64
import io
import unittest
from types import SimpleNamespace

from PIL import Image

import utils.vision as vision
import utils.openai_client as openai_client


class VisionUtilsTests(unittest.TestCase):
    def tearDown(self) -> None:
        openai_client._cached_openai_client.cache_clear()

    def test_prepare_vision_frame_downsizes_large_images(self) -> None:
        image = Image.new("RGB", (1200, 800), color="red")
        source = io.BytesIO()
        image.save(source, format="PNG")
        frame = vision.FrameCaptureResult(
            ok=True,
            base64=base64.b64encode(source.getvalue()).decode("utf-8"),
            mime_type="image/png",
            source="test",
        )
        cfg = SimpleNamespace(vision_max_image_dim_px=400, vision_jpeg_quality=55)

        prepared = vision._prepare_vision_frame(frame, cfg)

        self.assertEqual(prepared.mime_type, "image/jpeg")
        self.assertNotEqual(prepared.base64, frame.base64)
        prepared_image = Image.open(io.BytesIO(base64.b64decode(prepared.base64)))
        self.assertLessEqual(max(prepared_image.size), 400)

    def test_openai_client_is_cached_per_key_and_timeout(self) -> None:
        created = []
        original_constructor = openai_client.openai.OpenAI

        def fake_constructor(*, api_key: str, timeout: float, max_retries: int):
            client = object()
            created.append((api_key, timeout, max_retries, client))
            return client

        openai_client.openai.OpenAI = fake_constructor
        try:
            cfg = SimpleNamespace(openai_api_key="secret", openai_timeout_s=20.0)

            first = openai_client.get_openai_client(cfg)
            second = openai_client.get_openai_client(cfg)

            self.assertIs(first, second)
            self.assertEqual(len(created), 1)
            self.assertEqual(created[0][:3], ("secret", 20.0, 0))
        finally:
            openai_client.openai.OpenAI = original_constructor


if __name__ == "__main__":
    unittest.main()