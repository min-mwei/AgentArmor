#include "model.h"
#include "execution.h"

#include <cmath>
#include <chrono>
#include <fstream>
#include <sstream>

#ifdef KAI_ACCELERATE
#include <Accelerate/Accelerate.h>
#endif

namespace kai {

std::unique_ptr<Engine> make_engine(const std::string& dir, const std::string& backend) {
    if (backend != "cpu" && backend != "cpu-opt" && backend != "metal" && backend != "metal-opt")
        throw std::invalid_argument("unknown execution backend");
    // Apply the same complete tensor contract to control and optimized engines.
    { Weights validated(dir); }
    if (backend == "cpu") return make_cpu_engine(dir);
    if (backend == "cpu-opt") return make_cpu_optimized_engine(dir);
#ifdef KAI_METAL
    if (backend == "metal") return make_metal_engine(dir);
    if (backend == "metal-opt") return make_metal_optimized_engine(dir);
#endif
    throw std::runtime_error("backend not built: " + backend);
}

BatchResponse Engine::run_batch(const std::vector<Request>& requests, bool dump_states) {
    if (requests.empty()) throw std::runtime_error("empty request batch");
    const auto start = std::chrono::steady_clock::now();
    BatchResponse result;
    for (const auto& request : requests) result.responses.push_back(run(request, dump_states));
    result.seconds = std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
    for (auto& response : result.responses) response.seconds = result.seconds;
    return result;
}

Config load_config(const std::string& path) {
    std::ifstream f(path);
    if (!f) throw std::runtime_error("cannot read " + path);
    std::string text(1024 * 1024 + 1, '\0');
    f.read(text.data(), std::streamsize(text.size()));
    text.resize(size_t(f.gcount()));
    if (f.bad() || text.size() > 1024 * 1024) throw std::invalid_argument("invalid model config size");
    Json j = parse_json(text);
    if (j.at("architecture").str != "embeddinggemma2-text")
        throw std::invalid_argument("only the official EmbeddingGemma-2 encoder is supported");
    auto integer = [&](const char* key, int64_t low, int64_t high) {
        const auto value = j.at(key).i();
        if (value < low || value > high) throw std::invalid_argument("model dimension out of range");
        return int(value);
    };
    Config c;
    c.hidden = integer("hidden_size", 512, 512); c.intermediate = integer("intermediate_size", 2048, 2048);
    c.layers = integer("num_hidden_layers", 24, 24);
    c.heads = integer("num_attention_heads", 4, 4); c.kv_heads = integer("num_key_value_heads", 2, 2);
    c.head_dim = integer("head_dim", 256, 256);
    c.global_head_dim = integer("global_head_dim", 512, 512);
    c.global_kv_heads = integer("global_num_key_value_heads", 1, 1);
    c.ple = integer("hidden_size_per_layer_input", 512, 512);
    c.embedding_dim = integer("embedding_dim", 768, 768); c.vocab = integer("vocab_size", 262144, 262144);
    c.window = integer("sliding_window", 0, 8192); c.eps = j.at("rms_norm_eps").d();
    c.theta_sliding = j.at("rope_theta_sliding").d(); c.theta_global = j.at("rope_theta_global").d();
    if (!std::isfinite(c.eps) || c.eps <= 0 || !std::isfinite(c.theta_sliding) ||
        c.theta_sliding <= 0 || !std::isfinite(c.theta_global) || c.theta_global <= 0)
        throw std::invalid_argument("invalid model normalization or RoPE values");
    if (j.at("layer_types").kind != Json::Array) throw std::invalid_argument("layer_types must be an array");
    for (const auto& t : j.at("layer_types").arr) {
        if (t.str != "full_attention" && t.str != "sliding_attention")
            throw std::invalid_argument("unknown attention layer type");
        c.is_global.push_back(t.str == "full_attention");
    }
    c.read = j.at("read").str;
    if (c.read != "projected" && c.read != "hidden") throw std::invalid_argument("unknown readout mode");
    const auto mask = j.has("mask") ? j.at("mask").str : "prefix";
    if (mask != "prefix" && mask != "full") throw std::invalid_argument("unknown attention mask");
    c.full_mask = mask == "full";
    c.pointer_dp = integer("pointer_dp", 1, 1024); c.evidence_dh = integer("evidence_dh", 0, 1024);
    c.bos = integer("bos", 2, 2);
    if ((int)c.is_global.size() != c.layers) throw std::runtime_error("layer_types length != num_hidden_layers");
    return c;
}

static void rms_unit(const float* x, float* y, int d, float eps) {
    float ss = 0; for (int i = 0; i < d; ++i) ss += x[i] * x[i];
    float r = 1.0f / std::sqrt(ss / d + eps);
    for (int i = 0; i < d; ++i) y[i] = x[i] * r;
}

// y[out] = W[out, in] x + b, float32 accumulation in four independent lanes (auto-vectorised).
static void linear(const float* x, const float* w, const float* b, float* y, int out, int in) {
    for (int o = 0; o < out; ++o) {
        const float* row = w + (size_t)o * in;
        float a0 = 0, a1 = 0, a2 = 0, a3 = 0;
        int i = 0;
        for (; i + 4 <= in; i += 4) { a0 += row[i] * x[i]; a1 += row[i + 1] * x[i + 1]; a2 += row[i + 2] * x[i + 2]; a3 += row[i + 3] * x[i + 3]; }
        float acc = a0 + a1 + a2 + a3;
        for (; i < in; ++i) acc += row[i] * x[i];
        y[o] = acc + (b ? b[o] : 0.0f);
    }
}

void Heads::load(const SafeTensors& st, const Config& c) {
    d = c.d_read(); dp = c.pointer_dp; dh = c.evidence_dh;
    if (d <= 0 || dp <= 0 || dp > 1024 || dh < 0 || dh > 1024)
        throw std::invalid_argument("invalid readout dimensions");
    auto head = [&](const char* name, std::vector<int64_t> shape) {
        const auto& tensor = st.get(name);
        if (tensor.dtype != DType::F32 || tensor.shape != shape)
            throw std::invalid_argument("invalid readout tensor shape or dtype");
        return st.floats(name);
    };
    q_w = head("head.q.weight", {dp, d}); q_b = head("head.q.bias", {dp});
    k_w = head("head.k.weight", {dp, d}); k_b = head("head.k.bias", {dp});
    tau = std::exp(head("head.log_tau", {})[0]);
    if (!std::isfinite(tau) || tau <= 0) throw std::invalid_argument("invalid learned pointer temperature");
    if (dh > 0) {
        t_w = head("evidence.t.weight", {dh, d}); eq_w = head("evidence.q.weight", {dh, d});
        eq_b = head("evidence.q.bias", {dh}); out_w = head("evidence.out.weight", {1, dh});
        out_b = head("evidence.out.bias", {1});
    }
}

void Heads::apply(const Request& r, const float* states, Response& out, bool batched) const {
    const float eps = 1e-6f;
    auto normalise = [](std::vector<float>& v) { float ss = 0; for (float x : v) ss += x * x; float r = 1.0f / std::sqrt(ss + 1e-6f); for (float& x : v) x *= r; };
    const int L = r.ids.size();
    out.logits.clear(); out.evidence.clear();
    // unit-normalised states of the tokens the heads read: every state token (evidence) and every <decide> token
    std::vector<char> needed(L, 0);
    for (int t = 0; t < r.state_len; ++t) needed[t] = 1;
    for (int32_t di : r.decide_idx) needed[di] = 1;
    std::vector<float> unit((size_t)L * d);
    for (int t = 0; t < L; ++t) if (needed[t]) rms_unit(states + (size_t)t * d, unit.data() + (size_t)t * d, d, eps);
    // the pointer keys: each option's mean token state (tokens with seg == question, opt == option), then unit-normalised
    std::vector<std::vector<float>> pooled(r.decide_idx.size());
    for (size_t qi = 0; qi < r.decide_idx.size(); ++qi) {
        const int K = r.opt_idx[qi].size();
        std::vector<float> sum((size_t)K * d, 0.0f); std::vector<int> cnt(K, 0);
        for (int t = r.state_len; t < L; ++t) {
            if (r.seg[t] != (int32_t)qi + 1 || r.opt[t] < 0 || r.opt[t] >= K) continue;
            float* s = sum.data() + (size_t)r.opt[t] * d; const float* x = states + (size_t)t * d;
            for (int i = 0; i < d; ++i) s[i] += x[i];
            ++cnt[r.opt[t]];
        }
        pooled[qi].resize((size_t)K * d);
        for (int j = 0; j < K; ++j) {
            if (!cnt[j]) throw std::runtime_error("option without tokens");
            float* s = sum.data() + (size_t)j * d; for (int i = 0; i < d; ++i) s[i] /= cnt[j];
            rms_unit(s, pooled[qi].data() + (size_t)j * d, d, eps);
        }
    }
    // the evidence head's token projection, once per state token
    std::vector<float> et;
    if (dh > 0) {
        et.resize((size_t)r.state_len * dh);
#ifdef KAI_ACCELERATE
        if (batched)
            cblas_sgemm(CblasRowMajor, CblasNoTrans, CblasTrans, r.state_len, dh, d, 1,
                        unit.data(), d, t_w.data(), d, 0, et.data(), dh);
        else
#endif
            for (int t = 0; t < r.state_len; ++t)
                linear(unit.data() + (size_t)t * d, t_w.data(), nullptr, et.data() + (size_t)t * dh, dh, d);
    }
    std::vector<float> q(dp), k(dp), eq(dh);
#ifdef KAI_ACCELERATE
    std::vector<float> activation(batched ? et.size() : 0);
#endif
    for (size_t qi = 0; qi < r.decide_idx.size(); ++qi) {
        const float* hd = unit.data() + (size_t)r.decide_idx[qi] * d;
        linear(hd, q_w.data(), q_b.data(), q.data(), dp, d); normalise(q);
        std::vector<float> logits;
        for (size_t j = 0; j < r.opt_idx[qi].size(); ++j) {
            linear(pooled[qi].data() + j * d, k_w.data(), k_b.data(), k.data(), dp, d); normalise(k);
            float s = 0; for (int i = 0; i < dp; ++i) s += q[i] * k[i];
            logits.push_back(s * tau);
        }
        out.logits.push_back(logits);
        if (dh > 0) {
            linear(hd, eq_w.data(), eq_b.data(), eq.data(), dh, d);
            std::vector<float> ev(r.state_len);
#ifdef KAI_ACCELERATE
            if (batched) {
                const int count = r.state_len * dh;
                for (int t = 0; t < r.state_len; ++t)
                    for (int i = 0; i < dh; ++i) activation[size_t(t) * dh + i] = et[size_t(t) * dh + i] + eq[i];
                vvtanhf(activation.data(), activation.data(), &count);
                cblas_sgemv(CblasRowMajor, CblasNoTrans, r.state_len, dh, 1, activation.data(), dh,
                            out_w.data(), 1, 0, ev.data(), 1);
                for (float& value : ev) value += out_b[0];
            } else
#endif
            {
                for (int t = 0; t < r.state_len; ++t) {
                    const float* e = et.data() + (size_t)t * dh;
                    float s = out_b[0];
                    for (int i = 0; i < dh; ++i) s += out_w[i] * std::tanh(e[i] + eq[i]);
                    ev[t] = s;
                }
            }
            out.evidence.push_back(ev);
        }
    }
}

}  // namespace kai
