#include "overlay_manager.h"
#include <cstdio>
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

// stb_image's stbi_load() goes through fopen(), which on Windows interprets
// the path as ANSI — Cyrillic / non-ASCII paths fail to open. Open the file
// ourselves with the wide-char API and hand the FILE* to stb.
static FILE* fopen_utf8(const fs::path& p) {
#ifdef _WIN32
    FILE* fp = nullptr;
    if (_wfopen_s(&fp, p.wstring().c_str(), L"rb") != 0) return nullptr;
    return fp;
#else
    return std::fopen(p.string().c_str(), "rb");
#endif
}

void OverlayManager::load_folder(const std::string& folder_path_utf8) {
    clear();
    // u8path: treat the std::string as UTF-8 (matches how the GUI hands paths
    // through). Plain fs::path(string) on Windows assumes ANSI and corrupts
    // Cyrillic.
    fs::path root = fs::u8path(folder_path_utf8);
    std::error_code ec;
    if (!fs::exists(root, ec) || !fs::is_directory(root, ec)) return;

    static const std::vector<std::string> exts = {
        ".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tga", ".webp",
        ".PNG", ".JPG", ".JPEG", ".BMP", ".GIF", ".TGA", ".WEBP",
    };

    try {
        for (auto it = fs::recursive_directory_iterator(root, ec);
             it != fs::recursive_directory_iterator();
             it.increment(ec)) {
            if (ec) break;
            if (!it->is_regular_file(ec)) continue;
            std::string ext = it->path().extension().string();
            bool ok = false;
            for (auto& e : exts) if (ext == e) { ok = true; break; }
            if (ok) load_image(it->path());
        }
    } catch (const std::exception& e) {
        fprintf(stderr, "[overlay] scan error: %s\n", e.what());
    }
    fprintf(stderr, "[overlay] loaded %zu image(s) from %s\n",
            entries_.size(), folder_path_utf8.c_str());
}

bool OverlayManager::load_image(const fs::path& path) {
    FILE* fp = fopen_utf8(path);
    if (!fp) {
        fprintf(stderr, "[overlay] cannot open %s\n", path.u8string().c_str());
        return false;
    }
    int w = 0, h = 0, ch = 0;
    stbi_set_flip_vertically_on_load(0);
    uint8_t* data = stbi_load_from_file(fp, &w, &h, &ch, 4);
    std::fclose(fp);
    if (!data) {
        fprintf(stderr, "[overlay] decode failed: %s (%s)\n",
                path.u8string().c_str(), stbi_failure_reason());
        return false;
    }

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
