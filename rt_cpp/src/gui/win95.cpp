#include "win95.h"
#include <cstring>

namespace Win95 {

void draw_raised(ImDrawList* dl, ImVec2 a, ImVec2 b, ImU32 face) {
    dl->AddRectFilled(a, b, face);
    // внешняя чёрная рамка 1px
    dl->AddRect(a, b, kOuter, 0.f, 0, 1.f);
    // highlight сверху и слева (внутренний)
    dl->AddLine({a.x + 1, a.y + 1}, {b.x - 2, a.y + 1}, kHighlight);
    dl->AddLine({a.x + 1, a.y + 1}, {a.x + 1, b.y - 2}, kHighlight);
    // тень снизу и справа (внутренняя)
    dl->AddLine({a.x + 1, b.y - 2}, {b.x - 2, b.y - 2}, kShadow);
    dl->AddLine({b.x - 2, a.y + 1}, {b.x - 2, b.y - 2}, kShadow);
}

void draw_sunken(ImDrawList* dl, ImVec2 a, ImVec2 b, ImU32 face) {
    dl->AddRectFilled(a, b, face);
    dl->AddRect(a, b, kOuter, 0.f, 0, 1.f);
    // тень сверху и слева (эффект вдавленности)
    dl->AddLine({a.x + 1, a.y + 1}, {b.x - 2, a.y + 1}, kShadow);
    dl->AddLine({a.x + 1, a.y + 1}, {a.x + 1, b.y - 2}, kShadow);
    // highlight снизу и справа
    dl->AddLine({a.x + 1, b.y - 2}, {b.x - 2, b.y - 2}, kHighlight);
    dl->AddLine({b.x - 2, a.y + 1}, {b.x - 2, b.y - 2}, kHighlight);
}

bool button(const char* label, float width, float height) {
    const ImGuiStyle& style = ImGui::GetStyle();
    ImVec2 txt = ImGui::CalcTextSize(label);
    if (width  <= 0) width  = txt.x + style.FramePadding.x * 2.f + 10.f;
    if (height <= 0) height = txt.y + style.FramePadding.y * 2.f + 4.f;

    ImVec2 pos = ImGui::GetCursorScreenPos();
    ImVec2 end = {pos.x + width, pos.y + height};

    ImGui::PushID(label);
    bool clicked = ImGui::InvisibleButton("##btn", {width, height});
    bool active  = ImGui::IsItemActive();
    ImGui::PopID();

    ImDrawList* dl = ImGui::GetWindowDrawList();
    if (active) draw_sunken(dl, pos, end, kFacePress);
    else        draw_raised(dl, pos, end);

    // Текст по центру, при нажатии сдвигаем на 1px - визуально кнопка "проваливается".
    ImVec2 txt_pos = {pos.x + (width  - txt.x) * 0.5f,
                      pos.y + (height - txt.y) * 0.5f};
    if (active) { txt_pos.x += 1; txt_pos.y += 1; }
    dl->AddText(txt_pos, kOuter, label);
    return clicked;
}

void title_bar(const char* text) {
    const float h = 18.f;
    ImVec2 pos = ImGui::GetCursorScreenPos();
    float w = ImGui::GetContentRegionAvail().x;
    ImVec2 end = {pos.x + w, pos.y + h};

    ImDrawList* dl = ImGui::GetWindowDrawList();
    dl->AddRectFilled(pos, end, kTitleBg);
    dl->AddText({pos.x + 4, pos.y + 2}, kTitleFg, text);

    ImGui::Dummy({w, h + 2});
}

} // namespace Win95
