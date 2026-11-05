// Strict, bounded JSON for model metadata. Keep the engine's typed view.
#pragma once
#include <json.hpp>
#include <cmath>
#include <cstdint>
#include <limits>
#include <map>
#include <stdexcept>
#include <string>
#include <unordered_set>
#include <vector>

namespace kai {
inline nlohmann::json parse_document(const std::string& text, size_t max_bytes = 16 * 1024 * 1024) {
    if (text.size() > max_bytes) throw std::invalid_argument("JSON input exceeds its byte limit");
    std::vector<std::unordered_set<std::string>> keys(66);
    auto check = [&](int depth, nlohmann::json::parse_event_t event, nlohmann::json& value) {
        if (depth < 0 || depth > 64) throw std::invalid_argument("JSON nesting exceeds 64");
        if (event == nlohmann::json::parse_event_t::object_start) keys.at(size_t(depth) + 1).clear();
        if (event == nlohmann::json::parse_event_t::key &&
            !keys.at(size_t(depth)).insert(value.get<std::string>()).second)
            throw std::invalid_argument("duplicate JSON object key");
        return true;
    };
    return nlohmann::json::parse(text, check);
}

} // namespace kai
