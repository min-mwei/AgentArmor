#include "protocol.h"
#include "execution.h"
#include <cstddef>
#include <cstdint>

extern "C" int LLVMFuzzerTestOneInput(const uint8_t* data, size_t size) {
    if (size > 64 * 1024) return 0;
    try {
        const auto parsed = kai::parse_document(std::string(reinterpret_cast<const char*>(data), size), 64 * 1024);
        (void)kai::Json::from(parsed);
        const auto request = kai::wire::request(parsed);
        (void)kai::Layout(request, kai::Config{});
    } catch (const std::exception&) {}
    return 0;
}
