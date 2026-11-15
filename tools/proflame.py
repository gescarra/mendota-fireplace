#!/usr/bin/env python3
"""Decode, build and capture Proflame 2 packets.

    proflame.py decode <file | base64 | hex>
    proflame.py listen --host <rm4-ip> [--seconds 300]
    proflame.py encode --like <capture> --power --flame 3 ...
    proflame.py send   --like <capture> --host <rm4-ip> --power --flame 3

<capture> is any code from your own remote (a file, base64 or hex); the
serial and checksum come from it. `listen` and `send` need python-broadlink.
`send` transmits to a gas appliance. It asks before doing so.
"""

from __future__ import annotations

import argparse
import base64
import importlib.util
from pathlib import Path
import sys
import time

_path = Path(__file__).parents[1] / "custom_components/proflame2_broadlink/protocol.py"
_spec = importlib.util.spec_from_file_location("proflame_protocol", _path)
p = importlib.util.module_from_spec(_spec)
sys.modules["proflame_protocol"] = p
_spec.loader.exec_module(p)


def load(capture: str):
    path = Path(capture)
    return p.decode_any(path.read_text() if path.is_file() else capture)


def describe(d) -> str:
    s = d.state
    return (
        f"{' '.join(f'{w:02X}' for w in d.words)}  x{d.frames} | "
        f"power {int(s.power)} flame {s.flame} fan {s.fan} light {s.light} "
        f"pilot {int(s.pilot)} burner2 {int(s.front)} thermostat {int(s.thermostat)}"
        + (f" set {s.set_point}F room {s.room_temp / 10}F" if d.remote.extended else "")
    )


def state_args(parser) -> None:
    parser.add_argument("--like", required=True, help="a capture of your remote")
    for flag in ("power", "pilot", "front"):
        parser.add_argument(f"--{flag}", action="store_true")
    for level in ("flame", "fan", "light"):
        parser.add_argument(f"--{level}", type=int, default=0)


def build(args) -> bytes:
    state = p.State(power=args.power, pilot=args.pilot, front=args.front,
                    flame=args.flame, fan=args.fan, light=args.light)
    return p.encode_broadlink(load(args.like).remote, state)


def device(host):
    import broadlink

    dev = broadlink.hello(host, timeout=5)
    dev.auth()
    return dev


def listen(args) -> None:
    dev = device(args.host)
    out = Path(args.save) if args.save else None
    if out:
        out.mkdir(parents=True, exist_ok=True)
    start = time.time()
    count = 0
    print("Listening. Press buttons on the remote (Ctrl-C to stop).", flush=True)
    while time.time() - start < args.seconds:
        dev.find_rf_packet(p.FREQUENCY_KHZ / 1000)
        armed = time.time()
        data = None
        while data is None and time.time() - armed < 25:
            time.sleep(0.5)
            try:
                data = dev.check_data()
            except Exception:  # nothing captured yet
                pass
        if data is None:
            continue
        count += 1
        stamp = time.strftime("%H%M%S")
        if out:
            (out / f"{stamp}_{count:03d}.b64").write_text(
                base64.b64encode(data).decode() + "\n"
            )
        try:
            print(stamp, describe(p.decode_any(data)), flush=True)
        except p.ProflameError as err:
            print(stamp, f"not decoded: {err}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("decode").add_argument("capture")
    listener = sub.add_parser("listen")
    listener.add_argument("--host", required=True)
    listener.add_argument("--seconds", type=int, default=300)
    listener.add_argument("--save", help="directory to save raw captures in")
    state_args(sub.add_parser("encode"))
    sender = sub.add_parser("send")
    sender.add_argument("--host", required=True)
    sender.add_argument("--yes", action="store_true", help="skip the prompt")
    state_args(sender)
    args = parser.parse_args()

    if args.cmd == "decode":
        d = load(args.capture)
        print(describe(d))
        print(d.remote.as_dict())
    elif args.cmd == "listen":
        listen(args)
    elif args.cmd == "encode":
        data = build(args)
        print(base64.b64encode(data).decode())
        print(describe(p.decode_any(data)))
    elif args.cmd == "send":
        data = build(args)
        print(describe(p.decode_any(data)))
        if not args.yes and input("Transmit to the fireplace? [y/N] ").lower() != "y":
            sys.exit("Not sent.")
        device(args.host).send_data(data)
        print("Sent.")


if __name__ == "__main__":
    main()
