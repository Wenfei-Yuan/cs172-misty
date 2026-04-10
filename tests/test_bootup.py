from __future__ import annotations

import unittest
from types import SimpleNamespace

import stages.bootup as bootup


class _FakeLog:
    def __init__(self) -> None:
        self.records = []

    def record(self, name: str, **payload) -> None:
        self.records.append((name, payload))


class BootupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.originals = {
            "show_image": bootup.show_image,
            "speak_text": bootup.speak_text,
            "find_screen_position_with_vlm": bootup.find_screen_position_with_vlm,
            "look_at_screen": bootup.look_at_screen,
        }

    def tearDown(self) -> None:
        for name, value in self.originals.items():
            setattr(bootup, name, value)

    def test_announces_start_reading_after_first_screen_found(self) -> None:
        spoken = []
        screen_pos = SimpleNamespace(yaw=12.0, pitch=-4.0)

        bootup.show_image = lambda *args, **kwargs: None
        bootup.look_at_screen = lambda *args, **kwargs: None
        bootup.speak_text = lambda misty, cfg, utterance, **kwargs: spoken.append((utterance, kwargs.get("stage")))
        bootup.find_screen_position_with_vlm = lambda *args, **kwargs: SimpleNamespace(
            position=screen_pos,
            reason="verified",
        )

        log = _FakeLog()
        result = bootup.run_bootup(misty=object(), cfg=object(), log=log)

        self.assertIs(result, screen_pos)
        self.assertEqual(
            spoken,
            [
                ("Hello! I'm Misty. Let's focus today!", "bootup_intro"),
                ("I've found the screen!", "bootup_screen_found"),
                ("Let's start reading. You can open the browser extension and start reading now.", "bootup_start_reading"),
            ],
        )

    def test_run_bootup_does_not_use_arm_gestures_for_intro(self) -> None:
        calls = []
        screen_pos = SimpleNamespace(yaw=10.0, pitch=4.0)

        bootup.show_image = lambda misty, face: calls.append(("face", face))
        bootup.speak_text = lambda misty, cfg, text, **kwargs: calls.append(("speak", text, kwargs.get("stage")))
        bootup.find_screen_position_with_vlm = lambda misty, cfg, log=None: SimpleNamespace(position=screen_pos, reason=None)
        bootup.look_at_screen = lambda misty, position: calls.append(("look", position))

        log = _FakeLog()
        result = bootup.run_bootup(misty=object(), cfg=SimpleNamespace(), log=log)

        self.assertIs(result, screen_pos)
        self.assertEqual(
            calls,
            [
                ("face", bootup.BOOT_FACE),
                ("speak", "Hello! I'm Misty. Let's focus today!", "bootup_intro"),
                ("look", screen_pos),
                ("speak", "I've found the screen!", "bootup_screen_found"),
                ("speak", "Let's start reading. You can open the browser extension and start reading now.", "bootup_start_reading"),
                ("face", bootup.SPEAKING_FACE),
            ],
        )


if __name__ == "__main__":
    unittest.main()
