#include "device_lock.h"
#include "protocol.h"
#include <cstdlib>
#include <ctime>
#include <filesystem>
#include <fcntl.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <unistd.h>

namespace kai::server {
DeviceLock::DeviceLock(const std::string& command) {
    if (const char* guarded = std::getenv("KAI_GUARDED"); guarded && std::string(guarded) == "1") return;
    std::filesystem::path path;
    if (const char* configured = std::getenv("KAI_GPU_LOCK")) path = configured;
    else if (const char* home = std::getenv("HOME")) path = std::filesystem::path(home) / ".cache/kai/gpu.lock";
    else throw std::runtime_error("HOME or KAI_GPU_LOCK is required for the model lock");
    if (path.has_parent_path()) std::filesystem::create_directories(path.parent_path());
    fd_ = open(path.c_str(), O_RDWR | O_CREAT | O_NOFOLLOW | O_CLOEXEC, 0600);
    if (fd_ < 0) throw std::runtime_error("cannot open the model lock: " + path.string());
    struct stat info{};
    if (fstat(fd_, &info) || !S_ISREG(info.st_mode) || info.st_uid != geteuid() || info.st_nlink != 1) {
        close(fd_); fd_ = -1;
        throw std::runtime_error("model lock must be an owned, single-link regular file");
    }
    if (flock(fd_, LOCK_EX | LOCK_NB)) {
        close(fd_); fd_ = -1;
        throw std::runtime_error("model device is busy; retry when the trainer releases " + path.string());
    }
    const std::time_t now = std::time(nullptr);
    std::tm utc{}; gmtime_r(&now, &utc);
    char started[32]; std::strftime(started, sizeof started, "%Y-%m-%dT%H:%M:%S+00:00", &utc);
    const auto record = wire::Json{{"pid", getpid()}, {"command", command}, {"started", started}, {"lock", path.string()}}.dump();
    if (ftruncate(fd_, 0) || pwrite(fd_, record.data(), record.size(), 0) != ssize_t(record.size())) {
        flock(fd_, LOCK_UN); close(fd_); fd_ = -1;
        throw std::runtime_error("cannot publish the model lock owner");
    }
}

DeviceLock::~DeviceLock() {
    if (fd_ < 0) return;
    ftruncate(fd_, 0); flock(fd_, LOCK_UN); close(fd_);
}
}  // namespace kai::server
