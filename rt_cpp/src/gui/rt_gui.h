#pragma once
#include "../engine/rt_engine.h"
#include "../presets/preset_manager.h"
#include <string>

struct GLFWwindow;
class  MidiControl;

class RtGui {
public:
    bool init(GLFWwindow* window, RtEngine* engine, const std::string& presets_folder);
    void render(EngineSettings& settings, float fps, GLuint display_tex = 0);
    void shutdown();

    // Связь с main, настраивается один раз при старте.
    void set_midi(MidiControl* m) { midi_ = m; }
    void set_fx_bank(int b)       { fx_bank_ = b; }
    // Переход к следующему пресету в списке (дергается из MIDI-действия).
    void request_next_preset();

    bool want_start()      { bool v = want_start_; want_start_ = false; return v; }
    bool want_stop()       { bool v = want_stop_;  want_stop_  = false; return v; }
    // Настоящий индекс устройства в PortAudio для текущего выбранного пункта,
    // либо -1. Индекс в devices_ и индекс PortAudio - не одно и то же, потому
    // что список в GUI может быть отфильтрован.
    int  selected_device() const {
        if (selected_device_ < 0 || selected_device_ >= (int)devices_.size()) return -1;
        return devices_[selected_device_].index;
    }

    // Вызывается из GLFW drop-колбэка (кроссплатформенно). Добавляет видео,
    // а если в перетащенной папке есть изображения - грузит их как оверлеи.
    void handle_drop(int count, const char** paths);

    // Флаги запроса output-окна (разбираются в главном цикле).
    bool want_output_open()          { bool v = want_out_open_;  want_out_open_  = false; return v; }
    bool want_output_close()         { bool v = want_out_close_; want_out_close_ = false; return v; }
    int  requested_output_monitor() const { return requested_monitor_; }

    // Загрузка пресета по нажатию цифровой клавиши. Список пресетов хранит
    // GUI, main просто пробрасывает сюда номер клавиши, а мы применяем его
    // на следующем кадре рендера.
    void request_preset_by_index(int idx) { pending_preset_idx_ = idx; }
    void apply_pending_preset(EngineSettings& s);

    // Отрисовать только текстуру канваса на весь экран, без GUI. Используется,
    // когда пользователь прячет интерфейс по Tab.
    void render_bare(GLuint display_tex, int win_w, int win_h);

private:
    void draw_master_panel(EngineSettings& s);
    void draw_effects_panel(EngineSettings& s);
    void draw_effect_row(EngineSettings& s, int i, int bank_lo, int bank_hi);
    void draw_video_panel();
    void draw_audio_panel(EngineSettings& s);
    void draw_overlay_panel(EngineSettings& s);
    void draw_chroma_panel(EngineSettings& s);
    void draw_presets_panel(EngineSettings& s);
    void draw_video_preview(GLuint tex, int win_w, int win_h);
    void draw_transport(EngineSettings& s, float fps);
    void draw_midi_panel();

    MidiControl* midi_    = nullptr;
    int          fx_bank_ = 0;

    // Tap-tempo из GUI (отдельный от tap по клавиатуре/MIDI в main).
    double       tap_times_[8] = {};
    int          tap_n_        = 0;
    void         gui_tap();

    RtEngine*      engine_  = nullptr;
    PresetManager  presets_;
    std::string    presets_folder_;
    GLFWwindow*    window_  = nullptr;

    // Состояние UI пресетов
    int         preset_idx_   = -1;
    char        save_name_[64] = {};
    bool        show_save_dlg_ = false;

    // Индекс выбранного разрешения канваса (в kCanvasPresets)
    int         canvas_preset_ = 0;

    bool want_start_ = false;
    bool want_stop_  = false;
    bool running_    = false;

    // Список аудиоустройств
    std::vector<AudioDevice> devices_;
    int  selected_device_ = -1;
    bool devices_dirty_   = true;

    // Контролы output-окна
    void draw_output_panel();
    int  requested_monitor_ = 0;
    bool want_out_open_     = false;
    bool want_out_close_    = false;

    // Отложенная загрузка пресета (клавиши 1..9,0)
    int  pending_preset_idx_ = -1;
};
