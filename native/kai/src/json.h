// Strict, bounded JSON for model metadata. Keep the engine's typed view.
#pragma once
#include "../server/third_party/json.hpp"
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

struct Json {
    enum Kind { Null, Bool, Number, String, Array, Object };
    Kind kind = Null;
    bool b = false, is_integer = false;
    int64_t integer = 0;
    double num = 0;
    std::string str;
    std::vector<Json> arr;
    std::map<std::string, Json> obj;

    const Json& at(const std::string& key) const {
        if (kind != Object) throw std::invalid_argument("expected JSON object");
        auto it = obj.find(key);
        if (it == obj.end()) throw std::invalid_argument("missing model metadata field");
        return it->second;
    }
    bool has(const std::string& key) const { return kind == Object && obj.count(key); }
    const Json& operator[](size_t i) const { return arr.at(i); }
    size_t size() const { return kind == Array ? arr.size() : obj.size(); }
    int64_t i() const {
        if (kind != Number || !is_integer) throw std::invalid_argument("expected int64 metadata");
        return integer;
    }
    double d() const {
        if (kind != Number || !std::isfinite(num)) throw std::invalid_argument("expected finite numeric metadata");
        return num;
    }
    std::vector<int64_t> ints() const {
        if (kind != Array) throw std::invalid_argument("expected integer array");
        std::vector<int64_t> out;
        for (const auto& value : arr) out.push_back(value.i());
        return out;
    }
    static Json from(const nlohmann::json& value) {
        Json out;
        if (value.is_null()) return out;
        if (value.is_boolean()) { out.kind = Bool; out.b = value.get<bool>(); }
        else if (value.is_number()) {
            out.kind = Number; out.num = value.get<double>();
            if (!std::isfinite(out.num)) throw std::invalid_argument("nonfinite JSON number");
            if (value.is_number_unsigned() && value.get<uint64_t>() > uint64_t(INT64_MAX))
                throw std::invalid_argument("metadata integer exceeds int64");
            out.is_integer = value.is_number_integer();
            if (out.is_integer) out.integer = value.get<int64_t>();
        } else if (value.is_string()) { out.kind = String; out.str = value.get<std::string>(); }
        else if (value.is_array()) {
            out.kind = Array;
            for (const auto& item : value) out.arr.push_back(from(item));
        } else if (value.is_object()) {
            out.kind = Object;
            for (auto it = value.begin(); it != value.end(); ++it) out.obj.emplace(it.key(), from(it.value()));
        } else throw std::invalid_argument("unsupported JSON type");
        return out;
    }
};

inline Json parse_json(const std::string& text) { return Json::from(parse_document(text)); }

inline std::string json_floats(const std::vector<float>& values) {
    for (float value : values) if (!std::isfinite(value)) throw std::invalid_argument("nonfinite model output");
    return nlohmann::json(values).dump();
}
}  // namespace kai
