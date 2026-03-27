from __future__ import annotations

import unittest

import server


class ServerEventMappingTests(unittest.TestCase):
    def test_plain_text_events_are_supported(self) -> None:
        self.assertEqual(server.parse_message_event("start"), "start")
        self.assertEqual(server.parse_message_event("stop"), "stop")
        self.assertEqual(server.parse_message_event("shutdown"), "shutdown")

    def test_legacy_disengaged_flags_are_mapped(self) -> None:
        self.assertEqual(server.parse_message_event("disengaged=true"), "start")
        self.assertEqual(server.parse_message_event("disengaged=false"), "stop")

    def test_json_payloads_are_supported(self) -> None:
        self.assertEqual(server.parse_message_event('{"event": "start"}'), "start")
        self.assertEqual(server.parse_message_event('{"event": "shutdown"}'), "shutdown")
        self.assertEqual(server.parse_message_event('{"disengaged": true}'), "start")
        self.assertEqual(server.parse_message_event('{"disengaged": false}'), "stop")

    def test_unrecognized_messages_are_ignored(self) -> None:
        self.assertIsNone(server.parse_message_event("hello"))
        self.assertIsNone(server.parse_message_event('{"event": "unknown"}'))
        self.assertIsNone(server.parse_message_event(""))


if __name__ == "__main__":
    unittest.main()