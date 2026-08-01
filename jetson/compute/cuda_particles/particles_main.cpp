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

#include <chrono>
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

#include <unistd.h>     // gethostname (fleet jetson_id "auto")
#include <sys/stat.h>   // mkdir (see_dumps/ directory)

#include <cuda_runtime.h>
#include <helper_cuda.h>

#include "particleSystem.h"
#include "config.h"
#include "checksum.h"
#include "logger.h"

static volatile sig_atomic_t g_stop = 0;
static void handleSignal(int) { g_stop = 1; }

// ISO-8601 UTC with millisecond precision, e.g. 2026-07-30T18:22:04.531Z,
// matching the shared schema-v1 emitter (shared/event_log.py iso_now()).
static std::string nowIso()
{
    using namespace std::chrono;
    system_clock::time_point now = system_clock::now();
    time_t t = system_clock::to_time_t(now);
    int ms = (int)(duration_cast<milliseconds>(now.time_since_epoch()).count() % 1000);
    struct tm tmv;
#if defined(_WIN32)
    gmtime_s(&tmv, &t);
#else
    gmtime_r(&t, &tmv);
#endif
    char buf[32];
    strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%S", &tmv);
    char out[40];
    snprintf(out, sizeof(out), "%s.%03dZ", buf, ms);
    return std::string(out);
}

// Beam/run metadata trio, appended near the end of every record (schema v1
// "meta" fields). run_id / jetson_id live in the envelope, not here.
static std::string metaFields(const Config &c)
{
    std::string s;
    s += "\"beam_energy\":\"" + jsonEscape(c.beam_energy) + "\",";
    s += "\"fluence_source\":\"" + jsonEscape(c.fluence_source) + "\",";
    s += "\"shield_config\":\"" + jsonEscape(c.shield_config) + "\"";
    return s;
}

// Schema-v1 envelope: the seven required leading fields, opening brace included
// and a trailing comma so the caller appends its channel payload, then
// metaFields(c), then the closing brace. See docs/EVENT_SCHEMA.md.
static std::string envelope(const Config &c, const char *event,
                            const char *channel, const char *status)
{
    std::string s = "{";
    s += "\"schema_version\":1,";
    s += "\"ts\":\"" + nowIso() + "\",";
    s += "\"run_id\":\"" + jsonEscape(c.run_id) + "\",";
    s += "\"jetson_id\":\"" + jsonEscape(c.jetson_id) + "\",";
    s += "\"channel\":\"" + std::string(channel) + "\",";
    s += "\"event\":\"" + std::string(event) + "\",";
    s += "\"status\":\"" + std::string(status) + "\",";
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
                           unsigned long long epoch, unsigned int step,
                           unsigned long long seeEvents)
{
    FILE *f = fopen(path.c_str(), "w");
    if (!f) return;
    fprintf(f, "{\"iter\":%llu,\"epoch\":%llu,\"step\":%u,\"see_events\":%llu,\"unixtime\":%lld}\n",
            totalIter, epoch, step, seeEvents, (long long)time(nullptr));
    fflush(f);
    fclose(f);
}

// Write a SEE-epoch state dump: raw little-endian float32, laid out as
// nCheckpoints x [pos(count floats) then vel(count floats)]. The companion
// see_event JSONL record carries the shape (dump_checkpoints, dump_stride,
// num_particles, floats_per_checkpoint) so an offline reconstruction on a
// reference Orin can load and replay it. Returns true on a full write.
static bool writeSeeDump(const std::string &path, const std::vector<float> &snap,
                         size_t nFloats)
{
    FILE *f = fopen(path.c_str(), "wb");
    if (!f) return false;
    size_t w = fwrite(snap.data(), sizeof(float), nFloats, f);
    fclose(f);
    return w == nFloats;
}

int main(int argc, char **argv)
{
    std::string configPath = "config/particles.json";
    bool generateGolden = false;

    // --- fault injection (TEST ONLY, default OFF) ----------------------------
    // Deliberately corrupt one float of GPU particle state at a chosen iteration
    // so each detector path can be exercised on demand -- without a beam. The
    // write goes to the DEVICE buffer, so the corruption propagates through the
    // remaining integration steps exactly like a real upset would.
    //   bitflip -> flips one bit  => cuda_golden_mismatch
    //   nan     -> quiet NaN      => cuda_nonfinite
    //   oob     -> 1e6            => cuda_anomaly (|pos| > 2.0)
    // Every injected run also writes an `inject` record carrying "injected":true,
    // and the resulting see_event is tagged the same way, so injected events can
    // never be mistaken for -- or silently pollute -- real campaign data.
    std::string injectMode;                 // "" = disabled
    unsigned long long injectAt = 500;      // iteration to inject at
    unsigned int injectBit = 22;            // bit to flip (mantissa-ish, visible)
    size_t injectIndex = 0;                 // which float of the pos buffer

    // --- chaos mode (TEST ONLY, default OFF) ---------------------------------
    // The random-in-time-and-place cousin of --inject: at each step, with
    // probability chaosProb, flip a random bit of a random float in the device
    // pos buffer. Produces a continuous stream of randomly-placed upsets (mixed
    // subtypes) to stress the detect->dump->report chain and, when a corrupted
    // value derails a kernel, the CUDA-fault recovery path (surfaces as a
    // sim_fault -> service restart). Like --inject, it is a per-invocation flag
    // (never in cuda_particles.service), so it affects only this one manual run.
    // What it does NOT do: reboot/hang the whole SoC -- the GPU MMU protects the
    // rest of the system, so a full board crash remains the beam's domain.
    bool chaos = false;
    double chaosProb = 0.01;                // per-step probability of a flip
    unsigned int chaosSeed = 1u;            // deterministic by default (repeatable)

    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--config") && i + 1 < argc) {
            configPath = argv[++i];
        } else if (!strcmp(argv[i], "--generate-golden")) {
            generateGolden = true;
        } else if (!strcmp(argv[i], "--inject") && i + 1 < argc) {
            injectMode = argv[++i];
        } else if (!strcmp(argv[i], "--inject-at") && i + 1 < argc) {
            injectAt = strtoull(argv[++i], nullptr, 10);
        } else if (!strcmp(argv[i], "--inject-bit") && i + 1 < argc) {
            injectBit = (unsigned int)strtoul(argv[++i], nullptr, 10) & 31u;
        } else if (!strcmp(argv[i], "--inject-index") && i + 1 < argc) {
            injectIndex = (size_t)strtoull(argv[++i], nullptr, 10);
        } else if (!strcmp(argv[i], "--chaos")) {
            chaos = true;
        } else if (!strcmp(argv[i], "--chaos-prob") && i + 1 < argc) {
            chaosProb = strtod(argv[++i], nullptr);
        } else if (!strcmp(argv[i], "--chaos-seed") && i + 1 < argc) {
            chaosSeed = (unsigned int)strtoul(argv[++i], nullptr, 10);
        } else if (!strcmp(argv[i], "--help")) {
            printf("usage: %s [--config <path>] [--generate-golden]\n"
                   "       [--inject bitflip|nan|oob] [--inject-at <iter>]\n"
                   "       [--inject-bit <0-31>] [--inject-index <float idx>]\n"
                   "       [--chaos] [--chaos-prob <0..1>] [--chaos-seed <n>]\n"
                   "\n"
                   "  --inject  TEST ONLY. Corrupts one float of GPU particle state at\n"
                   "            --inject-at to exercise a detector without a beam.\n"
                   "  --chaos   TEST ONLY. Flips a random bit of random GPU state each\n"
                   "            step with probability --chaos-prob (default 0.01).\n"
                   "  Both tag their events (\"injected\"/\"chaos\":true) and are per-run\n"
                   "  CLI flags -- never in the service. Never use in a real beam run.\n",
                   argv[0]);
            return 0;
        }
    }

    if (!injectMode.empty() && injectMode != "bitflip" &&
        injectMode != "nan" && injectMode != "oob") {
        fprintf(stderr, "[cuda_particles] ERROR: --inject must be bitflip|nan|oob\n");
        return 1;
    }
    if ((!injectMode.empty() || chaos) && generateGolden) {
        fprintf(stderr, "[cuda_particles] ERROR: refusing to inject/chaos while generating "
                        "the golden table (it would bake corruption into the baseline)\n");
        return 1;
    }
    if (chaos && !(chaosProb > 0.0 && chaosProb <= 1.0)) {
        fprintf(stderr, "[cuda_particles] ERROR: --chaos-prob must be in (0, 1]\n");
        return 1;
    }
    if (chaos) srand(chaosSeed);

    Config cfg;
    if (!loadConfig(configPath, cfg)) {
        fprintf(stderr, "[cuda_particles] config '%s' not found; using defaults\n", configPath.c_str());
    }

    // Fleet identity: jetson_id "auto" -> the board's hostname, so one config
    // file is correct on every DUT (set each board's hostname to orin-nano-0N).
    if (cfg.jetson_id == "auto") {
        char host[256];
        if (gethostname(host, sizeof(host)) == 0) cfg.jetson_id = std::string(host);
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

    // Crash / unclean-shutdown detection (marker on the SSD). We hold a marker
    // file while running and remove it on a clean stop; if it is still present
    // at startup, the previous instance died WITHOUT a clean stop -- a CUDA
    // abort, segfault, hang->watchdog reboot, or power loss. During a beam run
    // that is itself a candidate SEE, so we flag it here as a crash. The systemd
    // unit (Restart=always, StartLimitIntervalSec=0) relaunches us within ~1 s;
    // the arbiter later pulls logs/ (incl. see_dumps/) over Ethernet -- both are
    // tentative until the Ethernet link is wired, but the data is on the SSD now.
    const std::string runFlag = cfg.log_dir + "/running.flag";
    if (!generateGolden) {
        std::ifstream prev(runFlag.c_str());
        std::string prevInfo;
        if (prev.good() && std::getline(prev, prevInfo) && log.isOpen()) {
            std::ostringstream os;
            os << envelope(cfg, "sim_fault", "compute", "crash")
               << "\"see_event\":true,\"reason\":\"unclean_restart\","
               << "\"prev_run\":\"" << jsonEscape(prevInfo) << "\","
               << metaFields(cfg) << "}";
            log.writeLine(os.str());
        }
        prev.close();
        FILE *rf = fopen(runFlag.c_str(), "w");
        if (rf) {
            fprintf(rf, "{\"pid\":%d,\"start\":\"%s\"}\n", (int)getpid(), nowIso().c_str());
            fclose(rf);
        }
    }

    if (log.isOpen()) {
        std::ostringstream os;
        os << envelope(cfg, "start", "compute", "info")
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
    const unsigned int nCk = K ? (E / K) : 0;   // checkpoints per epoch

    // Per-checkpoint state buffer, reused every epoch. On a detected SEE we write
    // this whole trajectory to <log_dir>/see_dumps/ for offline reconstruction.
    const bool saveDumps = !generateGolden && cfg.save_see_epochs && nCk > 0;
    const std::string dumpDir = cfg.log_dir + "/see_dumps";
    std::vector<float> snap;
    if (saveDumps) {
        snap.resize((size_t)nCk * 2 * count);
        mkdir(dumpDir.c_str(), 0755);           // ignore EEXIST
    }

    std::vector<uint64_t> genHashes; // used only in --generate-golden mode
    unsigned long long totalIter = 0;
    unsigned long long epoch = 0;
    unsigned long long seeEvents = 0; // # epochs with >=1 SEE (one-per-epoch count)
    int corruptionSeen = 0;
    bool injected = false;             // a TEST-ONLY fault was injected this run
    unsigned long long chaosHits = 0;  // TEST-ONLY chaos-mode flips applied

    if ((chaos || !injectMode.empty()) && log.isOpen()) {
        // Loud, unambiguous marker at the top of the log so nobody mistakes a
        // synthetic run for real data even before the tagged events appear.
        std::ostringstream os;
        os << envelope(cfg, "synthetic_run", "compute", "info")
           << "\"injected\":" << (injectMode.empty() ? "false" : "true") << ","
           << "\"inject_mode\":\"" << injectMode << "\","
           << "\"chaos\":" << (chaos ? "true" : "false") << ","
           << "\"chaos_prob\":" << chaosProb << ","
           << metaFields(cfg) << "}";
        log.writeLine(os.str());
    }

    while (!g_stop) {
        // Deterministic reset to the known initial state (srand(1973) inside).
        psystem.reset(ParticleSystem::CONFIG_GRID);
        unsigned int capturedCk = 0;   // checkpoints buffered this epoch
        bool epochAnomaly = false;     // final checksum mismatch/NaN/oob -> one SEE
        const char *seeType = "none";  // SEE subtype for the operator "SEE Detected" line
        char hbuf[24] = "0", gbuf[24] = "0";  // final hash / golden (for see_event)

        for (unsigned int step = 1; step <= E && !g_stop; step++) {
            psystem.update(cfg.timestep);
            totalIter++;

            if (cfg.iterations && totalIter >= cfg.iterations) g_stop = 1;

            // TEST-ONLY fault injection, fires exactly once. Writing to the DEVICE
            // buffer (not the host copy) means the corruption feeds back into the
            // next integration step, so it propagates through the remainder of the
            // epoch the way a real upset does -- and is caught by the same
            // final-checkpoint comparison, with a real state dump written.
            if (!injectMode.empty() && totalIter == injectAt) {
                float *dPos = (float *)psystem.getCudaPosVBO();
                const size_t idx = injectIndex % count;
                float v = 0.0f;
                checkCudaErrors(cudaMemcpy(&v, dPos + idx, sizeof(float),
                                           cudaMemcpyDeviceToHost));
                const float before = v;
                if (injectMode == "oob") {
                    v = 1.0e6f;                       // far outside |pos| <= 2.0
                } else {
                    uint32_t bits;
                    memcpy(&bits, &v, sizeof(bits));
                    if (injectMode == "bitflip") bits ^= (1u << injectBit);
                    else                         bits = 0x7FC00000u;   // quiet NaN
                    memcpy(&v, &bits, sizeof(v));
                }
                checkCudaErrors(cudaMemcpy(dPos + idx, &v, sizeof(float),
                                           cudaMemcpyHostToDevice));
                injected = true;
                fprintf(stderr, "[cuda_particles] INJECTED %s at iter %llu "
                                "(pos[%zu] %g -> %g)\n", injectMode.c_str(),
                        (unsigned long long)totalIter, idx, before, v);
                if (log.isOpen()) {
                    std::ostringstream os;
                    os << envelope(cfg, "inject", "compute", "info")
                       << "\"injected\":true,\"inject_mode\":\"" << injectMode << "\","
                       << "\"iter\":" << totalIter << ",\"epoch\":" << epoch << ","
                       << "\"index\":" << idx << ",\"bit\":" << injectBit << ","
                       << metaFields(cfg) << "}";
                    log.writeLine(os.str());
                }
            }

            // TEST-ONLY chaos: random bit, random float, random time. Ignores the
            // cudaMemcpy return -- if chaos derails the context, the next checkpoint
            // memcpy (checkCudaErrors) catches it and logs a sim_fault, exactly the
            // path a kernel-crashing upset takes.
            if (chaos && ((double)rand() / (double)RAND_MAX) < chaosProb) {
                float *dPos = (float *)psystem.getCudaPosVBO();
                const size_t idx = (size_t)rand() % count;
                const unsigned int bit = (unsigned int)rand() & 31u;
                float v = 0.0f;
                cudaMemcpy(&v, dPos + idx, sizeof(float), cudaMemcpyDeviceToHost);
                uint32_t bits;
                memcpy(&bits, &v, sizeof(bits));
                bits ^= (1u << bit);
                memcpy(&v, &bits, sizeof(v));
                cudaMemcpy(dPos + idx, &v, sizeof(float), cudaMemcpyHostToDevice);
                chaosHits++;
            }

            if (step % K == 0) {
                // The checkpoint memcpy is our natural GPU sync point, so a
                // CUDA fault (often an SEE that crashed a kernel) surfaces here.
                // Catch it gracefully instead of aborting: flag a crash SEE with
                // the error text, dump what we buffered, and exit 2 so systemd
                // restarts us fast to keep the test going.
                cudaError_t ce = cudaMemcpy(hPos.data(), psystem.getCudaPosVBO(), nBytes, cudaMemcpyDeviceToHost);
                if (ce == cudaSuccess)
                    ce = cudaMemcpy(hVel.data(), psystem.getCudaVel(), nBytes, cudaMemcpyDeviceToHost);
                if (ce != cudaSuccess) {
                    seeEvents++;
                    std::string dumpRel;
                    if (saveDumps && capturedCk > 0) {
                        char name[64];
                        snprintf(name, sizeof(name), "epoch_%llu_iter_%llu_fault.bin",
                                 (unsigned long long)epoch, (unsigned long long)totalIter);
                        if (writeSeeDump(dumpDir + "/" + name, snap, (size_t)capturedCk * 2 * count))
                            dumpRel = std::string("see_dumps/") + name;
                    }
                    if (log.isOpen()) {
                        std::ostringstream os;
                        os << envelope(cfg, "sim_fault", "compute", "crash")
                           << "\"see_event\":true,\"iter\":" << totalIter << ",\"epoch\":" << epoch << ","
                           << "\"error\":\"" << jsonEscape(cudaGetErrorString(ce)) << "\","
                           << "\"dump\":\"" << jsonEscape(dumpRel) << "\","
                           << "\"dump_checkpoints\":" << capturedCk << ",\"dump_stride\":" << K << ","
                           << "\"num_particles\":" << cfg.num_particles << ","
                           << metaFields(cfg) << "}";
                        log.writeLine(os.str());
                    }
                    {   // Operator-facing one-liner to the journal (StandardError).
                        const char *synth = (injected || chaos) ? " [SYNTHETIC]" : "";
                        std::string dumpMsg = dumpRel.empty()
                            ? std::string("post-processing dump NOT saved")
                            : ("post-processing dump saved -> " + dumpRel);
                        fprintf(stderr,
                                "[cuda_particles] SEE Detected: sim_fault (%s)%s | epoch %llu iter %llu | %s\n",
                                cudaGetErrorString(ce), synth,
                                (unsigned long long)epoch, (unsigned long long)totalIter,
                                dumpMsg.c_str());
                    }
                    remove(runFlag.c_str());   // we logged it -> clean exit for restart
                    return 2;
                }

                if (generateGolden) {
                    // Golden generation hashes EVERY checkpoint (writes the full
                    // table); runtime detection below compares only the last one.
                    genHashes.push_back(hashState(hPos.data(), count, hVel.data(), count));
                } else {
                    // Buffer this checkpoint's full state for a possible SEE dump.
                    if (saveDumps && capturedCk < nCk) {
                        float *dst = snap.data() + (size_t)capturedCk * 2 * count;
                        memcpy(dst,         hPos.data(), nBytes);
                        memcpy(dst + count, hVel.data(), nBytes);
                        capturedCk++;
                    }
                    // DETECTION: only the FINAL checkpoint of the epoch is compared
                    // to the golden's last hash. Any earlier upset cascades to the
                    // end, so the final hash still flags the epoch as anomalous.
                    if ((step + K > E) && haveGolden && !golden.empty()) {
                        uint64_t h    = hashState(hPos.data(), count, hVel.data(), count);
                        bool     fin  = allFinite(hPos.data(), count) && allFinite(hVel.data(), count);
                        float    mAbs = maxAbs(hPos.data(), count);
                        uint64_t gh   = golden.back();
                        bool mismatch = (h != gh);
                        bool anomaly  = mismatch || !fin || (mAbs > 2.0f);
                        if (anomaly) {
                            corruptionSeen = 1; epochAnomaly = true;
                            // Classify for the "SEE Detected" line: NaN/Inf first,
                            // then out-of-bounds magnitude, else a bit-level hash
                            // mismatch against the golden table.
                            seeType = !fin ? "nonfinite"
                                    : (mAbs > 2.0f ? "out_of_bounds" : "golden_mismatch");
                        }
                        snprintf(hbuf, sizeof(hbuf), "%016llx", (unsigned long long)h);
                        snprintf(gbuf, sizeof(gbuf), "%016llx", (unsigned long long)gh);
                        if (log.isOpen()) {
                            std::ostringstream os;
                            os << envelope(cfg, "checksum", "compute", anomaly ? "anomaly" : "ok")
                               << "\"iter\":" << totalIter << ",\"epoch\":" << epoch << ",\"step\":" << step << ","
                               << "\"hash\":\"" << hbuf << "\",\"golden\":\"" << gbuf << "\","
                               << "\"mismatch\":" << (mismatch ? "true" : "false") << ","
                               << "\"finite\":" << (fin ? "true" : "false") << ","
                               << "\"max_abs_pos\":" << mAbs << ","
                               << "\"anomaly\":" << (anomaly ? "true" : "false") << ","
                               << metaFields(cfg) << "}";
                            log.writeLine(os.str());
                        }
                    }
                }

                writeHeartbeat(cfg.heartbeat_path, totalIter, epoch, step, seeEvents);
            }
        }

        // One SEE per affected epoch (final-checkpoint detection). Because each
        // epoch resets to the golden initial state, a single upset cascades to
        // the final hash, so comparing only the last checkpoint is enough to flag
        // the epoch -- but it cannot tell 1 SEE from 2. To recover that, on a flag
        // we DUMP this epoch's buffered per-checkpoint trajectory to see_dumps/;
        // an offline reconstruction on a reference Orin replays the corrupted
        // state forward and counts further, unexplained divergences. Keep the
        // beam flux low enough that grouped SEEs stay rare (see BUILD_PLAN 1a).
        if (!generateGolden && epochAnomaly) {
            seeEvents++;
            std::string dumpRel;
            if (saveDumps && capturedCk > 0) {
                char name[64];
                snprintf(name, sizeof(name), "epoch_%llu_iter_%llu.bin",
                         (unsigned long long)epoch, (unsigned long long)totalIter);
                if (writeSeeDump(dumpDir + "/" + name, snap, (size_t)capturedCk * 2 * count))
                    dumpRel = std::string("see_dumps/") + name;
                else
                    fprintf(stderr, "[cuda_particles] WARNING: failed to write SEE dump %s\n", name);
            }
            if (log.isOpen()) {
                std::ostringstream os;
                os << envelope(cfg, "see_event", "compute", "anomaly")
                   << "\"iter\":" << totalIter << ",\"epoch\":" << epoch << ","
                   << "\"see_event\":true,\"see_events\":" << seeEvents << ","
                   << "\"injected\":" << (injected ? "true" : "false") << ","
                   << "\"chaos\":" << (chaos ? "true" : "false") << ","
                   << "\"hash\":\"" << hbuf << "\",\"golden\":\"" << gbuf << "\","
                   << "\"dump\":\"" << jsonEscape(dumpRel) << "\","
                   << "\"dump_checkpoints\":" << capturedCk << ","
                   << "\"dump_stride\":" << K << ","
                   << "\"num_particles\":" << cfg.num_particles << ","
                   << "\"floats_per_checkpoint\":" << (unsigned long long)(2 * count) << ","
                   << metaFields(cfg) << "}";
                log.writeLine(os.str());
            }
            {   // Operator-facing one-liner to the journal (StandardError).
                const char *synth = (injected || chaos) ? " [SYNTHETIC]" : "";
                std::string dumpMsg = dumpRel.empty()
                    ? std::string("post-processing dump NOT saved")
                    : ("post-processing dump saved -> " + dumpRel);
                fprintf(stderr,
                        "[cuda_particles] SEE Detected: %s%s | epoch %llu iter %llu | %s\n",
                        seeType, synth,
                        (unsigned long long)epoch, (unsigned long long)totalIter,
                        dumpMsg.c_str());
            }
            writeHeartbeat(cfg.heartbeat_path, totalIter, epoch, E, seeEvents);
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
        os << envelope(cfg, "stop", "compute", "info")
           << "\"total_iter\":" << totalIter << ",\"epochs\":" << epoch << ","
           << "\"see_events\":" << seeEvents << ","
           << "\"corruption_seen\":" << (corruptionSeen ? "true" : "false") << ","
           << metaFields(cfg) << "}";
        log.writeLine(os.str());
    }

    // Clean stop -> remove the run marker so the next start is not flagged as a
    // crash. (An abnormal death leaves it, and the next start flags the crash.)
    remove(runFlag.c_str());

    // Nonzero exit (2) if any corruption/anomaly was observed, so the arbiter /
    // systemd can distinguish a clean stop from a suspect run.
    return corruptionSeen ? 2 : 0;
}
