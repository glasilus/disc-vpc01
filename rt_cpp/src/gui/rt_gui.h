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
    // Real PortAudio device index for the currently selected entry, or -1.
    int  selected_device() const {
        if (selected_device_ < 0 || selected_device_ >= (int)devices_.size()) return -1;
        return devices_[selected_device_].index;
    }

    // Called by GLFW drop callback (cross-platform). Adds videos and, if a
    // dropped folder contains images, loads them as overlays.
    void handle_drop(int count, const char** paths);

    // Output-window request flags (consumed in main loop).
    bool want_output_open()          { bool v = want_out_open_;  want_out_open_  = false; return v; }
    bool want_output_close()         { bool v = want_out_close_; want_out_close_ = false; return v; }
    int  requested_output_monitor() const { return requested_monitor_; }

    // Keyboard-initiated preset load. The GUI owns the preset list; main
    // forwards a number key here and we consume it on the next render pass.
    void request_preset_by_index(int idx) { pending_preset_idx_ = idx; }
    void apply_pending_preset(EngineSettings& s);

    // Draw only the canvas texture fullscreen (no GUI chrome). Used when
    // the user toggles Tab to hide the GUI.
    void render_bare(GLuint display_tex, int win_w, int win_h);

private:
    void draw_master_panel(EngineSettings& s);
    void draw_effects_panel(EngineSettings& s);
    void draw_video_panel();
    void draw_audio_panel(EngineSettings& s);
    void draw_overlay_panel(EngineSettings& s);
    void draw_presets_panel(EngineSettings& s);
    void draw_video_preview(GLuint tex, int win_w, int win_h);
    void draw_transport(EngineSettings& s, float fps);

    RtEngine*      engine_  = nullptr;
    PresetManager  presets_;
    std::string    presets_folder_;
    GLFWwindow*    window_  = nullptr;

    // Preset UI state
    int         preset_idx_   = -1;
    char        save_name_[64] = {};
    bool        show_save_dlg_ = false;

    // Canvas resolution preset index (into kCanvasPresets)
    int         canvas_preset_ = 0;

    bool want_start_ = false;
    bool want_stop_  = false;
    bool running_    = false;

    // Audio device list
    std::vector<AudioDevice> devices_;
    int  selected_device_ = -1;
    bool devices_dirty_   = true;

    // Output-window controls
    void draw_output_panel();
    int  requested_monitor_ = 0;
    bool want_out_open_     = false;
    bool want_out_close_    = false;

    // Pending preset load (keyboard shortcut 1..9,0)
    int  pending_preset_idx_ = -1;
};
