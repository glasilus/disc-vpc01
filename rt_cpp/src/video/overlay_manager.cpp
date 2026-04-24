#include "overlay_manager.h"
#include <cstdlib>
#include <filesystem>

#define STB_IMAGE_IMPLEMENTATION
#include <stb_image.h>

namespace fs = std::filesystem;

OverlayManager::~OverlayManager() { clear(); }

void OverlayManager::clear() {
    for (auto& e : entries_)
        if (e.tex) glDeleteTextures(1, &e.tex);
    entries_.clear();
}

void OverlayManager::load_folder(const std::string& folder_path) {
    clear();
    if (!fs::exists(folder_path)) return;

    static const std::vector<std::string> exts = {".png",".jpg",".jpeg",".PNG",".JPG",".JPEG"};
    for (auto& entry : fs::directory_iterator(folder_path)) {
        if (!entry.is_regular_file()) continue;
        std::string ext = entry.path().extension().string();
        bool ok = false;
        for (auto& e : exts) if (ext == e) { ok = true; break; }
        if (ok) load_image(entry.path().string());
    }
}

bool OverlayManager::load_image(const std::string& path) {
    int w, h, ch;
    stbi_set_flip_vertically_on_load(0);
    uint8_t* data = stbi_load(path.c_str(), &w, &h, &ch, 4);
    if (!data) return false;

    OverlayEntry e;
    e.width  = w;
    e.height = h;
    glGenTextures(1, &e.tex);
    glBindTexture(GL_TEXTURE_2D, e.tex);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, w, h, 0, GL_RGBA, GL_UNSIGNED_BYTE, data);
    glBindTexture(GL_TEXTURE_2D, 0);
    stbi_image_free(data);

    entries_.push_back(e);
    return true;
}

const OverlayEntry* OverlayManager::random_entry() const {
    if (entries_.empty()) return nullptr;
    return &entries_[rand() % entries_.size()];
}
