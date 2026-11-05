add_executable(armor_kernel_contract kernel_contract.cpp)
target_link_libraries(armor_kernel_contract PRIVATE armor_kernels)
add_test(NAME kernel_contract COMMAND armor_kernel_contract)
add_test(NAME cli_help COMMAND agentarmor --help)
add_test(NAME cli_doctor COMMAND agentarmor doctor)
set_tests_properties(cli_doctor PROPERTIES PASS_REGULAR_EXPRESSION "\"model_loaded\":false")
add_test(NAME hook_fail_closed COMMAND /bin/bash "${CMAKE_CURRENT_SOURCE_DIR}/cli_hook.sh"
  $<TARGET_FILE:agentarmor>)
add_test(NAME release_assets COMMAND /bin/bash "${CMAKE_CURRENT_SOURCE_DIR}/release_assets.sh"
  $<TARGET_FILE:agentarmor> "${PROJECT_SOURCE_DIR}")
add_test(NAME installer_contract COMMAND /bin/bash "${CMAKE_CURRENT_SOURCE_DIR}/installer_contract.sh"
  $<TARGET_FILE:agentarmor> "${PROJECT_SOURCE_DIR}")

if(AGENTARMOR_METAL)
  add_test(NAME metal_kernel_contract COMMAND armor_kernel_contract --metal)
  set_tests_properties(metal_kernel_contract PROPERTIES LABELS "gpu" TIMEOUT 60)
endif()
