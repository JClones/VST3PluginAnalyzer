# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
cmake_minimum_required(VERSION 3.25)
project(VST3PluginAnalyzer VERSION 0.2.0 LANGUAGES CXX)

# Enable Objective-C++ before the SDK creates targets containing native macOS sources.
if(APPLE)
    enable_language(OBJCXX)
endif()

# This repository builds the application host without SDK example targets.
option(BUILD_TESTING "Build SDK tests" OFF)

# Build only the SDK libraries needed by the host; keep plugin installation local.
set(SMTG_ENABLE_VST3_PLUGIN_EXAMPLES OFF CACHE BOOL "" FORCE)
set(SMTG_ENABLE_VST3_HOSTING_EXAMPLES OFF CACHE BOOL "" FORCE)
set(SMTG_ENABLE_VSTGUI_SUPPORT OFF CACHE BOOL "" FORCE)
set(SMTG_ADD_VST3_UTILITIES OFF CACHE BOOL "" FORCE)
set(SMTG_CREATE_PLUGIN_LINK OFF CACHE BOOL "" FORCE)
set(SMTG_RUN_VST_VALIDATOR OFF CACHE BOOL "" FORCE)
set(SMTG_CREATE_MODULE_INFO OFF CACHE BOOL "" FORCE)
list(APPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_SOURCE_DIR}/vst3sdk/cmake/modules")
include(SMTG_VST3_SDK)
smtg_setup_platform_toolset()
add_subdirectory(vst3sdk EXCLUDE_FROM_ALL)

# Keep native module loading and editor windows behind platform-specific C++ backends.
add_library(analyzer_host STATIC cpp/host/plugin_host.cpp
    vst3sdk/public.sdk/source/vst/hosting/plugprovider.cpp
    vst3sdk/public.sdk/source/common/memorystream.cpp)
target_include_directories(analyzer_host PUBLIC cpp)
target_compile_features(analyzer_host PUBLIC cxx_std_17)
target_link_libraries(analyzer_host PUBLIC sdk_hosting)
if(APPLE)
    target_sources(analyzer_host PRIVATE vst3sdk/public.sdk/source/vst/hosting/module_mac.mm
        cpp/host/native_editor_mac.mm)
    set_source_files_properties(cpp/host/native_editor_mac.mm PROPERTIES COMPILE_OPTIONS "-fobjc-arc")
    set_source_files_properties(vst3sdk/public.sdk/source/vst/hosting/module_mac.mm
        PROPERTIES COMPILE_OPTIONS "-fobjc-arc")
    target_link_libraries(analyzer_host PUBLIC "-framework Cocoa")
elseif(CMAKE_SYSTEM_NAME STREQUAL "Linux")
    find_package(Threads REQUIRED)
    option(ANALYZER_LINUX_EDITOR "Build the Linux X11/XEmbed plugin editor (also works through XWayland)" ON)
    target_sources(analyzer_host PRIVATE vst3sdk/public.sdk/source/vst/hosting/module_linux.cpp
        cpp/host/linux_run_loop.cpp)
    target_link_libraries(analyzer_host PUBLIC ${CMAKE_DL_LIBS} Threads::Threads)
    if(ANALYZER_LINUX_EDITOR)
        # Disable this option explicitly for a headless host without X11 development libraries.
        find_package(X11 REQUIRED)
        target_sources(analyzer_host PRIVATE cpp/host/native_editor_linux.cpp)
        target_link_libraries(analyzer_host PRIVATE X11::X11)
        message(STATUS "Linux plugin editors: X11/XEmbed (requires DISPLAY at runtime)")
    else()
        target_sources(analyzer_host PRIVATE cpp/host/native_editor_stub.cpp)
        message(STATUS "Linux plugin editors: disabled; headless processing and parameter controls remain available")
    endif()
else()
    message(FATAL_ERROR "Only macOS and Linux are supported")
endif()

add_executable(vst3-host-probe cpp/cli/main.cpp)
target_link_libraries(vst3-host-probe PRIVATE analyzer_host)

# Protocol framing has no SDK dependency; transport and command handling remain separate.
add_library(analyzer_protocol STATIC cpp/protocol/frame.cpp cpp/ipc/unix_socket.cpp)
target_include_directories(analyzer_protocol PUBLIC cpp)
target_compile_features(analyzer_protocol PUBLIC cxx_std_17)
add_executable(vst3-host-service cpp/service/main.cpp cpp/service/ui_launcher.cpp)
target_include_directories(vst3-host-service SYSTEM PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}/vst3sdk/vstgui4/vstgui/thirdparty/rapidjson/include")
target_compile_definitions(vst3-host-service PRIVATE ANALYZER_SOURCE_ROOT="${CMAKE_CURRENT_SOURCE_DIR}")
target_link_libraries(vst3-host-service PRIVATE analyzer_host analyzer_protocol)
