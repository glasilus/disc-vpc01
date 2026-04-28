#pragma once
#include <glad/glad.h>
#include <string>

struct GLFWwindow;
struct GLFWmonitor;

// A second, borderless window dedicated to clean video output — no GUI chrome,
// no controls. Shares its OpenGL context with the control window so the
// canvas FBO texture can be sampled from either side without copying.
//
// Typical lifecycle:
//     OutputWindow out;
//     out.init(control_window);
//     ...
//     out.open(monitor_idx);                        // user clicks "Open"
//     out.render(canvas_tex, canvas_w, canvas_h);   // every frame, once
//     out.close();                                  // user presses ESC
class OutputWindow {
public:
    bool init(GLFWwindow* share_context);
    void destroy();

    // Open borderless fullscreen on the given monitor (0-based). If a window
    // is already open, it's closed first. Returns false on failure.
    bool open(int monitor_index);
    void close();

    bool is_open() const { return window_ != nullptr; }

    // Draw the canvas texture onto the monitor.
    // aspect_mode: 0=Contain (letterbox), 1=Cover (fill, crop), 2=Stretch,
    // 3=Native (1:1, centered). Same enum as EngineSettings::aspect_mode.
    // Switches GL context, renders, swaps, and restores the control context.
    void render(GLuint canvas_tex, int canvas_w, int canvas_h, int aspect_mode = 0);

    // True iff the user requested closure (ESC or X button) since last query.
    bool consume_close_request();

    // Current monitor index (or -1 if not open).
    int  monitor_index() const { return mon_idx_; }

private:
    void ensure_gl_objects();

    GLFWwindow* share_  = nullptr;   // control window context
    GLFWwindow* window_ = nullptr;
    int         mon_idx_ = -1;

    // GL objects created in the output context. VAOs are not shared across
    // GLFW contexts, so each window owns its own.
    GLuint vao_ = 0, vbo_ = 0, prog_ = 0;
    int    win_w_ = 0, win_h_ = 0;
};
