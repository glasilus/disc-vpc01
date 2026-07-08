#pragma once
#include <glad/glad.h>
#include <filesystem>
#include <string>
#include <vector>

struct OverlayEntry {
    GLuint tex    = 0;
    int    width  = 0;
    int    height = 0;
};

// Режим chroma key (соответствует Python OverlayManager)
enum class ChromaMode { None, Dominant, Secondary, Manual };

struct ChromaKeyParams {
    ChromaMode mode      = ChromaMode::None;
    float      tolerance = 30.f;   // допуск по оттенку (hue), в градусах
    float      softness  = 5.f;
    float      r = 0.f, g = 255.f, b = 0.f;  // цвет для ручного режима
    bool       gate_fx   = false;
    int        gate_mode = 0;
};

class OverlayManager {
public:
    ~OverlayManager();

    void load_folder(const std::string& folder_path);
    void clear();
    bool   empty() const { return entries_.empty(); }
    size_t size()  const { return entries_.size(); }

    // Возвращает случайный оверлей (или nullptr, если пусто)
    const OverlayEntry* random_entry() const;

    const ChromaKeyParams& chroma() const { return chroma_; }
    ChromaKeyParams&       chroma()       { return chroma_; }

private:
    bool load_image(const std::filesystem::path& path);

    std::vector<OverlayEntry> entries_;
    ChromaKeyParams           chroma_;
};
