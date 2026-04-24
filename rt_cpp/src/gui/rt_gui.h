#pragma once
#include "../engine/rt_engine.h"
#include "../presets/preset_manager.h"
#include <string>

struct GLFWwindow;

class RtGui {
public:
    bool init(GLFWwindow* window, RtEngine* engine, const std::string& presets_folder);
    void render(EngineSettings& settings, float fps, GLuint display_tex = 0);
    void shutdown();

    bool want_start()      { bool v = want_start_; want_start_ = false; return v; }
    bool want_stop()       { bool v = want_stop_;  want_stop_  = false; return v; }
    int  selected_device() const { return selected_device_; }

private:
    void draw_master_panel(EngineSettings& s);
    void draw_effects_panel(EngineSettings& s);
    void draw_video_panel();
    void draw_audio_panel(EngineSettings& s);
    void draw_overlay_panel(EngineSettings& s);
    void draw_presets_panel(EngineSettings& s);
    void draw_video_preview(GLuint tex, int win_w, int win_h);

    RtEngine*      engine_  = nullptr;
    PresetManager  presets_;
    std::string    presets_folder_;
    GLFWwindow*    window_  = nullptr;

    // Preset UI state
    int         preset_idx_   = -1;
    char        save_name_[64] = {};
    bool        show_save_dlg_ = false;

    bool want_start_ = false;
    bool want_stop_  = false;
    bool running_    = false;

    // Audio device list
    std::vector<AudioDevice> devices_;
    int  selected_device_ = -1;
    bool devices_dirty_   = true;
};
