#ifndef CUDA_PARTICLES_CONFIG_H
#define CUDA_PARTICLES_CONFIG_H

#include <string>

// Run configuration for the headless cuda_particles SEE workload.
// Loaded from a flat JSON file (see config/particles.json). Every field has a
// safe default, so a missing key is non-fatal.
struct Config {
    // --- workload ---
    unsigned int       num_particles     = 16384;   // particle count
    unsigned int       grid_dim          = 64;      // spatial-hash grid (grid_dim^3 cells)
    float              timestep          = 0.5f;    // integration dt per step
    unsigned int       epoch_iterations  = 1000;    // steps per deterministic epoch (E)
    unsigned int       checksum_interval = 50;      // checksum every K steps
    unsigned long long iterations        = 0ULL;    // total steps; 0 = run until stopped
    unsigned int       seed              = 1973;    // metadata only; fixed in initGrid()

    // "bitexact"  -> compare per-step hash against the golden table (default)
    // "invariant" -> only NaN/Inf + bounds checks, no golden comparison
    std::string        tolerance_mode    = "bitexact";

    // --- paths ---
    std::string        log_dir           = "./logs";
    std::string        golden_path       = "./data/golden_hashes.txt";
    std::string        heartbeat_path    = "./logs/heartbeat.txt";

    // --- run / beam metadata (copied into every log record) ---
    std::string        run_id            = "unset";
    std::string        jetson_id         = "unset";
    std::string        beam_energy       = "unset";
    std::string        fluence_source    = "unset";
    std::string        shield_config     = "unset";
};

// Load config from a flat JSON file. A missing file or missing keys keep the
// defaults. Returns false only if `path` was given but could not be opened.
bool loadConfig(const std::string &path, Config &out);

#endif // CUDA_PARTICLES_CONFIG_H
