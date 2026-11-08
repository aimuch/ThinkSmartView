#!/bin/bash
# On the device: ask by text for a long reply, interrupt it with the wake button after N seconds,
# then ask what it said last. Prints the page's state events (uploading must stay false while the
# reply plays) and the hub's log lines about the interruption.
# Usage: bash device_barge_in.sh [seconds-before-interrupting]   (mute the speaker first if you like)
set -u
AFTER=${1:-4}
API=http://127.0.0.1:8090
EV=$(mktemp)
post() { curl -s -X POST "$API$1" -H 'Content-Type: application/json' ${2:+-d "$2"} >/dev/null; }
wait_state() {   # wait_state <state> <timeout seconds>
  for _ in $(seq $(( $2 * 10 ))); do
    grep -a '^data: {"state"' "$EV" | tail -n 1 | grep -q "\"state\": \"$1\"" && return 0
    sleep 0.1
  done
  return 1
}

curl -sN "$API/events" > "$EV" &
CURL=$!
LOG=~/xiaozhi-hub-server/main/xiaozhi-server/tmp/server.log
from=$(wc -l < "$LOG")
sleep 0.5

post /api/text '{"text": "请给我讲一个关于一只小猫的故事，大约一分钟长，每句话都要完整"}'
wait_state speaking 40 || echo "reply never started"
sleep "$AFTER"
echo "--- interrupting after ${AFTER}s of reply"
post /api/wake
wait_state listening 5 || echo "not listening after the interruption"
sleep 1
post /api/text '{"text": "你刚才最后一句说的是什么？原样重复一遍"}'
wait_state speaking 40
wait_state listening 60
sleep 1
post /api/end
sleep 1
kill $CURL

echo "--- state events (state, uploading)"
grep '^data: {"state"' "$EV" | sed -E 's/.*"state": "([a-z]+)".*"uploading": (true|false).*/\1 uploading=\2/' | uniq
echo "--- messages"
grep '^data: {"id"' "$EV" | sed -E 's/.*"role": "([a-z]+)", "text": "([^"]*)".*"interrupted": (true).*/\1 [被打断] \2/; s/.*"role": "([a-z]+)", "text": "([^"]*)".*/\1 \2/' | awk '!seen[$0]++' | tail -n 8
echo "--- hub"
tail -n +"$((from + 1))" "$LOG" | grep -aE "回复被打断|大模型收到用户消息|Abort message received$" | cut -c1-300
rm -f "$EV"
