if(AGENTARMOR_BUILD_TESTS)
  add_executable(armor_layout_contract layout_contract.cpp)
  target_link_libraries(armor_layout_contract PRIVATE armor_kai)
  add_test(NAME layout_contract COMMAND armor_layout_contract)

  add_executable(armor_model_security model_security.cpp)
  target_link_libraries(armor_model_security PRIVATE armor_protocol)
  add_test(NAME model_security COMMAND armor_model_security)

  add_executable(armor_protocol_mutation protocol_fuzz.cpp protocol_mutation.cpp)
  target_link_libraries(armor_protocol_mutation PRIVATE armor_protocol)
  add_test(NAME protocol_mutation COMMAND armor_protocol_mutation)

  add_executable(armor_server_contract server_contract.cpp)
  target_link_libraries(armor_server_contract PRIVATE armor_server)
  add_test(NAME server_contract COMMAND armor_server_contract)
  set_tests_properties(server_contract PROPERTIES TIMEOUT 45)

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
    add_executable(armor_shader_compile compile_shaders.mm)
    target_include_directories(armor_shader_compile PRIVATE "${PROJECT_BINARY_DIR}/src/generated")
    target_compile_options(armor_shader_compile PRIVATE -fobjc-arc)
    target_link_libraries(armor_shader_compile PRIVATE armor_build_options
      "-framework Metal" "-framework Foundation")
    add_test(NAME shader_compile COMMAND armor_shader_compile)
    set_tests_properties(shader_compile PROPERTIES SKIP_RETURN_CODE 77 LABELS "metal-compile")
  endif()
endif()

if(AGENTARMOR_BUILD_FUZZERS)
  if(NOT AGENTARMOR_SANITIZER STREQUAL "address")
    message(FATAL_ERROR "Fuzzers require AGENTARMOR_SANITIZER=address")
  endif()
  execute_process(COMMAND "${CMAKE_CXX_COMPILER}" --print-file-name=libclang_rt.fuzzer_osx.a
    OUTPUT_VARIABLE armor_fuzzer_runtime OUTPUT_STRIP_TRAILING_WHITESPACE)
  if(NOT EXISTS "${armor_fuzzer_runtime}")
    message(FATAL_ERROR "This Clang lacks libFuzzer. Use a full LLVM toolchain; deterministic mutation tests remain available.")
  endif()
  add_executable(armor_protocol_fuzz protocol_fuzz.cpp)
  target_link_libraries(armor_protocol_fuzz PRIVATE armor_protocol)
  target_compile_options(armor_protocol_fuzz PRIVATE -fsanitize=fuzzer)
  target_link_options(armor_protocol_fuzz PRIVATE -fsanitize=fuzzer)
endif()
