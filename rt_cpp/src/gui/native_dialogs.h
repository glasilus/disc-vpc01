#pragma once
#include <string>
#include <vector>

// Нативные диалоги выбора файлов/папки. Реализация своя под каждую платформу:
//   - Windows - прямо в rt_gui.cpp (comdlg32 / SHBrowseForFolder).
//   - macOS   - mac_dialogs.mm (Cocoa NSOpenPanel).
//   - Linux   - нативного диалога нет, приём файлов только через drag-and-drop.
// Пути всегда в UTF-8. Пустой результат значит, что пользователь отменил
// выбор либо нативный диалог на этой платформе недоступен.
std::vector<std::string> native_open_files();   // выбор нескольких файлов
std::string              native_open_folder();  // выбор одной папки
