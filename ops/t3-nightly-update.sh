#!/usr/bin/env bash
# Nightly update of T3 Code and the coding-agent CLIs on this machine.
#
#   t3-nightly-update.sh check     print installed and latest versions, change nothing
#   t3-nightly-update.sh clis      upgrade the CLIs (run only while no agent is working)
#   t3-nightly-update.sh stage     download and unpack the latest nightly beside the app
#   t3-nightly-update.sh restart   stop T3 Code, swap in the staged build, start it,
#                                  and roll back if it does not come up
#
# Paths and tool lists come from a config file. `--config FILE` before the step names it.
# See ops/config.example and docs/operations.md.
#
# `restart` kills every running T3 thread. Start it detached and end the turn:
#   setsid -f ops/t3-nightly-update.sh restart </dev/null >/dev/null 2>&1
set -u
config_error() { printf '%s: config file %s %s\n' "${0##*/}" "$NIGHTLY_CONFIG" "$1" >&2; exit 78; }

check_words() {
  local key=$1 first_glob=$2 allowed_set=$3 word
  shift 3
  [ $# -gt 0 ] || config_error "gives $key no value"
  for word; do
    case "$word" in
      $first_glob*) case "$word" in *[!$allowed_set]*) ;; *) continue ;; esac ;;
    esac
    config_error "gives $key the value '$word'. It must start with $first_glob and use only the characters $allowed_set."
  done
}

load_config() {
  local LC_ALL=C ws=$' \t\r' line key value n=0 seen=" "
  [ -e "$NIGHTLY_CONFIG" ] || { printf '%s: no config file at %s. Copy ops/config.example to that path and set every key.\n' "${0##*/}" "$NIGHTLY_CONFIG" >&2; exit 78; }
  [ -f "$NIGHTLY_CONFIG" ] && [ -r "$NIGHTLY_CONFIG" ] || config_error "is not a readable file"
  while IFS= read -r line || [ -n "$line" ]; do
    n=$((n + 1))
    line=${line#"${line%%[!$ws]*}"}; line=${line%"${line##*[!$ws]}"}
    case "$line" in ''|'#'*) continue ;; esac
    key=${line%%=*}; value=${line#*=}; value=${value#"${value%%[!$ws]*}"}
    case "$line" in *=*) ;; *) config_error "line $n is not KEY=value" ;; esac
    case "$key" in ''|[!A-Z]*|*[!A-Z0-9_]*) config_error "line $n is not KEY=value" ;; esac
    case "$seen" in *" $key "*) config_error "line $n sets $key a second time" ;; esac
    seen="$seen$key "
    case "$key" in
      NIGHTLY_APPS_DIR) APPS=$value ;;
      NIGHTLY_APP_NAME) NIGHTLY_APP_NAME=$value ;;
      NIGHTLY_RESTART_ENV) NIGHTLY_RESTART_ENV=$value ;;
      NIGHTLY_MISE_TOOLS) read -r -a CLIS <<<"$value" ;;
      NIGHTLY_VERSION_COMMANDS) read -r -a NIGHTLY_VERSION_COMMANDS <<<"$value" ;;
      NIGHTLY_*) config_error "line $n sets $key, and this script has no such key" ;;
    esac
  done <"$NIGHTLY_CONFIG"
  for key in NIGHTLY_APPS_DIR NIGHTLY_APP_NAME NIGHTLY_RESTART_ENV NIGHTLY_MISE_TOOLS NIGHTLY_VERSION_COMMANDS; do
    case "$seen" in *" $key "*) ;; *) config_error "does not set $key" ;; esac
  done
  check_words NIGHTLY_APPS_DIR         /             'A-Za-z0-9._/-'    "$APPS"
  check_words NIGHTLY_APP_NAME         '[A-Za-z0-9]' 'A-Za-z0-9._-'     "$NIGHTLY_APP_NAME"
  check_words NIGHTLY_RESTART_ENV      /             'A-Za-z0-9._/-'    "$NIGHTLY_RESTART_ENV"
  check_words NIGHTLY_MISE_TOOLS       '[A-Za-z0-9]' 'A-Za-z0-9._:@/+-' "${CLIS[@]}"
  check_words NIGHTLY_VERSION_COMMANDS '[A-Za-z0-9]' 'A-Za-z0-9._+-'    "${NIGHTLY_VERSION_COMMANDS[@]}"
}

if [ "${1:-}" = --config ]; then
  [ $# -ge 2 ] || { printf '%s: --config needs a file\n' "${0##*/}" >&2; exit 2; }
  NIGHTLY_CONFIG=$2; shift 2
elif [ -n "${PSTACK_T3_OPS_CONFIG:-}" ]; then
  NIGHTLY_CONFIG=$PSTACK_T3_OPS_CONFIG
else
  case "${XDG_CONFIG_HOME:-}" in /*) NIGHTLY_CONFIG=$XDG_CONFIG_HOME ;; *) NIGHTLY_CONFIG=$HOME/.config ;; esac
  NIGHTLY_CONFIG=$NIGHTLY_CONFIG/pstack-t3/ops.conf
fi
APPS='' CLIS=() NIGHTLY_APP_NAME='' NIGHTLY_RESTART_ENV='' NIGHTLY_VERSION_COMMANDS=()
load_config
APP="$APPS/$NIGHTLY_APP_NAME"
NEW="$APPS/$NIGHTLY_APP_NAME.new"
hide_reader_from_restart_env() {
  unset -f load_config check_words config_error hide_reader_from_restart_env
  unset NIGHTLY_CONFIG NIGHTLY_APP_NAME
}
hide_reader_from_restart_env
BIN="$APP/t3code"
REPO="pingdotgg/t3code"
LOG="$APPS/t3-nightly-update.log"
STATE="$APPS/t3-nightly-update.state"
# mise holds a new release for 24 hours by default. The tools in NIGHTLY_MISE_TOOLS take the
# newest release at once. Every other mise tool keeps the hold.
NOHOLD=(env MISE_MINIMUM_RELEASE_AGE=0)

say() { printf '%s %s\n' "$(date -Is)" "$*" | tee -a "$LOG"; }

installed() {
  if [ -f "$APP/.nightly-version" ]; then cat "$APP/.nightly-version"; return; fi
  strings -n 20 "$APP/resources/app.asar" 2>/dev/null |
    grep -o '0\.0\.[0-9]*-nightly\.[0-9]*\.[0-9]*' | sort -u | tail -1
}

latest() {
  gh release list -R "$REPO" --limit 40 --json tagName,publishedAt \
    -q '[.[]|select(.tagName|test("-nightly\\."))]|sort_by(.publishedAt)|last|.tagName' |
    sed 's/^v//'
}

cmd_check() {
  echo "t3 installed: $(installed)"
  echo "t3 latest:    $(latest)"
  [ -d "$NEW" ] && echo "t3 staged:    $(cat "$NEW/.nightly-version" 2>/dev/null || echo unknown)"
  echo "clis outdated:"
  "${NOHOLD[@]}" mise outdated "${CLIS[@]}" 2>&1 | sed 's/^/  /'
  echo "cursor-cli: $(pacman -Q cursor-cli 2>/dev/null | awk '{print $2}')"
}

cmd_clis() {
  say "clis: upgrading ${CLIS[*]}"
  "${NOHOLD[@]}" mise upgrade "${CLIS[@]}" >>"$LOG" 2>&1 && say "clis: mise upgrade ok" || say "clis: mise upgrade FAILED, see log"
  if command -v yay >/dev/null && pacman -Q cursor-cli >/dev/null 2>&1; then
    yay -S --needed --noconfirm cursor-cli >>"$LOG" 2>&1 && say "clis: cursor-cli ok" || say "clis: cursor-cli FAILED, see log"
  fi
  for c in "${NIGHTLY_VERSION_COMMANDS[@]}"; do
    command -v "$c" >/dev/null && say "clis: $c $("$c" --version 2>/dev/null | head -1)"
  done
}

cmd_stage() {
  local have want file url_tag sum512 got512
  have=$(installed); want=$(latest)
  [ -n "$want" ] || { say "stage: cannot read the latest nightly"; return 1; }
  if [ "$have" = "$want" ]; then say "stage: already on $have"; rm -f "$STATE"; return 3; fi
  url_tag="v$want"; file="T3-Code-$want-x86_64.AppImage"
  say "stage: $have -> $want"
  cd "$APPS" || return 1
  if [ ! -f "$file" ]; then
    gh release download "$url_tag" -R "$REPO" -p "$file" -O "$file.part" >>"$LOG" 2>&1 || { say "stage: download FAILED"; rm -f "$file.part"; return 1; }
    mv -f "$file.part" "$file"
  fi
  sum512=$(gh release download "$url_tag" -R "$REPO" -p nightly-linux.yml -O - 2>>"$LOG" |
    awk -v f="$file" '$0 ~ "url: " f {hit=1} hit && /sha512:/ {print $2; exit}')
  got512=$(openssl dgst -sha512 -binary "$file" | base64 -w0)
  if [ -z "$sum512" ] || [ "$sum512" != "$got512" ]; then
    say "stage: checksum mismatch for $file (want '${sum512:0:16}', got '${got512:0:16}')"
    mv -f "$file" "$file.bad"; return 1
  fi
  chmod +x "$file"
  rm -rf "$APPS/squashfs-root" "$NEW"
  ( cd "$APPS" && "./$file" --appimage-extract >/dev/null 2>>"$LOG" ) || { say "stage: unpack FAILED"; return 1; }
  [ -x "$APPS/squashfs-root/t3code" ] || { say "stage: unpacked tree has no t3code"; rm -rf "$APPS/squashfs-root"; return 1; }
  mv -T "$APPS/squashfs-root" "$NEW" || return 1
  chmod 755 "$NEW"
  echo "$want" >"$NEW/.nightly-version"
  printf 'from=%s\nto=%s\nstaged=%s\n' "$have" "$want" "$(date -Is)" >"$STATE"
  say "stage: ready at $NEW"
}

running() { pgrep -f "^$BIN\$" >/dev/null; }

start_app() {
  # shellcheck disable=SC1091
  source "$NIGHTLY_RESTART_ENV"
  setsid -f "$BIN" >/dev/null 2>&1 </dev/null
  for _ in $(seq 1 45); do sleep 1; running && return 0; done
  return 1
}

cmd_restart() {
  local from to prev pid
  [ -d "$NEW" ] && [ -x "$NEW/t3code" ] || { say "restart: nothing staged"; return 1; }
  from=$(installed); to=$(cat "$NEW/.nightly-version" 2>/dev/null || echo unknown)
  prev="$APP.prev-${from#*nightly.}"
  [ -e "$prev" ] && prev="$prev.$(date +%H%M%S)"
  say "restart: $from -> $to"
  sleep 5
  pid=$(pgrep -o -f "^$BIN\$") && kill -TERM "$pid"
  for _ in $(seq 1 60); do pgrep -f "$APP/t3code" >/dev/null || break; sleep 1; done
  pkill -KILL -f "$APP/t3code" 2>/dev/null
  sleep 1
  mv -T "$APP" "$prev" || { say "restart: could not move the old build aside; starting it again"; start_app; return 1; }
  if ! mv -T "$NEW" "$APP"; then
    mv -T "$prev" "$APP"; say "restart: swap FAILED, old build restored"; start_app; return 1
  fi
  if start_app; then
    sleep 15
    if running; then
      printf 'from=%s\nto=%s\nrestarted=%s\nresult=ok\nprev=%s\n' "$from" "$to" "$(date -Is)" "$prev" >"$STATE"
      say "restart: running $to"
      return 0
    fi
  fi
  say "restart: $to did not stay up; rolling back to $from"
  pkill -KILL -f "$APP/t3code" 2>/dev/null; sleep 1
  mv -T "$APP" "$APP.failed-${to#*nightly.}" && mv -T "$prev" "$APP"
  if start_app; then
    printf 'from=%s\nto=%s\nrestarted=%s\nresult=rolled-back\n' "$from" "$to" "$(date -Is)" >"$STATE"
    say "restart: rolled back, running $from"
    notify-send -u critical "T3 Code update rolled back" "$to did not start. Running $from again." 2>/dev/null
  else
    printf 'from=%s\nto=%s\nrestarted=%s\nresult=down\n' "$from" "$to" "$(date -Is)" >"$STATE"
    say "restart: T3 Code is DOWN after rollback"
    notify-send -u critical "T3 Code is down" "The nightly update to $to failed and the rollback did not start. See $LOG." 2>/dev/null
  fi
  return 1
}

case "${1:-}" in
  check) cmd_check ;;
  clis) cmd_clis ;;
  stage) cmd_stage ;;
  restart) cmd_restart ;;
  *) sed -n '2,14p' "$0"; exit 2 ;;
esac
