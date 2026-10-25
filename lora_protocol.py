"""
Codec protokol frame LoRa GeoSense (v2).

Struktur frame (little-endian)::

    [ header 25 byte ][ payload N byte ][ CRC-16 2 byte ]

Layout header (dari firmware STM32):

===== ========================================= =====
Offset Field                                     Byte
===== ========================================= =====
0      Sync ``47 53`` (``GS``)                   2
2      Versi protokol (2)                        1
3      Tipe frame (DATA=1, ACK=2, END=3, META=4) 1
4      Node index                               1
5      Flags (bit0 denoised, bit1 GPS UTC valid)  1
6      Event ID                                 4
10     Packet sequence (zero-based)              2
12     Jumlah paket DATA dalam event             2
14     Indeks sampel pertama (DATA)              2
16     Jumlah sampel di frame (DATA)             1
17     Periode sampel nominal (us)               2
19     Timestamp MCU sampel pertama (us)         4
23     Encoding (1 = delta zigzag + LEB128)      1
24     Panjang payload                           1
===== ========================================= =====

CRC-16/CCITT-FALSE: poly 0x1021, init 0xFFFF, tanpa reflect/xorout,
dihitung atas header + payload, dikirim little-endian.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Iterable

SYNC = b"GS"
VERSION = 2
HEADER_SIZE = 25
CRC_SIZE = 2
FIXED_OVERHEAD = HEADER_SIZE + CRC_SIZE  # 27
# Firmware node membatasi 96 byte; receiver ESP32 memakai 128. Terima 128
# supaya tidak desync kalau ada frame sedikit lebih besar.
MAX_FRAME_SIZE = 128
MAX_JSON_LINE = 256

TYPE_DATA = 1
TYPE_ACK = 2
TYPE_EVENT_END = 3
TYPE_EVENT_META = 4

FRAME_NAMES = {
    TYPE_DATA: "DATA",
    TYPE_ACK: "ACK",
    TYPE_EVENT_END: "EVENT_END",
    TYPE_EVENT_META: "EVENT_META",
}

ENCODING_DELTA_ZIGZAG_VARINT = 1
FLAG_DENOISED = 0x01
FLAG_GPS_UTC_VALID = 0x02
WAVELET_HAAR = 1
META_PAYLOAD_BYTES = 12


def crc16_ccitt_false(data: bytes, crc: int = 0xFFFF) -> int:
    """CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF, tanpa reflect)."""
    value = crc & 0xFFFF
    for byte in data:
        value ^= byte << 8
        for _ in range(8):
            if value & 0x8000:
                value = ((value << 1) ^ 0x1021) & 0xFFFF
            else:
                value = (value << 1) & 0xFFFF
    return value


def zigzag_decode(value: int) -> int:
    return (value >> 1) ^ -(value & 1)


def zigzag_encode(value: int) -> int:
    return (value << 1) ^ (value >> 63) if value < 0 else value << 1


def decode_leb128(data: bytes, offset: int = 0, max_bytes: int = 10) -> tuple[int, int]:
    result = 0
    shift = 0
    position = offset
    while position < len(data):
        byte = data[position]
        position += 1
        result |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            return result, position
        shift += 7
        if shift > (max_bytes * 7):
            break
    raise ValueError("LEB128 tidak lengkap / terlalu panjang")


def encode_leb128(value: int) -> bytes:
    if value < 0:
        raise ValueError("LEB128 hanya untuk nilai non-negatif")
    output = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            output.append(byte | 0x80)
        else:
            output.append(byte)
            return bytes(output)


class FrameError(ValueError):
    """Frame tidak valid (header/CRC/field)."""


@dataclass(frozen=True)
class Frame:
    """Frame v2 yang sudah divalidasi."""

    raw: bytes
    type: int
    node_id: int
    flags: int
    event_id: int
    packet_sequence: int
    total_packets: int
    first_sample_index: int
    sample_count: int
    sample_period_us: int
    first_sample_timestamp_us: int
    encoding: int
    payload: bytes

    @property
    def name(self) -> str:
        return FRAME_NAMES.get(self.type, f"UNKNOWN({self.type})")

    @property
    def payload_length(self) -> int:
        return len(self.payload)

    @property
    def denoised(self) -> bool:
        return bool(self.flags & FLAG_DENOISED)

    @property
    def gps_utc_valid(self) -> bool:
        return bool(self.flags & FLAG_GPS_UTC_VALID)

    @property
    def header(self) -> bytes:
        return self.raw[:HEADER_SIZE]

    def as_dict(self) -> dict:
        return {
            "type": self.name,
            "node_id": self.node_id,
            "flags": self.flags,
            "event_id": self.event_id,
            "packet_sequence": self.packet_sequence,
            "total_packets": self.total_packets,
            "first_sample_index": self.first_sample_index,
            "sample_count": self.sample_count,
            "sample_period_us": self.sample_period_us,
            "first_sample_timestamp_us": self.first_sample_timestamp_us,
            "encoding": self.encoding,
            "payload_length": self.payload_length,
            "hex": " ".join(f"{byte:02X}" for byte in self.raw),
        }


def decode_frame(raw: bytes | bytearray) -> Frame:
    """Validasi + parse satu frame lengkap. Raise ``FrameError`` kalau invalid."""
    data = bytes(raw)
    if len(data) < FIXED_OVERHEAD:
        raise FrameError(f"frame terlalu pendek: {len(data)} byte")
    if data[0:2] != SYNC:
        raise FrameError("sync bukan 'GS'")
    if data[2] != VERSION:
        raise FrameError(f"versi tidak dikenal: {data[2]}")
    payload_length = data[24]
    if payload_length + FIXED_OVERHEAD != len(data):
        raise FrameError(
            f"panjang payload {payload_length} tidak cocok dengan frame {len(data)}"
        )
    supplied_crc = int.from_bytes(data[-CRC_SIZE:], "little")
    if supplied_crc != crc16_ccitt_false(data[:-CRC_SIZE]):
        raise FrameError("CRC tidak cocok")

    frame_type = data[3]
    node_id = data[4]
    flags = data[5]
    event_id = int.from_bytes(data[6:10], "little")
    packet_sequence = int.from_bytes(data[10:12], "little")
    total_packets = int.from_bytes(data[12:14], "little")
    first_sample_index = int.from_bytes(data[14:16], "little")
    sample_count = data[16]
    sample_period_us = int.from_bytes(data[17:19], "little")
    first_sample_timestamp_us = int.from_bytes(data[19:23], "little")
    encoding = data[23]
    payload = data[HEADER_SIZE:-CRC_SIZE]

    if frame_type not in FRAME_NAMES:
        raise FrameError(f"tipe frame tidak dikenal: {frame_type}")
    if frame_type == TYPE_DATA:
        if encoding != ENCODING_DELTA_ZIGZAG_VARINT:
            raise FrameError(f"encoding DATA tidak didukung: {encoding}")
        if sample_count <= 0:
            raise FrameError("DATA tanpa sampel")
        if payload_length < 4:
            raise FrameError("payload DATA kurang dari 4 byte")
        if total_packets <= 0 or packet_sequence >= total_packets:
            raise FrameError("nomor urut DATA di luar total paket")
    else:
        if sample_count != 0:
            raise FrameError("frame non-DATA tidak boleh punya sample_count")
        if frame_type == TYPE_EVENT_META:
            if encoding != 0 or payload_length != META_PAYLOAD_BYTES:
                raise FrameError("payload EVENT_META harus 12 byte, encoding 0")
        elif frame_type in (TYPE_ACK, TYPE_EVENT_END):
            if encoding != 0 or payload_length != 0:
                raise FrameError("ACK/EVENT_END harus tanpa payload, encoding 0")
        else:
            raise FrameError(f"tipe frame tidak valid: {frame_type}")

    return Frame(
        raw=data,
        type=frame_type,
        node_id=node_id,
        flags=flags,
        event_id=event_id,
        packet_sequence=packet_sequence,
        total_packets=total_packets,
        first_sample_index=first_sample_index,
        sample_count=sample_count,
        sample_period_us=sample_period_us,
        first_sample_timestamp_us=first_sample_timestamp_us,
        encoding=encoding,
        payload=payload,
    )


def decode_meta(frame: Frame) -> dict:
    """Decode payload EVENT_META."""
    if frame.type != TYPE_EVENT_META or len(frame.payload) != META_PAYLOAD_BYTES:
        raise FrameError("bukan frame EVENT_META yang valid")
    (
        utc_seconds,
        milliseconds,
        total_samples,
        trigger_index,
        wavelet,
        wavelet_level,
    ) = struct.unpack("<IHHHBB", frame.payload)
    return {
        "utc_seconds": utc_seconds,
        "milliseconds": milliseconds,
        "total_samples": total_samples,
        "trigger_index": trigger_index,
        "wavelet": wavelet,
        "wavelet_level": wavelet_level,
    }


def decode_samples(frame: Frame) -> list[int]:
    """Decode payload DATA menjadi daftar sampel sesuai ``sample_count``."""
    if frame.type != TYPE_DATA:
        raise FrameError("bukan frame DATA")
    if len(frame.payload) < 4:
        return []
    samples = [int.from_bytes(frame.payload[0:4], "little", signed=True)]
    offset = 4
    while len(samples) < frame.sample_count and offset < len(frame.payload):
        encoded, offset = decode_leb128(frame.payload, offset)
        samples.append(samples[-1] + zigzag_decode(encoded))
    if len(samples) != frame.sample_count or offset != len(frame.payload):
        raise FrameError(
            f"payload DATA tidak konsisten (sample_count={frame.sample_count}, "
            f"decoded={len(samples)}, sisa={len(frame.payload) - offset})"
        )
    return samples


def encode_data_payload(samples: Iterable[int]) -> bytes:
    """Kebalikan ``decode_samples`` untuk payload DATA (tes/simulasi)."""
    values = list(samples)
    if not values:
        return b""
    output = bytearray(int(values[0]).to_bytes(4, "little", signed=True))
    for previous, current in zip(values, values[1:]):
        delta = int(current) - int(previous)
        output.extend(encode_leb128(zigzag_encode(delta)))
    return bytes(output)


def build_frame(
    *,
    frame_type: int,
    node_id: int,
    event_id: int,
    packet_sequence: int,
    total_packets: int,
    payload: bytes = b"",
    flags: int = 0,
    first_sample_index: int = 0,
    sample_count: int = 0,
    sample_period_us: int = 0,
    first_sample_timestamp_us: int = 0,
    encoding: int = 0,
) -> bytes:
    """Bangun frame v2 lengkap + CRC (untuk tes atau simulator)."""
    if len(payload) > 255:
        raise ValueError("payload maksimum 255 byte")
    header = bytearray(HEADER_SIZE)
    header[0:2] = SYNC
    header[2] = VERSION
    header[3] = frame_type
    header[4] = node_id
    header[5] = flags
    header[6:10] = int(event_id).to_bytes(4, "little")
    header[10:12] = int(packet_sequence).to_bytes(2, "little")
    header[12:14] = int(total_packets).to_bytes(2, "little")
    header[14:16] = int(first_sample_index).to_bytes(2, "little")
    header[16] = sample_count & 0xFF
    header[17:19] = int(sample_period_us).to_bytes(2, "little")
    header[19:23] = int(first_sample_timestamp_us).to_bytes(4, "little")
    header[23] = encoding
    header[24] = len(payload)
    body = bytes(header) + payload
    return body + crc16_ccitt_false(body).to_bytes(2, "little")


def build_meta_frame(
    *,
    node_id: int,
    event_id: int,
    total_packets: int,
    utc_seconds: int,
    utc_milliseconds: int,
    total_samples: int,
    trigger_index: int,
    wavelet: int = WAVELET_HAAR,
    wavelet_level: int = 5,
    flags: int = FLAG_DENOISED | FLAG_GPS_UTC_VALID,
    sample_period_us: int = 11111,
    first_sample_timestamp_us: int = 0,
) -> bytes:
    payload = struct.pack(
        "<IHHHBB",
        utc_seconds,
        utc_milliseconds,
        total_samples,
        trigger_index,
        wavelet,
        wavelet_level,
    )
    return build_frame(
        frame_type=TYPE_EVENT_META,
        node_id=node_id,
        event_id=event_id,
        packet_sequence=0,
        total_packets=total_packets,
        payload=payload,
        flags=flags,
        sample_period_us=sample_period_us,
        first_sample_timestamp_us=first_sample_timestamp_us,
    )


def build_data_frame(
    *,
    node_id: int,
    event_id: int,
    packet_sequence: int,
    total_packets: int,
    first_sample_index: int,
    samples: list[int],
    sample_period_us: int = 11111,
    first_sample_timestamp_us: int = 0,
) -> bytes:
    payload = encode_data_payload(samples)
    return build_frame(
        frame_type=TYPE_DATA,
        node_id=node_id,
        event_id=event_id,
        packet_sequence=packet_sequence,
        total_packets=total_packets,
        payload=payload,
        flags=FLAG_DENOISED,
        first_sample_index=first_sample_index,
        sample_count=len(samples),
        sample_period_us=sample_period_us,
        first_sample_timestamp_us=first_sample_timestamp_us,
        encoding=ENCODING_DELTA_ZIGZAG_VARINT,
    )


def build_end_frame(
    *,
    node_id: int,
    event_id: int,
    total_packets: int,
    flags: int = FLAG_DENOISED,
) -> bytes:
    """EVENT_END: packet_sequence == total_packets (sesuai firmware)."""
    return build_frame(
        frame_type=TYPE_EVENT_END,
        node_id=node_id,
        event_id=event_id,
        packet_sequence=total_packets,
        total_packets=total_packets,
        flags=flags,
    )


def encode_ack(frame: Frame) -> bytes:
    """
    Bangun ACK 27 byte untuk frame META/DATA/EVENT_END.

    Firmware receiver menyalin node/event/sequence/total dari frame asal,
    mengosongkan payload/sample_count/encoding, dan mengisi flags dengan tipe
    frame yang di-ACK.
    """
    if frame.type not in (TYPE_DATA, TYPE_EVENT_META, TYPE_EVENT_END):
        raise FrameError("ACK hanya untuk DATA/EVENT_META/EVENT_END")
    header = bytearray(frame.raw[:HEADER_SIZE])
    header[3] = TYPE_ACK
    header[5] = frame.type
    header[16] = 0
    header[23] = 0
    header[24] = 0
    body = bytes(header)
    return body + crc16_ccitt_false(body).to_bytes(2, "little")


class PacketDemux:
    """
    Pisahkan stream radio menjadi frame biner v2 dan baris JSON legacy.

    Node mengirim event biner (``GS``) dan paket GPS JSON (``{...}\\n``) lewat
    link yang sama. Output: list ``("frame", bytes)`` / ``("json", bytes)``.
    """

    def __init__(self, max_frame: int = MAX_FRAME_SIZE, max_json: int = MAX_JSON_LINE):
        self.max_frame = max_frame
        self.max_json = max_json
        self.buffer = bytearray()
        self.errors = 0

    def feed(self, data: bytes) -> list[tuple[str, bytes]]:
        self.buffer.extend(data)
        output: list[tuple[str, bytes]] = []
        while self.buffer:
            first = self.buffer[0]
            if first == 0x47:  # 'G'
                if len(self.buffer) < 2:
                    break
                if self.buffer[1] != 0x53:
                    del self.buffer[0]
                    continue
                if len(self.buffer) < HEADER_SIZE:
                    break
                payload_length = self.buffer[24]
                total = FIXED_OVERHEAD + payload_length
                if total > self.max_frame:
                    self.errors += 1
                    del self.buffer[0]
                    continue
                if len(self.buffer) < total:
                    break
                candidate = bytes(self.buffer[:total])
                if crc16_ccitt_false(candidate[:-2]) != int.from_bytes(
                    candidate[-2:], "little"
                ):
                    self.errors += 1
                    del self.buffer[0]
                    continue
                del self.buffer[:total]
                output.append(("frame", candidate))
                continue

            if first == 0x7B:  # '{'
                newline = self.buffer.find(b"\n")
                if newline < 0:
                    if len(self.buffer) > self.max_json:
                        self.errors += 1
                        del self.buffer[0]
                    break
                line = bytes(self.buffer[:newline]).strip(b"\r")
                del self.buffer[: newline + 1]
                if line:
                    output.append(("json", line))
                continue

            # Buang byte sampah / sisa desync.
            del self.buffer[0]
        return output
