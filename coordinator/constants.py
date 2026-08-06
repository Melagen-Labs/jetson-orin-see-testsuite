"""Shared Test Coordinator protocol constants."""

PROTOCOL_VERSION = 1

START_TEST_COMMAND = "START_TEST"
STOP_TEST_COMMAND = "STOP_TEST"
# A no-beam reference run: the DUT starts the same workloads a beam test does
# (cuda_particles + mem_check_gpu) and additionally logs its INA3221 input current
# to a CSV, which is what an abnormal-current threshold gets derived from.
BASELINE_TEST_COMMAND = "BASELINE_TEST"

# Default run length (seconds). The DUT owns the authoritative auto-stop timer;
# the coordinator sends this in START_TEST and schedules its own STOP at the same
# duration to retrieve the summary/CSV. Operator-editable in the GUI.
DEFAULT_DURATION_S = 100
MAX_DURATION_S = 86400  # sanity cap (24 h), mirrors the DUT receiver

# Baseline runs are entered in MINUTES, not seconds: they are long by nature (the
# reference capture is an hour), and minutes is how the operator thinks about them.
DEFAULT_BASELINE_MINUTES = 60
MAX_BASELINE_MINUTES = MAX_DURATION_S // 60

BEAM_ENERGIES_MEV = (
    50,
    63,
    125,
    200,
)

SHIELDING_MATERIALS = (
    "Aluminium",
    "MLC1",
    "MLC2",
)

SHIELDING_THICKNESSES_MM = (
    8,
    12,
    16,
)