// Weight-free transport and scheduling tests. The fake exists only in this executable.
#include "device_lock.h"
#include "http.h"
#include "protocol.h"
#include <httplib.h>
#include <cstdlib>
#include <iostream>
#include <fstream>
#include <unistd.h>

using namespace kai;
using namespace kai::server;
using WireJson = kai::wire::Json;

static void check(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}

template<class F> static void fails(F fn, const char* message) {
    bool failed = false;
    try { fn(); } catch (const std::exception&) { failed = true; }
    check(failed, message);
}

static Request request(int key) {
    return {{2, 6, 7, 8, 20 + key, 9, 8, 21, 9, 10}, {0, 0, 1, 1, 1, 1, 1, 1, 1, 1},
            {0, 1, 2, 3, 4, 5, 6, 7, 8, 9}, {-1, -1, -1, 0, 0, 0, 1, 1, 1, -2}, 2, {9}, {{5, 8}}};
}

static WireJson json(const Request& r) {
    return {{"ids", r.ids}, {"seg", r.seg}, {"pos", r.pos}, {"opt", r.opt}, {"state_len", r.state_len},
            {"decide_idx", r.decide_idx}, {"opt_idx", r.opt_idx}};
}

class FakeEngine : public Engine {
    std::mutex mutex_;
    std::condition_variable entered_;
    bool paused_ = false;
    int failures_ = 0;
    std::vector<std::vector<int>> calls_;
  public:
    std::string name() const override { return "test"; }
    void pause() { std::lock_guard<std::mutex> lock(mutex_); paused_ = true; }
    void release() { { std::lock_guard<std::mutex> lock(mutex_); paused_ = false; } entered_.notify_all(); }
    void fail_once() { std::lock_guard<std::mutex> lock(mutex_); ++failures_; }
    void wait_calls(size_t count) {
        std::unique_lock<std::mutex> lock(mutex_);
        check(entered_.wait_for(lock, std::chrono::seconds(5), [&] { return calls_.size() >= count; }), "worker did not start");
    }
    std::vector<std::vector<int>> calls() { std::lock_guard<std::mutex> lock(mutex_); return calls_; }
    Response run(const Request& r, bool states) override { return run_batch({r}, states).responses[0]; }
    BatchResponse run_batch(const std::vector<Request>& requests, bool states) override {
        {
            std::unique_lock<std::mutex> lock(mutex_);
            std::vector<int> keys;
            for (const auto& r : requests) keys.push_back(r.ids[4] - 20);
            calls_.push_back(keys); entered_.notify_all();
            check(entered_.wait_for(lock, std::chrono::seconds(5), [&] { return !paused_; }), "test engine was not released");
            if (failures_) { --failures_; throw std::runtime_error("test engine failure"); }
        }
        BatchResponse batch;
        for (const auto& r : requests) {
            Response response;
            response.logits = {{float(r.ids[4]), -float(r.ids[4])}};
            response.evidence = {{.25f, .75f}};
            response.seconds = .001;
            if (states) response.states.assign(r.ids.size() * 3, float(r.ids[4]));
            batch.responses.push_back(std::move(response));
        }
        batch.seconds = .001;
        return batch;
    }
};

static void parsing() {
    auto value = json(request(0));
    check(wire::equal(request(0), wire::request(value)), "request roundtrip differs");
    for (auto invalid : {WireJson(1.5), WireJson(uint64_t(1) << 40), WireJson(-(int64_t(1) << 40)), WireJson(true)}) {
        auto bad = value; bad["ids"][0] = invalid;
        fails([&] { wire::request(bad); }, "non-int32 token accepted");
    }
    auto bad = value; bad["opt_idx"] = 1;
    fails([&] { wire::request(bad); }, "non-array options accepted");
}

static void batching_and_deduplication() {
    FakeEngine engine; engine.pause();
    Scheduler queue(engine, {2, 20, 16, 160});
    auto first = queue.submit({{request(0)}});
    engine.wait_calls(1);
    auto duplicate = queue.submit({{request(0)}});
    auto group = queue.submit({{request(1)}, {request(2)}, {request(1)}, {request(3)}, {request(4)}, {request(5), true}});
    check(duplicate[0].deduplicated && group[2].deduplicated, "inflight duplicates were not joined");
    engine.release();
    for (auto& ticket : group) {
        const auto& done = ticket.future.get();
        check(done.batch_records <= 2 && done.batch_tokens <= 20, "batch exceeded capacity");
    }
    check(duplicate[0].future.get().response.logits == first[0].future.get().response.logits, "duplicate response differs");
    check(group[0].future.get().response.states.empty(), "unrequested states leaked");
    check(group.back().future.get().response.states.size() == 30, "requested states missing");
    check(engine.calls().size() == 4 && queue.stats().deduplicated == 2, "duplicates consumed model work");
    check(queue.stats().completed == 6 && queue.stats().requests == 8, "request counters differ");
}

static void bounded_admission_and_shutdown() {
    FakeEngine engine; engine.pause();
    Scheduler queue(engine, {1, 10, 2, 20});
    auto active = queue.submit({{request(0)}}); engine.wait_calls(1);
    fails([&] { queue.submit({{request(1)}, {request(2)}, {request(3)}}); }, "oversized group admitted");
    check(queue.stats().queued == 0, "partial group admitted");
    auto pending = queue.submit({{request(1)}, {request(2)}});
    auto closing = std::async(std::launch::async, [&] { queue.close(); });
    for (const auto& ticket : pending) {
        check(ticket.future.wait_for(std::chrono::seconds(5)) == std::future_status::ready, "shutdown left pending work unresolved");
        fails([&] { (void)ticket.future.get(); }, "shutdown returned a fabricated response");
    }
    engine.release(); (void)active[0].future.get(); closing.get();
    fails([&] { queue.submit({{request(3)}}); }, "closed scheduler admitted work");
}

static void concurrent_shutdown() {
    FakeEngine engine; engine.pause();
    Scheduler queue(engine, {1, 10, 2, 20});
    auto active = queue.submit({{request(0)}}); engine.wait_calls(1);
    auto pending = queue.submit({{request(1)}});
    auto first = std::async(std::launch::async, [&] { queue.close(); });
    auto second = std::async(std::launch::async, [&] { queue.close(); });
    check(pending[0].future.wait_for(std::chrono::seconds(5)) == std::future_status::ready,
          "concurrent shutdown left queued work unresolved");
    fails([&] { (void)pending[0].future.get(); }, "shutdown fabricated a result");
    engine.release(); (void)active[0].future.get(); first.get(); second.get();
}

static void priority_and_failure() {
    FakeEngine engine; engine.pause();
    Scheduler queue(engine, {1, 10, 16, 160});
    auto active = queue.submit({{request(0)}}); engine.wait_calls(1);
    auto pending = queue.submit({{request(1), false, true}, {request(2)}, {request(3)}, {request(4)}, {request(5)}});
    engine.release(); (void)active[0].future.get();
    for (auto& ticket : pending) (void)ticket.future.get();
    auto calls = engine.calls();
    check(calls.size() == 6 && calls[1][0] == 2 && calls[2][0] == 3 && calls[3][0] == 1,
          "background fairness or foreground priority failed");
    engine.fail_once();
    auto failed = queue.submit({{request(10)}});
    fails([&] { (void)failed[0].future.get(); }, "model failure did not propagate");
    auto retry = queue.submit({{request(10)}});
    check(!retry[0].deduplicated && retry[0].future.get().response.logits[0][0] == 30, "failure was cached");
    check(queue.stats().failed == 1, "failure counter differs");
}

static void promotion() {
    FakeEngine engine; engine.pause();
    Scheduler queue(engine, {1, 10, 16, 160});
    auto active = queue.submit({{request(0)}}); engine.wait_calls(1);
    auto scans = queue.submit({{request(1), false, true}, {request(2), false, true}});
    auto guard = queue.submit({{request(2)}});
    engine.release(); (void)active[0].future.get();
    for (auto& ticket : scans) (void)ticket.future.get();
    check(guard[0].deduplicated && engine.calls()[1][0] == 2, "foreground duplicate did not promote a queued scan");
}

static void http() {
    FakeEngine engine;
    const Limits limits{4, 40, 8, 80};
    Scheduler queue(engine, limits);
    HttpService service(queue, Config{}, "test", "kai-test", limits, 8);
    const int port = service.bind("127.0.0.1", 0);
    check(port > 0, "HTTP bind failed");
    std::thread listener([&] { service.listen(); });
    try {
        httplib::Client client("127.0.0.1", port);
        client.set_connection_timeout(2); client.set_read_timeout(5);
        auto health = client.Get("/healthz");
        check(health && health->status == 200, "health check failed");
        auto models = client.Get("/v1/models");
        check(models && WireJson::parse(models->body)["data"][0]["id"] == "kai-test", "model discovery differs");
        auto missing = client.Get("/missing");
        check(missing && missing->status == 404 && WireJson::parse(missing->body).contains("error"), "HTTP errors are not JSON");
        auto row = json(request(0)); row["id"] = "one"; row["states"] = true;
        auto single = client.Post("/v1/infer", row.dump(), "application/json");
        check(single && single->status == 200, "single HTTP request failed");
        auto result = WireJson::parse(single->body);
        check(result["id"] == "one" && result["states"].size() == 10 && result["model"] == "kai-test", "single HTTP output differs");
        auto second = json(request(1)); second["id"] = "two";
        WireJson batch{{"requests", WireJson::array({row, second, row})}};
        auto multiple = client.Post("/v1/infer/batch", batch.dump(), "application/json");
        check(multiple && multiple->status == 200, "batch HTTP request failed");
        auto responses = WireJson::parse(multiple->body)["responses"];
        check(responses.size() == 3 && responses[0]["id"] == "one" && responses[1]["id"] == "two" &&
              responses[2]["id"] == "one" && responses[2]["deduplicated"] == true, "batch order or deduplication differs");
        auto malformed = client.Post("/v1/infer", "{", "application/json");
        check(malformed && malformed->status == 400, "malformed JSON was not rejected");
        row["pos"][3] = 100;
        auto invalid = client.Post("/v1/infer", row.dump(), "application/json");
        check(invalid && invalid->status == 422, "invalid packed layout was not rejected");
        row = json(request(0)); row["opt"][4] = 100;
        auto option = client.Post("/v1/infer", row.dump(), "application/json");
        check(option && option->status == 422, "out-of-bounds pooling index was not rejected");
        auto oversized = client.Post("/v1/infer/batch", WireJson{{"requests", std::vector<WireJson>(9, second)}}.dump(), "application/json");
        check(oversized && oversized->status == 429 && oversized->has_header("Retry-After"), "HTTP capacity limit failed");
        engine.pause();
        const auto previous = engine.calls().size();
        auto concurrent = [&](int key) {
            return std::async(std::launch::async, [&, key] {
                httplib::Client caller("127.0.0.1", port); caller.set_read_timeout(5);
                auto reply = caller.Post("/v1/infer", json(request(key)).dump(), "application/json");
                check(reply && reply->status == 200, "concurrent HTTP request failed");
                return WireJson::parse(reply->body);
            });
        };
        auto first = concurrent(20); engine.wait_calls(previous + 1);
        auto a = concurrent(21), b = concurrent(22), c = concurrent(23);
        const auto deadline = Clock::now() + std::chrono::seconds(3);
        while (queue.stats().queued != 3 && Clock::now() < deadline) std::this_thread::sleep_for(std::chrono::milliseconds(5));
        check(queue.stats().queued == 3, "concurrent HTTP callers did not reach the scheduler");
        engine.release(); (void)first.get();
        check(a.get()["batch_records"] == 3 && b.get()["batch_records"] == 3 && c.get()["batch_records"] == 3,
              "concurrent HTTP callers did not share a model batch");
        auto metrics = client.Get("/metrics");
        check(metrics && WireJson::parse(metrics->body)["completed"] == 7, "HTTP counters differ");
    } catch (...) { service.stop(); listener.join(); throw; }
    service.stop(); listener.join();
}

static void lock_contract() {
    const char* old_path = std::getenv("KAI_GPU_LOCK");
    const char* old_guarded = std::getenv("KAI_GUARDED");
    const std::string saved_path = old_path ? old_path : "", saved_guarded = old_guarded ? old_guarded : "";
    char pattern[] = "/tmp/agentarmor-lock-test.XXXXXX";
    const char* directory = mkdtemp(pattern);
    check(directory != nullptr, "cannot create lock test directory");
    const std::string path = std::string(directory) + "/lock", target = std::string(directory) + "/target";
    setenv("KAI_GPU_LOCK", path.c_str(), 1); unsetenv("KAI_GUARDED");
    {
        DeviceLock owner("test");
        fails([&] { DeviceLock duplicate("test"); }, "model lock allowed a second owner");
        setenv("KAI_GUARDED", "1", 1); DeviceLock nested("outer guard owns the lock");
    }
    unsetenv("KAI_GUARDED");
    { DeviceLock released("test"); }
    unlink(path.c_str());
    { std::ofstream file(target); file << "retained"; }
    check(symlink(target.c_str(), path.c_str()) == 0, "cannot create symlink fixture");
    fails([&] { DeviceLock symlinked("test"); }, "symlink lock was accepted");
    unlink(path.c_str());
    check(link(target.c_str(), path.c_str()) == 0, "cannot create hard-link fixture");
    fails([&] { DeviceLock linked("test"); }, "hard-linked lock was accepted");
    std::ifstream file(target); std::string content; file >> content;
    check(content == "retained", "lock validation modified the target");
    unlink(path.c_str()); unlink(target.c_str()); rmdir(directory);
    if (old_path) setenv("KAI_GPU_LOCK", saved_path.c_str(), 1); else unsetenv("KAI_GPU_LOCK");
    if (old_guarded) setenv("KAI_GUARDED", saved_guarded.c_str(), 1); else unsetenv("KAI_GUARDED");
}

int main() {
    try {
        parsing(); batching_and_deduplication(); bounded_admission_and_shutdown();
        concurrent_shutdown(); priority_and_failure(); promotion(); http(); lock_contract();
        std::cout << "8 server contracts passed\n";
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n'; return 1;
    }
}
