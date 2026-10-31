#pragma once
#include "model.h"
#include <chrono>
#include <condition_variable>
#include <deque>
#include <future>
#include <mutex>
#include <thread>
#include <unordered_map>

namespace kai::server {
using Clock = std::chrono::steady_clock;

struct Limits {
    size_t batch_records = 8, batch_tokens = 8192, queue_records = 128, queue_tokens = 65536;
};
struct Input { Request request; bool states = false, background = false; };
struct Completion { Response response; Clock::time_point started; size_t batch_records, batch_tokens; };
struct Ticket { std::shared_future<Completion> future; Clock::time_point arrived; bool deduplicated; };
struct Stats {
    uint64_t requests = 0, deduplicated = 0, completed = 0, failed = 0, batches = 0, rejected = 0;
    size_t queued = 0, queued_tokens = 0, active = 0, max_batch = 0;
};
struct QueueFull : std::runtime_error { using std::runtime_error::runtime_error; };

class Scheduler {
  public:
    Scheduler(Engine& engine, Limits limits);
    ~Scheduler();
    std::vector<Ticket> submit(std::vector<Input> inputs);
    Stats stats() const;
    void close();

  private:
    struct Task;
    Engine& engine_;
    Limits limits_;
    mutable std::mutex mutex_;
    std::condition_variable ready_;
    std::deque<std::shared_ptr<Task>> guards_, scans_;
    std::unordered_map<size_t, std::vector<std::weak_ptr<Task>>> inflight_;
    Stats stats_;
    bool stopped_ = false;
    size_t guard_streak_ = 0;
    std::thread worker_;
    std::once_flag close_once_;
    void work();
    void forget(const std::shared_ptr<Task>& task);
};
}  // namespace kai::server
