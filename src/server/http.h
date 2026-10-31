#pragma once
#include "scheduler.h"

namespace kai::server {
class HttpService {
  public:
    HttpService(Scheduler& scheduler, Config config, std::string backend, std::string model, Limits limits, size_t threads = 32);
    ~HttpService();
    int bind(const std::string& host, int port);
    bool listen();
    void stop();
  private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
}  // namespace kai::server
