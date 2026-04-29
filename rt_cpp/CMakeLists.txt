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
    passthrough canvas_place
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

# ── Embed AUDIO.png as window icon ────────────────────────────────────────────
# Loaded at runtime via stb_image_load_from_memory and handed to
# glfwSetWindowIcon (see src/main.cpp). Embedding the bytes keeps the
# binary fully self-contained — no missing-icon failures when the user
# moves the .exe / ELF outside the build dir.
# AUDIO.png lives at the repo root (one level above rt_cpp/).
set(ICON_PNG_SRC "${CMAKE_CURRENT_SOURCE_DIR}/../AUDIO.png")
set(ICON_PNG_HDR "${SHADER_OUT}/audio_icon_png.h")
add_custom_command(
    OUTPUT  "${ICON_PNG_HDR}"
    COMMAND ${CMAKE_COMMAND}
        -DSRC=${ICON_PNG_SRC} -DDST=${ICON_PNG_HDR} -DNAME=audio_icon_png
        -P "${CMAKE_CURRENT_SOURCE_DIR}/cmake/embed_binary.cmake"
    DEPENDS "${ICON_PNG_SRC}"
    COMMENT "Embedding AUDIO.png as binary header"
    VERBATIM
)
list(APPEND GENERATED_HEADERS "${ICON_PNG_HDR}")
add_custom_target(EmbedIcon ALL DEPENDS "${ICON_PNG_HDR}")

# ── Sources ───────────────────────────────────────────────────────────────────
set(SOURCES
    src/main.cpp
    src/core/log.cpp
    src/audio/audio_analyzer.cpp
    src/audio/segment.cpp
    src/video/video_source.cpp
    src/video/video_pool.cpp
    src/video/overlay_manager.cpp
    src/effects/effect_chain.cpp
    src/engine/rt_engine.cpp
    src/gui/rt_gui.cpp
    src/gui/theme.cpp
    src/gui/font_loader.cpp
    src/gui/output_window.cpp
    src/gui/win95.cpp
    src/presets/preset_manager.cpp
)

# Windows: compile the .rc resource into the PE so Explorer / taskbar pick
# up the AUDIO.ico icon on the .exe directly (no runtime work needed).
if(WIN32)
    list(APPEND SOURCES resources/app.rc)
endif()

# macOS: copy AUDIO.icns into the .app bundle's Contents/Resources/ and
# wire it up via Info.plist (MACOSX_BUNDLE_ICON_FILE). The runtime
# glfwSetWindowIcon() is a no-op on macOS — the bundle icon is what
# Finder, Dock and Cmd-Tab actually use.
if(APPLE)
    set(MAC_ICON_SRC "${CMAKE_CURRENT_SOURCE_DIR}/../AUDIO.icns")
    set_source_files_properties("${MAC_ICON_SRC}"
        PROPERTIES MACOSX_PACKAGE_LOCATION "Resources")
    list(APPEND SOURCES "${MAC_ICON_SRC}")
endif()

add_executable(DiscVPC01-RT WIN32 MACOSX_BUNDLE ${SOURCES} ${GENERATED_HEADERS})

if(APPLE)
    set_target_properties(DiscVPC01-RT PROPERTIES
        MACOSX_BUNDLE TRUE
        MACOSX_BUNDLE_BUNDLE_NAME "DiscVPC01-RT"
        MACOSX_BUNDLE_GUI_IDENTIFIER "org.discvpc.rt"
        MACOSX_BUNDLE_BUNDLE_VERSION "1.0"
        MACOSX_BUNDLE_SHORT_VERSION_STRING "1.0"
        MACOSX_BUNDLE_INFO_STRING "Disc VPC 01 RT"
        MACOSX_BUNDLE_ICON_FILE "AUDIO.icns"
    )
endif()
add_dependencies(DiscVPC01-RT EmbedShaders EmbedIcon)

target_include_directories(DiscVPC01-RT PRIVATE
    src
    "${SHADER_OUT}"          # generated/*_frag.h
    "${STB_INCLUDE_DIR}"
    ${FFMPEG_INCLUDE_DIRS}
)

target_link_directories(DiscVPC01-RT PRIVATE ${FFMPEG_LIBRARY_DIRS})

# Cross-platform OpenGL: OpenGL::GL on Linux/Win, OpenGL framework on macOS
find_package(OpenGL REQUIRED)

target_link_libraries(DiscVPC01-RT PRIVATE
    glfw
    imgui::imgui
    portaudio
    FFTW3::fftw3f            # single-precision (fftwf_*)
    ${FFMPEG_LIBRARIES}
    nlohmann_json::nlohmann_json
    glad::glad
    OpenGL::GL
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

# vcpkg's static portaudio on Linux embeds pa_jack.c / pa_alsa.c but does not
# propagate the transitive JACK / ALSA link deps — resolve them explicitly.
if(UNIX AND NOT APPLE)
    target_link_libraries(DiscVPC01-RT PRIVATE jack asound)
endif()

install(TARGETS DiscVPC01-RT DESTINATION .)
