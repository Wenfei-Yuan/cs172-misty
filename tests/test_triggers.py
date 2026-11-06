from __future__ import annotations

import unittest
from urllib import request

from utils.triggers import ExternalSignalReceiver


class TriggerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.receiver = ExternalSignalReceiver("127.0.0.1", 0)

    def tearDown(self) -> None:
        self.receiver.stop()

    def test_wait_for_start_discards_stale_stop(self) -> None:
        with self.receiver._lock:
            self.receiver._events.extend(["stop", "start"])

        result = self.receiver.wait_for_start()

        self.assertEqual(result.event, "start")
        self.assertEqual(result.stale_stop_events_cleared, 1)

    def test_consume_interrupt_drains_duplicate_stops(self) -> None:
        with self.receiver._lock:
            self.receiver._events.extend(["stop", "stop", "start"])

        event = self.receiver.consume_interrupt()

        self.assertEqual(event, "stop")
        with self.receiver._lock:
            self.assertEqual(list(self.receiver._events), ["start"])

    def test_wait_for_start_discards_duplicate_starts(self) -> None:
        with self.receiver._lock:
            self.receiver._events.extend(["start", "start"])

        result = self.receiver.wait_for_start()

        self.assertEqual(result.event, "start")
        with self.receiver._lock:
            self.assertEqual(list(self.receiver._events), [])

    def test_wait_for_start_keeps_stop_that_follows_the_start(self) -> None:
        """A stop queued behind a start (e.g. during a recap) ends that new episode."""
        with self.receiver._lock:
            self.receiver._events.extend(["start", "stop"])

        result = self.receiver.wait_for_start()

        self.assertEqual(result.event, "start")
        self.assertEqual(result.stale_stop_events_cleared, 0)
        self.assertEqual(self.receiver.consume_interrupt(), "stop")
        self.assertIsNone(self.receiver.consume_interrupt())

    def test_queued_episodes_are_processed_in_order(self) -> None:
        self.receiver._enqueue("start", "first")
        self.receiver._enqueue("stop")
        self.receiver._enqueue("start", "second")
        self.receiver._enqueue("stop")

        first = self.receiver.wait_for_start()
        self.assertEqual(first.trigger_reason, "first")
        self.assertEqual(self.receiver.consume_interrupt(), "stop")
        second = self.receiver.wait_for_start()
        self.assertEqual(second.trigger_reason, "second")
        self.assertEqual(self.receiver.consume_interrupt(), "stop")
        with self.receiver._lock:
            self.assertEqual(list(self.receiver._events), [])

    def test_consume_interrupt_leaves_a_later_episode_intact(self) -> None:
        self.receiver._enqueue("stop")
        self.receiver._enqueue("start")
        self.receiver._enqueue("stop")

        self.assertEqual(self.receiver.consume_interrupt(), "stop")

        with self.receiver._lock:
            self.assertEqual(list(self.receiver._events), ["start", "stop"])

    def test_signals_keep_their_receipt_time(self) -> None:
        self.receiver._enqueue("start")
        self.receiver._enqueue("stop")
        with self.receiver._lock:
            start_received, stop_received = [event.received_at for event in self.receiver._events]

        self.assertEqual(self.receiver.wait_for_start().received_at, start_received)
        self.assertIsNone(self.receiver.last_stop_time())
        self.receiver.consume_interrupt()
        self.assertEqual(self.receiver.last_stop_time(), stop_received)

    def test_http_signals_reach_the_queue_with_reason_and_time(self) -> None:
        self.receiver.start()
        port = self.receiver._server.server_address[1]

        def post(path: str, body: bytes = b"") -> None:
            req = request.Request(f"http://127.0.0.1:{port}{path}", data=body, method="POST")
            with request.urlopen(req, timeout=2.0) as response:
                self.assertEqual(response.status, 200)

        post("/distraction/start", b'{"reason": "looking_away"}')
        post("/distraction/stop")

        result = self.receiver.wait_for_start()
        self.assertEqual(result.trigger_reason, "looking_away")
        self.assertIsNotNone(result.received_at)
        self.assertTrue(self.receiver.redirect_stop_event.is_set())
        self.assertEqual(self.receiver.consume_interrupt(), "stop")
        self.assertIsNotNone(self.receiver.last_stop_time())

    def test_has_pending_start_does_not_consume(self) -> None:
        self.assertFalse(self.receiver.has_pending_start())
        self.receiver._enqueue("start")

        self.assertTrue(self.receiver.has_pending_start())
        self.assertEqual(self.receiver.wait_for_start().event, "start")

    def test_consume_interrupt_prioritizes_shutdown(self) -> None:
        with self.receiver._lock:
            self.receiver._events.extend(["stop", "shutdown"])

        event = self.receiver.consume_interrupt()

        self.assertEqual(event, "shutdown")
        with self.receiver._lock:
            self.assertEqual(list(self.receiver._events), ["stop"])

    def test_current_text_accessor_returns_latest_text(self) -> None:
        with self.receiver._lock:
            self.receiver._current_text = "latest reading text"

        self.assertEqual(self.receiver.current_text(), "latest reading text")

    def test_wait_for_stop_or_shutdown_returns_stop(self) -> None:
        with self.receiver._lock:
            self.receiver._events.extend(["stop", "start"])

        event = self.receiver.wait_for_stop_or_shutdown(timeout_s=0.0)

        self.assertEqual(event, "stop")
        with self.receiver._lock:
            self.assertEqual(list(self.receiver._events), ["start"])

    def test_wait_for_stop_or_shutdown_only_discards_start(self) -> None:
        with self.receiver._lock:
            self.receiver._events.extend(["start", "stop", "start"])

        event = self.receiver.wait_for_stop_or_shutdown_only(timeout_s=0.0)

        self.assertEqual(event, "stop")
        with self.receiver._lock:
            self.assertEqual(list(self.receiver._events), [])

    def test_wait_for_stop_or_shutdown_times_out(self) -> None:
        event = self.receiver.wait_for_stop_or_shutdown(timeout_s=0.0)

        self.assertIsNone(event)


if __name__ == "__main__":
    unittest.main()
