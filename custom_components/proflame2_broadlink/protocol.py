"""SIT Proflame 2 remote protocol codec.

Pure Python with no Home Assistant imports, so the CLI and the tests can use it.

On air: 314.973 MHz OOK at 2400 raw bits per second. A packet is a run of
13-symbol words, Manchester encoded to 26 raw bits each, sent 5 times.

    word   = S 1 d7..d0 pad parity 1
    S      = raw 11 (a deliberate Manchester violation)
    0 / 1  = raw 01 / raw 10
    pad    = 1 on the first word, 0 on the rest
    parity = XOR of the 8 data bits and the pad bit

Two packet layouts exist:

    classic  (7 words)   serial*3 cmd1 cmd2 check(cmd1) check(cmd2)
    extended (10 words)  serial*3 cmd1 cmd2 setpoint room_lo room_hi check2 check1

    cmd1     = pilot(7) light(6-4) unknown(3-2) thermostat(1) power(0)
    cmd2     = front(7) fan(6-4) aux(3) flame(2-0)
    setpoint = thermostat target in whole degrees Fahrenheit
    room     = room temperature in tenths of a degree Fahrenheit, little endian

The classic layout is the publicly documented one (rtl_433, smartfire). The
extended one was worked out from a GTMFSLA transmitter (FCC ID T99058404300).

Checksums: every byte is mixed nibble by nibble (see _mix) and XORed together
with a constant.

    classic   check1 = c1 ^ Z(cmd1)                  check2 = c2 ^ Z(cmd2)
    extended  check1 = c1 ^ X(cmd1) ^ Y(setpoint) ^ Z(room_hi)
              check2 = c2 ^ Y(cmd2) ^ Z(room_lo)

Thermostat modes are run by the remote, not the fireplace. With the thermostat
bit set and power clear ("on"), the remote sends flame 6 to heat and flame 0
to idle. With both bits set ("smart") it varies the flame level instead.

Every packet carries the complete state, so there is no "flame up" command,
only "this is what the fireplace should look like now".
"""

from __future__ import annotations

import base64
from collections import Counter
from dataclasses import dataclass, replace
import re

BIT_US = 1_000_000 / 2400
WORD_BITS = 26
REPEATS = 5
GAP_BITS = 13
MAX_LEVEL = 6
FREQUENCY_KHZ = 314_973

BROADLINK_TICK_US = 32.84
# RM4 Pro RF packets: 2 byte header, 2 byte length, 4 byte frequency in kHz.
BROADLINK_RF_HEADER = b"\x77\xc0"
BROADLINK_TAIL_TICKS = 1500

CLASSIC_WORDS = 7
EXTENDED_WORDS = 10

# Bits of cmd1 whose meaning is unknown. They are copied from the capture.
CMD1_UNKNOWN = 0x0C

# Each check nibble is an XOR of shifted copies of a byte's two nibbles. These
# are the shift patterns: for the byte's (high nibble, low nibble), the
# multipliers into the check byte's (high nibble, low nibble).
_X = ((0xD, 0x5), (0xA, 0x7))
_Y = ((0x7, 0x2), (0x4, 0x3))
_Z = ((0x3, 0x1), (0x2, 0x1))


class ProflameError(ValueError):
    """Raised when a capture cannot be decoded."""


@dataclass(frozen=True)
class State:
    """Everything a Proflame 2 packet says about the fireplace."""

    power: bool = False
    pilot: bool = False  # True = continuous pilot (CPI), False = intermittent
    light: int = 0
    thermostat: bool = False
    front: bool = False  # split flow, the secondary burner
    fan: int = 0
    aux: bool = False
    flame: int = 0
    set_point: int = 70  # extended only, whole degrees Fahrenheit
    room_temp: int = 720  # extended only, tenths of a degree Fahrenheit

    def __post_init__(self) -> None:
        for name in ("light", "fan", "flame"):
            if not 0 <= getattr(self, name) <= MAX_LEVEL:
                raise ProflameError(f"{name} must be 0-{MAX_LEVEL}")
        if not 0 <= self.set_point <= 0xFF or not 0 <= self.room_temp <= 0xFFFF:
            raise ProflameError("temperature out of range")

    @property
    def cmd1(self) -> int:
        return (
            self.pilot << 7 | self.light << 4 | self.thermostat << 1 | int(self.power)
        )

    @property
    def cmd2(self) -> int:
        return self.front << 7 | self.fan << 4 | self.aux << 3 | self.flame

    @classmethod
    def from_words(cls, words: tuple[int, ...]) -> State:
        if len(words) == EXTENDED_WORDS:
            return cls.from_cmd(
                words[3], words[4], set_point=words[5], room_temp=words[7] << 8 | words[6]
            )
        return cls.from_cmd(words[3], words[4])

    @classmethod
    def from_cmd(cls, cmd1: int, cmd2: int, **temps) -> State:
        return cls(
            **temps,
            power=bool(cmd1 & 0x01),
            thermostat=bool(cmd1 & 0x02),
            light=(cmd1 >> 4) & 0x07,
            pilot=bool(cmd1 & 0x80),
            flame=cmd2 & 0x07,
            aux=bool(cmd2 & 0x08),
            fan=(cmd2 >> 4) & 0x07,
            front=bool(cmd2 & 0x80),
        )


def _nibble_mul(k: int, x: int) -> int:
    """Carry-less multiply, keeping the low 4 bits."""
    out = 0
    for bit in range(4):
        if x >> bit & 1:
            out ^= k << bit
    return out & 0x0F


def _mix(mult, byte: int) -> int:
    (ah, al), (bh, bl) = mult
    a, b = byte >> 4, byte & 0x0F
    high = _nibble_mul(ah, a) ^ _nibble_mul(bh, b)
    low = _nibble_mul(al, a) ^ _nibble_mul(bl, b)
    return high << 4 | low


@dataclass(frozen=True)
class Remote:
    """The identity a fireplace is paired with, read from a capture."""

    serial: int
    extended: bool = False
    cmd1_fixed: int = 0  # the CMD1_UNKNOWN bits, as captured
    const1: int = 0xD0  # checksum constants
    const2: int = 0x07

    @property
    def serial_hex(self) -> str:
        return f"{self.serial:06X}"

    def as_dict(self) -> dict:
        return {**self.__dict__, "serial": self.serial_hex}

    @classmethod
    def from_dict(cls, data: dict) -> Remote:
        return cls(**{**data, "serial": int(data["serial"], 16)})

    def _checks(self, words: tuple[int, ...]) -> tuple[int, int]:
        """Checksums of a packet's data words, before the constants."""
        if self.extended:
            return (
                _mix(_X, words[3]) ^ _mix(_Y, words[5]) ^ _mix(_Z, words[7]),
                _mix(_Y, words[4]) ^ _mix(_Z, words[6]),
            )
        return _mix(_Z, words[3]), _mix(_Z, words[4])

    def build_words(self, state: State) -> tuple[int, ...]:
        words = (
            self.serial >> 16 & 0xFF,
            self.serial >> 8 & 0xFF,
            self.serial & 0xFF,
            state.cmd1 | self.cmd1_fixed,
            state.cmd2,
        )
        if self.extended:
            words += (state.set_point, state.room_temp & 0xFF, state.room_temp >> 8)
        check1, check2 = self._checks(words)
        check1 ^= self.const1
        check2 ^= self.const2
        return words + ((check2, check1) if self.extended else (check1, check2))

    @classmethod
    def from_words(cls, words: tuple[int, ...]) -> Remote:
        """Recover the identity, solving the checksum constants."""
        if len(words) not in (CLASSIC_WORDS, EXTENDED_WORDS):
            raise ProflameError(f"unsupported packet of {len(words)} words")
        extended = len(words) == EXTENDED_WORDS
        seen1, seen2 = (words[9], words[8]) if extended else (words[5], words[6])
        remote = cls(
            serial=words[0] << 16 | words[1] << 8 | words[2],
            extended=extended,
            cmd1_fixed=words[3] & CMD1_UNKNOWN,
        )
        check1, check2 = remote._checks(words)
        return replace(remote, const1=seen1 ^ check1, const2=seen2 ^ check2)


@dataclass(frozen=True)
class Decoded:
    """Result of decoding a capture."""

    remote: Remote
    state: State
    words: tuple[int, ...]
    frames: int  # how many repeats agreed on these words


# --- words <-> raw bits ---------------------------------------------------


def words_to_raw(words: tuple[int, ...]) -> str:
    """Manchester encode words into a string of 0s and 1s."""
    out = []
    for i, byte in enumerate(words):
        pad = 1 if i == 0 else 0
        parity = (byte.bit_count() + pad) & 1
        bits = f"1{byte:08b}{pad}{parity}1"
        out.append("11" + "".join("10" if b == "1" else "01" for b in bits))
    return "".join(out)


def _raw_to_word(raw: str, index: int) -> int:
    bits = ""
    for j in range(2, WORD_BITS, 2):
        pair = raw[j : j + 2]
        if pair not in ("10", "01"):
            raise ProflameError(f"word {index}: Manchester violation")
        bits += "1" if pair == "10" else "0"
    byte, pad, parity = int(bits[1:9], 2), int(bits[9]), int(bits[10])
    if bits[0] != "1" or bits[11] != "1":
        raise ProflameError(f"word {index}: bad guard bit")
    if pad != (index == 0):
        raise ProflameError(f"word {index}: bad pad bit")
    if (byte.bit_count() + pad) & 1 != parity:
        raise ProflameError(f"word {index}: parity error")
    return byte


# --- pulses ---------------------------------------------------------------


def encode_pulses(
    remote: Remote, state: State, repeats: int = REPEATS, gap_bits: int = GAP_BITS
) -> list[float]:
    """Return alternating on/off durations in microseconds, starting with on."""
    raw = (words_to_raw(remote.build_words(state)) + "0" * gap_bits) * repeats
    return [len(m.group()) * BIT_US for m in re.finditer(r"1+|0+", raw)]


def decode_pulses(pulses: list[tuple[bool, float]]) -> Decoded:
    """Decode (level, microseconds) pairs, voting across the repeats.

    Each word is decoded on its own, starting from its 3-bit-long sync pulse,
    so stretched gaps between words (which real captures have) do no harm.
    """
    frames: list[list[str]] = [[]]
    word: str | None = None

    def close_word() -> None:
        if word is not None:
            frames[-1].append(word.ljust(WORD_BITS, "0")[:WORD_BITS])

    for level, us in pulses:
        n = us / BIT_US
        if not level and n > 6:  # gap between repeats
            close_word()
            word = None
            frames.append([])
        elif level and n > 2.5:  # sync plus the guard bit's high half
            close_word()
            word = "111"
        elif word is not None:
            count = 1 if n < 1.5 else 2
            if not level:  # the last low of a word runs into the next sync
                count = min(count, WORD_BITS - len(word))
            word += ("1" if level else "0") * count
    close_word()

    votes: Counter[tuple[int, ...]] = Counter()
    error = None
    for frame in frames:
        try:
            votes[tuple(_raw_to_word(raw, i) for i, raw in enumerate(frame))] += 1
        except ProflameError as err:
            error = err
    votes.pop((), None)
    if not votes:
        raise ProflameError(
            "no valid Proflame 2 frame found" + (f" ({error})" if error else "")
        )
    words, count = votes.most_common(1)[0]
    remote = Remote.from_words(words)
    return Decoded(remote, State.from_words(words), words, count)


# --- Broadlink ------------------------------------------------------------


def encode_broadlink(remote: Remote, state: State, **kwargs) -> bytes:
    """Build an RM4 Pro RF packet for `send_data`."""
    body = bytearray(FREQUENCY_KHZ.to_bytes(4, "little"))
    elapsed_us = 0.0
    elapsed_ticks = 0
    pulses = encode_pulses(remote, state, **kwargs)
    for us in pulses[:-1]:
        # Round against the running total so timing error never accumulates.
        elapsed_us += us
        ticks = max(1, round(elapsed_us / BROADLINK_TICK_US) - elapsed_ticks)
        elapsed_ticks += ticks
        if ticks > 255:
            body += bytes((0, ticks >> 8, ticks & 0xFF))
        else:
            body.append(ticks)
    body += bytes((0, BROADLINK_TAIL_TICKS >> 8, BROADLINK_TAIL_TICKS & 0xFF))
    return BROADLINK_RF_HEADER + len(body).to_bytes(2, "little") + bytes(body)


def encode_broadlink_b64(remote: Remote, state: State, **kwargs) -> str:
    return base64.b64encode(encode_broadlink(remote, state, **kwargs)).decode()


def broadlink_to_pulses(data: bytes) -> list[tuple[bool, float]]:
    if len(data) < 9:
        raise ProflameError("Broadlink packet too short")
    end = min(len(data), 4 + int.from_bytes(data[2:4], "little"))
    i = 4
    # RM4 Pro RF captures carry the frequency in kHz before the pulses.
    if 300_000 <= int.from_bytes(data[4:8], "little") <= 440_000:
        i = 8
    pulses = []
    level = True
    while i < end:
        ticks = data[i]
        i += 1
        if ticks == 0:
            if i + 2 > end:
                break
            ticks = data[i] << 8 | data[i + 1]
            i += 2
        pulses.append((level, ticks * BROADLINK_TICK_US))
        level = not level
    return pulses


def flipper_to_pulses(text: str) -> list[tuple[bool, float]]:
    """Parse the RAW_Data lines of a Flipper Zero .sub file."""
    pulses = []
    for line in text.splitlines():
        if line.startswith("RAW_Data:"):
            for value in line.split(":", 1)[1].split():
                pulses.append((int(value) > 0, abs(int(value))))
    if not pulses:
        raise ProflameError("no RAW_Data in Flipper file (was it saved as RAW?)")
    return pulses


def decode_any(capture: str | bytes) -> Decoded:
    """Decode a Flipper .sub file's text, or a Broadlink code as b64/hex/bytes."""
    if isinstance(capture, str):
        text = capture.strip()
        if "RAW_Data:" in text:
            return decode_pulses(flipper_to_pulses(text))
        text = text.removeprefix("b64:")
        try:
            if re.fullmatch(r"[0-9a-fA-F\s]+", text):
                capture = bytes.fromhex(text)
            else:
                capture = base64.b64decode(text, validate=True)
        except ValueError as err:
            raise ProflameError("not a Flipper RAW file, base64 or hex") from err
    return decode_pulses(broadlink_to_pulses(capture))
