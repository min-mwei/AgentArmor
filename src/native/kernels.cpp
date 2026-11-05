#include "kernels.h"
#include <cmath>
#include <stdexcept>

namespace armor {
namespace {
void require(bool ok) { if (!ok) throw std::invalid_argument("invalid kernel input"); }
void finite(const Floats& v) {
    require(!v.empty() && v.size() <= size_t(1) << 28);
    for (float x : v) require(std::isfinite(x));
}
void result(const Floats& v) {
    for (float x : v) if (!std::isfinite(x)) throw std::runtime_error("nonfinite kernel output");
}
}
void validate_norm(const Floats& x, const Floats& w, size_t width, float eps, float offset) {
    require(width > 0 && width <= 65536 && w.size() == width && x.size() % width == 0);
    require(std::isfinite(eps) && eps > 0 && std::isfinite(offset));
    finite(x); finite(w);
}
void validate_pointer(const Floats& q, const Floats& k) {
    require(!q.empty() && q.size() <= 4096 && k.size() % q.size() == 0);
    finite(q); finite(k);
}
void validate_delta(const DeltaInput& in) {
    const auto t=in.tokens, kh=in.key_heads, vh=in.value_heads, d=in.dim;
    require(t > 0 && t <= 65536 && kh > 0 && kh <= 256 && vh > 0 && vh <= 256);
    require(vh % kh == 0 && d > 0 && d <= 256);
    require(in.q.size() == t*kh*d && in.k.size() == t*kh*d && in.v.size() == t*vh*d);
    require(in.decay.size() == t*vh && in.beta.size() == t*vh && in.state.size() == vh*d*d);
    for (const auto* v : {&in.q, &in.k, &in.v, &in.decay, &in.beta, &in.state}) finite(*v);
    for (float x : in.decay) require(x >= 0 && x <= 1);
    for (float x : in.beta) require(x >= 0 && x <= 1);
}
Floats rms_norm(const Floats& x, const Floats& w, size_t width, float eps, float offset) {
    validate_norm(x,w,width,eps,offset);
    Floats out(x.size());
    for (size_t row=0; row<x.size()/width; ++row) {
        float sum=0;
        for (size_t c=0; c<width; ++c) sum += x[row*width+c]*x[row*width+c];
        const float inv=1/std::sqrt(sum/float(width)+eps);
        for (size_t c=0; c<width; ++c) out[row*width+c]=(x[row*width+c]*inv)*(w[c]+offset);
    }
    result(out); return out;
}
Floats pointer_logits(const Floats& q, const Floats& k) {
    validate_pointer(q,k);
    Floats out(k.size()/q.size());
    for (size_t i=0; i<out.size(); ++i) {
        float sum=0;
        for (size_t c=0; c<q.size(); ++c) sum+=q[c]*k[i*q.size()+c];
        out[i]=sum/std::sqrt(float(q.size()));
    }
    result(out); return out;
}
DeltaResult delta_scan(const DeltaInput& in) {
    validate_delta(in);
    const auto d=in.dim, kh=in.key_heads, vh=in.value_heads;
    DeltaResult out{Floats(in.v.size()), in.state};
    for (size_t t=0; t<in.tokens; ++t) for (size_t h=0; h<vh; ++h) {
        const auto qbase=(t*kh+h/(vh/kh))*d;
        for (size_t r=0; r<d; ++r) {
            const auto sb=(h*d+r)*d;
            float memory=0;
            for (size_t c=0; c<d; ++c) {
                out.state[sb+c] *= in.decay[t*vh+h];
                memory += out.state[sb+c]*in.k[qbase+c];
            }
            float delta=(in.v[(t*vh+h)*d+r]-memory)*in.beta[t*vh+h], value=0;
            for (size_t c=0; c<d; ++c) {
                out.state[sb+c] += in.k[qbase+c]*delta;
                value += out.state[sb+c]*in.q[qbase+c];
            }
            out.output[(t*vh+h)*d+r]=value;
        }
    }
    result(out.output); result(out.state); return out;
}
} // namespace armor
