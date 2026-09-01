cmake_minimum_required(VERSION 3.22)
project(bv_sim_bridge LANGUAGES CXX)

include(CTest)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

find_package(gz-math7 REQUIRED)
find_package(gz-msgs10 REQUIRED)
find_package(gz-transport13 REQUIRED)
find_package(nlohmann_json 3.10 REQUIRED)
find_package(Threads REQUIRED)

add_executable(bv_sim_bridge
    src/bridge_config.cc
    src/gazebo_state_source.cc
    src/main.cc
    src/pose_graph.cc
    src/tcp_state_server.cc
)

target_include_directories(bv_sim_bridge PRIVATE include)

target_link_libraries(bv_sim_bridge PRIVATE
    gz-math7::gz-math7
    gz-msgs10::gz-msgs10
    gz-transport13::gz-transport13
    nlohmann_json::nlohmann_json
    Threads::Threads
)

if(BUILD_TESTING)
    add_executable(pose_graph_test
        src/pose_graph.cc
        test/pose_graph_test.cc
    )
    target_include_directories(pose_graph_test PRIVATE include)
    target_link_libraries(pose_graph_test PRIVATE
        gz-math7::gz-math7
        gz-msgs10::gz-msgs10
    )
    target_compile_options(pose_graph_test PRIVATE
        -Wall
        -Wextra
        -Wpedantic
        -Werror
    )
    add_test(NAME pose_graph_test COMMAND pose_graph_test)

    add_executable(tcp_state_server_test
        src/tcp_state_server.cc
        test/tcp_state_server_test.cc
    )
    target_include_directories(tcp_state_server_test PRIVATE include)
    target_link_libraries(tcp_state_server_test PRIVATE Threads::Threads)
    target_compile_options(tcp_state_server_test PRIVATE
        -Wall
        -Wextra
        -Wpedantic
        -Werror
    )
    add_test(NAME tcp_state_server_test COMMAND tcp_state_server_test)
endif()

target_compile_options(bv_sim_bridge PRIVATE
    -Wall
    -Wextra
    -Wpedantic
    -Werror
)
