# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
"""Command-line coherent THD measurement and native-host diagnostics."""
import argparse
from array import array
from dataclasses import replace
import json
import math
from pathlib import Path

from .dut.vst3 import VST3DUT
from .ipc.client import HostClient
from .settings import MeasurementSettings


def main():
    """Apply user settings, load an effect, and run a THD test or basic audio probe."""
    # Settings files and CLI overrides share the same validated data model as the future UI.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, help="Path to vst3-host-service")
    parser.add_argument("--transport", choices=("socketpair", "named"), default="socketpair")
    parser.add_argument("--plugin", required=True, help="Path to a VST3 module")
    parser.add_argument("--class-id", default="", help="Effect class ID from --list")
    parser.add_argument("--list", action="store_true", help="List effect classes and exit")
    parser.add_argument("--thd", action="store_true", help="Run a coherent 0 dBFS sine/FFT/THD test")
    parser.add_argument("--settings", help="Read measurement settings from JSON")
    parser.add_argument("--save-settings", help="Save effective measurement settings to JSON")
    parser.add_argument("--sample-rate", type=float, help="Hz (default 44100)")
    parser.add_argument("--fft-size", type=int, help="Analysis samples (default 8192)")
    parser.add_argument("--block-size", type=int, help="Native block size (default 512)")
    parser.add_argument("--frequency", type=float, help="Requested Hz, rounded to nearest FFT bin (default 1000)")
    parser.add_argument("--precision", choices=("auto", "float32", "float64"))
    parser.add_argument("--spectrum-csv", help="Export float64 frequency/peak-amplitude spectra for a THD test")
    parser.add_argument("--set", nargs=2, action="append", metavar=("PARAM_ID", "VALUE"))
    args = parser.parse_args()
    try:
        settings = MeasurementSettings.load(args.settings) if args.settings else MeasurementSettings()
        overrides = {name: getattr(args, name) for name in ("sample_rate", "fft_size", "block_size",
                     "frequency", "precision") if getattr(args, name) is not None}
        settings = replace(settings, **overrides)
        if args.save_settings:
            settings.save(args.save_settings)
        if args.spectrum_csv and not args.thd:
            raise ValueError("--spectrum-csv requires --thd")

        # A new process and new instance give each command-line test a fresh starting state.
        with HostClient(args.host, transport=args.transport) as client:
            if args.list:
                print(json.dumps(client.request("list_classes", path=str(Path(args.plugin).resolve())),
                                 indent=2, ensure_ascii=False))
                return 0
            dut = VST3DUT(client)
            info = dut.load(args.plugin, args.class_id)
            for parameter_id, value in args.set or []:
                dut.set_parameter(int(parameter_id), float(value))

            # Measurement algorithms stay in Python and use a generic DUT interface.
            if args.thd:
                from .measurements.thd import measure_thd
                measurement = measure_thd(dut, settings)
                summary = measurement.summary()
                summary["plugin"] = {key: info[key] for key in ("path", "class_id", "buses", "supported_precisions")}
                summary["wire_format"] = "f64le"
                summary["processing_mode"] = "offline"
                print(json.dumps(summary, indent=2, ensure_ascii=False, allow_nan=False))
                if args.spectrum_csv:
                    import numpy as np
                    columns = [measurement.channels[0].frequencies_hz]
                    columns.extend(channel.amplitudes_peak for channel in measurement.channels)
                    labels = ["frequency_hz"] + [f"channel_{i + 1}_peak" for i in range(len(measurement.channels))]
                    np.savetxt(args.spectrum_csv, np.column_stack(columns), delimiter=",",
                               header=",".join(labels), comments="", fmt="%.17g")
                return 0

            # Retain a small -12.04 dBFS probe for transport/lifecycle diagnostics.
            setup = dut.configure(settings.sample_rate, settings.block_size, settings.precision)
            frames = int(settings.sample_rate / 10)
            signal = array("d", (0.25 * math.sin(2 * math.pi * 1000 * i / settings.sample_rate)
                                 for i in range(frames)))
            output = dut.process([signal] * setup["channels"])
            print(json.dumps({"plugin": info["class_id"], "settings": setup,
                              "parameters": dut.parameters(), "input_peak": max(map(abs, signal)),
                              "output_peaks": [max(map(abs, channel)) for channel in output],
                              "latency_samples": dut.latency()}, indent=2, ensure_ascii=False))
            return 0
    except (OSError, RuntimeError, ValueError) as error:
        # Native failures stay in the service; invalid analysis data is reported explicitly.
        parser.exit(1, f"Analyzer error: {error}\n")


if __name__ == "__main__":
    # Return a meaningful exit status for shell scripts and reproducible measurements.
    raise SystemExit(main())
