# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
"""Paced tone, impulse and noise/silence acquisition with float64 analysis."""
from dataclasses import dataclass
import numpy as np

from ..dsp.distortion import THDResult, analyze_thd
from ..dsp.response import FrequencyResponse, analyze_frequency_response
from ..dsp.impulse import ImpulseResponse
from ..dsp.noise import NoiseGenerator
from ..dsp.spectrum import SpectrumResult, analyze_spectrum
from .impulse import ImpulseCapture
from .modes import GENERATORS, MEASUREMENTS
from ..dsp.generators import coherent_sine, nearest_bin
from ..settings import MeasurementSettings


@dataclass(frozen=True)
class LiveFrame:
    """One stable output record for the GUI; sample equality controls trace selection."""

    channels: tuple[THDResult | FrequencyResponse | ImpulseResponse | SpectrumResult, ...]  # Per-channel analysis records.
    linked: bool  # Mono or exactly equal stereo output samples, including their phase.
    actual_frequency_hz: float  # Bin-rounded sine frequency; zero for non-tonal modes.
    processing_precision: str  # Native float32/float64 buffer precision.
    latency_samples: int  # Currently reported plugin latency.
    samples_processed: int  # Total input frames sent since starting/reconfiguring this stream.
    rate_fps: float = 0.0  # Worker-measured average frames per wall-clock second.
    measurement: str = "harmonic"  # Stimulus and spectrum normalization used for this record.
    ms_mode: bool = False  # Channels contain Mid/Side instead of Left/Right.


class RealtimePacer:
    """Schedule sample frames against a monotonic clock without accumulating catch-up bursts."""

    def __init__(self, sample_rate: float, now: float):
        """Start a new schedule at the caller's monotonic timestamp."""
        # The caller supplies time so deterministic tests need no blocking sleeps.
        self.sample_rate = sample_rate  # Frames per second, independent of channel count.
        self.deadline = now  # Earliest scheduled start for the next audio request.

    def delay(self, now: float) -> float:
        """Return the remaining wait before processing another block."""
        # A late caller processes at most one immediate block, never an entire backlog.
        return max(0.0, self.deadline - now)

    def advance(self, frames: int, now: float):
        """Advance by actual frame count and discard elapsed scheduling debt after overruns."""
        # Processing/IPC/FFT time is included in the interval rather than added to it.
        self.deadline = max(self.deadline + frames / self.sample_rate, now)


class LiveAnalyzer:
    """Own continuous stimulus phase and a bounded rolling output record; never sleeps or owns UI."""

    def __init__(self, dut, settings: MeasurementSettings, measurement="harmonic", ms_mode=False):
        """Configure realtime processing and allocate capture history.

        Args:
            dut: Loaded processing device owned by the worker.
            settings: Immutable sample rate, FFT size, block size and precision settings.
            measurement: Selected impulse, harmonic or generator acquisition identifier.
            ms_mode: Alternate Mid/Side impulses or decode continuous output into Mid/Side.
        """
        # Live view retains processor history until an explicit load or settings change.
        if measurement not in MEASUREMENTS and measurement not in GENERATORS:
            raise ValueError("Unknown measurement")
        self.ms_mode = bool(ms_mode)  # Requested M/S acquisition and output interpretation.
        self.ms_pending = None  # Mid response awaiting the following Side capture.
        self.ms_capture_index = 0  # Completed impulse origins, alternating Mid then Side.
        self.measurement = measurement  # Selected stimulus; parameter edits never change it.
        self.dut = dut  # Generic device; the worker owns its lifetime and serialization.
        self.settings = settings  # Immutable settings snapshot for the running stream.
        self.setup = dut.configure(settings.sample_rate, settings.block_size,
                                   precision=settings.precision, processing_mode="realtime")  # Actual device setup.
        self.channel_count = self.setup["channels"]  # Mono/stereo processing width of the configured plugin.
        self.generators = (tuple(NoiseGenerator(measurement) for _ in range(self.channel_count))
                           if measurement in GENERATORS else ())  # Independent channel RNGs and pink-filter histories.
        self.bin_index = nearest_bin(settings.frequency, settings.sample_rate, settings.fft_size)  # Coherent bin.
        self.chunk_size = min(settings.block_size, max(1, int(settings.sample_rate * 0.02)))  # At most 20 ms per request.
        self.samples_processed = 0  # Continuous sine phase position, not reset by parameter edits.
        self.buffer = np.empty((self.channel_count, settings.fft_size), dtype=np.float64)  # Circular output record.
        self.write_position = 0  # Next circular-buffer write index.
        self.filled = 0  # Valid sample count, capped at one FFT period.
        self.since_update = 0  # Samples accumulated since the previous published spectrum.
        self.hop = min(settings.fft_size, max(1, int(settings.sample_rate / 10)))  # At most ten updates per second.
        self.latency = dut.latency()  # Reported delay; spectra skip the initial prefix, raw impulses retain it.
        self.impulse_capture = (ImpulseCapture(self.channel_count, settings.fft_size, settings.sample_rate)
                                if measurement == "impulse" or (self.ms_mode and measurement == "frequency") else None)  # Raw latency-preserving acquisition.
        self.first_block = True  # Resolve parameter-dependent latency after initial queued edits are delivered.
        self.valid_after = 0  # Earliest output frame eligible for an analysis record.
        self._initialize_history()

    def _initialize_history(self):
        """Exclude only initial reported latency when a stream is created."""
        # Keep startup transients after the latency prefix in the first FFT period.
        self.filled = self.since_update = 0
        self.valid_after = self.samples_processed + self.latency

    def reset_after_performance(self):
        """Start a fresh selected-mode capture, retaining the configured plugin and noise sources."""
        # Benchmark input interrupted the timeline; never concatenate FFT records from before and after it.
        self.samples_processed = self.write_position = 0
        self.latency = self.dut.latency()
        self.first_block = True
        self._initialize_history()
        self.ms_pending = None
        self.ms_capture_index = 0
        if self.impulse_capture is not None:
            self.impulse_capture = ImpulseCapture(self.channel_count, self.settings.fft_size, self.settings.sample_rate)

    def step(self) -> LiveFrame | None:
        """Process one small continuous block and optionally return a fresh FFT frame."""
        # Each impulse cycle contains an FFT-size measurement followed by an FFT-size silent recovery block.
        if self.measurement in ("frequency", "impulse"):
            remaining = (self.impulse_capture.remaining(self.latency) if self.impulse_capture is not None else
                         self.valid_after - self.samples_processed if self.samples_processed < self.valid_after else
                         self.settings.fft_size - self.filled)
            self.chunk_size = min(self.settings.block_size, max(1, int(self.settings.sample_rate * 0.02)), remaining)
            positions = np.arange(self.chunk_size) + self.samples_processed
            sine = (positions % (2 * self.settings.fft_size) == 0).astype(np.float64)
            audio = [sine] * self.channel_count
            if self.ms_mode and self.channel_count == 2:
                # Alternate common and opposite-polarity impulses without changing the plugin instance.
                polarity = np.where((positions // (2 * self.settings.fft_size)) % 2 == 0, 1.0, -1.0)
                audio = [sine, sine * polarity]
        elif self.generators:
            # Noise is independent per main-input channel; auxiliary buses remain silent in the native host.
            audio = [generator.generate(self.chunk_size) for generator in self.generators]
        else:
            # Both inputs receive identical phase-continuous samples at the requested peak level.
            sine = coherent_sine(self.bin_index, self.settings.fft_size, self.chunk_size,
                                 offset=self.samples_processed)
            sine *= 10.0 ** (self.settings.sine_level_dbfs / 20.0)
            audio = [sine] * self.channel_count
        output = np.asarray(self.dut.process(audio), dtype=np.float64)
        if output.shape != (self.channel_count, self.chunk_size) or not np.isfinite(output).all():
            raise ValueError("Plugin returned invalid live audio")
        previous = self.samples_processed
        self.samples_processed += self.chunk_size
        latency = self.dut.latency()
        if type(latency) is not int or not 0 <= latency <= self.settings.sample_rate * 60:
            raise ValueError("Plugin reported invalid live latency")
        self.latency = latency
        if self.first_block:
            # Initial parameter delivery can establish a new startup latency; later edits never clear history.
            self.valid_after = self.latency
            self.first_block = False

        # Time-domain measurement keeps the raw latency interval before the aligned zero-time sample.
        if self.impulse_capture is not None:
            captured = self.impulse_capture.append(output, self.latency)
            if captured is None:
                return None
            results, linked = captured
            if self.ms_mode:
                # Select M→M and S→S independently; cross-conversion is deliberately excluded.
                index = self.ms_capture_index % 2
                self.ms_capture_index += 1
                if len(results) == 2:
                    decoded = (results[0].samples + (1 if index == 0 else -1) * results[1].samples) / 2
                    selected = ImpulseResponse(results[0].times_ms, decoded)
                    if index == 0:
                        self.ms_pending = selected
                        return None
                    results, linked = (self.ms_pending, selected), False
                else:
                    results, linked = (results[0],), True
                if self.measurement == "frequency":
                    # Raw captures retain the prefix; FFT records start at latency-aligned time zero.
                    results = tuple(analyze_frequency_response(channel.samples[-self.settings.fft_size:],
                                                               self.settings.sample_rate) for channel in results)
            return LiveFrame(results, linked, 0.0, self.setup["processing_precision"], self.latency,
                             self.samples_processed, measurement=self.measurement, ms_mode=self.ms_mode)

        # Exclude the initial latency prefix or a scheduled recovery interval without resetting the plugin.
        skip = max(0, self.valid_after - previous)
        output = output[:, min(skip, self.chunk_size):]
        if not output.size:
            return None
        if output.shape[1] > self.settings.fft_size:
            output = output[:, -self.settings.fft_size:]
        count = output.shape[1]
        first = min(count, self.settings.fft_size - self.write_position)
        self.buffer[:, self.write_position:self.write_position + first] = output[:, :first]
        if first < count:
            self.buffer[:, :count - first] = output[:, first:]
        self.write_position = (self.write_position + count) % self.settings.fft_size
        self.filled = min(self.settings.fft_size, self.filled + count)
        self.since_update += count
        if self.filled < self.settings.fft_size or self.since_update < (self.settings.fft_size if self.measurement == "frequency" else self.hop):
            return None

        # Compare samples before FFT: equal magnitudes can conceal stereo phase differences.
        record = np.concatenate((self.buffer[:, self.write_position:], self.buffer[:, :self.write_position]), axis=1)
        if self.ms_mode and self.channel_count == 2:
            # Decode continuous output only; harmonic input remains identical on Left and Right.
            record = np.stack(((record[0] + record[1]) / 2, (record[0] - record[1]) / 2))
        linked = self.channel_count == 1 or (not self.ms_mode and np.array_equal(record[0], record[1]))
        if self.measurement == "frequency":
            results = tuple(analyze_frequency_response(channel, self.settings.sample_rate) for channel in record)
            # Process one more FFT-size block at the normal pace, ignoring its latency-aligned output.
            self.filled = 0
            self.valid_after = self.samples_processed + self.settings.fft_size
        elif self.generators:
            results = tuple(analyze_spectrum(channel, self.settings.sample_rate) for channel in record)
        else:
            results = tuple(analyze_thd(channel, self.settings.sample_rate, self.bin_index) for channel in record)
        self.since_update = 0
        return LiveFrame(results, linked, (self.bin_index * self.settings.sample_rate / self.settings.fft_size
                                           if self.measurement == "harmonic" else 0.0),
                         self.setup["processing_precision"], self.latency, self.samples_processed, measurement=self.measurement, ms_mode=self.ms_mode)
