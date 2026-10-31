// safetensors reader: an 8-byte little-endian header length, a JSON header {name: {dtype, shape, data_offsets}}, then
// the raw tensor bytes. The file is memory-mapped; tensors are views into it (bf16 or f32).
#pragma once
#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

#include <cstring>
#include <cmath>
#include <algorithm>
#include <limits>
#include <string>
#include <vector>

#include "json.h"

namespace kai {

enum class DType { BF16, F32 };

struct Tensor {
    DType dtype = DType::F32;
    std::vector<int64_t> shape;
    const uint8_t* data = nullptr;   // into the mapping
    size_t bytes = 0;
    int64_t numel() const {
        int64_t n = 1;
        for (auto s : shape) {
            if (s < 0 || (s && n > INT64_MAX / s)) throw std::invalid_argument("tensor dimension overflow");
            n *= s;
        }
        return n;
    }
    const uint16_t* bf16() const { return reinterpret_cast<const uint16_t*>(data); }
    const float* f32() const { return reinterpret_cast<const float*>(data); }
    int64_t dim(size_t i) const { return shape.at(i); }
};

inline float bf16_to_f32(uint16_t h) {
    uint32_t u = static_cast<uint32_t>(h) << 16;
    float f; std::memcpy(&f, &u, 4); return f;
}

class SafeTensors {
  public:
    explicit SafeTensors(const std::string& path, bool shared = false) : mapping_(path, shared) {
        uint64_t n; std::memcpy(&n, mapping_.data, 8);
        if (n > mapping_.size - 8 || n > 16 * 1024 * 1024)
            throw std::runtime_error("invalid safetensors header length");
        std::string header(reinterpret_cast<const char*>(mapping_.data + 8), n);
        Json h = parse_json(header);
        if (h.kind != Json::Object) throw std::runtime_error("safetensors header must be an object");
        const uint8_t* base = mapping_.data + 8 + n;
        std::vector<std::pair<int64_t, int64_t>> extents;
        for (auto& [name, v] : h.obj) {
            if (name == "__metadata__") continue;
            Tensor t;
            std::string dt = v.at("dtype").str;
            if (dt == "BF16") t.dtype = DType::BF16; else if (dt == "F32") t.dtype = DType::F32;
            else throw std::runtime_error("unsupported dtype " + dt + " for " + name);
            t.shape = v.at("shape").ints();
            auto off = v.at("data_offsets").ints();
            if (off.size() != 2 || off[0] < 0 || off[1] < off[0] || uint64_t(off[1]) > mapping_.size - 8 - n)
                throw std::runtime_error("invalid tensor extent " + name);
            t.data = base + off[0]; t.bytes = off[1] - off[0];
            const size_t width = t.dtype == DType::F32 ? 4 : 2;
            const auto count = uint64_t(t.numel());
            if (count > SIZE_MAX / width || count * width != t.bytes ||
                reinterpret_cast<uintptr_t>(t.data) % width)
                throw std::runtime_error("tensor shape, byte count or alignment mismatch");
            if (off[0] != off[1]) extents.emplace_back(off[0], off[1]);
            tensors_[name] = t;
        }
        std::sort(extents.begin(), extents.end());
        for (size_t i = 1; i < extents.size(); ++i)
            if (extents[i].first < extents[i - 1].second)
                throw std::runtime_error("overlapping tensor extents");
    }
    SafeTensors(const SafeTensors&) = delete;
    SafeTensors& operator=(const SafeTensors&) = delete;

    const Tensor& get(const std::string& name) const {
        auto it = tensors_.find(name);
        if (it == tensors_.end()) throw std::runtime_error("missing tensor " + name);
        return it->second;
    }
    bool has(const std::string& name) const { return tensors_.count(name) > 0; }
    const std::map<std::string, Tensor>& all() const { return tensors_; }
    const uint8_t* mapping_data() const { return mapping_.data; }
    size_t mapping_length() const { return mapping_.length; }

    // A float32 copy of a tensor (bf16 widened exactly).
    std::vector<float> floats(const std::string& name) const {
        const Tensor& t = get(name);
        std::vector<float> out(t.numel());
        if (t.dtype == DType::F32) std::memcpy(out.data(), t.data, out.size() * 4);
        else for (int64_t i = 0; i < t.numel(); ++i) out[i] = bf16_to_f32(t.bf16()[i]);
        for (float value : out)
            if (!std::isfinite(value)) throw std::runtime_error("nonfinite model tensor");
        return out;
    }

  private:
    struct Mapping {
        const uint8_t* data;
        size_t size, length;

        Mapping(const std::string& path, bool shared) {
            int fd = open(path.c_str(), O_RDONLY);
            if (fd < 0) throw std::runtime_error("cannot open " + path);
            struct stat st;
            if (fstat(fd, &st) != 0 || st.st_size < 8) { close(fd); throw std::runtime_error("invalid safetensors file " + path); }
            size = size_t(st.st_size);
            size_t page = size_t(getpagesize());
            if (size > std::numeric_limits<size_t>::max() - page) { close(fd); throw std::runtime_error("mapping too large"); }
            length = (size + page - 1) / page * page;
            void* ptr = mmap(nullptr, length, PROT_READ, shared ? MAP_SHARED : MAP_PRIVATE, fd, 0);
            close(fd);
            if (ptr == MAP_FAILED) throw std::runtime_error("mmap failed: " + path);
            data = static_cast<const uint8_t*>(ptr);
        }
        ~Mapping() { munmap(const_cast<uint8_t*>(data), length); }
        Mapping(const Mapping&) = delete;
        Mapping& operator=(const Mapping&) = delete;
    };
    Mapping mapping_;
    std::map<std::string, Tensor> tensors_;
};

}  // namespace kai
