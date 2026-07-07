#pragma once
#include <string>
#include <vector>

// Native file/folder pickers. Implemented per-platform:
//   • Windows — inline in rt_gui.cpp (comdlg32 / SHBrowseForFolder).
//   • macOS   — mac_dialogs.mm (Cocoa NSOpenPanel).
//   • Linux   — no native dialog; drag-and-drop is the ingest path.
// All paths are returned as UTF-8. An empty result means the user cancelled or
// no native dialog is available.
std::vector<std::string> native_open_files();   // multi-select files
std::string              native_open_folder();  // single folder
