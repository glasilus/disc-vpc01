#include <glad/glad.h>
#include <GLFW/glfw3.h>
#include <imgui.h>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <algorithm>
#if defined(__APPLE__)
#  include <unistd.h>       // chdir
#  include <filesystem>
#  include <string>
#endif

#define STB_IMAGE_IMPLEMENTATION_NOT_NEEDED  // маркер - stb_image.h уже
// реализован в другом месте сборки (overlay manager собирается с
// STB_IMAGE_IMPLEMENTATION). Подключение stb_image.h здесь без макроса
// реализации - осознанное решение: нам нужны только объявления декодера,
// чтобы вызвать stbi_load_from_memory() и превратить встроенные байты
// AUDIO.png в сырой RGBA для glfwSetWindowIcon().
#include <stb_image.h>



#include "engine/rt_engine.h"
#include "gui/rt_gui.h"
#include "gui/output_window.h"
#include "presets/preset_manager.h"
#include "control/midi_control.h"
#include "core/log.h"

// Встроенные байты AUDIO.png (генерируются cmake/embed_binary.cmake во
// время сборки). Заголовок лежит в `generated/` внутри build-директории
// и попадает в include path через target_include_directories.
#include "audio_icon_png.h"

static constexpr int kDefaultW = 1280;
static constexpr int kDefaultH = 720;

// Всё состояние, нужное key-колбэку. Привязываем это к GLFW-окну через
// glfwSetWindowUserPointer, чтобы колбэк мог до него достучаться без глобалов.
struct App {
    RtEngine*       engine   = nullptr;
    RtGui*          gui      = nullptr;
    OutputWindow*   output   = nullptr;
    EngineSettings* settings = nullptr;
    GLFWwindow*     control  = nullptr;

    bool  show_gui       = true;  // переключается по Tab
    bool  fullscreen_ctl = false; // переключается по F11 (на control-окне)
    int   windowed_x = 100, windowed_y = 100, windowed_w = kDefaultW, windowed_h = kDefaultH;

    // Клавиатурный банк FX: Q..P переключают 10 эффектов; \ листает на следующий
    // банк, чтобы были доступны все FxId::COUNT эффектов, а не только первые десять.
    int   fx_bank = 0;

    // Состояние tap-tempo (кольцевой буфер меток времени последних тапов).
    double tap_times[8] = {};
    int    tap_n        = 0;

    MidiControl* midi = nullptr;
};

static int fx_bank_count() { return ((int)FxId::COUNT + 9) / 10; }

// Регистрирует тап; когда набирается ≥2 тапов в окне, выводим BPM и включаем
// метроном движка. Пауза >2 сек с последнего тапа сбрасывает счётчик.
static void register_tap(App* app) {
    double now = glfwGetTime();
    if (app->tap_n > 0 && now - app->tap_times[(app->tap_n - 1) % 8] > 2.0)
        app->tap_n = 0;
    app->tap_times[app->tap_n % 8] = now;
    app->tap_n++;
    int have = app->tap_n < 8 ? app->tap_n : 8;
    if (have >= 2) {
        double first = app->tap_times[(app->tap_n - have) % 8];
        double last  = app->tap_times[(app->tap_n - 1) % 8];
        double interval = (last - first) / (have - 1);
        if (interval > 0.001) {
            app->engine->set_bpm((float)(60.0 / interval));
            app->engine->metronome = true;
        }
    }
}

static void glfw_error_cb(int code, const char* msg) {
    fprintf(stderr, "GLFW error %d: %s\n", code, msg);
}

// Декодирует встроенные байты AUDIO.png (k_audio_icon_png[]) и ставит их
// иконкой заданному GLFW-окну. Best-effort: ошибка декодирования логируется,
// но не прерывает запуск. macOS игнорирует glfwSetWindowIcon (иконка в доке
// берётся из .app-бандла), так что это в основном для тайтлбара Windows и
// декораций оконных менеджеров Linux.
static void install_window_icon(GLFWwindow* w) {
    int width = 0, height = 0, channels = 0;
    unsigned char* px = stbi_load_from_memory(
        k_audio_icon_png, (int)k_audio_icon_png_len,
        &width, &height, &channels, 4);
    if (!px) {
        fprintf(stderr, "[icon] stbi_load_from_memory failed: %s\n",
                stbi_failure_reason());
        return;
    }
    GLFWimage img{ width, height, px };
    glfwSetWindowIcon(w, 1, &img);
    stbi_image_free(px);
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

// Переключаем по клавиатурному SLOT (порядок как в сгруппированном отображении),
// а не по сырому FxId, чтобы ряд Q..P совпадал со сгруппированным списком в GUI.
// fx_slot_to_id() проверяет границы и возвращает -1 для пустых хвостовых слотов,
// которые мы игнорируем.
static void toggle_effect(EngineSettings* s, int slot) {
    int id = fx_slot_to_id(slot);
    if (id < 0 || id >= (int)FxId::COUNT) return;
    s->fx[id].enabled = !s->fx[id].enabled;
}

// ── Раскладка клавиш ──────────────────────────────────────────────────────────
//  Space    Старт/стоп аудио           Tab    Показать/скрыть GUI-оверлей
//  B        Blackout                   F11    Полный экран control-окна
//  F        Freeze                     Esc    Закрыть output / иначе выход
//  1..9, 0  Загрузить пресет по индексу   Q W E R T Y U I O P   переключить fx 0..9
//  [   ]    Chaos    -/+               ,   .  Cut interval  -/+
//  O        Открыть output на выбранном мониторе
//  Shift+O  Закрыть output
static void key_callback(GLFWwindow* w, int key, int /*sc*/, int action, int mods) {
    App* app = static_cast<App*>(glfwGetWindowUserPointer(w));
    if (!app) return;

    // Игнорируем клавиши, пока клавиатурный фокус у ImGui (текстовые поля и
    // т.п.) - кроме Tab (переключение GUI) и Esc (универсальный выход/закрытие).
    // Замечание: ImGui WantCaptureKeyboard валиден только после NewFrame. Мы
    // читаем сырое GLFW-событие, но сверяемся с ImGui. Для Tab/Esc проверку
    // обходим, чтобы они работали всегда.
    bool is_meta_key = (key == GLFW_KEY_TAB || key == GLFW_KEY_ESCAPE);
    if (!is_meta_key) {
        ImGuiIO* io = ImGui::GetCurrentContext() ? &ImGui::GetIO() : nullptr;
        if (io && io->WantCaptureKeyboard && io->WantTextInput) return;
    }

    if (action != GLFW_PRESS && action != GLFW_REPEAT) return;

    // Дискретные действия только на нажатие
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
                else a.start(app->gui->selected_device());  // -1 ⇒ автовыбор устройства
                return;
            }
            case GLFW_KEY_B: app->engine->blackout = !app->engine->blackout; return;
            case GLFW_KEY_F: app->engine->freeze   = !app->engine->freeze;   return;
            case GLFW_KEY_M:
                app->settings->cut_mode = app->settings->cut_mode ? 0 : 1;
                return;
            case GLFW_KEY_TAB: app->show_gui = !app->show_gui; return;
            case GLFW_KEY_F11: toggle_fullscreen_control(app); return;
            // Цифровой ряд: выбор активного видео (1..9 → слот 0..8, 0 → слот 9).
            // Shift+цифра вместо этого грузит пресет. Повторное нажатие цифры
            // текущего активного видео возвращает фокус на "все видео".
            case GLFW_KEY_1: case GLFW_KEY_2: case GLFW_KEY_3:
            case GLFW_KEY_4: case GLFW_KEY_5: case GLFW_KEY_6:
            case GLFW_KEY_7: case GLFW_KEY_8: case GLFW_KEY_9: {
                int idx = key - GLFW_KEY_1;
                if (mods & GLFW_MOD_SHIFT) {
                    app->gui->request_preset_by_index(idx);
                } else {
                    auto& pool = app->engine->video();
                    pool.set_active(pool.active() == idx ? -1 : idx);
                }
                return;
            }
            case GLFW_KEY_0: {
                if (mods & GLFW_MOD_SHIFT) {
                    app->gui->request_preset_by_index(9);
                } else {
                    auto& pool = app->engine->video();
                    pool.set_active(pool.active() == 9 ? -1 : 9);
                }
                return;
            }
            // Backtick / grave: явный сброс "отпустить активное видео".
            case GLFW_KEY_GRAVE_ACCENT:
                app->engine->video().set_active(-1);
                return;
            // Ряд переключения FX на Q W E R T Y U I O P → эффекты [bank*10 .. +9].
            case GLFW_KEY_Q: toggle_effect(app->settings, app->fx_bank*10 + 0); return;
            case GLFW_KEY_W: toggle_effect(app->settings, app->fx_bank*10 + 1); return;
            case GLFW_KEY_E: toggle_effect(app->settings, app->fx_bank*10 + 2); return;
            case GLFW_KEY_R: toggle_effect(app->settings, app->fx_bank*10 + 3); return;
            case GLFW_KEY_T: toggle_effect(app->settings, app->fx_bank*10 + 4); return;
            case GLFW_KEY_Y: toggle_effect(app->settings, app->fx_bank*10 + 5); return;
            case GLFW_KEY_U: toggle_effect(app->settings, app->fx_bank*10 + 6); return;
            case GLFW_KEY_I: toggle_effect(app->settings, app->fx_bank*10 + 7); return;
            case GLFW_KEY_O:
                if (mods & GLFW_MOD_SHIFT) {
                    if (app->output) app->output->close();
                } else {
                    toggle_effect(app->settings, app->fx_bank*10 + 8);
                }
                return;
            case GLFW_KEY_P: toggle_effect(app->settings, app->fx_bank*10 + 9); return;
            // Переключение клавиатурного банка FX, чтобы были доступны все эффекты.
            case GLFW_KEY_BACKSLASH:
                app->fx_bank = (app->fx_bank + 1) % fx_bank_count();
                fprintf(stderr, "[fx] keyboard bank %d/%d\n",
                        app->fx_bank + 1, fx_bank_count());
                return;
            // Tap tempo (включает метроном по сетке настуканного BPM).
            case GLFW_KEY_ENTER:
                register_tap(app);
                return;
        }
    }

    // Настройки, работающие при удержании / повторе
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
#if defined(__APPLE__)
    // .app, запущенный из Finder/Dock, наследует рабочую директорию "/", которая
    // доступна только на чтение. Все наши относительные пути (presets/, midi.json,
    // лог) тогда не смогут ни загрузиться, ни сохраниться. Переходим в доступную
    // на запись per-user директорию, чтобы приложение вело себя так же, как
    // бинарники под Windows/Linux (у них CWD рядом с исполняемым файлом).
    // Best-effort - если что-то не получится, просто остаёмся в исходной CWD.
    {
        const char* home = std::getenv("HOME");
        if (home && *home) {
            std::string dir = std::string(home) +
                              "/Library/Application Support/DiscVPC01-RT";
            std::error_code ec;
            std::filesystem::create_directories(dir + "/presets", ec);
            if (!ec) (void)chdir(dir.c_str());
        }
    }
#endif

    // Первым делом перенаправляем stderr/stdout в vpc01rt.log, чтобы после краша
    // можно было восстановить всю диагностику (включая записанную до того, как
    // подключилась хоть какая-то консоль). Логи пишутся рядом с рабочей директорией.
    Log::init();
    fprintf(stderr, "Disc VPC 01 - Realtime  (C++ edition)\n");
    fprintf(stderr, "Keybindings: Space=start/stop  B=blackout  F=freeze  M=mode  Tab=gui  F11=fullscreen\n");
    fprintf(stderr, "  1..9,0 = active video (` = release)   Shift+1..0 = load preset\n");
    fprintf(stderr, "  Q..P = toggle fx in current bank   \\ = next fx bank   Enter = tap tempo\n");
    fprintf(stderr, "  [ ] = chaos   , . = cut interval   Shift+O = close output   Esc = exit\n\n");

    glfwSetErrorCallback(glfw_error_cb);
    if (!glfwInit()) { fprintf(stderr, "GLFW init failed\n"); return 1; }

    glfwWindowHint(GLFW_CONTEXT_VERSION_MAJOR, 3);
    glfwWindowHint(GLFW_CONTEXT_VERSION_MINOR, 3);
    glfwWindowHint(GLFW_OPENGL_PROFILE, GLFW_OPENGL_CORE_PROFILE);
#ifdef __APPLE__
    glfwWindowHint(GLFW_OPENGL_FORWARD_COMPAT, GL_TRUE);
#endif

    GLFWwindow* window = glfwCreateWindow(kDefaultW, kDefaultH,
        "Disc VPC 01 - RT", nullptr, nullptr);
    if (!window) { fprintf(stderr, "Window creation failed\n"); glfwTerminate(); return 1; }

    install_window_icon(window);

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

    // Ставим свой key-колбэк ДО RtGui::init(), чтобы GLFW-бэкенд ImGui выстроил
    // его в цепочку, а не перезаписал. Указатели app заполняем до установки
    // колбэка, чтобы клавиатурное событие, случившееся прямо во время
    // инициализации ImGui (это реально происходит - ImGui опрашивает состояние),
    // не попало на нулевые поля. RtGui конструируется по умолчанию → &gui валиден
    // ещё до init; методы, вызываемые до init, просто возвращают безопасные дефолты.
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

    // ── MIDI control (опционально) ────────────────────────────────────────────
    // Полностью опционально: если нет MIDI-бэкенда/устройства, все вызовы
    // становятся no-op. Мапит физические ручки → параметры (0..1), а пэды →
    // разовые действия, с рантайм-режимом Learn, управляемым из GUI. Биндинги
    // сохраняются в midi.json.
    MidiControl midi;
    midi.load("midi.json");
    midi.init();
    midi.register_param("chaos",        [&](float v){ settings.chaos = v; });
    midi.register_param("intensity",    [&](float v){ settings.master_intensity = v; });
    midi.register_param("cut_interval", [&](float v){ settings.cut_interval = 0.05f + v * (2.f - 0.05f); });
    midi.register_param("overlay",      [&](float v){ settings.overlay_intensity = v;
                                                      if (v > 0.01f) settings.fx[(int)FxId::OVERLAYS].enabled = true; });
    midi.register_param("threshold",    [&](float v){ settings.sensitivity = 0.1f + v * (3.f - 0.1f); });
    midi.register_action("audio_toggle",[&](){ auto& a = engine.audio();
                                               if (a.is_running()) a.stop(); else a.start(gui.selected_device()); });
    midi.register_action("blackout",    [&](){ engine.blackout = !engine.blackout; });
    midi.register_action("freeze",      [&](){ engine.freeze   = !engine.freeze;   });
    midi.register_action("next_preset", [&](){ gui.request_next_preset(); });
    midi.register_action("tap",         [&](){ register_tap(&app); });
    app.midi = &midi;
    gui.set_midi(&midi);

    // ── Основной цикл ─────────────────────────────────────────────────────────
    double prev_time = glfwGetTime();
    GLuint display_tex = 0;
    float  fps_accum   = 0.f;
    int    fps_frames  = 0;
    float  fps         = 0.f;
    bool   prev_out_open = false;

    while (!glfwWindowShouldClose(window)) {
        glfwPollEvents();
        midi.poll();

        // Закрытие output-окна через его собственные ESC / X - детектим здесь,
        // чтобы освободить окно из главного потока.
        if (output.consume_close_request()) output.close();

        // Политика единственного vsync: когда output-окно открыто, оно является
        // vsync-мастером (его видит зритель); control-окно тогда свапится БЕЗ
        // vsync, чтобы они не блокировали друг друга и не создавали лишнюю
        // задержку из-за биений. Когда output закрыт, темп задаёт control-окно.
        bool out_open = output.is_open();
        if (out_open != prev_out_open) {
            glfwMakeContextCurrent(window);
            glfwSwapInterval(out_open ? 0 : 1);
            prev_out_open = out_open;
        }

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

        // Старт/стоп аудио из GUI (кнопкой) - клавиатурный путь через Space
        // бьёт напрямую в engine.audio().
        if (gui.want_start()) {
            // Пробрасываем -1, чтобы AudioAnalyzer::start сам выбрал дефолтное
            // устройство платформы (WASAPI на Windows). Лучше для UX, чем молча
            // ничего не делать, если пользователь не выбрал устройство.
            engine.audio().start(gui.selected_device());
        }
        if (gui.want_stop()) engine.audio().stop();

        // Запросы на открытие output-окна из GUI
        if (gui.want_output_open()) {
            output.open(gui.requested_output_monitor());
        }
        if (gui.want_output_close()) output.close();

        // Применяем отложенную загрузку пресета (по клавиатуре)
        gui.apply_pending_preset(settings);

        // Прогоняем цепочку эффектов → текстуру канваса.
        display_tex = engine.process_frame(dt, settings);



        // Рендер control-окна.
        int fb_w, fb_h;
        glfwGetFramebufferSize(window, &fb_w, &fb_h);
        glBindFramebuffer(GL_FRAMEBUFFER, 0);
        glViewport(0, 0, fb_w, fb_h);
        glClearColor(0.f, 0.f, 0.f, 1.f);
        glClear(GL_COLOR_BUFFER_BIT);
        if (app.show_gui) {
            gui.set_fx_bank(app.fx_bank);
            gui.render(settings, fps, display_tex);
        } else {
            // GUI скрыт: рисуем канвас на весь control-window тоже (для VJ
            // с одним монитором, которые используют control-окно как output).
            gui.render_bare(display_tex, fb_w, fb_h);
        }
        glfwSwapBuffers(window);

        // Рендер output-окна (второй монитор). Передаём общий aspect mode,
        // чтобы вписывание canvas→монитор шло по тому же правилу, что и
        // source→canvas; иначе output молча всё letterbox'ит.
        if (output.is_open()) {
            output.render(display_tex, engine.canvas_width(), engine.canvas_height(),
                          settings.aspect_mode);
        }
    }



    midi.save("midi.json");
    output.destroy();
    gui.shutdown();
    engine.destroy();
    glfwDestroyWindow(window);
    glfwTerminate();
    Log::shutdown();
    return 0;
}
