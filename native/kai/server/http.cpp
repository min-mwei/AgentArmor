#include "http.h"
#include "execution.h"
#include "protocol.h"
#include "third_party/httplib.h"
#include <algorithm>

namespace kai::server {
using Json = wire::Json;

struct HttpService::Impl {
    httplib::Server http;
    Scheduler& scheduler;
    Config config;
    std::string backend, model;
    Limits limits;

    Impl(Scheduler& queue, Config cfg, std::string engine, std::string identity, Limits caps, size_t threads)
        : scheduler(queue), config(std::move(cfg)), backend(std::move(engine)), model(std::move(identity)), limits(caps) {
        http.new_task_queue = [threads, caps] { return new httplib::ThreadPool(threads, caps.queue_records); };
        http.set_payload_max_length(4 * 1024 * 1024);
        http.set_read_timeout(10); http.set_write_timeout(20);
        http.set_error_handler([](const auto&, auto& response) {
            if (response.body.empty()) fail(response, response.status, httplib::status_message(response.status));
        });
        http.Get("/healthz", [this](const auto&, auto& response) {
            response.set_content(Json{{"status", "ok"}, {"backend", backend}}.dump(), "application/json");
        });
        http.Get("/readyz", [this](const auto&, auto& response) {
            response.set_content(Json{{"status", "ready"}, {"backend", backend}, {"model", model}}.dump(), "application/json");
        });
        http.Get("/v1/models", [this](const auto&, auto& response) {
            response.set_content(Json{{"object", "list"}, {"data", {{{"id", model}, {"backend", backend},
                {"input", "encoded-v1"}, {"mask", config.full_mask ? "full" : "prefix"}}}}}.dump(), "application/json");
        });
        http.Get("/metrics", [this](const auto&, auto& response) {
            const Stats s = scheduler.stats();
            response.set_content(Json{{"requests", s.requests}, {"deduplicated", s.deduplicated}, {"completed", s.completed},
                {"failed", s.failed}, {"batches", s.batches}, {"rejected", s.rejected}, {"queued", s.queued},
                {"queued_tokens", s.queued_tokens}, {"active", s.active}, {"max_batch", s.max_batch},
                {"batch_record_limit", limits.batch_records}, {"batch_token_limit", limits.batch_tokens}}.dump(), "application/json");
        });
        http.Post("/v1/infer", [this](const auto& request, auto& response) { infer(request, response, false); });
        http.Post("/v1/infer/batch", [this](const auto& request, auto& response) { infer(request, response, true); });
    }

    Input input(const Json& value, bool states) {
        Input item{wire::request(value), states, false};
        if (value.contains("states")) item.states = value.at("states").get<bool>();
        const std::string priority = value.value("priority", std::string("guard"));
        if (priority != "guard" && priority != "scan") throw std::invalid_argument("priority must be guard or scan");
        item.background = priority == "scan";
        if (value.contains("id") && !value.at("id").is_string()) throw std::invalid_argument("id must be a string");
        if (item.request.ids.size() > limits.batch_tokens) throw std::invalid_argument("request exceeds the token budget");
        (void)Layout(item.request, config);
        if (item.request.decide_idx.empty()) throw std::invalid_argument("request has no questions");
        return item;
    }

    void infer(const httplib::Request& request, httplib::Response& response, bool batch) {
        try {
            const auto body = parse_document(request.body, 4 * 1024 * 1024);
            Json rows = batch ? body.at("requests") : Json::array({body});
            if (!rows.is_array() || rows.empty()) throw std::invalid_argument("requests must be a nonempty array");
            if (rows.size() > limits.queue_records) throw QueueFull("batch exceeds the queue record limit");
            const bool states = batch && body.value("states", false);
            std::vector<Input> inputs;
            for (const auto& row : rows) inputs.push_back(input(row, states));
            auto tickets = scheduler.submit(std::move(inputs));
            Json results = Json::array();
            for (size_t i = 0; i < tickets.size(); ++i) {
                if (tickets[i].future.wait_until(tickets[i].arrived + std::chrono::seconds(20)) != std::future_status::ready)
                    throw std::runtime_error("inference deadline exceeded");
                const auto& done = tickets[i].future.get();
                const bool dump = rows[i].value("states", states);
                Json result = wire::response(done.response, rows[i].at("ids").size(), dump);
                result["model"] = model; result["backend"] = backend;
                result["batch_records"] = done.batch_records; result["batch_tokens"] = done.batch_tokens;
                result["deduplicated"] = tickets[i].deduplicated;
                result["queue_seconds"] = std::max(0.0, std::chrono::duration<double>(done.started - tickets[i].arrived).count());
                if (rows[i].contains("id")) result["id"] = rows[i]["id"];
                results.push_back(std::move(result));
            }
            response.set_content((batch ? Json{{"responses", results}} : results[0]).dump(), "application/json");
        } catch (const Json::parse_error&) { fail(response, 400, "invalid JSON input"); }
        catch (const QueueFull& error) { response.set_header("Retry-After", "1"); fail(response, 429, error.what()); }
        catch (const Json::exception& error) { fail(response, 422, error.what()); }
        catch (const std::invalid_argument& error) { fail(response, 422, error.what()); }
        catch (const std::exception& error) { fail(response, 503, error.what()); }
    }

    static void fail(httplib::Response& response, int status, const char* message) {
        response.status = status;
        response.set_content(Json{{"error", message}}.dump(), "application/json");
    }
};

HttpService::HttpService(Scheduler& scheduler, Config config, std::string backend, std::string model, Limits limits, size_t threads)
    : impl_(std::make_unique<Impl>(scheduler, std::move(config), std::move(backend), std::move(model), limits, threads)) {}
HttpService::~HttpService() = default;
int HttpService::bind(const std::string& host, int port) {
    return port == 0 ? impl_->http.bind_to_any_port(host) : impl_->http.bind_to_port(host, port) ? port : -1;
}
bool HttpService::listen() { return impl_->http.listen_after_bind(); }
void HttpService::stop() { impl_->http.stop(); }
}  // namespace kai::server
