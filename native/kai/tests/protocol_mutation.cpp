#include <cstdint>
#include <iostream>
#include <random>
#include <string>
#include <vector>
extern "C" int LLVMFuzzerTestOneInput(const uint8_t*, size_t);
int main() {
    const std::vector<std::string> seeds = {
        R"({"ids":[2,6,7,8,20,9,10],"seg":[0,0,1,1,1,1,1],"pos":[0,1,2,3,4,5,6],"opt":[-1,-1,-1,0,0,0,-2],"state_len":2,"decide_idx":[6],"opt_idx":[[5]]})",
        R"({"shape":[9223372036854775807,8],"data_offsets":[0,4]})",
        R"({"x":"\uD83E\uDD8A","nested":{"key":true}})",
        R"({"key":1,"key":2})"
    };
    std::mt19937 random(20261030);
    for (const auto& seed : seeds) {
        for (unsigned i = 0; i < 5000; ++i) {
            auto input = seed;
            const auto edits = random() % 8;
            for (unsigned j = 0; j < edits; ++j) input[random() % input.size()] = char(random() & 255);
            if (i % 3 == 0) input.resize(random() % (input.size() + 1));
            LLVMFuzzerTestOneInput(reinterpret_cast<const uint8_t*>(input.data()), input.size());
        }
    }
    std::cout << "20000 deterministic adversarial protocol mutations completed\n";
}
