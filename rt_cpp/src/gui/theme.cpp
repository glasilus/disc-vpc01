#include "theme.h"
#include <imgui.h>

void Theme::apply_win95() {
    ImGuiStyle& s = ImGui::GetStyle();
    s.WindowRounding    = 0.f;
    s.ChildRounding     = 0.f;
    s.FrameRounding     = 0.f;
    s.GrabRounding      = 0.f;
    s.PopupRounding     = 0.f;
    s.ScrollbarRounding = 0.f;
    s.TabRounding       = 0.f;
    s.WindowBorderSize  = 1.f;
    s.FrameBorderSize   = 1.f;
    s.FramePadding      = {4, 3};
    s.ItemSpacing       = {4, 4};
    s.IndentSpacing     = 16.f;

    ImVec4* c = s.Colors;
    // Classic Win95 gray palette
    c[ImGuiCol_WindowBg]          = {0.753f, 0.753f, 0.753f, 1.f};
    c[ImGuiCol_ChildBg]           = {0.753f, 0.753f, 0.753f, 1.f};
    c[ImGuiCol_PopupBg]           = {0.753f, 0.753f, 0.753f, 1.f};
    c[ImGuiCol_Border]            = {0.502f, 0.502f, 0.502f, 1.f};
    c[ImGuiCol_BorderShadow]      = {1.f,    1.f,    1.f,    1.f};
    c[ImGuiCol_FrameBg]           = {1.f,    1.f,    1.f,    1.f};
    c[ImGuiCol_FrameBgHovered]    = {0.878f, 0.878f, 1.f,    1.f};
    c[ImGuiCol_FrameBgActive]     = {0.753f, 0.753f, 0.753f, 1.f};
    c[ImGuiCol_TitleBg]           = {0.000f, 0.000f, 0.502f, 1.f};
    c[ImGuiCol_TitleBgActive]     = {0.000f, 0.000f, 0.753f, 1.f};
    c[ImGuiCol_TitleBgCollapsed]  = {0.502f, 0.502f, 0.502f, 1.f};
    c[ImGuiCol_MenuBarBg]         = {0.753f, 0.753f, 0.753f, 1.f};
    c[ImGuiCol_ScrollbarBg]       = {0.878f, 0.878f, 0.878f, 1.f};
    c[ImGuiCol_ScrollbarGrab]     = {0.753f, 0.753f, 0.753f, 1.f};
    c[ImGuiCol_ScrollbarGrabHovered] = {0.627f, 0.627f, 0.627f, 1.f};
    c[ImGuiCol_ScrollbarGrabActive]  = {0.502f, 0.502f, 0.502f, 1.f};
    c[ImGuiCol_CheckMark]         = {0.f,    0.f,    0.f,    1.f};
    c[ImGuiCol_SliderGrab]        = {0.753f, 0.753f, 0.753f, 1.f};
    c[ImGuiCol_SliderGrabActive]  = {0.502f, 0.502f, 0.753f, 1.f};
    c[ImGuiCol_Button]            = {0.753f, 0.753f, 0.753f, 1.f};
    c[ImGuiCol_ButtonHovered]     = {0.878f, 0.878f, 1.f,    1.f};
    c[ImGuiCol_ButtonActive]      = {0.502f, 0.502f, 0.753f, 1.f};
    c[ImGuiCol_Header]            = {0.000f, 0.000f, 0.502f, 1.f};
    c[ImGuiCol_HeaderHovered]     = {0.000f, 0.000f, 0.753f, 1.f};
    c[ImGuiCol_HeaderActive]      = {0.000f, 0.000f, 0.502f, 1.f};
    c[ImGuiCol_Tab]               = {0.753f, 0.753f, 0.753f, 1.f};
    c[ImGuiCol_TabHovered]        = {0.878f, 0.878f, 1.f,    1.f};
    c[ImGuiCol_TabActive]         = {0.878f, 0.878f, 1.f,    1.f};
    c[ImGuiCol_Text]              = {0.f,    0.f,    0.f,    1.f};
    c[ImGuiCol_TextDisabled]      = {0.502f, 0.502f, 0.502f, 1.f};
    c[ImGuiCol_Separator]         = {0.502f, 0.502f, 0.502f, 1.f};
    c[ImGuiCol_ResizeGrip]        = {0.753f, 0.753f, 0.753f, 0.f};
    c[ImGuiCol_PlotLines]         = {0.f,    0.f,    0.f,    1.f};
    c[ImGuiCol_PlotHistogram]     = {0.000f, 0.000f, 0.753f, 1.f};
    c[ImGuiCol_PlotHistogramHovered]= {0.000f, 0.000f, 1.f,  1.f};
}
