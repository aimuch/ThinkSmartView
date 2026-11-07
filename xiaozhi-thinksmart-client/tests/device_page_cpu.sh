#!/bin/bash
# On the device: CPU of the kiosk's web process (the page) in idle, listening and speaking.
# Listening is push-to-talk held for the sample; speaking is a long text-asked reply.
# Mutes the speaker during the run and restores it. Usage: bash device_page_cpu.sh [seconds per sample]
set -u
N=${1:-20}
API=http://127.0.0.1:8090
WP=$(pgrep -f WebKitWebProcess | head -n 1)
HZ=$(getconf CLK_TCK)
EV=$(mktemp)
post() { curl -s -X POST "$API$1" -H 'Content-Type: application/json' ${2:+-d "$2"} >/dev/null; }
wait_state() {   # wait_state <state> <timeout seconds>
  for _ in $(seq $(( $2 * 10 ))); do
    grep -a '^data: {"state"' "$EV" | tail -n 1 | grep -q "\"state\": \"$1\"" && return 0
    sleep 0.1
  done
  return 1
}
cpu() {   # cpu <label>: web process CPU over N seconds, % of one core
  local t0 t1
  t0=$(awk '{print $14+$15}' /proc/$WP/stat); sleep "$N"; t1=$(awk '{print $14+$15}' /proc/$WP/stat)
  echo "$1: $(( (t1 - t0) * 100 / N / HZ ))%"
}

MUTED=$(wpctl get-volume @DEFAULT_AUDIO_SINK@ | grep -c MUTED)
wpctl set-mute @DEFAULT_AUDIO_SINK@ 1
curl -sN "$API/events" > "$EV" &
CURL=$!
sleep 1

cpu idle
post /api/ptt/start
wait_state listening 5 || echo "not listening"
sleep 1
cpu listening
post /api/ptt/stop
sleep 2
post /api/text '{"text": "请给我讲一个关于一只小狗的故事，大约一分钟长"}'
wait_state speaking 40 || echo "reply never started"
sleep 2
cpu speaking
post /api/end
sleep 1
kill $CURL
rm -f "$EV"
[ "$MUTED" = 0 ] && wpctl set-mute @DEFAULT_AUDIO_SINK@ 0
