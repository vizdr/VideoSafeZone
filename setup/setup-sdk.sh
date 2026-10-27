#!/usr/bin/env bash
# setup-sdk.sh -- the KVS Producer SDK and its kvssink plugin (LAUNCH.md A3).
#
#   bash setup/setup-sdk.sh            # done? say so. cached? restore. else start the build
#   bash setup/setup-sdk.sh status     # how far the background build is
#   bash setup/setup-sdk.sh wait       # block until it has finished, then the proofs
#   bash setup/setup-sdk.sh build      # the build itself, in the foreground (what the
#                                      # background unit runs; also usable directly)
#   bash setup/setup-sdk.sh save-cache # copy the finished SDK to the stick by hand
#
# A from-source build takes 1.5-2.5 hours on a Pi 4. Fresh SD cards are frequent here, so a
# finished build is saved to the USB stick ($SETUP_CACHE, the stick stays in the Pi) and a
# fresh card restores it in under a minute. The cache is used only when everything it depends
# on matches: SDK tag, the patches, Debian release, GStreamer version, CPU, and the clone's
# path (the plugin's RUNPATH holds absolute paths into this directory).
#
# The build, step by step -- each is the reason for something that went wrong once:
#   - the SDK at a fixed tag ($KVS_SDK_TAG): the patches below were made against it;
#   - kvscproducer fetched on its own before configure, because patches 1-2 edit files that the
#     configure step would otherwise download and build in the same breath (FoundAndFixed #76);
#   - patch 1: no `--parallel` in nested dependency builds -- OpenSSL on all cores starves the
#     Pi and earlyoom kills every compiler at once (FoundAndFixed #2);
#   - patch 2 + 2b: no OpenSSL test submodules (boringssl & co., ~700 MB). 2b is the CMake policy
#     without which CMake >= 3.16 reads patch 2's empty list as "all" (FoundAndFixed #3, #77);
#   - patch 3: _GNU_SOURCE for GCC 14, in a file that exists only after configure (#4);
#   - patches 4 and 5: kvssink's timestamps (setup/patches/kvssink-vms-patches-4-5.patch): whole
#     milliseconds (FoundAndFixed #63) and a missing DTS taken from the PTS (#70);
#   - one compile job, pinned to cores 1-2, leaving the rest of the Pi responsive (#1).
source "$(dirname "$(readlink -f "$0")")/lib.sh"
[ "$(id -u)" -ne 0 ] || die "run this one as your normal user, without sudo"
LOG="$KVS_SDK/build/build.log"
UNIT=kvs-sdk-build
PATCH45="$SETUP_DIR/patches/kvssink-vms-patches-4-5.patch"
BUILD_RECIPE=1   # bump when the build steps below change, so older caches stop matching

cache_name() {
  local os gst
  os=$(. /etc/os-release; echo "$ID$VERSION_ID")
  gst=$(pkg-config --modversion gstreamer-1.0 2>/dev/null || echo unknown)
  printf 'kvs-sdk-%s-r%s-p%s-%s-gst%s-%s-h%s.tar.gz' "$KVS_SDK_TAG" "$BUILD_RECIPE" \
    "$(sha1sum "$PATCH45" | cut -c1-8)" "$os" "$gst" "$(uname -m)" \
    "$(printf %s "$VMS_HOME" | sha1sum | cut -c1-8)"
}

plugin_env() { env GST_PLUGIN_PATH="$KVS_SDK/build" LD_LIBRARY_PATH="$KVS_SDK/open-source/local/lib" "$@"; }

built() {   # the plugin exists, loads, and carries patches 4 and 5
  [ -e "$KVS_SDK/build/libgstkvssink.so" ] &&
  [ "$(grep -c 'VMS patch [45]' "$KVS_SDK/src/gstreamer/gstkvssink.cpp" 2>/dev/null)" -eq 2 ] &&
  plugin_env gst-inspect-1.0 kvssink >/dev/null 2>&1
}

proofs() {
  step "proofs"
  proof "SDK at $KVS_SDK_TAG"                       bash -c "[ \"\$(git -C '$KVS_SDK' describe --tags)\" = $KVS_SDK_TAG ]"
  proof "patch 1: no nested --parallel"            bash -c "! grep -q -- '--parallel' '$KVS_SDK/dependency/libkvscproducer/kvscproducer-src/CMake/Utilities.cmake'"
  proof "patch 2b: CMP0097 NEW (no OpenSSL submodules)" grep -q CMP0097 "$KVS_SDK/dependency/libkvscproducer/kvscproducer-src/CMake/Dependencies/libopenssl-CMakeLists.txt"
  proof "patch 3: _GNU_SOURCE in kvspic"           grep -q -- '-D_GNU_SOURCE' "$KVS_SDK/dependency/libkvscproducer/kvscproducer-src/dependency/libkvspic/kvspic-src/CMakeLists.txt"
  proof "patches 4 and 5 in kvssink"               bash -c "[ \"\$(grep -c 'VMS patch [45]' '$KVS_SDK/src/gstreamer/gstkvssink.cpp')\" -eq 2 ]"
  proof "libgstkvssink.so built"                   test -e "$KVS_SDK/build/libgstkvssink.so"
  proof "GStreamer loads kvssink"                  plugin_env gst-inspect-1.0 kvssink
  verdict "setup-sdk.sh"
}

save_cache() {
  buffer_ready || { note "no stick mounted -- SDK not cached (a fresh card will rebuild it)"; return 0; }
  mkdir -p "$SETUP_CACHE"
  local name; name=$(cache_name)
  if [ -e "$SETUP_CACHE/$name" ]; then ok "cache already holds $name"; return 0; fi
  note "saving the built SDK to $SETUP_CACHE/$name ..."
  tar -C "$(dirname "$KVS_SDK")" -czf "$SETUP_CACHE/.$name.tmp" "$(basename "$KVS_SDK")"
  mv "$SETUP_CACHE/.$name.tmp" "$SETUP_CACHE/$name"
  # Keep the two newest: an OS or GStreamer upgrade makes older ones unusable anyway.
  ls -1t "$SETUP_CACHE"/kvs-sdk-*.tar.gz 2>/dev/null | tail -n +3 | xargs -r rm -f
  ok "cached ($(du -h "$SETUP_CACHE/$name" | cut -f1))"
}

restore_cache() {
  buffer_ready || return 1
  local f; f="$SETUP_CACHE/$(cache_name)"
  [ -e "$f" ] || return 1
  note "restoring $(basename "$f") from the stick"
  rm -rf "$KVS_SDK.restoring" && mkdir -p "$KVS_SDK.restoring"
  tar -C "$KVS_SDK.restoring" -xzf "$f"
  rm -rf "$KVS_SDK" && mv "$KVS_SDK.restoring/$(basename "$KVS_SDK")" "$KVS_SDK" && rmdir "$KVS_SDK.restoring"
  built
}

build() {
  mkdir -p "$(dirname "$KVS_SDK")"
  if [ ! -d "$KVS_SDK/.git" ]; then
    step "clone the SDK at $KVS_SDK_TAG"
    git clone -q --branch "$KVS_SDK_TAG" --depth 1 \
      https://github.com/awslabs/amazon-kinesis-video-streams-producer-sdk-cpp.git "$KVS_SDK"
  fi
  mkdir -p "$KVS_SDK/build"
  local P="$KVS_SDK/dependency/libkvscproducer/kvscproducer-src"

  step "fetch kvscproducer on its own, so patches 1-2 have something to patch (#76)"
  if [ ! -f "$P/CMake/Utilities.cmake" ]; then
    mkdir -p "$KVS_SDK/dependency/libkvscproducer"
    cp "$KVS_SDK/CMake/Dependencies/libkvscproducer-CMakeLists.txt" "$KVS_SDK/dependency/libkvscproducer/CMakeLists.txt"
    (cd "$KVS_SDK/dependency/libkvscproducer" && cmake . >fetch.log 2>&1 && cmake --build . >>fetch.log 2>&1) ||
      die "fetching kvscproducer failed -- see $KVS_SDK/dependency/libkvscproducer/fetch.log"
  fi
  ok "kvscproducer source present"

  step "patches 1, 2, 2b"
  sed -i 's/--build \. --parallel/--build ./' "$P/CMake/Utilities.cmake"
  local O="$P/CMake/Dependencies/libopenssl-CMakeLists.txt"
  grep -q GIT_SUBMODULES "$O" || sed -i '/GIT_TAG *OpenSSL_1_1_1t/a\    GIT_SUBMODULES    ""' "$O"
  grep -q CMP0097 "$O" || sed -i '/^cmake_minimum_required(VERSION 3.6.3)$/a\cmake_policy(SET CMP0097 NEW)  # LAUNCH.md A3 patch 2b (FoundAndFixed #77)' "$O"
  ok "applied"

  step "patches 4 and 5 (kvssink timestamps)"
  if ! grep -q 'VMS patch 4' "$KVS_SDK/src/gstreamer/gstkvssink.cpp"; then
    git -C "$KVS_SDK" apply "$PATCH45" || die "patch 4-5 doesn't apply to this SDK -- was $KVS_SDK_TAG changed?"
  fi
  ok "applied"

  step "configure (builds OpenSSL, curl, log4cplus -- most of the hours are here)"
  if ! grep -q 'CONFIGURE_EXIT=0' "$LOG" 2>/dev/null; then
    (cd "$KVS_SDK/build" && taskset -c 1,2 cmake .. -DBUILD_GSTREAMER_PLUGIN=ON -DBUILD_DEPENDENCIES=ON \
       -DPARALLEL_BUILD=OFF -DCMAKE_BUILD_TYPE=Release >>"$LOG" 2>&1; echo "CONFIGURE_EXIT=$?" >>"$LOG")
    grep -q 'CONFIGURE_EXIT=0' "$LOG" || die "configure failed -- see $LOG (re-running resumes)"
    ! grep -q '^Submodule' "$LOG" || warn "OpenSSL submodules were fetched: patch 2b didn't take (#77)"
  fi
  ok "configured"

  step "patch 3 (_GNU_SOURCE for GCC 14, #4) -- its file exists only now"
  local F="$P/dependency/libkvspic/kvspic-src/CMakeLists.txt"
  grep -q -- '-D_GNU_SOURCE' "$F" ||
    sed -i '/^add_definitions(-DDETECTED_GIT_HASH)$/a\if(UNIX AND NOT APPLE)\n  add_definitions(-D_GNU_SOURCE)\nendif()' "$F"
  grep -q -- '-D_GNU_SOURCE' "$F" || die "patch 3 not applied: the anchor line moved in kvspic's CMakeLists.txt"
  ok "applied"

  step "compile (one job, cores 1-2)"
  (cd "$KVS_SDK/build" && taskset -c 1,2 make -j1 >>"$LOG" 2>&1; echo "EXIT_CODE=$?" >>"$LOG")
  tail -1 "$LOG" | grep -q 'EXIT_CODE=0' || die "compile failed -- see $LOG (re-running resumes)"
  built || die "the plugin was built but doesn't load -- see $LOG"
  ok "kvssink built"
  save_cache
}

case "${1:-start}" in
  build)      build; proofs ;;
  save-cache) built || die "no finished build to cache"; save_cache ;;
  status)
    if built; then ok "built"; else
      note "unit $UNIT: $(systemctl --user is-active $UNIT 2>/dev/null || true)"
      tail -c 400 "$LOG" 2>/dev/null | tr '\r' '\n' | tail -3
    fi ;;
  wait)
    while systemctl --user is-active --quiet $UNIT; do sleep 30; done
    proofs ;;
  start)
    step "KVS SDK ($KVS_SDK_TAG)"
    if built; then ok "already built and patched"; save_cache; proofs; exit $?; fi
    if restore_cache; then ok "restored from the stick's cache in seconds instead of hours"; proofs; exit $?; fi
    if systemctl --user is-active --quiet $UNIT; then ok "the build is already running"; exit 0; fi
    # In the background, as a transient user unit: survives a lost SSH/VS Code session (it is
    # outside the session's cgroup, and lingering is on -- setup-system.sh).
    systemd-run --user --unit=$UNIT --collect --working-directory="$VMS_HOME" \
      bash "$SETUP_DIR/setup-sdk.sh" build >/dev/null
    ok "build started in the background (1.5-2.5 h). Follow it with:"
    note "bash setup/setup-sdk.sh status      or      journalctl --user -u $UNIT -f"
    note "the rest of the setup can go on meanwhile; only the producers need the plugin" ;;
  *) die "usage: setup-sdk.sh [start|status|wait|build|save-cache]" ;;
esac
