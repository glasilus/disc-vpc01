#pragma once
#include <imgui.h>

// Classic Windows 95 widget helpers: 3D beveled buttons, sunken frames,
// raised panels, and blue-gradient title bars. These complement Theme's
// color palette - without the 3D bevels the flat colors look dated but
// not authentic. A real Win95 button has, in order from outside in:
//   - 1 px black outer border
//   - 1 px dark-gray shadow on the bottom + right
//   - 1 px white highlight on the top + left
//   - the gray face fill
// When pressed, the highlight and shadow swap so the button appears sunken.
namespace Win95 {

// Colors match Theme::apply_win95()'s palette.
constexpr ImU32 kFace      = IM_COL32(192, 192, 192, 255);
constexpr ImU32 kFacePress = IM_COL32(160, 160, 160, 255);
constexpr ImU32 kHighlight = IM_COL32(255, 255, 255, 255);
constexpr ImU32 kShadow    = IM_COL32(128, 128, 128, 255);
constexpr ImU32 kOuter     = IM_COL32(  0,   0,   0, 255);
constexpr ImU32 kTitleBg   = IM_COL32(  0,   0, 128, 255);
constexpr ImU32 kTitleFg   = IM_COL32(255, 255, 255, 255);

// Raised bevel (button face, dialog panel).
void draw_raised(ImDrawList* dl, ImVec2 a, ImVec2 b, ImU32 face = kFace);
// Sunken bevel (input field, inset frame).
void draw_sunken(ImDrawList* dl, ImVec2 a, ImVec2 b, ImU32 face = kFace);

// 3D-beveled button. Returns true on click. Width 0 = auto (text + padding).
bool button(const char* label, float width = 0.f, float height = 0.f);

// Classic blue title bar strip at the top of a panel. Call just after
// BeginChild(), before other widgets. Height ~18px.
void title_bar(const char* text);

} // namespace Win95
