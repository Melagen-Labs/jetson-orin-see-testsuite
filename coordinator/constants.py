"""Shared Test Coordinator protocol constants."""

PROTOCOL_VERSION = 1

START_TEST_COMMAND = "START_TEST"
STOP_TEST_COMMAND = "STOP_TEST"

# Default run length (seconds). The DUT owns the authoritative auto-stop timer;
# the coordinator sends this in START_TEST and schedules its own STOP at the same
# duration to retrieve the summary/CSV. Operator-editable in the GUI.
DEFAULT_DURATION_S = 100
MAX_DURATION_S = 86400  # sanity cap (24 h), mirrors the DUT receiver

# The 2026 proton campaign uses 50 MeV (63 MeV fallback), 125 MeV, and
# 200 MeV. Keep the older 53/100 MeV values accepted for compatibility with
# existing saved tests and local protocol fixtures; the campaign GUI only shows
# the campaign values.
BEAM_ENERGIES_MEV = (
    50,
    53,
    63,
    100,
    125,
    200,
)

SHIELDING_MATERIALS = (
    "Bare",
    "Aluminium",
    "MLC1",
    "MLC2",
)

# This field remains the MLC1 reference level in protocol version 1. The
# campaign GUI converts it to the material-specific physical thickness for the
# operator. Bare control uses 0.
SHIELDING_THICKNESSES_MM = (
    0,
    8,
    12,
    16,
)
