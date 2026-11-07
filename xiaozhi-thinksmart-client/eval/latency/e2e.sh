#!/bin/bash
# usage: e2e.sh rounds files...   (virtual mic lat_mic must exist and the client capture moved onto it)
R=$1; shift
cd ~/asr-lat
~/thinksmart-client/.venv/bin/python sse.py > sse.log 2>&1 &
SSE=$!
sleep 1
: > runs.log
for r in $(seq 1 $R); do for f in "$@"; do
  curl -s -X POST 127.0.0.1:8090/api/end >/dev/null; sleep 3
  curl -s -X POST 127.0.0.1:8090/api/wake >/dev/null; sleep 2.5
  d=$(python3 -c "import wave;w=wave.open('t_$f');print(w.getnframes()/w.getframerate())")
  t0=$(date +%s.%N)
  paplay -d lat_mic t_$f
  t1=$(date +%s.%N)
  echo -e "$f\t$t0\t$t1\t$d" >> runs.log
  sleep 14
done; done
curl -s -X POST 127.0.0.1:8090/api/end >/dev/null
kill $SSE
