#include "KeychainTestGuard.h"
#include <unistd.h>
#include <execinfo.h>

extern void install_test_keychain_guard(void);
__attribute__((constructor)) static void install_guard(void) {
    install_test_keychain_guard();
}

void keychain_guard_fail(void) {
    const char message[] = "FAIL: test attempted real Keychain access; inject credential storage instead.\n";
    write(STDERR_FILENO, message, sizeof(message) - 1);
    void *frames[64];
    int count = backtrace(frames, 64);
    backtrace_symbols_fd(frames, count, STDERR_FILENO);
    _exit(86);
}

