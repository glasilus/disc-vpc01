#pragma once
#include <imgui.h>

namespace FontLoader {

// Load a system font that includes Cyrillic glyphs. Returns the font pointer
// (already added to ImGui's atlas; safe to ignore the return value - ImGui
// uses it as default automatically).
//
// Lookup order (first existing file wins):
//   Windows : C:\Windows\Fonts\tahoma.ttf, segoeui.ttf, arial.ttf
//   macOS   : /System/Library/Fonts/Helvetica.ttc, /Library/Fonts/Arial.ttf
//   Linux   : /usr/share/fonts/truetype/dejavu/DejaVuSans.ttf,
//             /usr/share/fonts/TTF/DejaVuSans.ttf,
//             /usr/share/fonts/dejavu/DejaVuSans.ttf,
//             /usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf
//
// If nothing is found, falls back to ImGui default (no Cyrillic - logs a
// warning to stderr).
ImFont* load_default(float pixel_size = 14.f);

} // namespace FontLoader
