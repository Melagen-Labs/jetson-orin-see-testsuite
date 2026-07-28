# firmware/ — intentionally not populated by this repo

The power-monitor firmware (channel 5) is authored and version-controlled
**separately by the project's electrical engineer**. It is not vendored or
mirrored here.

What lives here: nothing but this note. What defines the boundary: the firmware
must implement the contract in
[`../docs/POWER_FIRMWARE_INTERFACE.md`](../docs/POWER_FIRMWARE_INTERFACE.md) —
the line-delimited JSON sample/event schema, the absolute + di/dt thresholds, the
latching trip behavior, and the arbiter-issued recovery command. As long as the
firmware honors that contract, [`../arbiter/power_reader.py`](../arbiter/power_reader.py)
can ingest and correlate its stream without any code in this directory.

If you later want the firmware source co-located, add it as its own git submodule
here rather than copying it in, so its history stays with the EE's repository.
