#include <glad/glad.h>
#include <GLFW/glfw3.h>
#include <imgui.h>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <algorithm>

#include "engine/rt_engine.h"
#include "gui/rt_gui.h"
#include "gui/output_window.h"
#include "presets/preset_manager.h"
#include "core/log.h"

static constexpr int kDefaultW = 1280;
static constexpr int kDefaultH = 720;

// All state the key callback needs. We attach this to the GLFW window via
// glfwSetWindowUserPointer so the callback can reach it without globals.
struct App {
    RtEngine*       engine   = nullptr;
    RtGui*          gui      = nullptr;
    OutputWindow*   output   = nullptr;
    EngineSettings* settings = nullptr;
    GLFWwindow*     control  = nullptr;

    bool  show_gui       = true;  // toggled with Tab
    bool  fullscreen_ctl = false; // toggled with F11 (on control window)
    int   windowed_x = 100, windowed_y = 100, windowed_w = kDefaultW, windowed_h = kDefaultH;
};

static void glfw_error_cb(int code, const char* msg) {
    fprintf(stderr, "GLFW error %d: %s\n", code, msg);
}

static void toggle_fullscreen_control(App* app) {
    GLFWmonitor* mon = glfwGetWindowMonitor(app->control);
    if (mon) {
        glfwSetWindowMonitor(app->control, nullptr,
            app->windowed_x, app->windowed_y,
            app->windowed_w, app->windowed_h, 0);
        app->fullscreen_ctl = false;
    } else {
        glfwGetWindowPos(app->control, &app->windowed_x, &app->windowed_y);
        glfwGetWindowSize(app->control, &app->windowed_w, &app->windowed_h);
        GLFWmonitor* primary = glfwGetPrimaryMonitor();
        const GLFWvidmode* mode = glfwGetVideoMode(primary);
        glfwSetWindowMonitor(app->control, primary, 0, 0,
            mode->width, mode->height, mode->refreshRate);
        app->fullscreen_ctl = true;
    }
}

static void toggle_effect(EngineSettings* s, int id) {
    if (id < 0 || id >= (int)FxId::COUNT) return;
    s->fx[id].enabled = !s->fx[id].enabled;
}

// ── Key bindings ─────────────────────────────────────────────────────────────
//  Space    Start/Stop audio           Tab    Toggle GUI overlay
//  B        Blackout                   F11    Fullscreen control window
//  F        Freeze                     Esc    Close output / else exit
//  1..9, 0  Load preset by index       Q W E R T Y U I O P   toggle fx 0..9
//  [   ]    Chaos    -/+               ,   .  Cut interval  -/+
//  O        Open output on selected monitor
//  Shift+O  Close output
static void key_callback(GLFWwindow* w, int key, int /*sc*/, int action, int mods) {
    App* app = static_cast<App*>(glfwGetWindowUserPointer(w));
    if (!app) return;

    // Ignore keys while ImGui has keyboard focus (text inputs, etc.) — except
    // for Tab (GUI toggle) and Esc (universal exit/close).
    // Note: ImGui WantCaptureKeyboard is only valid after NewFrame. We read
    // the raw GLFW event but consult ImGui. When Tab/Esc are pressed we
    // bypass the check so they always work.
    bool is_meta_key = (key == GLFW_KEY_TAB || key == GLFW_KEY_ESCAPE);
    if (!is_meta_key) {
        ImGuiIO* io = ImGui::GetCurrentContext() ? &ImGui::GetIO() : nullptr;
        if (io && io->WantCaptureKeyboard && io->WantTextInput) return;
    }

    if (action != GLFW_PRESS && action != GLFW_REPEAT) return;

    // Discrete press-only actions
    if (action == GLFW_PRESS) {
        switch (key) {
            case GLFW_KEY_ESCAPE:
                if (app->output && app->output->is_open()) {
                    app->output->close();
                } else {
                    glfwSetWindowShouldClose(w, GLFW_TRUE);
                }
                return;
            case GLFW_KEY_SPACE: {
                auto& a = app->engine->audio();
                if (a.is_running()) a.stop();
                else a.start(app->gui->selected_device());  // -1 ⇒ auto-default
                return;
            }
            case GLFW_KEY_B: app->engine->blackout = !app->engine->blackout; return;
            case GLFW_KEY_F: app->engine->freeze   = !app->engine->freeze;   return;
            case GLFW_KEY_TAB: app->show_gui = !app->show_gui; return;
            case GLFW_KEY_F11: toggle_fullscreen_control(app); return;
            // Preset by number
            case GLFW_KEY_1: case GLFW_KEY_2: case GLFW_KEY_3:
            case GLFW_KEY_4: case GLFW_KEY_5: case GLFW_KEY_6:
            case GLFW_KEY_7: case GLFW_KEY_8: case GLFW_KEY_9:
                app->gui->request_preset_by_index(key - GLFW_KEY_1);
                return;
            case GLFW_KEY_0:
                app->gui->request_preset_by_index(9);
                return;
            // FX toggle row 0..9 on Q W E R T Y U I O P
            case GLFW_KEY_Q: toggle_effect(app->settings, 0); return;
            case GLFW_KEY_W: toggle_effect(app->settings, 1); return;
            case GLFW_KEY_E: toggle_effect(app->settings, 2); return;
            case GLFW_KEY_R: toggle_effect(app->settings, 3); return;
            case GLFW_KEY_T: toggle_effect(app->settings, 4); return;
            case GLFW_KEY_Y: toggle_effect(app->settings, 5); return;
            case GLFW_KEY_U: toggle_effect(app->settings, 6); return;
            case GLFW_KEY_I: toggle_effect(app->settings, 7); return;
            case GLFW_KEY_O:
                if (mods & GLFW_MOD_SHIFT) {
                    if (app->output) app->output->close();
                } else {
                    toggle_effect(app->settings, 8);
                }
                return;
            case GLFW_KEY_P: toggle_effect(app->settings, 9); return;
        }
    }

    // Held / repeat-capable adjustments
    switch (key) {
        case GLFW_KEY_LEFT_BRACKET:
            app->settings->chaos = std::max(0.f, app->settings->chaos - 0.02f); break;
        case GLFW_KEY_RIGHT_BRACKET:
            app->settings->chaos = std::min(1.f, app->settings->chaos + 0.02f); break;
        case GLFW_KEY_COMMA:
            app->settings->cut_interval = std::max(0.05f, app->settings->cut_interval - 0.02f); break;
        case GLFW_KEY_PERIOD:
            app->settings->cut_interval = std::min(2.f, app->settings->cut_interval + 0.02f); break;
    }
}

int main() {
    // First thing: redirect stderr/stdout to disc_vpc.log so every diagnostic
    // (including ones written before any console can be attached) is
    // recoverable after a crash. Logs go next to the working directory.
    Log::init();
    fprintf(stderr, "Disc VPC 01 — Realtime  (C++ edition)\n");
    fprintf(stderr, "Keybindings: Space=start/stop  B=blackout  F=freeze  Tab=gui  F11=fullscreen\n");
    fprintf(stderr, "  1-9,0=presets   Q-P=toggle fx 0..9   [ ]=chaos   , .=cut interval\n");
    fprintf(stderr, "  Shift+O=close output   Esc=close output / exit\n\n");

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

    glfwMakeContextCurrent(window);
    glfwSwapInterval(1);

    if (!gladLoadGLLoader((GLADloadproc)glfwGetProcAddress)) {
        fprintf(stderr, "GLAD init failed\n"); return 1;
    }

    RtEngine engine;
    if (!engine.init(kDefaultW, kDefaultH)) {
        fprintf(stderr, "Engine init failed\n"); return 1;
    }

    EngineSettings settings;
    {
        PresetManager pm;
        pm.scan_folder("presets");
        int bi = pm.blank_index();
        if (bi >= 0) pm.load(pm.paths()[bi], settings);
    }

    OutputWindow output;
    output.init(window);

    // Install our key callback BEFORE RtGui::init() so ImGui's GLFW backend
    // chains it rather than overwriting it. Fill in app pointers before the
    // callback is installed so a keyboard event fired during ImGui's own
    // initialization (which is real: ImGui polls state) doesn't see null
    // fields. RtGui is default-constructible → &gui is valid pre-init; the
    // methods we call pre-init just return safe defaults.
    RtGui gui;
    App   app;
    app.engine   = &engine;
    app.gui      = &gui;
    app.output   = &output;
    app.settings = &settings;
    app.control  = window;
    glfwSetWindowUserPointer(window, &app);
    glfwSetKeyCallback(window, key_callback);

    if (!gui.init(window, &engine, "presets")) {
        fprintf(stderr, "GUI init failed\n"); return 1;
    }

    // ── Main loop ─────────────────────────────────────────────────────────────
    double prev_time = glfwGetTime();
    GLuint display_tex = 0;
    float  fps_accum   = 0.f;
    int    fps_frames  = 0;
    float  fps         = 0.f;

    while (!glfwWindowShouldClose(window)) {
        glfwPollEvents();

        // Output-window close via its own ESC / X — detected here so we can
        // release the window on the main thread.
        if (output.consume_close_request()) output.close();

        double now = glfwGetTime();
        float  dt  = (float)(now - prev_time);
        prev_time  = now;

        fps_accum  += dt;
        fps_frames++;
        if (fps_accum >= 0.5f) {
            fps        = fps_frames / fps_accum;
            fps_accum  = 0.f;
            fps_frames = 0;
        }

        // GUI-initiated audio start/stop (button) — keyboard-initiated uses
        // the Space shortcut which hits engine.audio() directly.
        if (gui.want_start()) {
            // Pass -1 through so AudioAnalyzer::start auto-selects the
            // platform default device (WASAPI on Windows). Better UX than
            // silently doing nothing when the user hasn't picked a device.
            engine.audio().start(gui.selected_device());
        }
        if (gui.want_stop()) engine.audio().stop();

        // Output-window open requests from GUI
        if (gui.want_output_open()) {
            output.open(gui.requested_output_monitor());
        }
        if (gui.want_output_close()) output.close();

        // Apply pending preset load (keyboard-triggered)
        gui.apply_pending_preset(settings);

        // Process effect chain → canvas texture.
        display_tex = engine.process_frame(dt, settings);

        // Control window render.
        int fb_w, fb_h;
        glfwGetFramebufferSize(window, &fb_w, &fb_h);
        glBindFramebuffer(GL_FRAMEBUFFER, 0);
        glViewport(0, 0, fb_w, fb_h);
        glClearColor(0.f, 0.f, 0.f, 1.f);
        glClear(GL_COLOR_BUFFER_BIT);
        if (app.show_gui) {
            gui.render(settings, fps, display_tex);
        } else {
            // Hide GUI: blit canvas fullscreen on control window too (for
            // single-monitor VJs who use the control window as output).
            gui.render_bare(display_tex, fb_w, fb_h);
        }
        glfwSwapBuffers(window);

        // Output window render (second monitor).
        if (output.is_open()) {
            output.render(display_tex, engine.canvas_width(), engine.canvas_height());
        }
    }

    output.destroy();
    gui.shutdown();
    engine.destroy();
    glfwDestroyWindow(window);
    glfwTerminate();
    Log::shutdown();
    return 0;
}
