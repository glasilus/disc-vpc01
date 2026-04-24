#include "preset_manager.h"
#include "../effects/effect_chain.h"
#include <nlohmann/json.hpp>
#include <filesystem>
#include <fstream>
#include <algorithm>
#include <numeric>

namespace fs = std::filesystem;
using json = nlohmann::json;

void PresetManager::scan_folder(const std::string& folder) {
    names_.clear(); paths_.clear();
    if (!fs::exists(folder)) return;
    for (auto& e : fs::directory_iterator(folder)) {
        if (!e.is_regular_file()) continue;
        std::string p = e.path().string();
        std::string n = e.path().stem().string();
        if (e.path().extension() == ".json" &&
            n.rfind("rt_", 0) == 0)
        {
            paths_.push_back(p);
            names_.push_back(n);
        }
    }
    // Sort alphabetically
    std::vector<int> idx(names_.size());
    std::iota(idx.begin(), idx.end(), 0);
    std::sort(idx.begin(), idx.end(), [&](int a, int b){return names_[a]<names_[b];});
    std::vector<std::string> sn, sp;
    for (int i : idx) { sn.push_back(names_[i]); sp.push_back(paths_[i]); }
    names_ = sn; paths_ = sp;
}

int PresetManager::blank_index() const {
    for (int i = 0; i < (int)names_.size(); ++i)
        if (names_[i] == "rt_blank") return i;
    return -1;
}

bool PresetManager::load(const std::string& path, EngineSettings& out) {
    std::ifstream f(path);
    if (!f.is_open()) return false;
    json j;
    try { f >> j; } catch(...) { return false; }

    if (j.contains("chaos"))            out.chaos            = j["chaos"].get<float>();
    if (j.contains("sensitivity"))      out.sensitivity      = j["sensitivity"].get<float>();
    if (j.contains("master_intensity")) out.master_intensity = j["master_intensity"].get<float>();
    if (j.contains("cut_interval"))     out.cut_interval     = j["cut_interval"].get<float>();
    if (j.contains("overlay_intensity"))out.overlay_intensity= j["overlay_intensity"].get<float>();
    if (j.contains("sequential"))       out.sequential       = j["sequential"].get<bool>();
    if (j.contains("ck_mode")) {
        std::string m = j["ck_mode"].get<std::string>();
        if      (m == "none")      out.ck_mode = 0;
        else if (m == "dominant")  out.ck_mode = 1;
        else if (m == "secondary") out.ck_mode = 2;
        else if (m == "manual")    out.ck_mode = 3;
    }
    if (j.contains("ck_tolerance")) out.ck_tolerance = j["ck_tolerance"].get<float>();
    if (j.contains("ck_softness"))  out.ck_softness  = j["ck_softness"].get<float>();
    if (j.contains("ck_r"))         out.ck_r         = j["ck_r"].get<float>();
    if (j.contains("ck_g"))         out.ck_g         = j["ck_g"].get<float>();
    if (j.contains("ck_b"))         out.ck_b         = j["ck_b"].get<float>();

    if (j.contains("fx_state")) {
        auto& fx = j["fx_state"];
        for (int i = 0; i < (int)FxId::COUNT; ++i) {
            const char* key = fx_key((FxId)i);
            if (fx.contains(key))
                out.fx[i].enabled = fx[key].get<bool>();
        }
        // Backward compat: old presets used "fx_rgb" → map to fx_derivwarp
        if (fx.contains("fx_rgb") && !fx.contains("fx_derivwarp"))
            out.fx[(int)FxId::DERIVWARP].enabled = fx["fx_rgb"].get<bool>();
    }
    return true;
}

bool PresetManager::save(const std::string& path, const EngineSettings& s) {
    static const char* ck_modes[] = {"none","dominant","secondary","manual"};
    json j;
    j["chaos"]            = s.chaos;
    j["sensitivity"]      = s.sensitivity;
    j["master_intensity"] = s.master_intensity;
    j["cut_interval"]     = s.cut_interval;
    j["overlay_intensity"]= s.overlay_intensity;
    j["sequential"]       = s.sequential;
    j["ck_mode"]          = ck_modes[std::clamp(s.ck_mode, 0, 3)];
    j["ck_tolerance"]     = s.ck_tolerance;
    j["ck_softness"]      = s.ck_softness;
    j["ck_r"]             = s.ck_r;
    j["ck_g"]             = s.ck_g;
    j["ck_b"]             = s.ck_b;

    json fx;
    for (int i = 0; i < (int)FxId::COUNT; ++i)
        fx[fx_key((FxId)i)] = s.fx[i].enabled;
    j["fx_state"] = fx;

    std::ofstream f(path);
    if (!f.is_open()) return false;
    f << j.dump(2);
    return true;
}
