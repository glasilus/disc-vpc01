#pragma once
#include "../engine/rt_engine.h"
#include <string>
#include <vector>

class PresetManager {
public:
    void scan_folder(const std::string& folder);
    const std::vector<std::string>& names() const { return names_; }
    const std::vector<std::string>& paths() const { return paths_; }

    bool load(const std::string& path, EngineSettings& out);
    bool save(const std::string& path, const EngineSettings& settings);

    // Returns index of rt_blank.json or -1
    int  blank_index() const;

private:
    std::vector<std::string> names_;
    std::vector<std::string> paths_;
};
