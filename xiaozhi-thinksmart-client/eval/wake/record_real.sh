#!/bin/bash
# Record the device's microphone into $WAKE_EVAL_DATA/real/<name>.wav for run.py.
#   eval/wake/record_real.sh pos-xiaomai-1m-normal-n10 40
# Needs TS_HOST=<device IP> (thinksmart-ubuntu-image/ts.sh). The client is stopped while recording,
# so it doesn't answer the wake words being recorded (its chime would end up in the recording),
# and started again afterwards.
set -e
name=$1 seconds=$2
TS="$(cd "$(dirname "$0")/../../.." && pwd)/thinksmart-ubuntu-image/ts.sh"
[ -x "$TS" ] || TS="$(cd "$(dirname "$0")/../../../.." && pwd)/thinksmart-ubuntu-image/ts.sh"
out="${WAKE_EVAL_DATA:-$HOME/thinksmart-wake-eval}/real"
mkdir -p "$out"
"$TS" "sudo systemctl stop thinksmart-client; echo recording $seconds s; \
       timeout $((seconds + 1)) pw-record --rate 16000 --channels 1 --format s16 /tmp/rec.wav; \
       sudo systemctl start thinksmart-client; echo done"
"$TS" 'cat /tmp/rec.wav; rm -f /tmp/rec.wav' > "$out/$name.wav"
ls -la "$out/$name.wav"
