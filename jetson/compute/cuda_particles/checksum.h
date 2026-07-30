#ifndef CUDA_PARTICLES_CHECKSUM_H
#define CUDA_PARTICLES_CHECKSUM_H

#include <cstddef>
#include <cstdint>

// FNV-1a 64-bit: deterministic, order-sensitive, dependency-free. Used as the
// per-iteration SDC signature over the particle position/velocity buffers.
constexpr uint64_t FNV1A64_OFFSET = 1469598103934665603ULL;
constexpr uint64_t FNV1A64_PRIME  = 1099511628211ULL;

uint64_t fnv1a64(const void *data, size_t bytes, uint64_t seed = FNV1A64_OFFSET);

// Convenience: hash pos then vel into a single 64-bit state signature.
uint64_t hashState(const float *pos, size_t posCount, const float *vel, size_t velCount);

// Invariant checks: secondary SDC signal, used in "invariant" mode and always logged.
bool  allFinite(const float *a, size_t count);   // false if any NaN/Inf
float maxAbs(const float *a, size_t count);       // largest |value|, for bounds sanity

#endif // CUDA_PARTICLES_CHECKSUM_H
