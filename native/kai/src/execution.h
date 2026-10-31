#pragma once
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <limits>

#include "model.h"

namespace kai {

struct Range {
    uint32_t begin = 0, end = 0;
};

struct KeyRanges {
    Range state, branch;
};

// A query tile never crosses a segment. Key ranges may be disjoint and positions may restart.
struct AttentionWork {
    uint32_t query_begin, query_end;
    KeyRanges keys;
};
static_assert(sizeof(AttentionWork) == 24);

class Layout {
  public:
    Layout(const Request& r, const Config& c) : request_(r), full_(c.full_mask), window_(c.window) {
        const size_t n = r.ids.size();
        if (!n || n > size_t(std::numeric_limits<int32_t>::max()) || r.seg.size() != n || r.pos.size() != n ||
            r.opt.size() != n || r.state_len < 1 || size_t(r.state_len) > n || r.opt_idx.size() != r.decide_idx.size())
            throw std::invalid_argument("invalid packed request dimensions");
        if (full_ && r.decide_idx.size() != 1) throw std::invalid_argument("full mask requires one question");
        segments_.push_back({0, uint32_t(r.state_len)});
        for (size_t q = 0, begin = r.state_len; q < r.decide_idx.size(); ++q) {
            size_t end = begin;
            while (end < n && r.seg[end] == int32_t(q + 1)) ++end;
            if (begin == end || r.decide_idx[q] < int32_t(begin) || r.decide_idx[q] >= int32_t(end))
                throw std::invalid_argument("invalid question segment or readout");
            const size_t options = r.opt_idx[q].size();
            if (!options || r.opt[r.decide_idx[q]] != -2) throw std::invalid_argument("invalid decision or empty options");
            for (size_t i = begin; i < end; ++i) {
                int32_t option = r.opt[i];
                if (option < -2 || option >= int64_t(options)) throw std::invalid_argument("invalid option token index");
            }
            for (size_t option = 0; option < options; ++option) {
                int32_t index = r.opt_idx[q][option];
                if (index < int32_t(begin) || index >= int32_t(end) || r.opt[index] != int64_t(option))
                    throw std::invalid_argument("invalid option readout");
            }
            segments_.push_back({uint32_t(begin), uint32_t(end)});
            begin = end;
        }
        if (segments_.back().end != n) throw std::invalid_argument("unassigned packed tokens");
        for (size_t s = 0; s < segments_.size(); ++s) {
            Range block = segments_[s];
            for (uint32_t i = block.begin; i < block.end; ++i) {
                int32_t position = int32_t(i - block.begin) + (s == 0 ? 0 : r.state_len);
                if (r.seg[i] != int32_t(s) || r.pos[i] != position || r.ids[i] < 0 || r.ids[i] >= c.vocab)
                    throw std::invalid_argument("request does not follow the packed segment/position contract");
            }
        }
    }

    KeyRanges keys(uint32_t i, bool global) const { return keys(i, i + 1, global); }

    std::vector<AttentionWork> work(uint32_t queries, bool global) const {
        std::vector<AttentionWork> result;
        for (Range block : segments_)
            for (uint32_t i = block.begin; i < block.end; i += queries) {
                uint32_t end = std::min(i + queries, block.end);
                result.push_back({i, end, keys(i, end, global)});
            }
        return result;
    }

  private:
    const Request& request_;
    bool full_;
    int window_;
    std::vector<Range> segments_;

    KeyRanges keys(uint32_t first, uint32_t end, bool global) const {
        const int segment = request_.seg[first];
        KeyRanges ranges{segments_[0], {}};
        if (full_ && segment == 0) ranges.state.end = uint32_t(request_.ids.size());
        else if (segment > 0) ranges.branch = segments_[segment];
        if (global) return ranges;
        const int64_t lo = int64_t(request_.pos[first]) - window_;
        const int64_t hi = int64_t(request_.pos[end - 1]) + window_ + 1;
        auto clip = [&](Range range) -> Range {
            if (range.begin == range.end) return range;
            const int64_t origin = int64_t(request_.pos[range.begin]) - range.begin;
            const int64_t begin = std::clamp(lo - origin, int64_t(range.begin), int64_t(range.end));
            const int64_t finish = std::clamp(hi - origin, begin, int64_t(range.end));
            return {uint32_t(begin), uint32_t(finish)};
        };
        return {clip(ranges.state), clip(ranges.branch)};
    }
};

// Linear operations share one unpadded token buffer; attention ranges retain each record's boundaries.
class BatchLayout {
  public:
    std::vector<int32_t> ids, pos;
    std::vector<uint32_t> offsets;

    BatchLayout(const std::vector<const Request*>& requests, const Config& config, bool row_keys = false) {
        if (requests.empty()) throw std::runtime_error("empty request batch");
        size_t total = 0;
        for (const Request* r : requests) total += r->ids.size();
        if (total > size_t(std::numeric_limits<int32_t>::max())) throw std::runtime_error("packed batch too large");
        ids.reserve(total); pos.reserve(total);
        if (row_keys) { local_.reserve(total); global_.reserve(total); }
        for (const Request* r : requests) {
            const uint32_t offset = uint32_t(ids.size());
            offsets.push_back(offset);
            layouts_.emplace_back(*r, config);
            ids.insert(ids.end(), r->ids.begin(), r->ids.end());
            pos.insert(pos.end(), r->pos.begin(), r->pos.end());
            if (row_keys)
                for (uint32_t row = 0; row < r->ids.size(); ++row) {
                    local_.push_back(shift(layouts_.back().keys(row, false), offset));
                    global_.push_back(shift(layouts_.back().keys(row, true), offset));
                }
        }
    }

    KeyRanges keys(uint32_t row, bool global) const { return (global ? global_ : local_).at(row); }

    std::vector<AttentionWork> work(uint32_t queries, bool global) const {
        std::vector<AttentionWork> result;
        for (size_t i = 0; i < layouts_.size(); ++i)
            for (auto item : layouts_[i].work(queries, global)) {
                item.query_begin += offsets[i]; item.query_end += offsets[i];
                item.keys = shift(item.keys, offsets[i]);
                result.push_back(item);
            }
        return result;
    }

  private:
    std::vector<Layout> layouts_;
    std::vector<KeyRanges> local_, global_;

    static KeyRanges shift(KeyRanges ranges, uint32_t offset) {
        for (Range* range : {&ranges.state, &ranges.branch}) { range->begin += offset; range->end += offset; }
        return ranges;
    }
};

struct RopeTable {
    int width = 0, positions = 0;
    float theta = 0;
    std::vector<float> values;

    void reserve(int count, int head_dim, float base) {
        if (count <= positions && width == head_dim && theta == base) return;
        width = head_dim; theta = base; positions = count;
        values.resize(size_t(count) * head_dim);
        for (int i = 0; i < head_dim / 2; ++i) {
            double frequency = std::pow(double(base), -2.0 * i / head_dim);
            for (int p = 0; p < count; ++p) {
                double angle = double(p) * frequency;
                values[size_t(p) * head_dim + 2 * i] = float(std::cos(angle));
                values[size_t(p) * head_dim + 2 * i + 1] = float(std::sin(angle));
            }
        }
    }
};

struct LayerWeights {
    const Tensor *q, *k, *v, *o, *gate, *up, *down, *ple_gate, *ple_proj;
    std::vector<float> in_norm, post_attn, pre_ffw, post_ffw, q_norm, k_norm, ple_norm;
    float scalar, theta;
    int hd, kvh;
    bool global;
};

// Shared by the optimized engines; the mapping owns every tensor view.
struct Weights {
    SafeTensors file;
    Config config;
    const Tensor *embed, *ple_proj, *output;
    std::vector<float> ple_norm, final_norm;
    std::vector<LayerWeights> layers;
    Heads heads;

    explicit Weights(const std::string& dir) : file(dir + "/model.safetensors", true), config(load_config(dir + "/config.json")) {
        const Config& c = config;
        if (c.layers <= 0 || c.hidden <= 0 || c.intermediate <= 0 || c.ple <= 0 || c.heads <= 0 || c.kv_heads <= 0 ||
            c.global_kv_heads <= 0 || c.heads % c.kv_heads || c.heads % c.global_kv_heads ||
            c.head_dim <= 0 || c.global_head_dim <= 0 || c.head_dim % 8 || c.global_head_dim % 8 || c.window < 0)
            throw std::runtime_error("unsupported optimized engine dimensions");
        auto matrix = [&](const std::string& name, int rows, int cols) {
            const Tensor& t = file.get(name);
            if (t.dtype != DType::BF16 || t.shape != std::vector<int64_t>{rows, cols} || t.bytes != size_t(rows) * cols * 2)
                throw std::runtime_error("invalid bf16 matrix " + name);
            return &t;
        };
        auto norm = [&](const std::string& name, int width) {
            const Tensor& t = file.get(name);
            if (t.shape != std::vector<int64_t>{width}) throw std::runtime_error("invalid norm " + name);
            return file.floats(name);
        };
        embed = matrix("text.embed_tokens.weight", c.vocab, c.hidden);
        ple_proj = matrix("text.ple.per_layer_model_projection.weight", c.layers * c.ple, c.hidden);
        output = matrix("text.embedding_projection.weight", c.embedding_dim, c.hidden);
        ple_norm = norm("text.ple.per_layer_projection_norm.weight", c.ple);
        final_norm = norm("text.norm.weight", c.hidden);
        for (int i = 0; i < c.layers; ++i) {
            std::string p = "text.layers." + std::to_string(i) + ".";
            LayerWeights w;
            w.global = c.is_global[i]; w.hd = w.global ? c.global_head_dim : c.head_dim;
            w.kvh = w.global ? c.global_kv_heads : c.kv_heads; w.theta = w.global ? c.theta_global : c.theta_sliding;
            w.q = matrix(p + "self_attn.q_proj.weight", c.heads * w.hd, c.hidden);
            w.k = matrix(p + "self_attn.k_proj.weight", w.kvh * w.hd, c.hidden);
            w.v = matrix(p + "self_attn.v_proj.weight", w.kvh * w.hd, c.hidden);
            w.o = matrix(p + "self_attn.o_proj.weight", c.hidden, c.heads * w.hd);
            w.gate = matrix(p + "mlp.gate_proj.weight", c.intermediate, c.hidden);
            w.up = matrix(p + "mlp.up_proj.weight", c.intermediate, c.hidden);
            w.down = matrix(p + "mlp.down_proj.weight", c.hidden, c.intermediate);
            w.ple_gate = matrix(p + "ple_block.per_layer_input_gate.weight", c.ple, c.hidden);
            w.ple_proj = matrix(p + "ple_block.per_layer_projection.weight", c.hidden, c.ple);
            w.in_norm = norm(p + "input_layernorm.weight", c.hidden);
            w.post_attn = norm(p + "post_attention_layernorm.weight", c.hidden);
            w.pre_ffw = norm(p + "pre_feedforward_layernorm.weight", c.hidden);
            w.post_ffw = norm(p + "post_feedforward_layernorm.weight", c.hidden);
            w.q_norm = norm(p + "self_attn.q_norm.weight", w.hd);
            w.k_norm = norm(p + "self_attn.k_norm.weight", w.hd);
            w.ple_norm = norm(p + "ple_block.post_per_layer_input_norm.weight", c.hidden);
            w.scalar = file.floats(p + "layer_scalar").at(0);
            layers.push_back(std::move(w));
        }
        heads.load(file, c);
    }
};

}  // namespace kai
