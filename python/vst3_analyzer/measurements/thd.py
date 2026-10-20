# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
"""Acquire a coherent sine through any compatible DUT and measure per-channel THD."""
from dataclasses import asdict, dataclass
import math
import numpy as np

from ..dut.base import DUT
from ..settings import MeasurementSettings
from ..dsp.generators import coherent_sine, nearest_bin
from ..dsp.distortion import THDResult, analyze_thd


@dataclass(frozen=True)
class THDMeasurement:
    """Measurement data plus the settings needed to interpret or reproduce it."""

    settings: MeasurementSettings  # Requested acquisition settings.
    fundamental_bin: int  # Rounded coherent fundamental bin.
    actual_frequency_hz: float  # Actual generated frequency after bin rounding.
    processing_precision: str  # Negotiated native plugin precision.
    pre_roll_samples: int  # Discarded output samples: exactly the initial reported latency.
    reported_latency_samples: int  # Latency reported for the captured record.
    parameters: list[dict]  # Plugin parameters queried before acquisition.
    channels: tuple[THDResult, ...]  # Per-output-channel harmonic and spectrum results.

    def summary(self) -> dict:
        """Return JSON-safe metadata and harmonic figures, without large spectrum arrays."""
        # Represent exact-zero THD as -inf text; JSON null remains reserved for undefined THD.
        channels = []
        for result in self.channels:
            channels.append({"fundamental_peak": result.fundamental_peak,
                             "fundamental_rms": result.fundamental_rms,
                             "thd_ratio": result.thd_ratio, "thd_percent": result.thd_percent,
                             "thd_db": "-inf" if result.thd_db == -math.inf else result.thd_db,
                             "status": "ok" if result.thd_ratio is not None else "no_fundamental",
                             "harmonics": [asdict(harmonic) for harmonic in result.harmonics]})
        return {"settings": asdict(self.settings), "requested_frequency_hz": self.settings.frequency,
                "actual_frequency_hz": self.actual_frequency_hz, "fundamental_bin": self.fundamental_bin,
                "input_peak_amplitude": 1.0, "input_level_dbfs_peak": 0.0,
                "analysis_precision": "float64", "window": "none",
                "processing_precision": self.processing_precision, "channels_driven": len(self.channels),
                "routing": "same sine on every main input channel; auxiliary inputs silent",
                "pre_roll_samples": self.pre_roll_samples, "reported_latency_samples": self.reported_latency_samples,
                "parameters": self.parameters, "channels": channels}


def measure_thd(dut: DUT, settings: MeasurementSettings = MeasurementSettings()) -> THDMeasurement:
    """Configure the current DUT, skip only initial reported latency, then analyze exactly N samples."""
    # Settings do not reload/reset the device; callers can load a fresh instance for repeatability.
    setup = dut.configure(settings.sample_rate, settings.block_size, precision=settings.precision)
    channels = setup["channels"]
    selected = nearest_bin(settings.frequency, settings.sample_rate, settings.fft_size)
    parameters = dut.parameters()
    latency = dut.latency()
    if channels not in (1, 2) or type(latency) is not int or latency < 0:
        raise ValueError("DUT returned invalid channels or latency")

    # Preserve the first block while resolving latency after pending parameter delivery.
    captured = np.empty((channels, settings.fft_size), dtype=np.float64)
    chunk_size = min(settings.block_size, settings.fft_size, 65536)
    processed = filled = 0
    first_block = True
    while filled < settings.fft_size:
        count = min(chunk_size, max(0, latency - processed) + settings.fft_size - filled)
        sine = coherent_sine(selected, settings.fft_size, count, offset=processed)
        sine *= 10.0 ** (settings.sine_level_dbfs / 20.0)
        returned = np.asarray(dut.process([sine] * channels), dtype=np.float64)
        if returned.shape != (channels, count) or not np.isfinite(returned).all():
            raise ValueError("DUT returned invalid output audio")
        if first_block:
            latency = dut.latency()
            if type(latency) is not int or not 0 <= latency <= int(settings.sample_rate * 60):
                raise ValueError("DUT latency exceeds the supported one-minute acquisition limit")
            first_block = False

        # Discard exactly the latency prefix; startup transients belong in the FFT record.
        skip = min(count, max(0, latency - processed))
        take = min(count - skip, settings.fft_size - filled)
        captured[:, filled:filled + take] = returned[:, skip:skip + take]
        filled += take
        processed += count
    pre_roll = latency
    if dut.latency() != latency:
        raise ValueError("Plugin latency changed during acquisition; repeat after it stabilizes")
    results = tuple(analyze_thd(channel, settings.sample_rate, selected) for channel in captured)
    return THDMeasurement(settings, selected, selected * settings.sample_rate / settings.fft_size,
                          setup["processing_precision"], pre_roll, latency, parameters, results)
