#include "log.h"
#include <cstdio>
#include <cstdlib>

#if defined(_WIN32)
#  define WIN32_LEAN_AND_MEAN
#  define NOMINMAX
#  include <windows.h>
#endif

namespace Log {

static FILE* g_file = nullptr;

#if defined(_WIN32)
// Re-open stderr & stdout to the log file. After this, every fprintf(stderr,...)
// call in the program goes into vpc01rt.log automatically — no need to touch
// existing call sites.
static void redirect_streams_to_file(const char* path) {
    FILE* f = nullptr;
    if (freopen_s(&f, path, "w", stderr) == 0 && f) {
        setvbuf(f, nullptr, _IONBF, 0);
        g_file = f;
    }
    // Mirror stdout into the same file. _dup2 connects fd1 → fd0.
    FILE* fo = nullptr;
    freopen_s(&fo, path, "a", stdout);
    if (fo) setvbuf(fo, nullptr, _IONBF, 0);
}

// SEH handler — writes crash address into the log so users can send it to us.
static LONG WINAPI crash_handler(EXCEPTION_POINTERS* ep) {
    if (g_file && ep && ep->ExceptionRecord) {
        fprintf(g_file,
                "\n[CRASH] code=0x%08lX  at=0x%p\n",
                ep->ExceptionRecord->ExceptionCode,
                ep->ExceptionRecord->ExceptionAddress);
        fflush(g_file);
    }
    return EXCEPTION_CONTINUE_SEARCH;  // let Windows show its dialog
}
#endif

void init() {
    const char* path = "vpc01rt.log";
#if defined(_WIN32)
    redirect_streams_to_file(path);
    SetUnhandledExceptionFilter(crash_handler);
#else
    g_file = std::freopen(path, "w", stderr);
    if (g_file) std::setvbuf(g_file, nullptr, _IONBF, 0);
#endif
    std::fprintf(stderr, "[log] vpc01rt.log opened\n");
}

void shutdown() {
    if (g_file) { std::fflush(g_file); }
    // Don't fclose — that would also close stderr's underlying handle.
}

} // namespace Log
