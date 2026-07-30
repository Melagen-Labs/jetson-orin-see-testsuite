#ifndef CUDA_PARTICLES_LOGGER_H
#define CUDA_PARTICLES_LOGGER_H

#include <cstdio>
#include <string>

// Append-only JSONL logger. Writes to the DUT-local compute log dir first so a
// record survives an Ethernet drop. Each line is flushed immediately.
class Logger {
public:
    Logger() : f_(nullptr) {}
    ~Logger();

    // Opens <dir>/<name> for append. Caller ensures the directory exists.
    bool open(const std::string &dir, const std::string &name);

    // Write one already-formed JSON object as a line (newline appended, flushed).
    void writeLine(const std::string &jsonObject);

    bool isOpen() const { return f_ != nullptr; }

private:
    FILE *f_;
};

// Minimal JSON string escaping for the values we emit.
std::string jsonEscape(const std::string &s);

#endif // CUDA_PARTICLES_LOGGER_H
