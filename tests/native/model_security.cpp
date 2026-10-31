// No model weights or GPU work: adversarial parser, tensor and config contracts.
#include "protocol.h"
#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>
#include <fcntl.h>
#include <unistd.h>

using namespace kai;
namespace {
void check(bool ok, const char* why) { if (!ok) throw std::runtime_error(why); }
template<class F> void rejects(F fn) {
    bool rejected = false;
    try { fn(); } catch (const std::exception&) { rejected = true; }
    check(rejected, "malformed input was accepted");
}
struct Temporary {
    std::filesystem::path path;
    Temporary() {
        char pattern[] = "/tmp/agentarmor-model-security.XXXXXX";
        auto* value = mkdtemp(pattern);
        if (!value) throw std::runtime_error("cannot create test directory");
        path = value;
    }
    ~Temporary() { std::error_code error; std::filesystem::remove_all(path, error); }
};
void tensor_file(const std::filesystem::path& path, std::string header, const std::string& bytes) {
    while (header.size() % 8) header.push_back(' ');
    const uint64_t length = header.size();
    std::ofstream out(path, std::ios::binary);
    out.write(reinterpret_cast<const char*>(&length), sizeof length);
    out.write(header.data(), std::streamsize(header.size()));
    out.write(bytes.data(), std::streamsize(bytes.size()));
    if (!out) throw std::runtime_error("cannot write test fixture");
}
int open_fds() {
    int count = 0;
    for (int fd = 0; fd < 1024; ++fd) if (fcntl(fd, F_GETFD) != -1) ++count;
    return count;
}
void json_security() {
    for (const auto& input : {"{\"x\":1,\"x\":2}", "1.2.3", "[1,]", "\"\\uD800\"",
                             "\"\\uDC00\"", "\"\\uD800\\u0000\"", "1e999", "\"\n\""}) {
        rejects([&] { parse_json(input); });
    }
    rejects([] { parse_json("1.5").i(); });
    rejects([] { parse_json("18446744073709551615").i(); });
    rejects([] { parse_json("[]").i(); });
    rejects([] { parse_document(std::string(66, '[') + "0" + std::string(66, ']')); });
    rejects([] { parse_document("1234", 3); });
    check(parse_json("\"\\uD83E\\uDD8A\"").str == "🦊", "Unicode surrogate pair changed");
    check(parse_json("{\"a\":{\"x\":1},\"b\":{\"x\":2}}").at("b").at("x").i() == 2,
          "independent object keys rejected");
    check(parse_json("-9223372036854775808").i() == INT64_MIN, "int64 minimum changed");
    rejects([] { json_floats({std::numeric_limits<float>::infinity()}); });
}
void tensor_security() {
    Temporary temp;
    const auto path = temp.path / "fixture.safetensors";
    const std::string zero(4, '\0');
    tensor_file(path, R"({"x":{"dtype":"F32","shape":[1],"data_offsets":[0,4]}})", zero);
    check(SafeTensors(path.string()).floats("x") == std::vector<float>{0}, "valid tensor changed");
    for (const auto& header : {
        R"({"x":{"dtype":"F32","shape":[1024],"data_offsets":[0,4]}})",
        R"({"x":{"dtype":"F32","shape":[-1],"data_offsets":[0,4]}})",
        R"({"x":{"dtype":"F32","shape":[9223372036854775807,8],"data_offsets":[0,4]}})",
        R"({"x":{"dtype":"F32","shape":[1],"data_offsets":[0,8]}})",
        R"({"x":{"dtype":"I64","shape":[1],"data_offsets":[0,4]}})",
        R"({"x":{"dtype":"F32","shape":[1],"data_offsets":[0,4]},"y":{"dtype":"F32","shape":[1],"data_offsets":[0,4]}})",
        "[]"
    }) {
        tensor_file(path, header, zero);
        const int before = open_fds();
        for (int i = 0; i < 40; ++i) rejects([&] { SafeTensors file(path.string()); });
        check(open_fds() == before, "descriptor leaked during failed model load");
    }
    tensor_file(path, R"({"x":{"dtype":"F32","shape":[1],"data_offsets":[1,5]}})", std::string(5, '\0'));
    rejects([&] { SafeTensors file(path.string()); });
    const float nan = std::numeric_limits<float>::quiet_NaN();
    tensor_file(path, R"({"x":{"dtype":"F32","shape":[1],"data_offsets":[0,4]}})",
                std::string(reinterpret_cast<const char*>(&nan), sizeof nan));
    rejects([&] { SafeTensors(path.string()).floats("x"); });
}
void config_security() {
    Temporary temp;
    const auto path = temp.path / "config.json";
    auto write = [&](const wire::Json& value) { std::ofstream(path) << value.dump(); };
    wire::Json config = {
        {"architecture","embeddinggemma2-text"}, {"hidden_size",512}, {"intermediate_size",2048},
        {"num_hidden_layers",24}, {"num_attention_heads",4}, {"num_key_value_heads",2},
        {"head_dim",256}, {"global_head_dim",512}, {"global_num_key_value_heads",1},
        {"hidden_size_per_layer_input",512}, {"embedding_dim",768}, {"vocab_size",262144},
        {"sliding_window",512}, {"rms_norm_eps",1e-6}, {"rope_theta_sliding",10000.0},
        {"rope_theta_global",1000000.0}, {"layer_types",std::vector<std::string>(24,"sliding_attention")},
        {"read","projected"}, {"mask","prefix"}, {"pointer_dp",256}, {"evidence_dh",256}, {"bos",2}
    };
    write(config);
    check(load_config(path.string()).hidden == 512, "valid config changed");
    for (const auto& field : {"hidden_size", "num_hidden_layers", "head_dim", "pointer_dp", "evidence_dh"}) {
        auto bad = config; bad[field] = INT64_MAX; write(bad);
        rejects([&] { load_config(path.string()); });
    }
    for (const auto& field : {"architecture","read","mask"}) {
        auto bad = config; bad[field] = "unknown"; write(bad);
        rejects([&] { load_config(path.string()); });
    }
    auto bad = config; bad["rms_norm_eps"] = -1; write(bad);
    rejects([&] { load_config(path.string()); });
}
}
int main() {
    try {
        json_security(); tensor_security(); config_security();
        std::cout << "JSON, tensor, config, Unicode, overflow and failed-load descriptor contracts passed\n";
        return 0;
    } catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
