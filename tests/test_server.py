from __future__ import annotations

import asyncio
import json
import unittest

import server


class ServerEventMappingTests(unittest.TestCase):
    def setUp(self) -> None:
        server.tracker = server.DisengagementTracker()
        server.client_roles.clear()
        server.role_clients.clear()

    def test_plain_text_events_are_supported(self) -> None:
        self.assertEqual(server.parse_message_event("start"), "start")
        self.assertEqual(server.parse_message_event("stop"), "stop")
        self.assertEqual(server.parse_message_event("shutdown"), "shutdown")

    def test_legacy_disengaged_flags_trigger_start_stop(self) -> None:
        self.assertEqual(server.parse_message_event("disengaged=true"), "start")
        self.assertEqual(server.parse_message_event("disengaged=false"), "stop")

    def test_repeated_disengaged_true_does_not_retrigger_start(self) -> None:
        self.assertEqual(server.parse_message_event("disengaged=true"), "start")
        self.assertIsNone(server.parse_message_event("disengaged=true"))

    def test_disengaged_false_without_active_distraction_is_ignored(self) -> None:
        self.assertIsNone(server.parse_message_event("disengaged=false"))

    def test_repeated_disengaged_false_does_not_retrigger_stop(self) -> None:
        self.assertEqual(server.parse_message_event("disengaged=true"), "start")
        self.assertEqual(server.parse_message_event("disengaged=false"), "stop")
        self.assertIsNone(server.parse_message_event("disengaged=false"))

    def test_json_payloads_are_supported(self) -> None:
        self.assertEqual(server.parse_message_event('{"event": "start"}'), "start")
        self.assertEqual(server.parse_message_event('{"event": "shutdown"}'), "shutdown")
        self.assertEqual(server.parse_message_event('{"disengaged": true}'), "start")
        self.assertEqual(server.parse_message_event('{"disengaged": false}'), "stop")
        self.assertEqual(server.parse_message_event('{"disengage": true}'), "start")
        self.assertEqual(server.parse_message_event('{"disengage": false}'), "stop")

    def test_string_bool_payloads_are_supported(self) -> None:
        self.assertEqual(server.parse_message_event('{"posture_disengaged": "true"}'), "start")
        self.assertEqual(server.parse_message_event('{"posture_disengaged": "false"}'), "stop")
        self.assertEqual(server.parse_message_event('{"disengage": "true"}'), "start")
        self.assertEqual(server.parse_message_event('{"disengage": "false"}'), "stop")

    def test_extension_covert_disengagement_alias_starts_distraction(self) -> None:
        self.assertEqual(server.parse_message_event("covert_disengagemnt=ture"), "start")
        self.assertIsNone(server.parse_message_event("covert_disengagemnt=ture"))

    def test_posture_disengaged_and_reengagement_are_supported(self) -> None:
        self.assertEqual(server.parse_message_event("posture_disengaged=true"), "start")
        self.assertEqual(server.parse_message_event("re-engagement"), "stop")
        self.assertEqual(server.parse_message_event('{"posture_disengaged": true}'), "start")
        self.assertEqual(server.parse_message_event('{"re_engagement": true}'), "stop")
        self.assertEqual(server.parse_message_event('{"posture_disengaged": true}'), "start")
        self.assertEqual(server.parse_message_event("re-engament"), "stop")
        self.assertEqual(server.parse_message_event('{"posture_disengaged": true}'), "start")
        self.assertEqual(server.parse_message_event('{"re_engament": true}'), "stop")

    def test_extension_reengagement_signal_is_ignored(self) -> None:
        self.assertEqual(server.parse_message_event("posture_disengaged=true"), "start")
        self.assertIsNone(server.parse_message_event("re-engagement", source_role="extension"))
        self.assertIsNone(server.parse_message_event('{"re_engagement": true}', source_role="extension"))

    def test_unrecognized_messages_are_ignored(self) -> None:
        self.assertIsNone(server.parse_message_event("hello"))
        self.assertIsNone(server.parse_message_event('{"event": "unknown"}'))
        self.assertIsNone(server.parse_message_event(""))

    def test_current_text_message_is_parseable(self) -> None:
        self.assertEqual(server.parse_current_text_message('{"text":"abc"}'), "abc")
        self.assertEqual(server.parse_current_text_message('{"currentText":"hello"}'), "hello")
        self.assertEqual(server.parse_current_text_message('{"current_text":"hello2"}'), "hello2")
        self.assertIsNone(server.parse_current_text_message('{"text":"   "}'))
        self.assertIsNone(server.parse_current_text_message("start"))


    def test_posture_payload_with_client_field_is_not_treated_as_registration(self) -> None:
        payload = (
            '{"client":"webcam","source":"webcam","type":"posture",'
            '"disengage":true,"face_present":true}'
        )
        self.assertIsNone(server.parse_client_registration(payload))


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
        server.tracker = server.DisengagementTracker()
        self.original_forward_event = server.forward_event
        self.original_forward_current_text = server.forward_current_text
        server.client_roles.clear()
        server.role_clients.clear()

    def tearDown(self) -> None:
        server.forward_event = self.original_forward_event
        server.forward_current_text = self.original_forward_current_text
        server.client_roles.clear()
        server.role_clients.clear()

    def test_registration_message_registers_client_role(self) -> None:
        websocket = FakeWebSocket(['{"client": "extension"}'])

        asyncio.run(server.handler(websocket))

        self.assertEqual(len(websocket.sent_messages), 1)
        self.assertEqual(json.loads(websocket.sent_messages[0]), {"ok": True, "type": "registered", "client": "extension"})

    def test_stop_event_sends_reengagement_message_to_extension(self) -> None:
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
        self.assertEqual(
            extension_socket.sent_messages,
            [
                server.POSTURE_DISENGAGED_MESSAGE,
                server.REENGAGEMENT_MESSAGE,
            ],
        )

    def test_posture_disengaged_true_sends_posture_disengaged_message_to_extension(self) -> None:
        async def fake_forward_event(event: str) -> tuple[bool, str]:
            return True, json.dumps({"event": event})

        server.forward_event = fake_forward_event
        extension_socket = FakeWebSocket([])
        webcam_socket = FakeWebSocket(['{"posture_disengaged": true}'])
        server.register_client(extension_socket, "extension")

        asyncio.run(server.handler(webcam_socket))

        self.assertEqual(len(webcam_socket.sent_messages), 1)
        self.assertEqual(json.loads(webcam_socket.sent_messages[0])["event"], "start")
        self.assertEqual(extension_socket.sent_messages, [server.POSTURE_DISENGAGED_MESSAGE])

    def test_extension_covert_disengagement_message_triggers_start(self) -> None:
        async def fake_forward_event(event: str) -> tuple[bool, str]:
            return True, json.dumps({"event": event})

        server.forward_event = fake_forward_event
        extension_socket = FakeWebSocket(["covert_disengagemnt=ture"])
        server.register_client(extension_socket, "extension")

        asyncio.run(server.handler(extension_socket))

        self.assertEqual(len(extension_socket.sent_messages), 1)
        self.assertEqual(json.loads(extension_socket.sent_messages[0])["event"], "start")

    def test_extension_reengagement_message_is_rejected(self) -> None:
        async def fake_forward_event(event: str) -> tuple[bool, str]:
            return True, json.dumps({"event": event})

        server.forward_event = fake_forward_event
        server.tracker.distraction_active = True
        extension_socket = FakeWebSocket(["re-engagement"])
        server.register_client(extension_socket, "extension")

        asyncio.run(server.handler(extension_socket))

        self.assertEqual(len(extension_socket.sent_messages), 1)
        self.assertEqual(json.loads(extension_socket.sent_messages[0]), {"ok": False, "reason": "unrecognized_message"})

    def test_extension_notifications_are_skipped_when_no_extension_registered(self) -> None:
        async def fake_forward_event(event: str) -> tuple[bool, str]:
            return True, json.dumps({"event": event})

        server.forward_event = fake_forward_event
        webcam_socket = FakeWebSocket(["disengaged=true", "disengaged=false"])

        asyncio.run(server.handler(webcam_socket))

        self.assertEqual(len(webcam_socket.sent_messages), 2)
        self.assertEqual(json.loads(webcam_socket.sent_messages[0])["event"], "start")
        self.assertEqual(json.loads(webcam_socket.sent_messages[1])["event"], "stop")

    def test_current_text_message_is_forwarded(self) -> None:
        async def fake_forward_current_text(text: str) -> tuple[bool, str]:
            return True, json.dumps({"ok": True, "text": text})

        server.forward_current_text = fake_forward_current_text
        extension_socket = FakeWebSocket(['{"currentText":"Reading chapter 2"}'])
        server.register_client(extension_socket, "extension")

        asyncio.run(server.handler(extension_socket))

        self.assertEqual(len(extension_socket.sent_messages), 1)
        payload = json.loads(extension_socket.sent_messages[0])
        self.assertEqual(payload["ok"], True)
        self.assertEqual(payload["type"], "current_text")

if __name__ == "__main__":
    unittest.main()
