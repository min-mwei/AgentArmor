#include "execution.h"
#include <iostream>

using namespace kai;

static Request request(int state, const std::vector<int>& branches) {
    Request r;
    r.state_len = state;
    for (size_t segment = 0; segment <= branches.size(); ++segment) {
        int count = segment == 0 ? state : branches[segment - 1];
        int begin = int(r.ids.size());
        for (int i = 0; i < count; ++i) {
            r.ids.push_back(20); r.seg.push_back(int32_t(segment));
            r.pos.push_back(segment == 0 ? i : state + i); r.opt.push_back(segment == 0 ? -1 : 0);
        }
        if (segment) {
            r.decide_idx.push_back(int32_t(r.ids.size()) - 1);
            r.opt_idx.push_back({begin}); r.opt.back() = -2;
        }
    }
    return r;
}

static bool contains(KeyRanges ranges, int j) {
    return (j >= int(ranges.state.begin) && j < int(ranges.state.end)) ||
           (j >= int(ranges.branch.begin) && j < int(ranges.branch.end));
}

static void check(const Request& r, bool full) {
    Config c; c.full_mask = full;
    Layout layout(r, c);
    for (bool global : {false, true}) {
        for (size_t i = 0; i < r.ids.size(); ++i)
            for (size_t j = 0; j < r.ids.size(); ++j) {
                bool expected = allowed(r, int(i), int(j), full) && (global || std::abs(r.pos[i] - r.pos[j]) <= c.window);
                if (contains(layout.keys(uint32_t(i), global), int(j)) != expected) throw std::runtime_error("CPU key range mismatch");
            }
        for (uint32_t queries : {16u, 32u}) {
            std::vector<int> visits(r.ids.size());
            for (const AttentionWork& w : layout.work(queries, global)) {
                for (uint32_t i = w.query_begin; i < w.query_end; ++i) {
                    ++visits[i];
                    if (r.seg[i] != r.seg[w.query_begin]) throw std::runtime_error("query tile crossed a segment");
                    for (size_t j = 0; j < r.ids.size(); ++j) {
                        bool expected = allowed(r, int(i), int(j), full) && (global || std::abs(r.pos[i] - r.pos[j]) <= c.window);
                        bool got = contains(w.keys, int(j)) && (global || std::abs(r.pos[i] - r.pos[j]) <= c.window);
                        if (got != expected) throw std::runtime_error("Metal key range mismatch");
                    }
                }
            }
            for (int count : visits) if (count != 1) throw std::runtime_error("query tile coverage mismatch");
        }
    }
}

static void check_batch(bool full) {
    Config c; c.full_mask = full;
    std::vector<Request> records;
    for (int n : {31, 32, 33, 511, 512, 513}) records.push_back(request(n, full ? std::vector<int>{65} : std::vector<int>{17, 33}));
    std::vector<const Request*> refs;
    for (const auto& r : records) refs.push_back(&r);
    BatchLayout batch(refs, c, true);
    std::vector<size_t> owner;
    for (size_t b = 0; b < records.size(); ++b) owner.insert(owner.end(), records[b].ids.size(), b);
    auto expected = [&](uint32_t i, uint32_t j, bool global) {
        if (owner[i] != owner[j]) return false;
        const auto& r = records[owner[i]];
        uint32_t a = i - batch.offsets[owner[i]], b = j - batch.offsets[owner[j]];
        return allowed(r, a, b, full) && (global || std::abs(r.pos[a] - r.pos[b]) <= c.window);
    };
    for (bool global : {false, true}) {
        for (uint32_t i = 0; i < owner.size(); ++i)
            for (uint32_t j = 0; j < owner.size(); ++j)
                if (contains(batch.keys(i, global), j) != expected(i, j, global))
                    throw std::runtime_error("batched CPU attention crossed a record or lost a key");
        for (uint32_t queries : {16u, 32u}) {
            std::vector<int> visits(owner.size());
            for (const AttentionWork& work : batch.work(queries, global))
                for (uint32_t i = work.query_begin; i < work.query_end; ++i) {
                    ++visits[i];
                    if (owner[i] != owner[work.query_begin]) throw std::runtime_error("query tile crossed a record");
                    for (uint32_t j = 0; j < owner.size(); ++j) {
                        bool got = contains(work.keys, j) && (global || std::abs(batch.pos[i] - batch.pos[j]) <= c.window);
                        if (got != expected(i, j, global)) throw std::runtime_error("batched Metal attention crossed a record or lost a key");
                    }
                }
            for (int n : visits) if (n != 1) throw std::runtime_error("batch query tile coverage mismatch");
        }
    }
}

int main() {
    for (int n : {31, 32, 33, 511, 512, 513}) {
        check(request(n, {17, 33}), false);
        check(request(n, {65}), true);
    }
    check_batch(false); check_batch(true);
    std::cout << "layout contract passed\n";
}
