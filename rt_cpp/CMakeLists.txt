cmake_minimum_required(VERSION 3.20)
project(DiscVPC01_RT VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Static runtime on MSVC
if(MSVC)
    set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
    add_compile_options(/W3 /MP)
endif()

# ── Dependencies via vcpkg ────────────────────────────────────────────────────
find_package(glfw3   CONFIG REQUIRED)
find_package(imgui   CONFIG REQUIRED)
find_package(portaudio CONFIG REQUIRED)
find_package(FFTW3f  CONFIG REQUIRED)   # single-precision (libfftw3f)
find_package(nlohmann_json CONFIG REQUIRED)
find_package(glad    CONFIG REQUIRED)

# FFmpeg: vcpkg exposes FFMPEG_INCLUDE_DIRS / FFMPEG_LIBRARY_DIRS / FFMPEG_LIBRARIES
find_package(FFMPEG REQUIRED)

# stb — header-only; locate the include path
find_path(STB_INCLUDE_DIR stb_image.h REQUIRED)

# ── Embed shaders as C++ raw-string headers ───────────────────────────────────
set(SHADER_DIR "${CMAKE_CURRENT_SOURCE_DIR}/src/effects/shaders")
set(SHADER_OUT "${CMAKE_CURRENT_BINARY_DIR}/generated")
file(MAKE_DIRECTORY "${SHADER_OUT}")

set(SHADER_FILES
    passthrough
    rgb_shift flash zoom_glitch mosaic scanlines negative interlace
    ghost_trails feedback_loop vhs_tracking color_bleed dither fisheye
    kali_mirror phase_shift pixel_drift block_glitch bitcrush bad_signal
    pixel_sort stutter temporal_rgb waveshaper chroma_key
    deriv_warp vortex fractal_noise self_disp ascii
)

set(GENERATED_HEADERS "")
foreach(SHADER ${SHADER_FILES})
    set(SRC "${SHADER_DIR}/${SHADER}.frag")
    set(DST "${SHADER_OUT}/${SHADER}_frag.h")
    add_custom_command(
        OUTPUT  "${DST}"
        COMMAND ${CMAKE_COMMAND}
            -DSRC=${SRC} -DDST=${DST} -DNAME=${SHADER}
            -P "${CMAKE_CURRENT_SOURCE_DIR}/cmake/embed_shader.cmake"
        DEPENDS "${SRC}"
        COMMENT "Embedding shader ${SHADER}.frag"
        VERBATIM
    )
    list(APPEND GENERATED_HEADERS "${DST}")
endforeach()

add_custom_target(EmbedShaders ALL DEPENDS ${GENERATED_HEADERS})

# ── Sources ───────────────────────────────────────────────────────────────────
set(SOURCES
    src/main.cpp
    src/audio/audio_analyzer.cpp
    src/audio/segment.cpp
    src/video/video_source.cpp
    src/video/video_pool.cpp
    src/video/overlay_manager.cpp
    src/effects/effect_chain.cpp
    src/engine/rt_engine.cpp
    src/gui/rt_gui.cpp
    src/gui/theme.cpp
    src/presets/preset_manager.cpp
)

add_executable(DiscVPC01-RT WIN32 ${SOURCES} ${GENERATED_HEADERS})
add_dependencies(DiscVPC01-RT EmbedShaders)

target_include_directories(DiscVPC01-RT PRIVATE
    src
    "${SHADER_OUT}"          # generated/*_frag.h
    "${STB_INCLUDE_DIR}"
    ${FFMPEG_INCLUDE_DIRS}
)

target_link_directories(DiscVPC01-RT PRIVATE ${FFMPEG_LIBRARY_DIRS})

target_link_libraries(DiscVPC01-RT PRIVATE
    glfw
    imgui::imgui
    portaudio
    FFTW3::fftw3f            # single-precision (fftwf_*)
    ${FFMPEG_LIBRARIES}
    nlohmann_json::nlohmann_json
    glad::glad
    opengl32
)

if(WIN32)
    target_link_libraries(DiscVPC01-RT PRIVATE ole32 uuid comdlg32 shell32)
    if(MSVC)
        # WIN32 subsystem hides the console, but our entry point is int main().
        # Without /ENTRY the linker expects WinMain and fails with LNK2019.
        set_target_properties(DiscVPC01-RT PROPERTIES
            LINK_FLAGS "/ENTRY:mainCRTStartup")
    endif()
endif()

install(TARGETS DiscVPC01-RT DESTINATION .)
