#pragma once
#include <imgui.h>

// Хелперы для классических виджетов Windows 95: кнопки с 3D-фаской, вдавленные
// рамки, выпуклые панели, синие title bar. Дополняют палитру из Theme - без
// фасок плоские цвета выглядят просто устаревшими, а не аутентичным Win95.
// У настоящей кнопки Win95, если смотреть снаружи внутрь:
//   - 1px чёрная внешняя рамка
//   - 1px тёмно-серая тень снизу и справа
//   - 1px белый highlight сверху и слева
//   - серая заливка
// При нажатии highlight и тень меняются местами - кнопка выглядит вдавленной.
namespace Win95 {

// Цвета совпадают с палитрой из Theme::apply_win95().
constexpr ImU32 kFace      = IM_COL32(192, 192, 192, 255);
constexpr ImU32 kFacePress = IM_COL32(160, 160, 160, 255);
constexpr ImU32 kHighlight = IM_COL32(255, 255, 255, 255);
constexpr ImU32 kShadow    = IM_COL32(128, 128, 128, 255);
constexpr ImU32 kOuter     = IM_COL32(  0,   0,   0, 255);
constexpr ImU32 kTitleBg   = IM_COL32(  0,   0, 128, 255);
constexpr ImU32 kTitleFg   = IM_COL32(255, 255, 255, 255);

// Выпуклая фаска (лицевая сторона кнопки, панель диалога).
void draw_raised(ImDrawList* dl, ImVec2 a, ImVec2 b, ImU32 face = kFace);
// Вдавленная фаска (поле ввода, врезанная рамка).
void draw_sunken(ImDrawList* dl, ImVec2 a, ImVec2 b, ImU32 face = kFace);

// Кнопка с 3D-фаской. true при клике. width/height = 0 -> размер по тексту.
bool button(const char* label, float width = 0.f, float height = 0.f);

// Классическая синяя полоса title bar сверху панели. Вызывать сразу после
// BeginChild(), до остальных виджетов. Высота ~18px.
void title_bar(const char* text);

} // namespace Win95
