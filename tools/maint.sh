#!/bin/bash
# maint.sh "<reason>" <command...>: runs a test with the maintenance switch on (outside requests get 503), and
# turns it off when the command ends, also when it fails or is interrupted.  Waits first for a running request.
reason=$1; shift
echo "$reason" > ~/strata-maintenance
trap 'rm -f ~/strata-maintenance' EXIT INT TERM
for i in $(seq 1 120); do   # a request still running: its progress lines keep coming
  last=$(stat -c %Y ${STRATA_SERVER_LOG:-~/server.out} 2>/dev/null || echo 0)
  [ $(( $(date +%s) - last )) -ge 5 ] && break
  sleep 2
done
"$@"
