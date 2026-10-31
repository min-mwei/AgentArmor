#include "device_lock.h"
#include "http.h"
#include "protocol.h"
#include <atomic>
#include <csignal>
#include <cstdio>
#include <limits>
#include <iostream>

using namespace kai;
using namespace kai::server;

namespace {
std::atomic<bool> stopping{false};
static_assert(std::atomic<bool>::is_always_lock_free);
void stop(int) { stopping.store(true, std::memory_order_relaxed); }

class ResidentEngine : public Engine {
    std::unique_ptr<Engine> engine_;
  public:
    ResidentEngine(const std::string& weights, const std::string& backend) {
        DeviceLock lock("kaiinfer-server: load " + backend);
        engine_ = make_engine(weights, backend);
    }
    Response run(const Request& r, bool states) override {
        DeviceLock lock("kaiinfer-server: " + name());
        return engine_->run(r, states);
    }
    BatchResponse run_batch(const std::vector<Request>& requests, bool states) override {
        DeviceLock lock("kaiinfer-server: " + name());
        return engine_->run_batch(requests, states);
    }
    std::string name() const override { return engine_->name(); }
};

size_t positive(const std::string& value) {
    if (value.empty() || value.find_first_not_of("0123456789") != std::string::npos)
        throw std::invalid_argument("expected a positive integer, got " + value);
    const auto n = std::stoull(value);
    if (!n || n > std::numeric_limits<size_t>::max()) throw std::invalid_argument("integer out of range: " + value);
    return size_t(n);
}

void usage() {
    fprintf(stderr, "AgentArmor native runtime (experimental; macOS Apple Silicon M4+)\n"
        "  agentarmor doctor\n"
        "  agentarmor hook --agent codex_code|claude_code\n"
        "  agentarmor serve --encoded-only --weights DIR [--device cpu|gpu|auto]\n"
        "    [--host 127.0.0.1] [--port 8009] [--batch-records 8] [--batch-tokens 8192]\n"
        "    [--queue-records 128] [--queue-tokens 65536] [--http-threads 32]\n"
        "Text policy/tokenizer integration is pending qualification. Hook checks deny explicitly.\n"
        "Encoded serving is a diagnostic API, not an AgentArmor policy-enforcement service.\n");
}

int hook(int argc, char** argv) {
    if (argc != 4 || std::string(argv[2]) != "--agent" ||
        (std::string(argv[3]) != "codex_code" && std::string(argv[3]) != "claude_code"))
        throw std::invalid_argument("hook requires --agent codex_code|claude_code");
    std::string input(1'000'001, '\0');
    std::cin.read(input.data(), std::streamsize(input.size()));
    const auto count = std::cin.gcount();
    if (count > 1'000'000) throw std::invalid_argument("hook input exceeds 1 MB");
    input.resize(size_t(count));
    const auto event = parse_document(input, 1'000'000);
    const auto name = event.at("hook_event_name").get<std::string>();
    const char* reason = "AgentArmor native text policy evaluation is not qualified; no clear verdict is available.";
    wire::Json out;
    if (name == "PreToolUse") {
        out = {{"hookSpecificOutput", {{"hookEventName", name}, {"permissionDecision", "deny"},
            {"permissionDecisionReason", reason}}}};
    } else if (name == "PermissionRequest") {
        out = {{"hookSpecificOutput", {{"hookEventName", name},
            {"decision", {{"behavior", "deny"}, {"message", reason}}}}}};
    } else {
        out = {{"systemMessage", "AgentArmor native observation is incomplete; no protection claim is available."}};
        std::cout << out.dump() << '\n';
        return 0;
    }
    std::cout << out.dump() << '\n';
    return 2;
}
}  // namespace

int main(int argc, char** argv) {
    try {
        if (argc == 1 || std::string(argv[1]) == "--help") { usage(); return 0; }
        if (std::string(argv[1]) == "doctor") {
            std::cout << wire::Json{{"runtime", "native"}, {"platform", "macos-arm64"},
                {"model_loaded", false}, {"policy_ready", false}, {"cpu_built", true},
#ifdef KAI_METAL
                {"gpu_built", true},
#else
                {"gpu_built", false},
#endif
                {"gpu_backend", "apple-metal"}, {"experimental", true}}.dump() << '\n';
            return 0;
        }
        if (std::string(argv[1]) == "hook") return hook(argc, argv);
        if (std::string(argv[1]) != "serve") throw std::invalid_argument("unknown command; use --help");
        std::string weights, backend, device = "auto", host = "127.0.0.1", model = "kai";
        bool encoded_only = false;
        Limits limits;
        int port = 8009;
        size_t threads = 32;
        for (int i = 2; i < argc; ++i) {
            const std::string arg = argv[i];
            auto next = [&]() {
                if (i + 1 >= argc) throw std::invalid_argument("missing value for " + arg);
                return std::string(argv[++i]);
            };
            if (arg == "--help") { usage(); return 0; }
            if (arg == "--encoded-only") { encoded_only = true; continue; }
            if (arg == "--weights") weights = next();
            else if (arg == "--device") device = next();
            else if (arg == "--host") host = next();
            else if (arg == "--model") model = next();
            else if (arg == "--port") {
                const std::string value = next();
                const size_t n = value == "0" ? 0 : positive(value);
                if (n > 65535) throw std::invalid_argument("port must be 0..65535");
                port = int(n);
            }
            else if (arg == "--batch-records") limits.batch_records = positive(next());
            else if (arg == "--batch-tokens") limits.batch_tokens = positive(next());
            else if (arg == "--queue-records") limits.queue_records = positive(next());
            else if (arg == "--queue-tokens") limits.queue_tokens = positive(next());
            else if (arg == "--http-threads") threads = positive(next());
            else throw std::invalid_argument("unknown argument " + arg);
        }
        if (!encoded_only) throw std::invalid_argument("encoded diagnostics require --encoded-only; policy serving is not ready");
        if (weights.empty()) { usage(); return 2; }
        if (device != "cpu" && device != "gpu" && device != "auto")
            throw std::invalid_argument("device must be cpu, gpu, or auto");
        bool gpu = false;
#ifdef KAI_METAL
        if (device != "cpu") gpu = metal_available();
#endif
        if (device == "gpu" && !gpu) throw std::invalid_argument("GPU mode is unavailable in this build or on this device");
        backend = device != "cpu" && gpu ? "metal-opt" : "cpu-opt";
        if (host != "127.0.0.1" && host != "::1" && host != "localhost")
            throw std::invalid_argument("encoded diagnostics may bind only to loopback");
        if (threads > 256) throw std::invalid_argument("http-threads must be 1..256");
        Config config = load_config(weights + "/config.json");
        ResidentEngine engine(weights, backend);
        Scheduler scheduler(engine, limits);
        HttpService service(scheduler, config, engine.name(), model, limits, threads);
        port = service.bind(host, port);
        if (port < 0) throw std::runtime_error("cannot bind the HTTP listener");
        std::signal(SIGINT, stop); std::signal(SIGTERM, stop);
        std::atomic<bool> finished{false};
        std::thread shutdown([&] {
            while (!finished && !stopping) std::this_thread::sleep_for(std::chrono::milliseconds(100));
            if (stopping) { service.stop(); scheduler.close(); }
        });
        printf("%s\n", wire::Json{{"status", "ready"}, {"host", host}, {"port", port},
            {"backend", engine.name()}, {"device", backend == "cpu-opt" ? "cpu" : "gpu"},
            {"policy_ready", false}, {"model", model}}.dump().c_str());
        fflush(stdout);
        bool served;
        try { served = service.listen(); }
        catch (...) { finished = true; shutdown.join(); throw; }
        finished = true; shutdown.join();
        scheduler.close();
        if (!served && !stopping) throw std::runtime_error("HTTP listener stopped with an error");
    } catch (const std::exception& error) {
        if (argc > 1 && std::string(argv[1]) == "hook")
            fprintf(stderr, "AgentArmor could not complete the native policy check; this action is denied.\n");
        else fprintf(stderr, "error: %s\n", error.what());
        return 2;
    }
    return 0;
}
