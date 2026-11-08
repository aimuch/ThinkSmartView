#!/bin/bash
# Run a command on the device over SSH with a dedicated key (used by image/pull-from-device.sh and
# image/restore-userdata.sh). Usage: TS_HOST=<device IP> ./ts.sh 'command'
# The key is ~/.ssh/thinksmart_ed25519 and its known hosts ~/.ssh/known_hosts_thinksmart (see README section 4).
exec ssh -o BatchMode=yes -o ConnectTimeout=10 \
  -o UserKnownHostsFile="$HOME/.ssh/known_hosts_thinksmart" \
  -i "$HOME/.ssh/thinksmart_ed25519" \
  "ubuntu@${TS_HOST:?set TS_HOST=<device IP>}" "$@"
