#include "font_loader.h"
#include <cstdio>
#include <filesystem>
#include <vector>

namespace fs = std::filesystem;

namespace FontLoader {

static const ImWchar* full_glyph_ranges() {
    // Латиница + кириллица + распространённая пунктуация. static - потому что
    // ImGui хранит только указатель на массив, он должен жить всё время
    // существования атласа.
    static const ImWchar ranges[] = {
        0x0020, 0x00FF, // базовая латиница + Latin-1 Supplement
        0x0400, 0x052F, // кириллица + Cyrillic Supplement
        0x2000, 0x206F, // общая пунктуация
        0x2070, 0x209F, // над/подстрочные знаки
        0x20A0, 0x20CF, // символы валют
        0x2DE0, 0x2DFF, // Cyrillic Extended-A
        0xA640, 0xA69F, // Cyrillic Extended-B
        0,
    };
    return &ranges[0];
}

static const char* candidates[] = {
#if defined(_WIN32)
    "C:\\Windows\\Fonts\\tahoma.ttf",
    "C:\\Windows\\Fonts\\segoeui.ttf",
    "C:\\Windows\\Fonts\\arial.ttf",
#elif defined(__APPLE__)
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/Library/Fonts/Arial.ttf",
#else
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/liberation-sans/LiberationSans-Regular.ttf",
#endif
    nullptr,
};

ImFont* load_default(float pixel_size) {
    ImGuiIO& io = ImGui::GetIO();
    for (const char** p = candidates; *p; ++p) {
        std::error_code ec;
        if (!fs::exists(*p, ec)) continue;
        ImFontConfig cfg;
        cfg.OversampleH = 2;
        cfg.OversampleV = 1;
        cfg.PixelSnapH  = true;
        ImFont* f = io.Fonts->AddFontFromFileTTF(*p, pixel_size, &cfg, full_glyph_ranges());
        if (f) {
            fprintf(stderr, "[font] loaded %s @ %.0fpx\n", *p, pixel_size);
            return f;
        }
    }
    fprintf(stderr, "[font] WARNING: no system font with Cyrillic found, "
                    "falling back to ImGui default (no Cyrillic glyphs)\n");
    return io.Fonts->AddFontDefault();
}

} // namespace FontLoader
