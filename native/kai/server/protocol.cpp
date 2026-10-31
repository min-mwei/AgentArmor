#include "protocol.h"
#include <cmath>
#include <fstream>
#include <limits>

namespace kai::wire {
namespace {
int32_t integer(const Json& value) {
    if (!value.is_number_integer() ||
        (value.is_number_unsigned() && value.get<uint64_t>() > uint64_t(INT32_MAX)))
        throw std::invalid_argument("request indices must be int32 integers");
    int64_t n = value.get<int64_t>();
    if (n < INT32_MIN || n > INT32_MAX) throw std::invalid_argument("request index exceeds int32");
    return int32_t(n);
}

std::vector<int32_t> indices(const Json& value) {
    if (!value.is_array()) throw std::invalid_argument("request indices must be arrays");
    std::vector<int32_t> result;
    result.reserve(value.size());
    for (const auto& item : value) result.push_back(integer(item));
    return result;
}

void finite(const std::vector<float>& values) {
    for (float value : values) if (!std::isfinite(value)) throw std::runtime_error("model returned nonfinite values");
}
}  // namespace

Request request(const Json& value) {
    Request r;
    r.ids = indices(value.at("ids")); r.seg = indices(value.at("seg"));
    r.pos = indices(value.at("pos")); r.opt = indices(value.at("opt"));
    r.state_len = integer(value.at("state_len"));
    r.decide_idx = indices(value.at("decide_idx"));
    const auto& options = value.at("opt_idx");
    if (!options.is_array()) throw std::invalid_argument("opt_idx must be an array");
    for (const auto& row : options) r.opt_idx.push_back(indices(row));
    return r;
}

Json response(const Response& value, size_t tokens, bool states) {
    for (const auto& row : value.logits) finite(row);
    for (const auto& row : value.evidence) finite(row);
    Json out{{"seconds", value.seconds}, {"tokens", tokens}, {"logits", value.logits}, {"evidence", value.evidence}};
    if (states) {
        finite(value.states);
        if (!tokens || value.states.size() % tokens) throw std::runtime_error("invalid state output dimensions");
        const size_t width = value.states.size() / tokens;
        out["states"] = Json::array();
        for (size_t i = 0; i < tokens; ++i)
            out["states"].push_back(std::vector<float>(value.states.begin() + i * width, value.states.begin() + (i + 1) * width));
    }
    return out;
}

Json read(const std::string& path) {
    std::ifstream stream(path, std::ios::binary);
    if (!stream) throw std::runtime_error("cannot read " + path);
    std::string text(4 * 1024 * 1024 + 1, '\0');
    stream.read(text.data(), std::streamsize(text.size()));
    text.resize(size_t(stream.gcount()));
    if (stream.bad()) throw std::runtime_error("cannot read complete JSON input");
    return parse_document(text, 4 * 1024 * 1024);
}

bool equal(const Request& a, const Request& b) {
    return a.state_len == b.state_len && a.ids == b.ids && a.seg == b.seg && a.pos == b.pos && a.opt == b.opt &&
           a.decide_idx == b.decide_idx && a.opt_idx == b.opt_idx;
}

size_t hash(const Request& r, bool states) {
    size_t h = 1469598103934665603ULL;
    auto add = [&](int32_t n) { h = (h ^ uint32_t(n)) * 1099511628211ULL; };
    auto array = [&](const auto& a) { add(int32_t(a.size())); for (int32_t n : a) add(n); };
    add(states); add(r.state_len);
    array(r.ids); array(r.seg); array(r.pos); array(r.opt); array(r.decide_idx);
    for (const auto& row : r.opt_idx) array(row);
    return h;
}
}  // namespace kai::wire
