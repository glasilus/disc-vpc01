#pragma once
#include <cstdio>

// Lightweight logger that mirrors writes to:
//   1. A file `vpc01rt.log` next to the executable's working directory.
//   2. stderr (visible if a console is attached).
//
// All writes are flushed immediately so log lines survive a crash. Initialize
// once in main() before anything else writes diagnostics.
namespace Log {
    void init();
    void shutdown();
}
