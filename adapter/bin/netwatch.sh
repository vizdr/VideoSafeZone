#!/usr/bin/env bash
# Network watcher for "SSH / VS Code drops and the Pi has to be restarted" (FoundAndFixed.md #79).
# Runs as the user unit netwatch.service (LAUNCH.md A1). One line every 10 s to
# ~/.local/state/netwatch.log, fsync'd at once: journald keeps
# info-level lines in RAM for up to 5 min, so a power cut erases exactly the minutes before
# a drop. Columns:
#   uptime_s  wall-clock  gw=<rtt ms|FAIL>  net=<rtt|FAIL>  dns=<ok|FAIL>  sig=<dBm>
#   retries/failed (cumulative tx)  ps=<wifi power save>  ssh=<conns>/<max Send-Q bytes>
#   mem=<MiB available>  swap=<MiB used>  load=<1 min>
# Reading it: gw=FAIL -> the Wi-Fi link itself died; gw ok but net/dns FAIL -> router/ISP;
# all ok but ssh Send-Q stuck growing -> the path back to the PC (inbound side, power save).
LOG="$HOME/.local/state/netwatch.log"
GW=$(ip -4 route show default 2>/dev/null | awk '{print $3; exit}')
while true; do
  [ -n "$GW" ] || GW=$(ip -4 route show default 2>/dev/null | awk '{print $3; exit}')
  gw=$(ping -c1 -W2 -q "${GW:-192.168.178.1}" 2>/dev/null | awk -F/ '/rtt/{printf "%.0f", $5}'); gw=${gw:-FAIL}
  net=$(ping -c1 -W2 -q 1.1.1.1 2>/dev/null | awk -F/ '/rtt/{printf "%.0f", $5}'); net=${net:-FAIL}
  dns=$(timeout 3 getent ahostsv4 github.com >/dev/null 2>&1 && echo ok || echo FAIL)
  sd=$(iw dev wlan0 station dump 2>/dev/null)
  sig=$(awk '/^\tsignal:/{print $2; exit}' <<<"$sd")
  rtr=$(awk '/tx retries/{print $3; exit}' <<<"$sd"); tfl=$(awk '/tx failed/{print $3; exit}' <<<"$sd")
  ps=$(iw dev wlan0 get power_save 2>/dev/null | awk '{print $3}')
  ssh=$(ss -Htn state established '( sport = :22 )' 2>/dev/null | awk '{n++; if ($2>m) m=$2} END{printf "%d/%d", n, m}')
  mem=$(awk '/MemAvailable/{printf "%d", $2/1024}' /proc/meminfo)
  swp=$(awk '/SwapTotal/{t=$2} /SwapFree/{f=$2} END{printf "%d", (t-f)/1024}' /proc/meminfo)
  printf '%s %s gw=%s net=%s dns=%s sig=%s retries/failed=%s/%s ps=%s ssh=%s mem=%s swap=%s load=%s\n' \
    "$(cut -d' ' -f1 /proc/uptime | cut -d. -f1)" "$(date '+%F %T')" "$gw" "$net" "$dns" "${sig:-?}" \
    "${rtr:-?}" "${tfl:-?}" "${ps:-?}" "$ssh" "$mem" "$swp" "$(cut -d' ' -f1 /proc/loadavg)" >> "$LOG"
  sync "$LOG"
  # keep it bounded: ~2 MB/day; trim to the newest 20000 lines past 5 MB
  if [ "$(stat -c %s "$LOG")" -gt 5000000 ]; then tail -n 20000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"; fi
  sleep 10
done
