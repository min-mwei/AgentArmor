#pragma once
#include <string>

namespace kai::server {
// Uses the same kernel lock as kai.guard. An idle server releases it between batches.
class DeviceLock {
  public:
    explicit DeviceLock(const std::string& command);
    ~DeviceLock();
    DeviceLock(const DeviceLock&) = delete;
    DeviceLock& operator=(const DeviceLock&) = delete;
  private:
    int fd_ = -1;
};
}  // namespace kai::server
