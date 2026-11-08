#!/usr/bin/env python3
"""Restart the kiosk browser after a GPU fault.

2026-10-31: on one boot in five the GPU faulted ~20 s after the kiosk started ("CP | opcode error",
"gpu fault", "hangcheck recover"); the GPU recovered but the screen kept showing the first frame
of the welcome page until the browser was restarted (the root cause is still open, see the README
fix table). This follows the kernel log and, a few seconds after a fault (the GPU recovers and the
burst of messages ends), restarts thinksmart-kiosk.py; weston's autolaunch (watch=true) starts it
again and it loads the page afresh. A fault that didn't freeze anything costs a page reload.
Started before the kiosk session, so a fault right after boot is seen. Runs as root (kernel log, pkill).
"""
import re
import selectors
import subprocess
import sys
import time

FAULT = re.compile(r'hangcheck recover|gpu fault|opcode error|preemption timed out')
SETTLE = 5        # seconds: let the GPU recover and the burst of fault messages end
MIN_GAP = 60      # seconds between restarts, so a fault loop can't keep the screen restarting
KIOSK = '^/usr/bin/python3 /usr/local/bin/thinksmart-kiosk.py'


def log(msg):
    print(msg, flush=True)


def main():
    log('watching the kernel log for GPU faults')
    kmsg = subprocess.Popen(['journalctl', '-k', '-f', '-n0', '-o', 'cat'], stdout=subprocess.PIPE, text=True)
    last_restart = 0.0
    fault_at = None
    sel = selectors.DefaultSelector()
    sel.register(kmsg.stdout, selectors.EVENT_READ)
    while True:
        timeout = None if fault_at is None else max(0, fault_at + SETTLE - time.monotonic())
        if sel.select(timeout):
            line = kmsg.stdout.readline()
            if not line:
                sys.exit('journalctl ended')
            if FAULT.search(line):
                log(f'GPU fault: {line.strip()[:160]}')
                if fault_at is None:
                    fault_at = time.monotonic()
            continue
        fault_at = None
        if time.monotonic() - last_restart < MIN_GAP:
            log('restarted less than a minute ago, leaving it')
            continue
        last_restart = time.monotonic()
        r = subprocess.run(['pkill', '-f', KIOSK])
        log('kiosk browser restarted' if r.returncode == 0 else 'kiosk browser was not running')


if __name__ == '__main__':
    main()
