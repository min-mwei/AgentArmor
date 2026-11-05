#include "json.h"
#include <iostream>
#include <cstdio>
namespace wire { using Json = nlohmann::json; }
using kai::parse_document;
namespace {
void usage() {
    std::cerr << "AgentArmor experimental Qwen runtime\n"
        "  agentarmor doctor\n"
        "  agentarmor hook --agent codex_code|claude_code\n"
        "The vLLM baseline is a separate Python service. Native Qwen serving is pending.\n";
}
int hook(int argc, char** argv) {
    if (argc != 4 || std::string(argv[2]) != "--agent" ||
        (std::string(argv[3]) != "codex_code" && std::string(argv[3]) != "claude_code"))
        throw std::invalid_argument("hook requires --agent codex_code|claude_code");
    std::string input(1'000'001, '\0');
    std::cin.read(input.data(), std::streamsize(input.size()));
    const auto count = std::cin.gcount();
    if (count > 1'000'000) throw std::invalid_argument("hook input exceeds 1 MB");
    input.resize(size_t(count));
    const auto event = parse_document(input, 1'000'000);
    const auto name = event.at("hook_event_name").get<std::string>();
    const char* reason = "AgentArmor native text policy evaluation is not qualified; no clear verdict is available.";
    wire::Json out;
    if (name == "PreToolUse") {
        out = {{"hookSpecificOutput", {{"hookEventName", name}, {"permissionDecision", "deny"},
            {"permissionDecisionReason", reason}}}};
    } else if (name == "PermissionRequest") {
        out = {{"hookSpecificOutput", {{"hookEventName", name},
            {"decision", {{"behavior", "deny"}, {"message", reason}}}}}};
    } else {
        out = {{"systemMessage", "AgentArmor native observation is incomplete; no protection claim is available."}};
        std::cout << out.dump() << '\n';
        return 0;
    }
    std::cout << out.dump() << '\n';
    return 2;
}
} // namespace
int main(int argc, char** argv) {
    try {
        if (argc == 1 || std::string(argv[1]) == "--help") { usage(); return 0; }
        if (argc == 2 && std::string(argv[1]) == "doctor") {
            std::cout << wire::Json{{"runtime", "native"}, {"model_family", "qwen3.5"},
                {"model_loaded", false}, {"policy_ready", false}, {"native_backbone_ready", false},
                {"experimental", true}}.dump() << '\n';
            return 0;
        }
        if (std::string(argv[1]) == "hook") return hook(argc, argv);
        throw std::invalid_argument("unsupported command; native Qwen serving is not implemented");
    } catch (const std::exception&) {
        std::cerr << "AgentArmor could not complete the check; this action is denied.\n";
        return 2;
    }
}
