// Диалоги выбора файла/папки на Cocoa для macOS-сборки. Компилируется как
// Objective-C++ (.mm) и линкуется с фреймворком Cocoa. NSOpenPanel обязан
// выполняться в главном потоке - GUI вызывает эти функции из
// render/main-потока, где и так уже живёт GLFW, так что требование выполнено.
//
// Типы файлов намеренно не фильтруются: -setAllowedFileTypes: устарел,
// а новый UTType API тянет за собой версионно-зависимый код, и видео-пул
// и так отбрасывает всё, что не открывает FFmpeg. Свободный выбор файла
// держит этот файл маленьким и без предупреждений компилятора на любых SDK.
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
