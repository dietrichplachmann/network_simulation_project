cmake_minimum_required(VERSION 3.20)

# The bundled ns-3 installation is built for Linux. Stop before importing its
# targets when Visual Studio falls back to its default Windows configuration.
if(CMAKE_HOST_WIN32)
    message(FATAL_ERROR
        "Select the Ubuntu-18.04 WSL target in Visual Studio, then select "
        "'WSL Local Debug (existing Ubuntu 18.04)'. The bundled ns-3 libraries "
        "are Linux libraries. If only 'Local Machine / x64 Debug' appears, "
        "enable Tools > Options > CMake > General > Always use CMakePresets.json "
        "and reopen the folder. See README.md for the Visual Studio setup.")
endif()

project(network_simulation_project VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

# bootstrap.sh installs the pinned dependency here. An explicit ns3_DIR or
# CMAKE_PREFIX_PATH can instead select an existing ns-3.45 installation.
list(APPEND CMAKE_PREFIX_PATH "${CMAKE_CURRENT_SOURCE_DIR}/.deps/install")
find_package(ns3 3.45 EXACT CONFIG REQUIRED)

add_executable(gateway-bottleneck src/gateway-bottleneck.cc)
target_link_libraries(gateway-bottleneck PRIVATE
    ns3::core ns3::network ns3::internet ns3::mobility ns3::wifi
    ns3::applications ns3::olsr ns3::aodv ns3::dsdv ns3::point-to-point
    ns3::traffic-control ns3::flow-monitor)
if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
    target_compile_options(gateway-bottleneck PRIVATE -Wall -Wextra -Wpedantic)
endif()

include(CTest)
if(BUILD_TESTING)
    find_package(Python3 3.8 REQUIRED COMPONENTS Interpreter)
    add_test(NAME gateway-baseline
        COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/check_baseline.py"
        "$<TARGET_FILE:gateway-bottleneck>" "${CMAKE_CURRENT_BINARY_DIR}/test-results")
    set_tests_properties(gateway-baseline PROPERTIES TIMEOUT 180)
    add_test(NAME gateway-study-runner
        COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/check_study.py"
        "$<TARGET_FILE:gateway-bottleneck>" "${CMAKE_CURRENT_BINARY_DIR}/test-results")
    set_tests_properties(gateway-study-runner PROPERTIES TIMEOUT 180)
endif()

# add the hop-count file to make it executable
add_executable(hop-count src/hop-count.cc)
target_link_libraries(hop-count PRIVATE
    ns3::core ns3::network ns3::internet ns3::mobility ns3::wifi
    ns3::applications ns3::olsr)
