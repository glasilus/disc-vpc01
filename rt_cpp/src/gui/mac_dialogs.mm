// Cocoa file/folder pickers for the macOS build. Compiled as Objective-C++
// (.mm) and linked against the Cocoa framework. NSOpenPanel must run on the
// main thread — the GUI calls these from the render/main thread, which is
// where GLFW already lives, so that requirement is satisfied.
//
// We deliberately do NOT restrict file types: filtering via the deprecated
// -setAllowedFileTypes: (or the newer UTType API) adds version-specific code,
// and the video pool already rejects anything FFmpeg can't open. Letting the
// user pick freely keeps this file tiny and warning-free across SDK versions.
#import <Cocoa/Cocoa.h>
#include "native_dialogs.h"

std::vector<std::string> native_open_files() {
    std::vector<std::string> out;
    @autoreleasepool {
        NSOpenPanel* panel = [NSOpenPanel openPanel];
        [panel setCanChooseFiles:YES];
        [panel setCanChooseDirectories:NO];
        [panel setAllowsMultipleSelection:YES];
        [panel setTitle:@"Add Videos"];
        if ([panel runModal] == NSModalResponseOK) {
            for (NSURL* url in [panel URLs]) {
                const char* p = [[url path] UTF8String];
                if (p) out.emplace_back(p);
            }
        }
    }
    return out;
}

std::string native_open_folder() {
    std::string result;
    @autoreleasepool {
        NSOpenPanel* panel = [NSOpenPanel openPanel];
        [panel setCanChooseFiles:NO];
        [panel setCanChooseDirectories:YES];
        [panel setAllowsMultipleSelection:NO];
        [panel setTitle:@"Select Folder"];
        if ([panel runModal] == NSModalResponseOK) {
            NSURL* url = [[panel URLs] firstObject];
            if (url) {
                const char* p = [[url path] UTF8String];
                if (p) result = p;
            }
        }
    }
    return result;
}
