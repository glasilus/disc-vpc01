#include "output_window.h"
#include <GLFW/glfw3.h>
#include <cstdio>

// Fullscreen textured quad with aspect-preserving fit (Contain). The canvas
// is already composited at the right aspect, so here we only need to fit
// the canvas into the monitor without stretching.
static const char* k_vert =
    "#version 330 core\n"
    "layout(location=0) in vec2 aPos;\n"
    "layout(location=1) in vec2 aUV;\n"
    "out vec2 vUV;\n"
    "void main(){ vUV=aUV; gl_Position=vec4(aPos,0.0,1.0); }\n";

// Same four aspect modes as canvas_place: Contain / Cover / Stretch / Native.
// Without this, a 4:3 canvas on a 16:9 monitor was always letterboxed even
// when the user picked Cover at the master level — output had its own
// hardcoded Contain that overrode the inner choice.
static const char* k_frag =
    "#version 330 core\n"
    "in vec2 vUV;\n"
    "out vec4 fragColor;\n"
    "uniform sampler2D uTex;\n"
    "uniform vec2 uCanvasSize;\n"
    "uniform vec2 uWindowSize;\n"
    "uniform int  uMode;\n"
    "void main(){\n"
    "  if (uMode == 2) { fragColor = texture(uTex, vUV); return; }\n"
    "  float cA = uCanvasSize.x / uCanvasSize.y;\n"
    "  float wA = uWindowSize.x / uWindowSize.y;\n"
    "  vec2 frac;\n"
    "  if (uMode == 3) {\n"
    "      frac = uCanvasSize / uWindowSize;\n"
    "  } else if (uMode == 1) {\n"
    "      // Cover: crop to fill — never any black bars.\n"
    "      frac = (cA > wA) ? vec2(cA / wA, 1.0) : vec2(1.0, wA / cA);\n"
    "  } else {\n"
    "      // Contain: letterbox/pillarbox.\n"
    "      frac = (cA > wA) ? vec2(1.0, wA / cA) : vec2(cA / wA, 1.0);\n"
    "  }\n"
    "  vec2 uv = (vUV - 0.5) / frac + 0.5;\n"
    "  if (any(lessThan(uv, vec2(0.0))) || any(greaterThan(uv, vec2(1.0))))\n"
    "       fragColor = vec4(0.0);\n"
    "  else fragColor = texture(uTex, uv);\n"
    "}\n";

static GLuint compile_shader(GLenum type, const char* src) {
    GLuint s = glCreateShader(type);
    glShaderSource(s, 1, &src, nullptr);
    glCompileShader(s);
    GLint ok = 0; glGetShaderiv(s, GL_COMPILE_STATUS, &ok);
    if (!ok) {
        char log[1024]; glGetShaderInfoLog(s, sizeof(log), nullptr, log);
        fprintf(stderr, "[output] shader compile: %s\n", log);
    }
    return s;
}

static GLuint link_program(const char* vs, const char* fs) {
    GLuint v = compile_shader(GL_VERTEX_SHADER,   vs);
    GLuint f = compile_shader(GL_FRAGMENT_SHADER, fs);
    GLuint p = glCreateProgram();
    glAttachShader(p, v); glAttachShader(p, f); glLinkProgram(p);
    glDeleteShader(v); glDeleteShader(f);
    return p;
}

// Close-request flag, set from GLFW callback (no closures on GLFW callbacks).
static bool g_close_requested = false;

static void key_cb(GLFWwindow* w, int key, int, int action, int) {
    if (action != GLFW_PRESS) return;
    if (key == GLFW_KEY_ESCAPE) {
        g_close_requested = true;
        glfwSetWindowShouldClose(w, GLFW_TRUE);
    }
}

static void close_cb(GLFWwindow* w) {
    g_close_requested = true;
    glfwSetWindowShouldClose(w, GLFW_TRUE);
}

bool OutputWindow::init(GLFWwindow* share) {
    share_ = share;
    return share_ != nullptr;
}

void OutputWindow::destroy() {
    close();
}

void OutputWindow::ensure_gl_objects() {
    if (vao_) return;
    static const float verts[] = {
        -1.f,-1.f, 0.f,0.f,
         1.f,-1.f, 1.f,0.f,
        -1.f, 1.f, 0.f,1.f,
         1.f,-1.f, 1.f,0.f,
         1.f, 1.f, 1.f,1.f,
        -1.f, 1.f, 0.f,1.f,
    };
    glGenVertexArrays(1, &vao_);
    glGenBuffers(1, &vbo_);
    glBindVertexArray(vao_);
    glBindBuffer(GL_ARRAY_BUFFER, vbo_);
    glBufferData(GL_ARRAY_BUFFER, sizeof(verts), verts, GL_STATIC_DRAW);
    glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, 4*sizeof(float), (void*)0);
    glEnableVertexAttribArray(0);
    glVertexAttribPointer(1, 2, GL_FLOAT, GL_FALSE, 4*sizeof(float), (void*)(2*sizeof(float)));
    glEnableVertexAttribArray(1);
    glBindVertexArray(0);
    prog_ = link_program(k_vert, k_frag);
}

bool OutputWindow::open(int monitor_index) {
    if (window_) close();

    int count = 0;
    GLFWmonitor** mons = glfwGetMonitors(&count);
    if (count <= 0) return false;
    if (monitor_index < 0 || monitor_index >= count) monitor_index = 0;

    GLFWmonitor* mon = mons[monitor_index];
    const GLFWvidmode* mode = glfwGetVideoMode(mon);

    // Borderless fullscreen: position at monitor origin, no decorations,
    // no resize. This preserves desktop compositor / window manager
    // behavior so dragging other windows on top still works (important for
    // OBS capture during a VJ set).
    glfwWindowHint(GLFW_DECORATED, GLFW_FALSE);
    glfwWindowHint(GLFW_RESIZABLE, GLFW_FALSE);
    glfwWindowHint(GLFW_FLOATING,  GLFW_FALSE);
    glfwWindowHint(GLFW_CONTEXT_VERSION_MAJOR, 3);
    glfwWindowHint(GLFW_CONTEXT_VERSION_MINOR, 3);
    glfwWindowHint(GLFW_OPENGL_PROFILE, GLFW_OPENGL_CORE_PROFILE);
#ifdef __APPLE__
    glfwWindowHint(GLFW_OPENGL_FORWARD_COMPAT, GL_TRUE);
#endif

    window_ = glfwCreateWindow(mode->width, mode->height,
                               "Disc VPC 01 — Output", nullptr, share_);
    // Reset hints so they don't leak into any future window creation.
    glfwDefaultWindowHints();
    if (!window_) { fprintf(stderr, "[output] glfwCreateWindow failed\n"); return false; }

    int mx, my;
    glfwGetMonitorPos(mon, &mx, &my);
    glfwSetWindowPos(window_, mx, my);
    glfwSetWindowSize(window_, mode->width, mode->height);

    glfwSetKeyCallback(window_, key_cb);
    glfwSetWindowCloseCallback(window_, close_cb);

    // Build our GL objects in this context.
    GLFWwindow* prev = glfwGetCurrentContext();
    glfwMakeContextCurrent(window_);
    glfwSwapInterval(1);
    ensure_gl_objects();
    glfwMakeContextCurrent(prev);

    mon_idx_  = monitor_index;
    g_close_requested = false;
    win_w_ = mode->width;
    win_h_ = mode->height;
    return true;
}

void OutputWindow::close() {
    if (!window_) return;
    GLFWwindow* prev = glfwGetCurrentContext();
    glfwMakeContextCurrent(window_);
    if (vao_)  { glDeleteVertexArrays(1, &vao_); vao_ = 0; }
    if (vbo_)  { glDeleteBuffers(1, &vbo_);      vbo_ = 0; }
    if (prog_) { glDeleteProgram(prog_);         prog_ = 0; }
    // Restore context BEFORE destroying the window — otherwise `prev`
    // becomes invalid if it was the one we just deleted.
    glfwMakeContextCurrent(prev == window_ ? nullptr : prev);
    glfwDestroyWindow(window_);
    window_  = nullptr;
    mon_idx_ = -1;
}

void OutputWindow::render(GLuint canvas_tex, int canvas_w, int canvas_h, int aspect_mode) {
    if (!window_) return;
    if (glfwWindowShouldClose(window_)) { close(); return; }

    GLFWwindow* prev = glfwGetCurrentContext();
    glfwMakeContextCurrent(window_);

    int fb_w, fb_h;
    glfwGetFramebufferSize(window_, &fb_w, &fb_h);
    win_w_ = fb_w; win_h_ = fb_h;

    glBindFramebuffer(GL_FRAMEBUFFER, 0);
    glViewport(0, 0, fb_w, fb_h);
    glClearColor(0.f, 0.f, 0.f, 1.f);
    glClear(GL_COLOR_BUFFER_BIT);

    if (canvas_tex) {
        glUseProgram(prog_);
        glActiveTexture(GL_TEXTURE0);
        glBindTexture(GL_TEXTURE_2D, canvas_tex);
        glUniform1i(glGetUniformLocation(prog_, "uTex"), 0);
        glUniform2f(glGetUniformLocation(prog_, "uCanvasSize"),
                    (float)canvas_w, (float)canvas_h);
        glUniform2f(glGetUniformLocation(prog_, "uWindowSize"),
                    (float)fb_w,     (float)fb_h);
        glUniform1i(glGetUniformLocation(prog_, "uMode"), aspect_mode);
        glBindVertexArray(vao_);
        glDrawArrays(GL_TRIANGLES, 0, 6);
        glBindVertexArray(0);
    }

    glfwSwapBuffers(window_);
    glfwMakeContextCurrent(prev);
}

bool OutputWindow::consume_close_request() {
    bool r = g_close_requested;
    g_close_requested = false;
    return r;
}
