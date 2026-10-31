add_library(armor_protocol STATIC protocol.cpp)
target_include_directories(armor_protocol PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}")
target_link_libraries(armor_protocol PUBLIC armor_kai)

find_package(Threads REQUIRED)
add_library(armor_server STATIC scheduler.cpp http.cpp device_lock.cpp)
target_link_libraries(armor_server PUBLIC armor_protocol Threads::Threads)
add_executable(agentarmor main.cpp)
target_link_libraries(agentarmor PRIVATE armor_server)
