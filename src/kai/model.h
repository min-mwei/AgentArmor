// The exported decision model: config.json + model.safetensors (train/kai/export.py), the packed request and the
// engine interface shared by the CPU and Metal backends.
#pragma once
#include <memory>
#include <string>
#include <vector>

#include "safetensors.h"

namespace kai {

struct Config {
    int hidden = 512, intermediate = 2048, layers = 24, heads = 4, kv_heads = 2, head_dim = 256;
    int global_head_dim = 512, global_kv_heads = 1, ple = 512, embedding_dim = 768, vocab = 262144, window = 512;
    float eps = 1e-6f, theta_sliding = 10000.f, theta_global = 1000000.f;
    std::vector<bool> is_global;        // per layer
    std::string read = "projected";     // which states the heads read: "projected" (768) or "hidden" (512)
    bool full_mask = false;             // "full": the state also attends to the branch (one question per record)
    int pointer_dp = 256, evidence_dh = 0;
    int bos = 2;
    int d_read() const { return read == "projected" ? embedding_dim : hidden; }
};

Config load_config(const std::string& path);

// One packed record (kai.encode.Encoded): the state block (seg 0) then one block per question; branch positions
// restart after the state. The mask is computed from seg and pos, never materialised.
struct Request {
    std::vector<int32_t> ids, seg, pos;
    std::vector<int32_t> opt;                      // [L] option index within the question (-1 none, -2 <decide>)
    int state_len = 0;
    std::vector<int32_t> decide_idx;               // [Q]
    std::vector<std::vector<int32_t>> opt_idx;     // [Q][K] each option's </opt> (kept for the layout; the heads pool seg/opt)
};

struct Response {
    std::vector<std::vector<float>> logits;        // [Q][K]
    std::vector<std::vector<float>> evidence;      // [Q][state_len] (empty without an evidence head)
    std::vector<float> states;                     // [L, d_read] when requested
    double seconds = 0;
};

struct BatchResponse {
    std::vector<Response> responses;
    double seconds = 0;
};

inline bool allowed(const Request& r, int i, int j, bool full = false) {
    return r.seg[j] == 0 || r.seg[i] == r.seg[j] || (full && r.seg[i] == 0);
}

class Engine {
  public:
    virtual ~Engine() = default;
    virtual Response run(const Request& r, bool dump_states = false) = 0;
    virtual BatchResponse run_batch(const std::vector<Request>& requests, bool dump_states = false);
    virtual std::string name() const = 0;
};

std::unique_ptr<Engine> make_cpu_engine(const std::string& weights_dir, int threads = 0);
std::unique_ptr<Engine> make_cpu_optimized_engine(const std::string& weights_dir);
std::unique_ptr<Engine> make_engine(const std::string& weights_dir, const std::string& backend);
#ifdef KAI_METAL
bool metal_available();
std::unique_ptr<Engine> make_metal_engine(const std::string& weights_dir);
std::unique_ptr<Engine> make_metal_optimized_engine(const std::string& weights_dir);
#endif

// Shared: the heads on top of the read states (float32, both backends), and the rope tables.
struct Heads {
    int d = 0, dp = 0, dh = 0;
    std::vector<float> q_w, q_b, k_w, k_b;                 // pointer: [dp, d], [dp]
    float tau = 10.0f;                                     // pointer temperature: logits = tau . cos(q, k)
    std::vector<float> t_w, eq_w, eq_b, out_w, out_b;      // evidence: [dh, d], [dh, d], [dh], [1, dh], [1]
    void load(const SafeTensors& st, const Config& c);
    void apply(const Request& r, const float* states, Response& out, bool batched = false) const;   // states [L, d]
};

}  // namespace kai
