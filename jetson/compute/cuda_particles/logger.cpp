#include "logger.h"

Logger::~Logger()
{
    if (f_) { fflush(f_); fclose(f_); f_ = nullptr; }
}

bool Logger::open(const std::string &dir, const std::string &name)
{
    std::string path = dir;
    if (!path.empty() && path.back() != '/' && path.back() != '\\') path += '/';
    path += name;
    f_ = fopen(path.c_str(), "a");
    return f_ != nullptr;
}

void Logger::writeLine(const std::string &jsonObject)
{
    if (!f_) return;
    fputs(jsonObject.c_str(), f_);
    fputc('\n', f_);
    fflush(f_);
}

std::string jsonEscape(const std::string &s)
{
    std::string o;
    o.reserve(s.size() + 8);
    for (char c : s) {
        switch (c) {
        case '"':  o += "\\\""; break;
        case '\\': o += "\\\\"; break;
        case '\n': o += "\\n";  break;
        case '\r': o += "\\r";  break;
        case '\t': o += "\\t";  break;
        default:
            if ((unsigned char)c < 0x20) {
                char buf[8];
                snprintf(buf, sizeof(buf), "\\u%04x", (unsigned int)(unsigned char)c);
                o += buf;
            } else {
                o += c;
            }
        }
    }
    return o;
}
