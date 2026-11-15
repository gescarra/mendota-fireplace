"""Codec tests. Run with: .venv/bin/python -m pytest tests"""

from dataclasses import replace
import itertools
import json
from pathlib import Path

import pytest

from custom_components.proflame2_broadlink import protocol as p

HERE = Path(__file__).parent
# Every distinct packet heard from a real GTMFSLA remote, with the serial
# words swapped for a placeholder. Check words are exactly as transmitted.
REAL_PACKETS = [
    tuple(bytes.fromhex(line))
    for line in json.loads((HERE / "packets.json").read_text())["packets"]
]
# An RM4 Pro code for one of them: power on, flame 6, fan 1, light 3.
REAL_CAPTURE = json.loads((HERE / "sample_capture.json").read_text())["b64"]

CLASSIC = p.Remote(0x257A02)


def test_real_capture_decodes():
    got = p.decode_any(REAL_CAPTURE)
    assert got.frames == 5
    assert got.words == (0x12, 0x34, 0x56, 0xB5, 0x16, 0x47, 0xD0, 0x02, 0xF6, 0x73)
    assert got.remote.serial_hex == "123456"
    assert got.remote.extended
    assert got.state == p.State(
        power=True, pilot=True, light=3, fan=1, flame=6, set_point=71, room_temp=720
    )


def test_checksum_matches_every_real_packet():
    """One capture is enough to predict every other packet from that remote."""
    remote = p.decode_any(REAL_CAPTURE).remote
    assert len(REAL_PACKETS) >= 60
    for words in REAL_PACKETS:
        assert p.Remote.from_words(words) == remote
        assert remote.build_words(p.State.from_words(words)) == words


def test_real_packets_cover_temperatures():
    states = [p.State.from_words(w) for w in REAL_PACKETS]
    assert {s.room_temp for s in states} == {720, 740, 760, 770, 780}
    assert {s.set_point for s in states} >= set(range(63, 82))


def test_real_packets_cover_every_control():
    states = [p.State.from_words(w) for w in REAL_PACKETS]
    assert {s.flame for s in states} == set(range(7)) - {1}
    assert {s.fan for s in states} == set(range(7))
    assert {s.light for s in states} >= {0, 3, 4, 5, 6}
    for field in ("power", "pilot", "front", "thermostat"):
        assert {getattr(s, field) for s in states} == {False, True}


def test_broadlink_container_matches_rm4_pro():
    """Header, frequency and trailer as seen in codes learned by an RM4 Pro."""
    real = p.decode_any(REAL_CAPTURE)
    data = p.encode_broadlink(real.remote, real.state)
    assert data[:2] == bytes.fromhex("77c0")
    assert int.from_bytes(data[2:4], "little") == len(data) - 4
    assert int.from_bytes(data[4:8], "little") == 314_973
    assert data[-3:] == bytes.fromhex("0005dc")
    assert 900 <= len(data) <= 980  # learned codes were 920-970 bytes


def test_published_classic_example():
    # From the smartfire README: cmd1 0x51 -> 0x04, cmd2 0xE2 -> 0x6B.
    words = CLASSIC.build_words(p.State.from_cmd(0x51, 0xE2))
    assert words == (0x25, 0x7A, 0x02, 0x51, 0xE2, 0x04, 0x6B)
    assert p.Remote.from_words(words) == CLASSIC


@pytest.mark.parametrize(
    "remote", [CLASSIC, p.decode_any(REAL_CAPTURE).remote], ids=["classic", "extended"]
)
def test_every_state_round_trips_through_broadlink(remote):
    for power, pilot, front in itertools.product((False, True), repeat=3):
        for light, fan, flame in itertools.product(range(7), repeat=3):
            state = p.State(
                power=power, pilot=pilot, front=front, light=light, fan=fan, flame=flame
            )
            if remote.extended:
                state = replace(state, set_point=60 + flame + fan, room_temp=600 + 37 * light)
            got = p.decode_any(p.encode_broadlink_b64(remote, state))
            assert (got.remote, got.state, got.frames) == (remote, state, 5)


def test_flipper_raw_with_jitter():
    state = p.State(power=True, flame=4, fan=2, light=6, pilot=True)
    values, sign = [], 1
    for i, us in enumerate(p.encode_pulses(CLASSIC, state)):
        values.append(sign * int(us + (40 if i % 2 else -40)))
        sign = -sign
    text = "Filetype: Flipper SubGhz RAW File\nFrequency: 314973000\n"
    text += "RAW_Data: " + " ".join(map(str, values[:100])) + "\n"
    text += "RAW_Data: " + " ".join(map(str, values[100:])) + "\n"
    got = p.decode_any(text)
    assert (got.remote, got.state) == (CLASSIC, state)


def test_remote_survives_storage():
    remote = p.decode_any(REAL_CAPTURE).remote
    assert p.Remote.from_dict(json.loads(json.dumps(remote.as_dict()))) == remote


def test_rejects_garbage():
    with pytest.raises(p.ProflameError):
        p.decode_any("JgAEAAECAwQFBgcI")
    with pytest.raises(p.ProflameError):
        p.State(flame=7)
