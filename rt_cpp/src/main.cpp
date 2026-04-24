#include <glad/glad.h>
#include <GLFW/glfw3.h>
#include <cstdio>
#include <cstdlib>
#include <cstring>

#include "engine/rt_engine.h"
#include "gui/rt_gui.h"

static constexpr int kDefaultW = 1280;
static constexpr int kDefaultH = 720;

static RtEngine* g_engine = nullptr;

static void glfw_error_cb(int code, const char* msg) {
    fprintf(stderr, "GLFW error %d: %s\n", code, msg);
}

static void key_callback(GLFWwindow* window, int key, int /*scancode*/, int action, int /*mods*/) {
    if (action != GLFW_PRESS) return;
    if (key == GLFW_KEY_ESCAPE) glfwSetWindowShouldClose(window, GLFW_TRUE);
    if (key == GLFW_KEY_F11) {
        GLFWmonitor* mon = glfwGetWindowMonitor(window);
        if (mon) {
            // Currently fullscreen — go windowed
            glfwSetWindowMonitor(window, nullptr, 100, 100, kDefaultW, kDefaultH, 0);
        } else {
            // Go fullscreen on primary monitor
            GLFWmonitor* primary = glfwGetPrimaryMonitor();
            const GLFWvidmode* mode = glfwGetVideoMode(primary);
            glfwSetWindowMonitor(window, primary, 0, 0, mode->width, mode->height, mode->refreshRate);
        }
    }
    if (key == GLFW_KEY_F12) {
        // Second monitor output
        int mon_count = 0;
        GLFWmonitor** monitors = glfwGetMonitors(&mon_count);
        if (mon_count >= 2) {
            const GLFWvidmode* mode = glfwGetVideoMode(monitors[1]);
            int mx, my;
            glfwGetMonitorPos(monitors[1], &mx, &my);
            glfwSetWindowMonitor(window, monitors[1], mx, my, mode->width, mode->height, mode->refreshRate);
        }
    }
}

int main() {
    printf("Disc VPC 01 — Realtime  (C++ edition)\n");
    printf("F11=Fullscreen  F12=2nd monitor  ESC=Exit\n\n");

    glfwSetErrorCallback(glfw_error_cb);
    if (!glfwInit()) { fprintf(stderr, "GLFW init failed\n"); return 1; }

    glfwWindowHint(GLFW_CONTEXT_VERSION_MAJOR, 3);
    glfwWindowHint(GLFW_CONTEXT_VERSION_MINOR, 3);
    glfwWindowHint(GLFW_OPENGL_PROFILE, GLFW_OPENGL_CORE_PROFILE);
#ifdef __APPLE__
    glfwWindowHint(GLFW_OPENGL_FORWARD_COMPAT, GL_TRUE);
#endif

    GLFWwindow* window = glfwCreateWindow(kDefaultW, kDefaultH,
        "Disc VPC 01 — RT", nullptr, nullptr);
    if (!window) { fprintf(stderr, "Window creation failed\n"); glfwTerminate(); return 1; }

    glfwSetKeyCallback(window, key_callback);
    glfwMakeContextCurrent(window);
    glfwSwapInterval(1);  // vsync

    if (!gladLoadGLLoader((GLADloadproc)glfwGetProcAddress)) {
        fprintf(stderr, "GLAD init failed\n"); return 1;
    }

    // ── Engine ────────────────────────────────────────────────────────────────
    RtEngine engine;
    g_engine = &engine;
    if (!engine.init(kDefaultW, kDefaultH)) {
        fprintf(stderr, "Engine init failed\n"); return 1;
    }

    // ── GUI ───────────────────────────────────────────────────────────────────
    RtGui gui;
    if (!gui.init(window, &engine, "presets")) {
        fprintf(stderr, "GUI init failed\n"); return 1;
    }

    // ── Default settings (loaded from rt_blank.json if present) ──────────────
    EngineSettings settings;
    // Load blank preset as default
    {
        PresetManager pm;
        pm.scan_folder("presets");
        int bi = pm.blank_index();
        if (bi >= 0) pm.load(pm.paths()[bi], settings);
    }

    // ── Main loop ─────────────────────────────────────────────────────────────
    double prev_time = glfwGetTime();
    GLuint display_tex = 0;
    float  fps_accum   = 0.f;
    int    fps_frames  = 0;
    float  fps         = 0.f;

    while (!glfwWindowShouldClose(window)) {
        glfwPollEvents();

        double now = glfwGetTime();
        float  dt  = (float)(now - prev_time);
        prev_time  = now;

        // FPS counter
        fps_accum  += dt;
        fps_frames++;
        if (fps_accum >= 0.5f) {
            fps        = fps_frames / fps_accum;
            fps_accum  = 0.f;
            fps_frames = 0;
        }

        // Handle GUI requests
        if (gui.want_start()) {
            int dev = gui.selected_device();
            if (dev >= 0) engine.audio().start(dev);
        }
        if (gui.want_stop()) {
            engine.audio().stop();
        }

        // Process frame
        int fb_w, fb_h;
        glfwGetFramebufferSize(window, &fb_w, &fb_h);
        display_tex = engine.process_frame(dt, settings);

        // Render
        glBindFramebuffer(GL_FRAMEBUFFER, 0);
        glViewport(0, 0, fb_w, fb_h);
        glClearColor(0.f, 0.f, 0.f, 1.f);
        glClear(GL_COLOR_BUFFER_BIT);

        // Blit display texture to screen (full-quad via passthrough)
        // The GUI will draw it as ImGui::Image
        gui.render(settings, fps, display_tex);

        glfwSwapBuffers(window);
    }

    gui.shutdown();
    engine.destroy();
    glfwDestroyWindow(window);
    glfwTerminate();
    return 0;
}
