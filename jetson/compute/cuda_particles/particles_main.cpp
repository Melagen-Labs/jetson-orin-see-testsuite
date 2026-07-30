/*
 * particles_main.cpp -- headless deterministic CUDA particle workload for
 * proton-beam Single Event Effect (SEE) testing on Jetson Orin Nano.
 *
 * Adapted from NVIDIA/cuda-samples "particles" (BSD-3-Clause). The rendering /
 * GLUT UI and the one-shot benchmark main() are replaced by a continuous,
 * epoch-based, per-iteration checksummed loop with structured JSONL logging,
 * a heartbeat counter, and graceful SIGTERM handling.
 *
 * Determinism model
 * -----------------
 * initGrid() seeds srand(1973), so reset(CONFIG_GRID) reproduces a bit-identical
 * initial state on a fixed build. We run in EPOCHS of `epoch_iterations` steps:
 * at the start of every epoch we reset to that known state, so the sequence of
 * per-step checksums is identical epoch-to-epoch absent a radiation upset. A
 * golden table of the clean hashes (one epoch's worth) is generated once on the
 * target with --generate-golden, committed, then compared during beam runs.
 */

#include <csignal>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

#include <cuda_runtime.h>
#include <helper_cuda.h>

#include "particleSystem.h"
#include "config.h"
#include "checksum.h"
#include "logger.h"

static volatile sig_atomic_t g_stop = 0;
static void handleSignal(int) { g_stop = 1; }

static std::string nowIso()
{
    time_t t = time(nullptr);
    struct tm tmv;
#if defined(_WIN32)
    gmtime_s(&tmv, &t);
#else
    gmtime_r(&t, &tmv);
#endif
    char buf[32];
    strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%SZ", &tmv);
    return std::string(buf);
}

// Common metadata fragment appended to every JSON record.
static std::string metaFields(const Config &c)
{
    std::string s;
    s += "\"run_id\":\"" + jsonEscape(c.run_id) + "\",";
    s += "\"jetson_id\":\"" + jsonEscape(c.jetson_id) + "\",";
    s += "\"beam_energy\":\"" + jsonEscape(c.beam_energy) + "\",";
    s += "\"fluence_source\":\"" + jsonEscape(c.fluence_source) + "\",";
    s += "\"shield_config\":\"" + jsonEscape(c.shield_config) + "\"";
    return s;
}

static bool loadGolden(const std::string &path, std::vector<uint64_t> &out)
{
    std::ifstream f(path.c_str());
    if (!f.is_open()) return false;
    std::string line;
    while (std::getline(f, line)) {
        if (line.empty()) continue;
        out.push_back(strtoull(line.c_str(), nullptr, 16));
    }
    return true;
}

static bool writeGolden(const std::string &path, const std::vector<uint64_t> &hashes)
{
    std::ofstream f(path.c_str(), std::ios::trunc);
    if (!f.is_open()) return false;
    char buf[24];
    for (size_t i = 0; i < hashes.size(); i++) {
        snprintf(buf, sizeof(buf), "%016llx", (unsigned long long)hashes[i]);
        f << buf << "\n";
    }
    return true;
}

static void writeHeartbeat(const std::string &path, unsigned long long totalIter,
                           unsigned long long epoch, unsigned int step)
{
    FILE *f = fopen(path.c_str(), "w");
    if (!f) return;
    fprintf(f, "{\"iter\":%llu,\"epoch\":%llu,\"step\":%u,\"unixtime\":%lld}\n",
            totalIter, epoch, step, (long long)time(nullptr));
    fflush(f);
    fclose(f);
}

int main(int argc, char **argv)
{
    std::string configPath = "config/particles.json";
    bool generateGolden = false;

    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--config") && i + 1 < argc) {
            configPath = argv[++i];
        } else if (!strcmp(argv[i], "--generate-golden")) {
            generateGolden = true;
        } else if (!strcmp(argv[i], "--help")) {
            printf("usage: %s [--config <path>] [--generate-golden]\n", argv[0]);
            return 0;
        }
    }

    Config cfg;
    if (!loadConfig(configPath, cfg)) {
        fprintf(stderr, "[cuda_particles] config '%s' not found; using defaults\n", configPath.c_str());
    }

    // Select the fastest CUDA device and bind to it.
    int devID = gpuGetMaxGflopsDeviceId();
    checkCudaErrors(cudaSetDevice(devID));

    // Build the particle system (headless: bUseOpenGL = false).
    uint3 gridSize = make_uint3(cfg.grid_dim, cfg.grid_dim, cfg.grid_dim);
    ParticleSystem psystem(cfg.num_particles, gridSize, false);

    const size_t count  = (size_t)cfg.num_particles * 4;
    const size_t nBytes = count * sizeof(float);
    std::vector<float> hPos(count), hVel(count);

    Logger log;
    if (!log.open(cfg.log_dir, "cuda_particles.jsonl")) {
        fprintf(stderr, "[cuda_particles] WARNING: cannot open log dir '%s'\n", cfg.log_dir.c_str());
    }

    // Golden table (bitexact mode).
    std::vector<uint64_t> golden;
    bool haveGolden = false;
    if (!generateGolden && cfg.tolerance_mode == "bitexact") {
        haveGolden = loadGolden(cfg.golden_path, golden);
        if (!haveGolden) {
            fprintf(stderr, "[cuda_particles] no golden table at '%s'; running without bitexact compare\n",
                    cfg.golden_path.c_str());
        }
    }

    signal(SIGTERM, handleSignal);
    signal(SIGINT, handleSignal);

    if (log.isOpen()) {
        std::ostringstream os;
        os << "{\"ts\":\"" << nowIso() << "\",\"event\":\"start\","
           << "\"num_particles\":" << cfg.num_particles << ","
           << "\"grid_dim\":" << cfg.grid_dim << ","
           << "\"timestep\":" << cfg.timestep << ","
           << "\"epoch_iterations\":" << cfg.epoch_iterations << ","
           << "\"checksum_interval\":" << cfg.checksum_interval << ","
           << "\"tolerance_mode\":\"" << jsonEscape(cfg.tolerance_mode) << "\","
           << "\"generate_golden\":" << (generateGolden ? "true" : "false") << ","
           << metaFields(cfg) << "}";
        log.writeLine(os.str());
    }

    const unsigned int K = cfg.checksum_interval ? cfg.checksum_interval : 1;
    const unsigned int E = cfg.epoch_iterations ? cfg.epoch_iterations : K;

    std::vector<uint64_t> genHashes; // used only in --generate-golden mode
    unsigned long long totalIter = 0;
    unsigned long long epoch = 0;
    int corruptionSeen = 0;

    while (!g_stop) {
        // Deterministic reset to the known initial state (srand(1973) inside).
        psystem.reset(ParticleSystem::CONFIG_GRID);
        unsigned int stepIdx = 0; // index into the golden table within this epoch

        for (unsigned int step = 1; step <= E && !g_stop; step++) {
            psystem.update(cfg.timestep);
            totalIter++;

            if (cfg.iterations && totalIter >= cfg.iterations) g_stop = 1;

            if (step % K == 0) {
                checkCudaErrors(cudaMemcpy(hPos.data(), psystem.getCudaPosVBO(), nBytes, cudaMemcpyDeviceToHost));
                checkCudaErrors(cudaMemcpy(hVel.data(), psystem.getCudaVel(),    nBytes, cudaMemcpyDeviceToHost));

                uint64_t h    = hashState(hPos.data(), count, hVel.data(), count);
                bool     fin  = allFinite(hPos.data(), count) && allFinite(hVel.data(), count);
                float    mAbs = maxAbs(hPos.data(), count);

                bool mismatch = false;
                if (generateGolden) {
                    genHashes.push_back(h);
                } else if (haveGolden && stepIdx < golden.size()) {
                    mismatch = (h != golden[stepIdx]);
                }

                bool anomaly = mismatch || !fin || (mAbs > 2.0f);
                if (anomaly) corruptionSeen = 1;

                if (log.isOpen()) {
                    char hbuf[24], gbuf[24];
                    snprintf(hbuf, sizeof(hbuf), "%016llx", (unsigned long long)h);
                    unsigned long long gh = (haveGolden && stepIdx < golden.size()) ? golden[stepIdx] : 0ULL;
                    snprintf(gbuf, sizeof(gbuf), "%016llx", gh);
                    std::ostringstream os;
                    os << "{\"ts\":\"" << nowIso() << "\",\"event\":\"checksum\","
                       << "\"iter\":" << totalIter << ",\"epoch\":" << epoch << ",\"step\":" << step << ","
                       << "\"hash\":\"" << hbuf << "\","
                       << "\"golden\":\"" << gbuf << "\","
                       << "\"mismatch\":" << (mismatch ? "true" : "false") << ","
                       << "\"finite\":" << (fin ? "true" : "false") << ","
                       << "\"max_abs_pos\":" << mAbs << ","
                       << "\"anomaly\":" << (anomaly ? "true" : "false") << ","
                       << metaFields(cfg) << "}";
                    log.writeLine(os.str());
                }

                writeHeartbeat(cfg.heartbeat_path, totalIter, epoch, step);
                stepIdx++;
            }
        }

        if (generateGolden) {
            if (!writeGolden(cfg.golden_path, genHashes)) {
                fprintf(stderr, "[cuda_particles] ERROR: failed to write golden '%s'\n", cfg.golden_path.c_str());
                return 1;
            }
            printf("[cuda_particles] wrote %zu golden hashes to %s\n", genHashes.size(), cfg.golden_path.c_str());
            break; // one clean epoch defines the golden table
        }
        epoch++;
    }

    if (log.isOpen()) {
        std::ostringstream os;
        os << "{\"ts\":\"" << nowIso() << "\",\"event\":\"stop\","
           << "\"total_iter\":" << totalIter << ",\"epochs\":" << epoch << ","
           << "\"corruption_seen\":" << (corruptionSeen ? "true" : "false") << ","
           << metaFields(cfg) << "}";
        log.writeLine(os.str());
    }

    // Nonzero exit (2) if any corruption/anomaly was observed, so the arbiter /
    // systemd can distinguish a clean stop from a suspect run.
    return corruptionSeen ? 2 : 0;
}
