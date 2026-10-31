#pragma once
#include "model.h"
#include "third_party/json.hpp"

namespace kai::wire {
using Json = nlohmann::json;
Request request(const Json& value);
Json response(const Response& value, size_t tokens, bool states);
Json read(const std::string& path);
bool equal(const Request& a, const Request& b);
size_t hash(const Request& request, bool states);
}  // namespace kai::wire
