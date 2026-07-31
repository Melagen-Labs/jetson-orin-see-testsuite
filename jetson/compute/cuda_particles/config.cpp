#include "config.h"

#include <cctype>
#include <cstdlib>
#include <fstream>
#include <sstream>
#include <string>

namespace {

// Find the raw value token for "key" in a flat JSON object. Returns true and
// fills `val` (without surrounding quotes for strings) if found. This is a
// deliberately minimal extractor for our own controlled, flat config file --
// not a general-purpose JSON parser.
bool findValue(const std::string &json, const std::string &key, std::string &val)
{
    const std::string needle = "\"" + key + "\"";
    size_t k = json.find(needle);
    if (k == std::string::npos) return false;
    size_t c = json.find(':', k + needle.size());
    if (c == std::string::npos) return false;
    size_t i = c + 1;
    while (i < json.size() && std::isspace((unsigned char)json[i])) i++;
    if (i >= json.size()) return false;

    if (json[i] == '"') {
        // string value
        ++i;
        std::string out;
        while (i < json.size() && json[i] != '"') {
            if (json[i] == '\\' && i + 1 < json.size()) { out += json[i + 1]; i += 2; }
            else { out += json[i]; i++; }
        }
        val = out;
        return true;
    }
    // number / bool / null: read until ',' '}' or whitespace
    size_t start = i;
    while (i < json.size() && json[i] != ',' && json[i] != '}' &&
           !std::isspace((unsigned char)json[i])) i++;
    val = json.substr(start, i - start);
    return !val.empty();
}

void getStr(const std::string &j, const char *key, std::string &dst)
{
    std::string v; if (findValue(j, key, v)) dst = v;
}
void getU(const std::string &j, const char *key, unsigned int &dst)
{
    std::string v; if (findValue(j, key, v)) dst = (unsigned int)strtoul(v.c_str(), nullptr, 10);
}
void getULL(const std::string &j, const char *key, unsigned long long &dst)
{
    std::string v; if (findValue(j, key, v)) dst = strtoull(v.c_str(), nullptr, 10);
}
void getF(const std::string &j, const char *key, float &dst)
{
    std::string v; if (findValue(j, key, v)) dst = strtof(v.c_str(), nullptr);
}
void getB(const std::string &j, const char *key, bool &dst)
{
    std::string v; if (findValue(j, key, v)) dst = (v == "true" || v == "1");
}

} // namespace

bool loadConfig(const std::string &path, Config &out)
{
    std::ifstream f(path.c_str());
    if (!f.is_open()) return false;
    std::stringstream ss;
    ss << f.rdbuf();
    const std::string j = ss.str();

    getU  (j, "num_particles",     out.num_particles);
    getU  (j, "grid_dim",          out.grid_dim);
    getF  (j, "timestep",          out.timestep);
    getU  (j, "epoch_iterations",  out.epoch_iterations);
    getU  (j, "checksum_interval", out.checksum_interval);
    getULL(j, "iterations",        out.iterations);
    getU  (j, "seed",              out.seed);
    getStr(j, "tolerance_mode",    out.tolerance_mode);
    getB  (j, "save_see_epochs",   out.save_see_epochs);

    getStr(j, "log_dir",           out.log_dir);
    getStr(j, "golden_path",       out.golden_path);
    getStr(j, "heartbeat_path",    out.heartbeat_path);

    getStr(j, "run_id",            out.run_id);
    getStr(j, "jetson_id",         out.jetson_id);
    getStr(j, "beam_energy",       out.beam_energy);
    getStr(j, "fluence_source",    out.fluence_source);
    getStr(j, "shield_config",     out.shield_config);
    return true;
}
