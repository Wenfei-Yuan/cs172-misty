from __future__ import annotations

import asyncio
import json
import unittest

import server


class ServerEventMappingTests(unittest.TestCase):
    def setUp(self) -> None:
        server.tracker = server.DisengagementTracker(stop_streak=6)
        server.client_roles.clear()
        server.role_clients.clear()

    def test_plain_text_events_are_supported(self) -> None:
        self.assertEqual(server.parse_message_event("start"), "start")
        self.assertEqual(server.parse_message_event("stop"), "stop")
        self.assertEqual(server.parse_message_event("shutdown"), "shutdown")

    def test_legacy_disengaged_flags_are_debounced(self) -> None:
        self.assertEqual(server.parse_message_event("disengaged=true"), "start")
        for _ in range(5):
            self.assertIsNone(server.parse_message_event("disengaged=false"))
        self.assertEqual(server.parse_message_event("disengaged=false"), "stop")

    def test_repeated_disengaged_true_does_not_retrigger_start(self) -> None:
        self.assertEqual(server.parse_message_event("disengaged=true"), "start")
        self.assertIsNone(server.parse_message_event("disengaged=true"))

    def test_disengaged_false_without_active_distraction_is_ignored(self) -> None:
        self.assertIsNone(server.parse_message_event("disengaged=false"))

    def test_false_streak_resets_when_disengaged_true_returns(self) -> None:
        self.assertEqual(server.parse_message_event("disengaged=true"), "start")
        for _ in range(3):
            self.assertIsNone(server.parse_message_event("disengaged=false"))
        self.assertIsNone(server.parse_message_event("disengaged=true"))
        for _ in range(5):
            self.assertIsNone(server.parse_message_event("disengaged=false"))
        self.assertEqual(server.parse_message_event("disengaged=false"), "stop")

    def test_json_payloads_are_supported(self) -> None:
        self.assertEqual(server.parse_message_event('{"event": "start"}'), "start")
        self.assertEqual(server.parse_message_event('{"event": "shutdown"}'), "shutdown")
        self.assertEqual(server.parse_message_event('{"disengaged": true}'), "start")
        for _ in range(5):
            self.assertIsNone(server.parse_message_event('{"disengaged": false}'))
        self.assertEqual(server.parse_message_event('{"disengaged": false}'), "stop")

    def test_unrecognized_messages_are_ignored(self) -> None:
        self.assertIsNone(server.parse_message_event("hello"))
        self.assertIsNone(server.parse_message_event('{"event": "unknown"}'))
        self.assertIsNone(server.parse_message_event(""))


class FakeWebSocket:
    def __init__(self, messages: list[str]) -> None:
        self.messages = iter(messages)
        self.sent_messages: list[str] = []
        self.remote_address = ("127.0.0.1", 12345)

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        try:
            return next(self.messages)
        except StopIteration as exc:
            raise StopAsyncIteration from exc

    async def send(self, message: str) -> None:
        self.sent_messages.append(message)


class ServerHandlerTests(unittest.TestCase):
    def setUp(self) -> None:
        server.tracker = server.DisengagementTracker(stop_streak=1)
        self.original_forward_event = server.forward_event
        server.client_roles.clear()
        server.role_clients.clear()

    def tearDown(self) -> None:
        server.forward_event = self.original_forward_event
        server.client_roles.clear()
        server.role_clients.clear()

    def test_registration_message_registers_client_role(self) -> None:
        websocket = FakeWebSocket(['{"client": "extension"}'])

        asyncio.run(server.handler(websocket))

        self.assertEqual(len(websocket.sent_messages), 1)
        self.assertEqual(json.loads(websocket.sent_messages[0]), {"ok": True, "type": "registered", "client": "extension"})

    def test_stop_event_sends_robot_redirect_message_to_extension(self) -> None:
        async def fake_forward_event(event: str) -> tuple[bool, str]:
            return True, json.dumps({"event": event})

        server.forward_event = fake_forward_event
        extension_socket = FakeWebSocket([])
        webcam_socket = FakeWebSocket(["disengaged=true", "disengaged=false"])
        server.register_client(extension_socket, "extension")
        server.register_client(webcam_socket, "webcam")

        asyncio.run(server.handler(webcam_socket))

        self.assertEqual(len(webcam_socket.sent_messages), 2)
        self.assertEqual(json.loads(webcam_socket.sent_messages[0])["event"], "start")
        self.assertEqual(json.loads(webcam_socket.sent_messages[1])["event"], "stop")
        self.assertEqual(extension_socket.sent_messages, [server.POSTURE_DISENGAGEMENT_MESSAGE, server.ROBOT_REDIRECT_MESSAGE])

    def test_disengaged_true_sends_posture_disengagement_message_to_extension(self) -> None:
        async def fake_forward_event(event: str) -> tuple[bool, str]:
            return True, json.dumps({"event": event})

        server.forward_event = fake_forward_event
        extension_socket = FakeWebSocket([])
        webcam_socket = FakeWebSocket(["disengaged=true"])
        server.register_client(extension_socket, "extension")

        asyncio.run(server.handler(webcam_socket))

        self.assertEqual(len(webcam_socket.sent_messages), 1)
        self.assertEqual(json.loads(webcam_socket.sent_messages[0])["event"], "start")
        self.assertEqual(extension_socket.sent_messages, [server.POSTURE_DISENGAGEMENT_MESSAGE])

    def test_extension_notifications_are_skipped_when_no_extension_registered(self) -> None:
        async def fake_forward_event(event: str) -> tuple[bool, str]:
            return True, json.dumps({"event": event})

        server.forward_event = fake_forward_event
        webcam_socket = FakeWebSocket(["disengaged=true", "disengaged=false"])

        asyncio.run(server.handler(webcam_socket))

        self.assertEqual(len(webcam_socket.sent_messages), 2)
        self.assertEqual(json.loads(webcam_socket.sent_messages[0])["event"], "start")
        self.assertEqual(json.loads(webcam_socket.sent_messages[1])["event"], "stop")


if __name__ == "__main__":
    unittest.main()