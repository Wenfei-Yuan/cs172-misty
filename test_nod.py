from __future__ import annotations

import argparse
import os

import requests


DEFAULT_ROBOT_IP = "10.5.13.70"
DEFAULT_ACTION_NAME = "head-down-up-nod"


def start_action(robot_ip: str, action_name: str, timeout: float) -> requests.Response:
    url = f"http://{robot_ip}/api/actions/start"
    payload = {"Name": action_name}
    return requests.post(url, json=payload, timeout=timeout)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Send one built-in Misty action start request."
    )
    parser.add_argument(
        "--ip",
        default=os.getenv("MISTY_IP", DEFAULT_ROBOT_IP),
        help=f"Misty IP address (default: {DEFAULT_ROBOT_IP})",
    )
    parser.add_argument(
        "--action",
        default=os.getenv("MISTY_ACTION", DEFAULT_ACTION_NAME),
        help=f"Built-in Misty action name (default: {DEFAULT_ACTION_NAME})",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="HTTP timeout in seconds (default: 10.0)",
    )
    args = parser.parse_args()

    try:
        response = start_action(args.ip, args.action, args.timeout)
    except requests.RequestException as exc:
        print(f"Request failed: {exc}")
        return 1

    print(response.status_code)
    print(response.text)
    return 0 if response.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
