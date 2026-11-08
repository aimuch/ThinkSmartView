#!/bin/bash
# On the device: CPU of the client process over 15 s of a spoken reply (started by a wake + text
# question, so the conversation is "live" as after a voice question). Usage: bash device_reply_cpu.sh
API=http://127.0.0.1:8090
PID=$(systemctl show -p MainPID --value thinksmart-client)
EV=$(mktemp); curl -sN "$API/events" > "$EV" & CURL=$!
post() { curl -s -X POST "$API$1" -H 'Content-Type: application/json' ${2:+-d "$2"} >/dev/null; }
post /api/wake; sleep 2
post /api/text '{"text": "请给我讲一个关于一只小狗的故事，大约一分钟长"}'
for _ in $(seq 400); do grep -a '^data: {"state"' "$EV" | tail -n 1 | grep -q '"speaking"' && break; sleep 0.1; done
sleep 2
t0=$(awk '{print $14+$15}' /proc/$PID/stat); sleep 15; t1=$(awk '{print $14+$15}' /proc/$PID/stat)
grep -a '^data: {"state"' "$EV" | tail -n 1 | grep -o '"state": "[a-z]*", "connected": [a-z]*, "uploading": [a-z]*'
echo "client CPU during the reply: $(( (t1 - t0) * 100 / 15 / $(getconf CLK_TCK) ))% of one core"
post /api/end; sleep 1; kill $CURL; rm -f "$EV"
