#include "scheduler.h"
#include "protocol.h"
#include <algorithm>

namespace kai::server {
struct Scheduler::Task {
    Input input;
    size_t key;
    bool queued = true;
    std::promise<Completion> promise;
    std::shared_future<Completion> future = promise.get_future().share();
    Task(Input value, size_t hash) : input(std::move(value)), key(hash) {}
};

Scheduler::Scheduler(Engine& engine, Limits limits) : engine_(engine), limits_(limits) {
    if (!limits.batch_records || !limits.batch_tokens || !limits.queue_records || !limits.queue_tokens)
        throw std::invalid_argument("scheduler limits must be positive");
    worker_ = std::thread([this] { work(); });
}

Scheduler::~Scheduler() { close(); }

std::vector<Ticket> Scheduler::submit(std::vector<Input> inputs) {
    if (inputs.empty()) throw std::invalid_argument("empty request batch");
    std::lock_guard<std::mutex> lock(mutex_);
    if (stopped_) throw std::runtime_error("inference scheduler is stopped");
    const auto arrived = Clock::now();
    std::vector<Ticket> tickets;
    std::vector<std::shared_ptr<Task>> added, promote;
    size_t tokens = 0;
    for (auto& input : inputs) {
        if (input.request.ids.empty() || input.request.ids.size() > limits_.batch_tokens)
            throw std::invalid_argument("request exceeds the batch token budget");
        const size_t key = wire::hash(input.request, input.states);
        std::shared_ptr<Task> task;
        auto match = [&](const auto& candidate) {
            return candidate->input.states == input.states && wire::equal(candidate->input.request, input.request);
        };
        auto found = inflight_.find(key);
        if (found != inflight_.end())
            for (const auto& weak : found->second)
                if (auto candidate = weak.lock(); candidate && match(candidate)) { task = candidate; break; }
        if (!task)
            for (const auto& candidate : added)
                if (candidate->key == key && match(candidate)) { task = candidate; break; }
        const bool duplicate = bool(task);
        if (!task) {
            tokens += input.request.ids.size();
            task = std::make_shared<Task>(std::move(input), key);
            added.push_back(task);
        } else if (!input.background && task->input.background && task->queued) promote.push_back(task);
        tickets.push_back({task->future, arrived, duplicate});
    }
    if (stats_.queued + added.size() > limits_.queue_records || stats_.queued_tokens + tokens > limits_.queue_tokens) {
        stats_.rejected += inputs.size();
        throw QueueFull("inference queue is full");
    }
    for (auto& task : added) {
        inflight_[task->key].push_back(task);
        (task->input.background ? scans_ : guards_).push_back(task);
    }
    for (auto& task : promote) {
        auto at = std::find(scans_.begin(), scans_.end(), task);
        if (at != scans_.end()) { scans_.erase(at); task->input.background = false; guards_.push_back(task); }
    }
    stats_.queued += added.size(); stats_.queued_tokens += tokens;
    stats_.requests += tickets.size();
    for (const auto& ticket : tickets) stats_.deduplicated += ticket.deduplicated;
    ready_.notify_one();
    return tickets;
}

void Scheduler::forget(const std::shared_ptr<Task>& task) {
    auto at = inflight_.find(task->key);
    auto& entries = at->second;
    entries.erase(std::remove_if(entries.begin(), entries.end(), [&](const auto& weak) {
        auto value = weak.lock(); return !value || value == task;
    }), entries.end());
    if (entries.empty()) inflight_.erase(at);
}

Stats Scheduler::stats() const { std::lock_guard<std::mutex> lock(mutex_); return stats_; }

void Scheduler::work() {
    for (;;) {
        std::vector<std::shared_ptr<Task>> tasks;
        size_t tokens = 0;
        bool states = false;
        {
            std::unique_lock<std::mutex> lock(mutex_);
            ready_.wait(lock, [&] { return stopped_ || !guards_.empty() || !scans_.empty(); });
            if (stopped_) return;
            while (tasks.size() < limits_.batch_records && (!guards_.empty() || !scans_.empty())) {
                const bool background = guards_.empty() || (!scans_.empty() && guard_streak_ >= 3);
                auto& queue = background ? scans_ : guards_;
                auto task = queue.front();
                if (!tasks.empty() && tokens + task->input.request.ids.size() > limits_.batch_tokens) break;
                queue.pop_front(); task->queued = false;
                tokens += task->input.request.ids.size(); states |= task->input.states;
                tasks.push_back(task);
                if (background) guard_streak_ = 0; else ++guard_streak_;
            }
            stats_.queued -= tasks.size(); stats_.queued_tokens -= tokens;
            stats_.active = tasks.size(); stats_.max_batch = std::max(stats_.max_batch, tasks.size());
        }
        const auto started = Clock::now();
        BatchResponse result;
        std::exception_ptr error;
        try {
            std::vector<Request> requests;
            for (const auto& task : tasks) requests.push_back(task->input.request);
            result = engine_.run_batch(requests, states);
            if (result.responses.size() != tasks.size()) throw std::runtime_error("engine returned an incomplete batch");
        } catch (...) { error = std::current_exception(); }
        {
            std::lock_guard<std::mutex> lock(mutex_);
            for (const auto& task : tasks) forget(task);
            if (error) stats_.failed += tasks.size(); else stats_.completed += tasks.size();
            ++stats_.batches; stats_.active = 0;
        }
        for (size_t i = 0; i < tasks.size(); ++i) {
            if (error) tasks[i]->promise.set_exception(error);
            else {
                if (!tasks[i]->input.states) result.responses[i].states.clear();
                tasks[i]->promise.set_value({std::move(result.responses[i]), started, tasks.size(), tokens});
            }
        }
    }
}

void Scheduler::close() {
    std::call_once(close_once_, [&] {
    std::vector<std::shared_ptr<Task>> pending;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        stopped_ = true;
        for (auto* queue : {&guards_, &scans_})
            while (!queue->empty()) {
                pending.push_back(queue->front()); forget(queue->front()); queue->pop_front();
            }
        stats_.failed += pending.size(); stats_.queued = 0; stats_.queued_tokens = 0;
    }
    for (auto& task : pending) task->promise.set_exception(std::make_exception_ptr(std::runtime_error("inference scheduler stopped")));
    ready_.notify_all();
    if (worker_.joinable()) worker_.join();
    });
}
}  // namespace kai::server
