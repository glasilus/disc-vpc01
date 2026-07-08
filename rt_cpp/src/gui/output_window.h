#pragma once
#include <glad/glad.h>
#include <string>

struct GLFWwindow;
struct GLFWmonitor;

// Второе окно без рамки - чистый видеовыход, никакого GUI и контролов.
// Использует общий OpenGL-контекст с окном управления, поэтому текстуру FBO
// канваса можно семплить из обоих окон без копирования.
//
// Типичный жизненный цикл:
//     OutputWindow out;
//     out.init(control_window);
//     ...
//     out.open(monitor_idx);                        // пользователь нажал "Open"
//     out.render(canvas_tex, canvas_w, canvas_h);   // каждый кадр
//     out.close();                                  // пользователь нажал ESC
class OutputWindow {
public:
    bool init(GLFWwindow* share_context);
    void destroy();

    // Открыть fullscreen без рамки на заданном мониторе (индекс с 0). Если
    // окно уже открыто, сначала закрывает его. false при ошибке.
    bool open(int monitor_index);
    void close();

    bool is_open() const { return window_ != nullptr; }

    // Отрисовать текстуру канваса на мониторе.
    // aspect_mode: 0=Contain (letterbox), 1=Cover (заполнение с обрезкой),
    // 2=Stretch, 3=Native (1:1, по центру). Тот же enum, что и
    // EngineSettings::aspect_mode.
    // Переключает GL-контекст на время рендера и возвращает контекст окна
    // управления обратно.
    void render(GLuint canvas_tex, int canvas_w, int canvas_h, int aspect_mode = 0);

    // true, если с последнего опроса пользователь запросил закрытие (ESC
    // или крестик).
    bool consume_close_request();

    // Индекс текущего монитора (-1, если окно не открыто).
    int  monitor_index() const { return mon_idx_; }

private:
    void ensure_gl_objects();

    GLFWwindow* share_  = nullptr;   // контекст окна управления
    GLFWwindow* window_ = nullptr;
    int         mon_idx_ = -1;

    // GL-объекты созданы в контексте output-окна. VAO не шарятся между
    // GLFW-контекстами, поэтому у каждого окна свои.
    GLuint vao_ = 0, vbo_ = 0, prog_ = 0;
    int    win_w_ = 0, win_h_ = 0;
};
