"""Shared Test Coordinator protocol constants."""

PROTOCOL_VERSION = 1

START_TEST_COMMAND = "START_TEST"
STOP_TEST_COMMAND = "STOP_TEST"

# Default run length (seconds). The DUT owns the authoritative auto-stop timer;
# the coordinator sends this in START_TEST and schedules its own STOP at the same
# duration to retrieve the summary/CSV. Operator-editable in the GUI.
DEFAULT_DURATION_S = 100
MAX_DURATION_S = 86400  # sanity cap (24 h), mirrors the DUT receiver

BEAM_ENERGIES_MEV = (
    53,
    100,
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