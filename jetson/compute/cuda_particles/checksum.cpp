#include "checksum.h"

#include <cmath>

uint64_t fnv1a64(const void *data, size_t bytes, uint64_t seed)
{
    const unsigned char *p = static_cast<const unsigned char *>(data);
    uint64_t h = seed;
    for (size_t i = 0; i < bytes; i++) {
        h ^= (uint64_t)p[i];
        h *= FNV1A64_PRIME;
    }
    return h;
}

uint64_t hashState(const float *pos, size_t posCount, const float *vel, size_t velCount)
{
    uint64_t h = fnv1a64(pos, posCount * sizeof(float), FNV1A64_OFFSET);
    h = fnv1a64(vel, velCount * sizeof(float), h);
    return h;
}

bool allFinite(const float *a, size_t count)
{
    for (size_t i = 0; i < count; i++) {
        if (!std::isfinite(a[i])) return false;
    }
    return true;
}

float maxAbs(const float *a, size_t count)
{
    float m = 0.0f;
    for (size_t i = 0; i < count; i++) {
        float v = std::fabs(a[i]);
        if (v > m) m = v;
    }
    return m;
}
