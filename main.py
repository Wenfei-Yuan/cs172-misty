from misty2py.robot import Misty
from misty2py.utils.env_loader import EnvLoader

env_loader = EnvLoader()

# Example: get device info to verify connection
#response = my_misty.get_info("device")
#print(response.parse_to_dict())

import argparse

from config import load_config
from pipeline import run


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--username",
        help="Username for this session, e.g. alice",
    )
    parser.add_argument(
        "--participant",
        help=argparse.SUPPRESS,
    )
    return parser


def _build_misty() -> Misty:
    misty_ip = env_loader.get_ip()
    if not misty_ip:
        raise EnvironmentError("MISTY_IP_ADDRESS not set in .env")
    misty = Misty(misty_ip)

    try:
        misty.get_info("device")
        print(f"Connected to Misty at {misty_ip}")
    except Exception as exc:
        print(f"Failed to connect to Misty at {misty_ip}: {exc}")
        raise

    return misty


def _resolve_username(args: argparse.Namespace) -> str:
    candidate = (args.username or args.participant or "").strip()
    while not candidate:
        candidate = input("Enter username: ").strip()
    return candidate


if __name__ == "__main__":
    args = _build_parser().parse_args()
    username = _resolve_username(args)
    run(_build_misty(), load_config(username))
