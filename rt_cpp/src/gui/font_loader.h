#pragma once
#include <imgui.h>

namespace FontLoader {

// Ищет в системе шрифт с кириллицей и грузит его. Возвращаемый указатель
// можно игнорировать - шрифт уже добавлен в атлас ImGui и станет дефолтным
// автоматически.
//
// Порядок поиска (берётся первый найденный файл):
//   Windows : C:\Windows\Fonts\tahoma.ttf, segoeui.ttf, arial.ttf
//   macOS   : /System/Library/Fonts/Helvetica.ttc, /Library/Fonts/Arial.ttf
//   Linux   : /usr/share/fonts/truetype/dejavu/DejaVuSans.ttf,
//             /usr/share/fonts/TTF/DejaVuSans.ttf,
//             /usr/share/fonts/dejavu/DejaVuSans.ttf,
//             /usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf
//
// Если ничего не нашлось - откат на дефолтный шрифт ImGui (без кириллицы,
// в stderr пишется предупреждение).
ImFont* load_default(float pixel_size = 14.f);

} // namespace FontLoader
