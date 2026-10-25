"""Penyusun event dari frame biner GeoSense (EVENT_META/DATA/EVENT_END).

Receiver menerima frame satu per satu (bisa out-of-order / duplikat), lalu
menyusunnya menjadi satu waveform lengkap berdasarkan ``first_sample_index``.
Event dianggap selesai hanya jika:

- semua paket DATA yang dideklarasikan sudah diterima, **dan**
- EVENT_END sudah diterima, **dan**
- tidak ada sampel yang bolong.
"""

from __future__ import annotations

import datetime
import math
import time
from dataclasses import dataclass, field

import lora_protocol as proto

MAX_SAMPLES_PER_EVENT = 5000
MAX_CONCURRENT_EVENTS = 4
EVENT_TIMEOUT_S = 30.0


def _statistics(samples: list[int]) -> dict:
    if not samples:
        return {"p2p": None, "rms": None, "peak": None, "mean": None}
    count = len(samples)
    return {
        "p2p": max(samples) - min(samples),
        "rms": round(math.sqrt(sum(value * value for value in samples) / count), 4),
        "peak": max(abs(value) for value in samples),
        "mean": round(sum(samples) / count, 4),
    }


def _utc_iso(seconds: int, milliseconds: int) -> str | None:
    if not seconds:
        return None
    try:
        moment = datetime.datetime.fromtimestamp(seconds, tz=datetime.timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    millis = min(max(int(milliseconds), 0), 999)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{millis:03d}Z"


@dataclass
class EventSlot:
    node_id: int
    event_id: int
    total_packets: int
    expected_samples: int
    trigger_index: int
    sample_period_us: int
    utc_seconds: int
    utc_milliseconds: int
    wavelet: int
    wavelet_level: int
    flags: int
    first_sample_timestamp_us: int
    samples: list[int | None] = field(default_factory=list)
    present: bytearray = field(default_factory=bytearray)
    packets: set[int] = field(default_factory=set)
    end_seen: bool = False
    reported: bool = False
    updated_at: float = field(default_factory=time.monotonic)

    @property
    def received_samples(self) -> int:
        return sum(self.present)

    @property
    def complete(self) -> bool:
        return (
            self.end_seen
            and len(self.packets) == self.total_packets
            and self.received_samples == self.expected_samples
        )


@dataclass
class HandleResult:
    accept: bool
    status: str
    event: dict | None = None
    detail: str = ""


class EventAssembler:
    """State event per ``(node_id, event_id)``."""

    def __init__(
        self,
        *,
        max_samples: int = MAX_SAMPLES_PER_EVENT,
        max_events: int = MAX_CONCURRENT_EVENTS,
        timeout_s: float = EVENT_TIMEOUT_S,
    ) -> None:
        self.max_samples = int(max_samples)
        self.max_events = int(max_events)
        self.timeout_s = float(timeout_s)
        self._slots: dict[tuple[int, int], EventSlot] = {}

    def pending(self) -> list[dict]:
        return [
            {
                "node_id": slot.node_id,
                "event_id": slot.event_id,
                "packets": len(slot.packets),
                "total_packets": slot.total_packets,
                "samples": slot.received_samples,
                "expected_samples": slot.expected_samples,
                "end_seen": slot.end_seen,
            }
            for slot in self._slots.values()
        ]

    def _cleanup(self) -> None:
        now = time.monotonic()
        expired = [
            key
            for key, slot in self._slots.items()
            if (now - slot.updated_at) > self.timeout_s
        ]
        for key in expired:
            slot = self._slots.pop(key)
            if slot.complete:
                continue
            print(
                f"[EVENT] timeout node={slot.node_id} event={slot.event_id} "
                f"packets={len(slot.packets)}/{slot.total_packets} "
                f"samples={slot.received_samples}/{slot.expected_samples}"
            )

    def handle(self, frame: proto.Frame) -> HandleResult:
        self._cleanup()

        if frame.type == proto.TYPE_ACK:
            return HandleResult(False, "ack-frame")
        if frame.type == proto.TYPE_EVENT_META:
            return self._handle_meta(frame)
        if frame.type == proto.TYPE_DATA:
            return self._handle_data(frame)
        if frame.type == proto.TYPE_EVENT_END:
            return self._handle_end(frame)
        return HandleResult(False, "unknown")

    def _handle_meta(self, frame: proto.Frame) -> HandleResult:
        try:
            meta = proto.decode_meta(frame)
        except proto.FrameError as exc:
            return HandleResult(False, "meta-invalid", detail=str(exc))

        total_samples = int(meta["total_samples"])
        trigger_index = int(meta["trigger_index"])
        if (
            total_samples <= 0
            or total_samples > self.max_samples
            or trigger_index >= total_samples
            or int(meta["milliseconds"]) >= 1000
            or frame.total_packets <= 0
        ):
            return HandleResult(False, "meta-rejected", detail="field meta di luar batas")

        key = (frame.node_id, frame.event_id)
        slot = self._slots.get(key)
        duplicate = slot is not None
        if slot is None:
            if len(self._slots) >= self.max_events:
                return HandleResult(False, "event-table-full")
            slot = EventSlot(
                node_id=frame.node_id,
                event_id=frame.event_id,
                total_packets=frame.total_packets,
                expected_samples=total_samples,
                trigger_index=trigger_index,
                sample_period_us=frame.sample_period_us,
                utc_seconds=int(meta["utc_seconds"]),
                utc_milliseconds=int(meta["milliseconds"]),
                wavelet=int(meta["wavelet"]),
                wavelet_level=int(meta["wavelet_level"]),
                flags=frame.flags,
                first_sample_timestamp_us=frame.first_sample_timestamp_us,
            )
            slot.samples = [None] * total_samples
            slot.present = bytearray(total_samples)
            self._slots[key] = slot
        else:
            # Duplikat META (ACK hilang) — perbarui metadata, jangan reset data.
            if slot.total_packets != frame.total_packets:
                return HandleResult(False, "meta-mismatch")
            slot.utc_seconds = int(meta["utc_seconds"])
            slot.utc_milliseconds = int(meta["milliseconds"])
            slot.sample_period_us = frame.sample_period_us or slot.sample_period_us
            slot.flags = frame.flags

        slot.updated_at = time.monotonic()
        return HandleResult(True, "meta-duplicate" if duplicate else "meta")

    def _handle_data(self, frame: proto.Frame) -> HandleResult:
        slot = self._slots.get((frame.node_id, frame.event_id))
        if slot is None:
            return HandleResult(False, "data-without-meta")
        if frame.total_packets != slot.total_packets:
            return HandleResult(False, "data-packet-count-mismatch")
        if frame.first_sample_index + frame.sample_count > slot.expected_samples:
            return HandleResult(False, "data-index-out-of-range")

        try:
            samples = proto.decode_samples(frame)
        except (proto.FrameError, ValueError) as exc:
            return HandleResult(False, "data-decode-error", detail=str(exc))

        indices = range(frame.first_sample_index, frame.first_sample_index + len(samples))
        was_present = all(slot.present[index] for index in indices)
        duplicate = frame.packet_sequence in slot.packets and was_present
        expected_sequence = len(slot.packets)
        out_of_order = frame.packet_sequence != expected_sequence

        if not duplicate:
            for index, value in zip(indices, samples):
                slot.samples[index] = value
                slot.present[index] = 1
            slot.packets.add(frame.packet_sequence)

        slot.updated_at = time.monotonic()
        status = "data-duplicate" if duplicate else (
            "data-out-of-order" if out_of_order else "data"
        )
        return HandleResult(True, status)

    def _handle_end(self, frame: proto.Frame) -> HandleResult:
        slot = self._slots.get((frame.node_id, frame.event_id))
        if slot is None:
            return HandleResult(False, "end-without-meta")
        if (
            frame.total_packets != slot.total_packets
            or frame.packet_sequence != slot.total_packets
        ):
            return HandleResult(False, "end-mismatch")

        slot.end_seen = True
        slot.updated_at = time.monotonic()

        if not slot.complete:
            return HandleResult(
                True,
                "end-incomplete",
                detail=(
                    f"packets={len(slot.packets)}/{slot.total_packets} "
                    f"samples={slot.received_samples}/{slot.expected_samples}"
                ),
            )

        if slot.reported:
            # Retry EVENT_END karena ACK sebelumnya hilang: ACK lagi, tetapi
            # jangan publish event yang sama dua kali.
            return HandleResult(True, "event-duplicate")

        slot.reported = True
        return HandleResult(True, "event-complete", event=self._record(slot))

    def _record(self, slot: EventSlot) -> dict:
        samples = [int(value) for value in slot.samples if value is not None]
        stats = _statistics(samples)
        period = slot.sample_period_us or 0
        sampling_rate = round(1_000_000.0 / period, 4) if period > 0 else None
        utc = _utc_iso(slot.utc_seconds, slot.utc_milliseconds)
        utc_valid = bool(slot.flags & proto.FLAG_GPS_UTC_VALID) and utc is not None
        wavelet_name = "haar" if slot.wavelet == proto.WAVELET_HAAR else f"id_{slot.wavelet}"

        return {
            "type": "event",
            "source": "lora",
            "protocol": proto.VERSION,
            "n": f"geo_{slot.node_id + 1}",
            "node_index": slot.node_id,
            "event_id": slot.event_id,
            "utc": utc if utc_valid else None,
            "utc_valid": utc_valid,
            "sample_count": len(samples),
            "trigger_index": slot.trigger_index,
            "sample_period_us": period,
            "sampling_rate": sampling_rate,
            "wavelet": {"name": wavelet_name, "level": slot.wavelet_level},
            "denoised": bool(slot.flags & proto.FLAG_DENOISED),
            "unit": "adc_counts",
            "samples": samples,
            "packets": slot.total_packets,
            "first_sample_timestamp_us": slot.first_sample_timestamp_us,
            **stats,
        }
