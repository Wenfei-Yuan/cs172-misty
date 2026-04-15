from __future__ import annotations

import unittest

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

    def test_wait_for_start_clears_trailing_stops(self) -> None:
        with self.receiver._lock:
            self.receiver._events.extend(["start", "stop"])

        result = self.receiver.wait_for_start()

        self.assertEqual(result.event, "start")
        self.assertEqual(result.stale_stop_events_cleared, 1)
        self.assertIsNone(self.receiver.consume_interrupt())

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
