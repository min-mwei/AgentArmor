#include "kernels.h"
#include <cmath>
#include <future>
#include <iostream>
#include <stdexcept>
#include <string>
using namespace armor;
void check(bool ok) { if (!ok) throw std::runtime_error("kernel contract failed"); }
void close(const Floats& a,const Floats& b,float tol=3e-5f) {
    check(a.size()==b.size());
    for(size_t i=0;i<a.size();++i) check(std::abs(a[i]-b[i]) <= tol*(1+std::abs(b[i])));
}
template<class F> void rejects(F f) { bool failed=false; try { f(); } catch(const std::invalid_argument&) {failed=true;} check(failed); }
DeltaInput fixture() {
    DeltaInput in; in.tokens=3; in.key_heads=1; in.value_heads=2; in.dim=128;
    in.q.resize(3*128); in.k.resize(3*128); in.v.resize(3*2*128);
    for(size_t i=0;i<in.q.size();++i) {in.q[i]=float(int(i%13)-6)*0.01f; in.k[i]=float(int(i%7)-3)*0.02f;}
    for(size_t i=0;i<in.v.size();++i) in.v[i]=float(int(i%17)-8)*0.03f;
    in.state=Floats(2*128*128,0.01f); in.decay=Floats(6,0.8f); in.beta=Floats(6,0.4f);
    return in;
}
int main(int argc,char** argv) {
    try {
        bool metal=argc==2 && std::string(argv[1])=="--metal";
        Floats x(2*2560),w(2560);
        for(size_t i=0;i<x.size();++i) x[i]=float(int(i%23)-11)*0.03f;
        for(size_t i=0;i<w.size();++i) w[i]=float(i%7)*0.02f;
        auto norm=rms_norm(x,w,2560,1e-6f,1);
        // Analytic RMS and affine-offset checks.
        close(rms_norm({3,4},{1,2},2,0.5f,1), {6/std::sqrt(13.f),12/std::sqrt(13.f)});
        close(pointer_logits({1,2},{3,4,5,6}),{11/std::sqrt(2.f),17/std::sqrt(2.f)});
        rejects([] {rms_norm({1},{1},0,1e-6f,1);});
        rejects([] {pointer_logits({},{});});
        rejects([] {pointer_logits({NAN},{1});});
        auto in=fixture(); const auto original=in.state; auto full=delta_scan(in);
        check(in.state==original); // Branches cannot mutate the shared prefix state.
        // Known one-dimensional recurrence: decay then delta correction, then query.
        DeltaInput tiny{{2},{3},{5},{0.5f},{0.25f},{4},1,1,1,1};
        auto known=delta_scan(tiny); close(known.state,{1.25f}); close(known.output,{2.5f});
        // Splitting the prefix must reproduce the whole scan.
        auto prefix=in; prefix.tokens=1; prefix.q.resize(128); prefix.k.resize(128);
        prefix.v.resize(256); prefix.decay.resize(2); prefix.beta.resize(2);
        auto cached=delta_scan(prefix); auto suffix=in; suffix.tokens=2;
        suffix.q.erase(suffix.q.begin(),suffix.q.begin()+128); suffix.k.erase(suffix.k.begin(),suffix.k.begin()+128);
        suffix.v.erase(suffix.v.begin(),suffix.v.begin()+256); suffix.decay.erase(suffix.decay.begin(),suffix.decay.begin()+2);
        suffix.beta.erase(suffix.beta.begin(),suffix.beta.begin()+2); suffix.state=cached.state;
        auto rest=delta_scan(suffix); close(rest.state,full.state);
        close(rest.output,Floats(full.output.begin()+256,full.output.end()));
        auto bad=in; bad.state.pop_back(); rejects([&] {delta_scan(bad);});
        auto work=std::async(std::launch::async,[&] {return delta_scan(in);});
        close(delta_scan(in).state,work.get().state);
#ifdef AGENTARMOR_HAS_METAL
        if(metal) {
            MetalKernels gpu;
            close(gpu.rms_norm(x,w,2560,1e-6f,1),norm);
            close(gpu.rms_norm({3,4},{1,2},2,0.5f,1),{6/std::sqrt(13.f),12/std::sqrt(13.f)});
            close(gpu.pointer_logits({1,2},{3,4,5,6}),pointer_logits({1,2},{3,4,5,6}));
            auto actual=gpu.delta_scan(in); close(actual.output,full.output); close(actual.state,full.state);
            auto a=gpu.delta_scan(prefix); suffix.state=a.state; auto b=gpu.delta_scan(suffix);
            close(b.state,full.state); close(gpu.delta_scan(in).state,actual.state);
        }
#else
        if(metal) throw std::runtime_error("Metal disabled");
#endif
        std::cout << "kernel contracts passed\n";
    } catch(const std::exception& e) {std::cerr<<e.what()<<'\n';return 1;}
}
