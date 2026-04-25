#include "rt_gui.h"
#include "theme.h"
#include "font_loader.h"
#include "win95.h"
#include <imgui.h>
#include <cfloat>
#include <imgui_impl_glfw.h>
#include <imgui_impl_opengl3.h>
#include <GLFW/glfw3.h>
#include <cstdio>
#include <algorithm>
#include <filesystem>

namespace fs = std::filesystem;

// ── File dialog (Windows-native, UTF-8) ──────────────────────────────────────
// On Linux/macOS the primary mechanism is drag-and-drop into the window
// (GLFW's drop callback already delivers UTF-8 paths cross-platform).
#ifdef _WIN32
#define NOMINMAX
#include <windows.h>
#include <commdlg.h>
#include <shlobj.h>

static std::string wide_to_utf8(const wchar_t* w, int wlen = -1) {
    if (!w) return {};
    int len = WideCharToMultiByte(CP_UTF8, 0, w, wlen, nullptr, 0, nullptr, nullptr);
    if (len <= 0) return {};
    std::string s(wlen < 0 ? len - 1 : len, '\0');
    WideCharToMultiByte(CP_UTF8, 0, w, wlen, s.data(), len, nullptr, nullptr);
    return s;
}

static std::vector<std::string> open_file_dialog_multi(const wchar_t* filter) {
    std::vector<std::string> result;
    wchar_t buf[8192] = {};
    OPENFILENAMEW ofn{};
    ofn.lStructSize = sizeof(ofn);
    ofn.lpstrFilter = filter;
    ofn.lpstrFile   = buf;
    ofn.nMaxFile    = sizeof(buf) / sizeof(wchar_t);
    ofn.Flags       = OFN_FILEMUSTEXIST | OFN_ALLOWMULTISELECT | OFN_EXPLORER;
    if (!GetOpenFileNameW(&ofn)) return result;
    // Multi-select result: dir\0file1\0file2\0\0
    wchar_t* p = buf;
    std::wstring wdir = p; p += wdir.size() + 1;
    std::string dir = wide_to_utf8(wdir.c_str(), (int)wdir.size());
    if (*p == L'\0') { result.push_back(dir); return result; }
    while (*p) {
        std::wstring wf = p; p += wf.size() + 1;
        std::string f = wide_to_utf8(wf.c_str(), (int)wf.size());
        result.push_back(dir + "\\" + f);
    }
    return result;
}

static std::string open_folder_dialog() {
    wchar_t buf[MAX_PATH] = {};
    BROWSEINFOW bi{};
    bi.lpszTitle = L"Select Folder";
    bi.ulFlags   = BIF_RETURNONLYFSDIRS | BIF_NEWDIALOGSTYLE;
    LPITEMIDLIST pidl = SHBrowseForFolderW(&bi);
    if (!pidl) return {};
    SHGetPathFromIDListW(pidl, buf);
    CoTaskMemFree(pidl);
    return wide_to_utf8(buf);
}
#else
static std::vector<std::string> open_file_dialog_multi(const wchar_t*) { return {}; }
static std::string open_folder_dialog() { return {}; }
#endif

// ── Static drop callback bridge (GLFW gives us a window pointer, not `this`) ─
static RtGui* g_drop_owner = nullptr;
static void drop_callback(GLFWwindow* /*w*/, int count, const char** paths) {
    if (!g_drop_owner) return;
    g_drop_owner->handle_drop(count, paths);
}

bool RtGui::init(GLFWwindow* window, RtEngine* engine, const std::string& presets_folder) {
    window_         = window;
    engine_         = engine;
    presets_folder_ = presets_folder;

    IMGUI_CHECKVERSION();
    ImGui::CreateContext();
    ImGuiIO& io = ImGui::GetIO();
    io.IniFilename = nullptr;

    // Load a system font with full Cyrillic glyph coverage BEFORE the GL backend
    // builds the atlas — otherwise Cyrillic shows as '?'.
    FontLoader::load_default(14.f);

    ImGui_ImplGlfw_InitForOpenGL(window, true);
    ImGui_ImplOpenGL3_Init("#version 330");

    Theme::apply_win95();

    // Cross-platform file ingest: drag-and-drop. GLFW delivers UTF-8 paths on
    // Win/macOS/Linux, so we don't need any per-OS dialog code for the common
    // case.
    g_drop_owner = this;
    glfwSetDropCallback(window, drop_callback);

    presets_.scan_folder(presets_folder_);
    int bi = presets_.blank_index();
    if (bi >= 0) preset_idx_ = bi;

    return true;
}

void RtGui::shutdown() {
    if (g_drop_owner == this) g_drop_owner = nullptr;
    ImGui_ImplOpenGL3_Shutdown();
    ImGui_ImplGlfw_Shutdown();
    ImGui::DestroyContext();
}

static bool has_ext(const std::string& s, std::initializer_list<const char*> exts) {
    auto pos = s.find_last_of('.');
    if (pos == std::string::npos) return false;
    std::string e = s.substr(pos);
    for (char& c : e) c = (char)std::tolower((unsigned char)c);
    for (auto* x : exts) if (e == x) return true;
    return false;
}

void RtGui::handle_drop(int count, const char** paths) {
    static const std::initializer_list<const char*> kVideoExts =
        {".mp4",".avi",".mov",".mkv",".mpg",".mpeg",".wmv",".webm",".m4v",".flv"};
    static const std::initializer_list<const char*> kImageExts =
        {".png",".jpg",".jpeg",".bmp",".gif",".tga"};

    // Filesystem iteration over dropped paths may throw on permission errors
    // or invalid paths, especially on Windows. A throw inside a GLFW callback
    // will tear down the whole process — wrap the entire scan.
    try {
        bool any_image_folder = false;
        for (int i = 0; i < count; ++i) {
            std::error_code ec;
            fs::path p = fs::u8path(paths[i]);
            if (fs::is_directory(p, ec)) {
                bool has_image = false;
                // The non-throwing iterator ctor still may throw on
                // operator++; catch it too.
                try {
                    for (auto it = fs::recursive_directory_iterator(p, ec);
                         it != fs::recursive_directory_iterator();
                         it.increment(ec)) {
                        if (ec) break;
                        if (!it->is_regular_file(ec)) continue;
                        // path.u8string() guarantees UTF-8 output on Windows;
                        // .string() would return ANSI and corrupt non-ASCII.
                        auto u8 = it->path().u8string();
                        std::string sp(u8.begin(), u8.end());
                        if (has_ext(sp, kVideoExts))      engine_->video().add_source(sp);
                        else if (has_ext(sp, kImageExts)) has_image = true;
                    }
                } catch (const std::exception& e) {
                    fprintf(stderr, "[drop] scan error: %s\n", e.what());
                }
                if (has_image && !any_image_folder) {
                    auto u8 = p.u8string();
                    engine_->overlays().load_folder(std::string(u8.begin(), u8.end()));
                    any_image_folder = true;
                }
            } else if (fs::is_regular_file(p, ec)) {
                auto u8 = p.u8string();
                std::string sp(u8.begin(), u8.end());
                if (has_ext(sp, kVideoExts)) engine_->video().add_source(sp);
            }
        }
    } catch (const std::exception& e) {
        fprintf(stderr, "[drop] fatal: %s\n", e.what());
    }
}

void RtGui::render(EngineSettings& settings, float fps, GLuint display_tex) {
    ImGui_ImplOpenGL3_NewFrame();
    ImGui_ImplGlfw_NewFrame();
    ImGui::NewFrame();

    int win_w, win_h;
    glfwGetFramebufferSize(window_, &win_w, &win_h);

    // Full-window dockspace-style layout
    ImGui::SetNextWindowPos({0, 0});
    ImGui::SetNextWindowSize({(float)win_w, (float)win_h});
    ImGui::Begin("##root", nullptr,
        ImGuiWindowFlags_NoTitleBar | ImGuiWindowFlags_NoResize |
        ImGuiWindowFlags_NoMove     | ImGuiWindowFlags_NoBringToFrontOnFocus |
        ImGuiWindowFlags_NoScrollbar);

    // ── Top strip: compact preview + audio meters + transport ────────────────
    // The preview is a THUMBNAIL now (the primary output surface is the
    // dedicated OutputWindow on a chosen monitor). This frees the bulk of
    // the control window for controls, which is what a VJ actually needs on
    // their control surface.
    const float kPrevH = 180.f;
    const float kPrevW = 320.f;
    ImGui::BeginChild("##prev", {kPrevW, kPrevH}, false);
    draw_video_preview(display_tex, (int)kPrevW, (int)kPrevH);
    ImGui::EndChild();

    ImGui::SameLine();
    ImGui::BeginChild("##topbar", {win_w - kPrevW - 16.f, kPrevH}, false);
    draw_transport(settings, fps);
    ImGui::EndChild();

    ImGui::Separator();

    // ── Control panels (4 columns) ───────────────────────────────────────────
    float col_w = win_w * 0.25f;
    float ctrl_h = win_h - kPrevH - 40.f;

    ImGui::BeginChild("##master",  {col_w, ctrl_h}, true);
    Win95::title_bar("Master");
    draw_master_panel(settings);
    ImGui::EndChild();

    ImGui::SameLine();
    ImGui::BeginChild("##effects", {col_w, ctrl_h}, true);
    Win95::title_bar("Effects");
    draw_effects_panel(settings);
    ImGui::EndChild();

    ImGui::SameLine();
    ImGui::BeginChild("##vidaudio", {col_w, ctrl_h}, true);
    Win95::title_bar("Video / Audio");
    draw_video_panel();
    ImGui::Separator();
    draw_audio_panel(settings);
    ImGui::EndChild();

    ImGui::SameLine();
    ImGui::BeginChild("##ovpreset", {col_w - 4, ctrl_h}, true);
    Win95::title_bar("Overlays / Presets / Output");
    draw_overlay_panel(settings);
    ImGui::Separator();
    draw_presets_panel(settings);
    ImGui::Separator();
    draw_output_panel();
    ImGui::EndChild();

    ImGui::End();

    ImGui::Render();
    ImGui_ImplOpenGL3_RenderDrawData(ImGui::GetDrawData());
}

void RtGui::draw_video_preview(GLuint tex, int win_w, int win_h) {
    // Preserve the canvas aspect ratio inside the allocated preview rectangle
    // (letterbox / pillarbox with black borders).
    int cw = engine_->canvas_width();
    int ch = engine_->canvas_height();
    float cA = (cw > 0 && ch > 0) ? (float)cw / (float)ch : 16.f / 9.f;
    float winA = (float)win_w / (float)std::max(1, win_h);

    float img_w, img_h;
    if (winA > cA) { img_h = (float)win_h;          img_w = img_h * cA; }
    else           { img_w = (float)win_w;          img_h = img_w / cA; }

    ImVec2 origin = ImGui::GetCursorScreenPos();
    ImGui::Dummy({(float)win_w, (float)win_h});
    ImDrawList* dl = ImGui::GetWindowDrawList();
    dl->AddRectFilled(origin, {origin.x + win_w, origin.y + win_h}, IM_COL32(0,0,0,255));

    ImVec2 img_pos = {origin.x + (win_w - img_w) * 0.5f,
                      origin.y + (win_h - img_h) * 0.5f};
    if (tex != 0) {
        dl->AddImage((ImTextureID)(intptr_t)tex,
                     img_pos, {img_pos.x + img_w, img_pos.y + img_h},
                     {0, 1}, {1, 0});
    } else {
        dl->AddText({origin.x + win_w * 0.5f - 40, origin.y + win_h * 0.5f},
                    IM_COL32(128,128,128,255), "No video loaded");
    }
}

void RtGui::draw_transport(EngineSettings& s, float fps) {
    // Left: big START/STOP button + toggles.
    if (!running_) {
        if (Win95::button("START", 110.f, 32.f)) { want_start_ = true; running_ = true; }
    } else {
        if (Win95::button("STOP",  110.f, 32.f)) { want_stop_  = true; running_ = false; }
    }
    ImGui::SameLine();
    ImGui::BeginGroup();
    ImGui::Checkbox("Freeze##tr",     &engine_->freeze);
    ImGui::Checkbox("Blackout##tr",   &engine_->blackout);
    ImGui::EndGroup();
    ImGui::SameLine();
    ImGui::BeginGroup();
    ImGui::Checkbox("Sequential##tr", &s.sequential);
    ImGui::Text("FPS: %.0f", fps);
    ImGui::EndGroup();

    ImGui::Separator();

    // Right: live audio meters, segment readout, device line.
    AudioStats st = engine_->audio().get_stats();
    float vals[3]     = { st.bass, st.mid, st.treble };
    const char* lbl[3]= {"Bass", "Mid ", "Treb"};
    for (int i = 0; i < 3; ++i) {
        float v = std::min(vals[i] / 0.3f, 1.f);
        ImGui::TextUnformatted(lbl[i]); ImGui::SameLine();
        ImGui::ProgressBar(v, {-FLT_MIN, 10}, "");
    }
    ImGui::Text("Segment: %s   %s",
        segment_name(engine_->current_segment().type),
        st.beat ? "BEAT" : "----");

    const char* dev_label = (selected_device_ >= 0 &&
                             selected_device_ < (int)devices_.size())
                          ? devices_[selected_device_].name.c_str()
                          : "(no device selected — will auto-pick on Start)";
    ImGui::TextDisabled("Device: %s", dev_label);
}

void RtGui::draw_master_panel(EngineSettings& s) {
    ImGui::TextUnformatted("MASTER");
    ImGui::Separator();
    ImGui::SliderFloat("Chaos##m",     &s.chaos,            0.f, 1.f, "%.2f");
    ImGui::SliderFloat("Intensity##m", &s.master_intensity, 0.f, 1.f, "%.2f");
    ImGui::SliderFloat("Threshold##m", &s.sensitivity,      0.1f,3.f, "%.2f");
    ImGui::SliderFloat("Cut Interval", &s.cut_interval,     0.05f,2.f,"%.2f");

    ImGui::Separator();
    ImGui::TextUnformatted("CANVAS");

    // Canvas resolution preset
    int cur_preset = canvas_preset_;
    const char* cur_label = kCanvasPresets[cur_preset].label;
    if (ImGui::BeginCombo("Resolution", cur_label)) {
        for (int i = 0; i < kCanvasPresetCount; ++i) {
            bool sel = (i == cur_preset);
            if (ImGui::Selectable(kCanvasPresets[i].label, sel)) {
                canvas_preset_ = i;
                engine_->set_canvas_size(kCanvasPresets[i].width,
                                         kCanvasPresets[i].height);
            }
        }
        ImGui::EndCombo();
    }

    // Aspect fit mode
    static const char* kAspectLabels[] = {"Contain", "Cover", "Stretch", "Native 1:1"};
    ImGui::Combo("Aspect", &s.aspect_mode, kAspectLabels, 4);
}

void RtGui::draw_effects_panel(EngineSettings& s) {
    ImGui::TextUnformatted("EFFECTS");
    ImGui::Separator();
    static const char* labels[] = {
        "Deriv Warp","Flash","Stutter","Pixel Sort",
        "Ghost Trails","Scanlines","Bitcrush","Block Glitch",
        "Negative","Color Bleed","Interlace","Bad Signal",
        "Zoom Glitch","Mosaic","Phase Shift","Dither",
        "Feedback","Temporal RGB","Waveshaper","Overlays",
        "Vortex","Fractal Noise","Self Displace","ASCII"
    };
    for (int i = 0; i < (int)FxId::COUNT; ++i) {
        ImGui::Checkbox(labels[i], &s.fx[i].enabled);
        if (i % 2 == 0 && i + 1 < (int)FxId::COUNT) ImGui::SameLine(130);
    }
}

void RtGui::draw_video_panel() {
    ImGui::TextUnformatted("VIDEO FILES");
    ImGui::Separator();

    if (ImGui::Button("Add Videos")) {
        auto files = open_file_dialog_multi(
            L"Video Files\0*.mp4;*.avi;*.mov;*.mkv;*.mpg;*.mpeg;*.wmv;*.webm\0All\0*.*\0\0");
        for (auto& f : files)
            engine_->video().add_source(f);
    }
    ImGui::SameLine();
    if (ImGui::Button("Clear")) engine_->video().clear();
    ImGui::TextDisabled("(drag files/folders into window)");

    const auto& paths = engine_->video().paths();
    ImGui::BeginChild("##vlist", {0, 80}, false);
    for (auto& p : paths) {
        // u8string() returns UTF-8 even on Windows (where .string() gives
        // ANSI and would render Cyrillic as mojibake inside ImGui).
        auto u8 = fs::path(p).filename().u8string();
        std::string name(u8.begin(), u8.end());
        ImGui::Selectable(name.c_str());
    }
    ImGui::EndChild();
}

void RtGui::draw_audio_panel(EngineSettings& s) {
    ImGui::TextUnformatted("AUDIO");
    ImGui::Separator();

    if (devices_dirty_) {
        devices_ = engine_->audio().enumerate_devices();
        devices_dirty_ = false;
    }

    if (ImGui::Button("Refresh##dev")) devices_dirty_ = true;

    // Device dropdown
    const char* preview = (selected_device_ >= 0 && selected_device_ < (int)devices_.size())
        ? devices_[selected_device_].name.c_str() : "(none)";
    if (ImGui::BeginCombo("Device", preview)) {
        for (int i = 0; i < (int)devices_.size(); ++i) {
            bool sel = (selected_device_ == i);
            if (ImGui::Selectable(devices_[i].name.c_str(), sel))
                selected_device_ = i;
        }
        ImGui::EndCombo();
    }

    // Audio meters
    AudioStats st = engine_->audio().get_stats();
    float vals[3] = { st.bass, st.mid, st.treble };
    const char* labels[3] = {"B","M","T"};
    for (int i = 0; i < 3; ++i) {
        float v = std::min(vals[i] / 0.3f, 1.f);
        ImGui::ProgressBar(v, {30, 12}, ""); ImGui::SameLine();
        ImGui::TextUnformatted(labels[i]);   ImGui::SameLine();
    }
    ImGui::NewLine();
    if (st.beat) ImGui::TextColored({1,0.8f,0,1}, "BEAT");
    else         ImGui::TextDisabled("----");
}

void RtGui::draw_overlay_panel(EngineSettings& s) {
    ImGui::TextUnformatted("OVERLAYS");
    ImGui::Separator();

    if (ImGui::Button("Load Folder##ov")) {
        std::string folder = open_folder_dialog();
        if (!folder.empty()) engine_->overlays().load_folder(folder);
    }
    ImGui::SliderFloat("OvIntensity", &s.overlay_intensity, 0.f, 1.f, "%.2f");

    static const char* ck_modes[] = {"None","Dominant","Secondary","Manual"};
    ImGui::Combo("Chroma Key", &s.ck_mode, ck_modes, 4);
    if (s.ck_mode != 0) {
        ImGui::SliderFloat("Tolerance",  &s.ck_tolerance, 0.f, 90.f, "%.1f");
        ImGui::SliderFloat("Softness##ck",&s.ck_softness,  0.f, 30.f, "%.1f");
        if (s.ck_mode == 3) {
            ImGui::ColorEdit3("Key Color", &s.ck_r);
        }
    }
}

void RtGui::draw_output_panel() {
    ImGui::TextUnformatted("OUTPUT");
    ImGui::Separator();

    int mon_count = 0;
    GLFWmonitor** monitors = glfwGetMonitors(&mon_count);
    if (mon_count <= 0) {
        ImGui::TextDisabled("(no monitors)");
        return;
    }

    // Clamp stored selection
    if (requested_monitor_ >= mon_count) requested_monitor_ = 0;

    // Build a human-readable label for each monitor
    char preview[128];
    const GLFWvidmode* mode = glfwGetVideoMode(monitors[requested_monitor_]);
    const char* mname = glfwGetMonitorName(monitors[requested_monitor_]);
    std::snprintf(preview, sizeof(preview), "%d: %s %dx%d",
                  requested_monitor_,
                  mname ? mname : "?",
                  mode ? mode->width  : 0,
                  mode ? mode->height : 0);
    if (ImGui::BeginCombo("Monitor", preview)) {
        for (int i = 0; i < mon_count; ++i) {
            char label[128];
            const GLFWvidmode* m = glfwGetVideoMode(monitors[i]);
            const char* n = glfwGetMonitorName(monitors[i]);
            std::snprintf(label, sizeof(label), "%d: %s %dx%d",
                          i, n ? n : "?",
                          m ? m->width : 0, m ? m->height : 0);
            bool sel = (i == requested_monitor_);
            if (ImGui::Selectable(label, sel)) requested_monitor_ = i;
        }
        ImGui::EndCombo();
    }

    if (ImGui::Button("Open Output##ow"))  want_out_open_  = true;
    ImGui::SameLine();
    if (ImGui::Button("Close Output##ow")) want_out_close_ = true;
    ImGui::TextDisabled("(Tab=hide GUI  F11=fs  Esc=close)");
}

void RtGui::apply_pending_preset(EngineSettings& s) {
    if (pending_preset_idx_ < 0) return;
    const auto& paths = presets_.paths();
    if (pending_preset_idx_ < (int)paths.size()) {
        presets_.load(paths[pending_preset_idx_], s);
        preset_idx_ = pending_preset_idx_;
    }
    pending_preset_idx_ = -1;
}

void RtGui::render_bare(GLuint display_tex, int win_w, int win_h) {
    // Even in bare mode we need to run an ImGui frame (otherwise the GLFW
    // callbacks installed by imgui_impl_glfw can assert on missing state).
    // We draw a transparent fullscreen window whose sole content is the
    // canvas image — no chrome, no buttons.
    ImGui_ImplOpenGL3_NewFrame();
    ImGui_ImplGlfw_NewFrame();
    ImGui::NewFrame();
    ImGui::SetNextWindowPos({0, 0});
    ImGui::SetNextWindowSize({(float)win_w, (float)win_h});
    ImGui::PushStyleVar(ImGuiStyleVar_WindowPadding, {0,0});
    ImGui::PushStyleColor(ImGuiCol_WindowBg, {0,0,0,1});
    ImGui::Begin("##bare", nullptr,
        ImGuiWindowFlags_NoTitleBar | ImGuiWindowFlags_NoResize |
        ImGuiWindowFlags_NoMove     | ImGuiWindowFlags_NoBringToFrontOnFocus |
        ImGuiWindowFlags_NoScrollbar| ImGuiWindowFlags_NoDecoration);
    draw_video_preview(display_tex, win_w, win_h);
    ImGui::End();
    ImGui::PopStyleColor();
    ImGui::PopStyleVar();
    ImGui::Render();
    ImGui_ImplOpenGL3_RenderDrawData(ImGui::GetDrawData());
}

void RtGui::draw_presets_panel(EngineSettings& s) {
    ImGui::TextUnformatted("PRESETS");
    ImGui::Separator();

    if (ImGui::Button("Refresh##pr")) presets_.scan_folder(presets_folder_);

    const auto& names = presets_.names();
    const char* preview = (preset_idx_ >= 0 && preset_idx_ < (int)names.size())
        ? names[preset_idx_].c_str() : "(none)";
    if (ImGui::BeginCombo("##preset_combo", preview)) {
        for (int i = 0; i < (int)names.size(); ++i) {
            bool sel = (preset_idx_ == i);
            if (ImGui::Selectable(names[i].c_str(), sel)) preset_idx_ = i;
        }
        ImGui::EndCombo();
    }

    if (ImGui::Button("Load") && preset_idx_ >= 0 && preset_idx_ < (int)presets_.paths().size())
        presets_.load(presets_.paths()[preset_idx_], s);

    ImGui::SameLine();
    if (ImGui::Button("Save As...")) show_save_dlg_ = !show_save_dlg_;

    if (show_save_dlg_) {
        ImGui::InputText("Name", save_name_, sizeof(save_name_));
        if (ImGui::Button("Save##confirm") && save_name_[0]) {
            std::string p = presets_folder_ + "/rt_" + save_name_ + ".json";
            presets_.save(p, s);
            presets_.scan_folder(presets_folder_);
            show_save_dlg_ = false;
        }
    }
}
