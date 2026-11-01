#!/usr/bin/env bash
# Sets up a Raspberry Pi to run Airspace unattended: a systemd service for the backend and a
# full-screen Chromium window that opens with the desktop. Safe to rerun.
#
#   ./scripts/install-pi-kiosk.sh                 Install or update the setup
#   ./scripts/install-pi-kiosk.sh --uninstall     Remove the service and kiosk autostart
#   ./scripts/install-pi-kiosk.sh --print-unit    Print the rendered systemd unit and exit
#   ./scripts/install-pi-kiosk.sh --print-autostart   Print the desktop autostart entry and exit
#
# Options:
#   --no-autologin   Leave the boot and login settings alone
#   --no-blanking    Leave the screen blanking setting alone
#
# Environment (defaults in brackets):
#   AIRSPACE_DIR      repository [the checkout containing this script]
#   AIRSPACE_SERVICE  systemd service name [airspace]
#   AIRSPACE_PORT     port on 127.0.0.1 [8000]
#   AIRSPACE_USER     account that runs the service and desktop [the current user]
#   AIRSPACE_KIOSK_AUTOSTART  auto, xdg or labwc [auto]
#
# Never writes airspace.toml, web/.env.local or data/.

set -euo pipefail

repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
dir=${AIRSPACE_DIR:-$repo}
service=${AIRSPACE_SERVICE:-airspace}
port=${AIRSPACE_PORT:-8000}
user=${AIRSPACE_USER:-$(id -un)}
group=${AIRSPACE_GROUP:-$(id -gn "$user" 2>/dev/null || echo "$user")}
autostart_mode=${AIRSPACE_KIOSK_AUTOSTART:-auto}

action=install
set_autologin=1
set_blanking=1
while [ $# -gt 0 ]; do
  case $1 in
    --uninstall) action=uninstall ;;
    --print-unit) action=print-unit ;;
    --print-autostart) action=print-autostart ;;
    --no-autologin) set_autologin=0 ;;
    --no-blanking) set_blanking=0 ;;
    -h|--help) sed -n '2,21p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option $1; see --help" >&2; exit 64 ;;
  esac
  shift
done

kiosk=$dir/scripts/airspace-kiosk
unit_path=/etc/systemd/system/$service.service
desktop_file=${XDG_CONFIG_HOME:-$HOME/.config}/autostart/airspace-kiosk.desktop
labwc_autostart=${XDG_CONFIG_HOME:-$HOME/.config}/labwc/autostart
marker_start="# >>> airspace kiosk >>>"
marker_end="# <<< airspace kiosk <<<"
health_url=http://127.0.0.1:$port/api/health

say() { printf '%s\n' "$*"; }
step() { printf '\n== %s\n' "$*"; }
warn() { printf 'Warning: %s\n' "$*" >&2; }
die() { printf 'Error: %s\n' "$*" >&2; exit 1; }

validate_settings() {
  case $port in
    ''|*[!0-9]*) die "AIRSPACE_PORT must be a number, got '$port'" ;;
  esac
  [ "$port" -ge 1 ] && [ "$port" -le 65535 ] || die "AIRSPACE_PORT must be between 1 and 65535"
  case $service in
    ''|*[!A-Za-z0-9_.@-]*) die "AIRSPACE_SERVICE may only contain letters, digits and _ . @ -" ;;
  esac
  case $dir in
    /*) ;;
    *) die "AIRSPACE_DIR must be an absolute path" ;;
  esac
  case $dir$user$group in
    *'|'*|*'"'*|*' '*) die "paths and names can't contain spaces, quotes or '|'" ;;
  esac
  case $autostart_mode in
    auto|xdg|labwc) ;;
    *) die "AIRSPACE_KIOSK_AUTOSTART must be auto, xdg or labwc" ;;
  esac
}

render_unit() {
  sed \
    -e "s|@USER@|$user|g" \
    -e "s|@GROUP@|$group|g" \
    -e "s|@DIR@|$dir|g" \
    -e "s|@PORT@|$port|g" \
    "$repo/deploy/airspace.service.in"
}

kiosk_command() {
  printf '%s run --port %s' "$kiosk" "$port"
}

render_desktop() {
  cat <<EOF
[Desktop Entry]
Type=Application
Name=Airspace kiosk
Comment=Shows the Airspace dashboard full screen. Installed by scripts/install-pi-kiosk.sh
Exec=$(kiosk_command)
Terminal=false
NoDisplay=true
X-GNOME-Autostart-enabled=true
EOF
}

# Writes stdin to a file only when the content differs. Returns 0 if it changed.
write_if_changed() {
  local target=$1 tmp
  tmp=$(mktemp)
  cat >"$tmp"
  if [ -f "$target" ] && cmp -s "$tmp" "$target"; then
    rm -f "$tmp"
    return 1
  fi
  mkdir -p "$(dirname "$target")"
  mv "$tmp" "$target"
  chmod 0644 "$target"
}

remove_labwc_block() {
  [ -f "$labwc_autostart" ] && grep -qF "$marker_start" "$labwc_autostart" || return 0
  sed -i "/^$marker_start\$/,/^$marker_end\$/d" "$labwc_autostart"
  say "Removed the kiosk lines from $labwc_autostart"
}

python_in_repo() {
  (cd "$dir" && AIRSPACE_CONFIG="$dir/airspace.toml" "$dir/.venv/bin/python" -c "$1")
}

preflight() {
  step "Checking the system"
  [ "$(uname -s)" = Linux ] || die "this installer is for the Raspberry Pi (Linux)"
  [ "$(id -u)" -ne 0 ] || die "run as the desktop user (e.g. canterlot), not root; the script uses sudo when needed"
  [ "$user" = "$(id -un)" ] || die "AIRSPACE_USER ($user) must be the user running the installer"
  local tool
  for tool in sudo systemctl curl flock sed; do
    command -v "$tool" >/dev/null || die "$tool is not installed"
  done
  command -v chromium >/dev/null || command -v chromium-browser >/dev/null ||
    die "Chromium is not installed; run sudo apt install -y chromium"
  [ -f "$dir/deploy/airspace.service.in" ] || die "$dir doesn't look like the Airspace repository"
  [ -x "$dir/.venv/bin/airspace" ] || die "$dir/.venv/bin/airspace is missing; run uv sync in $dir"

  data_dir=$(python_in_repo 'from airspace import config; print(config.load().storage.data_dir.resolve())') ||
    die "$dir/airspace.toml is missing or invalid; run uv run airspace init or fix the file"
  say "Config:    $dir/airspace.toml (valid)"
  say "Data:      $data_dir"

  [ -f "$dir/web/dist/index.html" ] ||
    die "the dashboard isn't built; run cd $dir/web && npm ci && npm run build"
  local problem
  problem=$(python_in_repo 'from airspace.api import frontend_problem; print(frontend_problem() or "")')
  [ -z "$problem" ] || warn "$problem"
  [ -f "$dir/web/.env.local" ] ||
    warn "web/.env.local is missing, so the build has no Mapbox token; add VITE_MAPBOX_TOKEN and rebuild"
  chmod +x "$kiosk"
}

detect_desktop() {
  step "Detecting the desktop session"
  compositor=unknown
  if pgrep -u "$user" -x labwc >/dev/null; then compositor=labwc
  elif pgrep -u "$user" -x wayfire >/dev/null; then compositor=wayfire
  elif pgrep -x Xorg >/dev/null || pgrep -x X >/dev/null; then compositor=x11
  fi
  local configured
  configured=$(grep -hsE '^[[:space:]]*(autologin-session|user-session)[[:space:]]*=' \
    /etc/lightdm/lightdm.conf /etc/lightdm/lightdm.conf.d/*.conf 2>/dev/null | tail -n 1 | cut -d= -f2 | tr -d ' ')
  if [ "$compositor" = unknown ]; then
    case $configured in
      *labwc*) compositor=labwc ;;
      *wayfire*) compositor=wayfire ;;
      LXDE*|*x) compositor=x11 ;;
    esac
  fi
  say "Running:   $compositor${configured:+ (display manager session: $configured)}"
  [ "$compositor" != unknown ] ||
    warn "couldn't tell which compositor the desktop uses; is Raspberry Pi OS with Desktop installed?"
}

install_service() {
  step "Installing $service.service"
  local tmpdir changed=0
  tmpdir=$(mktemp -d)
  render_unit >"$tmpdir/$service.service"
  if command -v systemd-analyze >/dev/null && ! systemd-analyze verify "$tmpdir/$service.service" 2>"$tmpdir/verify.log"; then
    warn "systemd-analyze reported problems with the unit:"
    cat "$tmpdir/verify.log" >&2
  fi
  if ! cmp -s "$tmpdir/$service.service" "$unit_path" 2>/dev/null; then
    sudo install -m 0644 "$tmpdir/$service.service" "$unit_path"
    sudo systemctl daemon-reload
    changed=1
    say "Wrote $unit_path"
  else
    say "$unit_path is up to date"
  fi
  rm -rf "$tmpdir"
  sudo systemctl enable --quiet "$service"

  if systemctl is-active --quiet "$service"; then
    if [ "$changed" = 1 ]; then
      say "Restarting $service to apply the new unit"
      sudo systemctl restart "$service"
    fi
  else
    local lock=$data_dir/collector.lock
    if [ -f "$lock" ] && ! flock -n "$lock" true; then
      local holder
      holder=$(tr -dc 0-9 <"$lock")
      die "another collector (pid ${holder:-unknown}: $(ps -o args= -p "${holder:-0}" 2>/dev/null || echo '?')) holds $lock.
Stop it (Ctrl-C in its terminal, or kill ${holder:-<pid>}) and rerun the installer."
    fi
    sudo systemctl start "$service"
  fi

  say "Waiting for $health_url"
  for _ in $(seq 1 45); do
    if curl -fsS -o /dev/null --max-time 2 "$health_url"; then
      say "Backend is responding"
      return 0
    fi
    sleep 2
  done
  journalctl -u "$service" -n 30 --no-pager >&2 || true
  die "the backend didn't respond within 90 seconds; see journalctl -u $service"
}

install_autostart() {
  step "Installing the kiosk autostart"
  local mode=$autostart_mode
  if [ "$mode" = auto ]; then
    mode=xdg
    # labwc runs XDG autostart entries only when its autostart script calls a handler for them
    if [ "$compositor" = labwc ] &&
      ! grep -qsE 'xdg-autostart|lxsession|dex' /etc/xdg/labwc/autostart "$labwc_autostart"; then
      mode=labwc
    fi
  fi

  if [ "$mode" = xdg ]; then
    remove_labwc_block
    if render_desktop | write_if_changed "$desktop_file"; then
      say "Wrote $desktop_file"
    else
      say "$desktop_file is up to date"
    fi
  else
    rm -f "$desktop_file"
    remove_labwc_block
    if [ ! -f "$labwc_autostart" ] && [ -f /etc/xdg/labwc/autostart ]; then
      warn "creating $labwc_autostart may stop labwc from running /etc/xdg/labwc/autostart (panel and wallpaper)"
    fi
    mkdir -p "$(dirname "$labwc_autostart")"
    printf '%s\n%s &\n%s\n' "$marker_start" "$(kiosk_command)" "$marker_end" >>"$labwc_autostart"
    say "Added the kiosk to $labwc_autostart"
  fi
}

configure_desktop() {
  step "Configuring login and screen blanking"
  if ! command -v raspi-config >/dev/null; then
    warn "raspi-config isn't installed; set up desktop autologin and turn off screen blanking by hand"
    return 0
  fi
  if [ "$set_autologin" = 1 ]; then
    sudo raspi-config nonint do_boot_behaviour B4
    say "Boot to the desktop with $user logged in automatically"
  else
    say "Left the boot and login settings alone (--no-autologin)"
  fi
  if [ "$set_blanking" = 1 ]; then
    sudo raspi-config nonint do_blanking 1
    say "Screen blanking turned off (applies after a reboot)"
  else
    say "Left screen blanking alone (--no-blanking)"
  fi
  if grep -sE '^[^#]*swayidle' /etc/xdg/labwc/autostart "$labwc_autostart" >/dev/null; then
    warn "a labwc autostart file still starts swayidle, which can blank the screen:"
    grep -nsE '^[^#]*swayidle' /etc/xdg/labwc/autostart "$labwc_autostart" >&2 || true
  fi
}

check_network() {
  step "Checking networking and SSH"
  if command -v nmcli >/dev/null; then
    if nmcli -t -f TYPE,AUTOCONNECT connection show 2>/dev/null | grep -q '^802-11-wireless:yes$'; then
      say "Wi-Fi:     $(nmcli -t -f NAME,TYPE,AUTOCONNECT connection show | grep ':802-11-wireless:yes$' | cut -d: -f1 | paste -sd, -) (connects at boot)"
    else
      warn "no Wi-Fi connection is set to connect automatically; ignore this if the Pi uses Ethernet"
    fi
  fi
  if systemctl list-unit-files NetworkManager-wait-online.service >/dev/null 2>&1 &&
    ! systemctl is-enabled --quiet NetworkManager-wait-online.service 2>/dev/null; then
    warn "NetworkManager-wait-online.service is disabled, so $service may start before Wi-Fi is up (it retries)"
  fi
  if systemctl is-enabled --quiet ssh 2>/dev/null; then
    say "SSH:       enabled"
  else
    warn "the ssh service isn't enabled; run sudo systemctl enable --now ssh to keep remote access"
  fi
}

install_all() {
  validate_settings
  preflight
  detect_desktop
  install_service
  install_autostart
  configure_desktop
  check_network

  step "Done"
  cat <<EOF
The backend runs as $service.service and starts at boot. Chromium opens with the desktop.

Open the kiosk now without rebooting:  $kiosk start
Check it:                              $kiosk status
Backend logs:                          journalctl -u $service -f
Kiosk logs:                            journalctl -t airspace-kiosk -f

Reboot to test the full startup:       sudo reboot
EOF
}

uninstall() {
  validate_settings
  step "Removing the Airspace kiosk setup"
  [ "$(id -u)" -ne 0 ] || die "run as the desktop user, not root"
  if [ -x "$kiosk" ]; then "$kiosk" stop || true; fi
  rm -f "$desktop_file"
  remove_labwc_block
  if [ -f "$unit_path" ]; then
    sudo systemctl disable --now "$service" || true
    sudo rm -f "$unit_path"
    sudo systemctl daemon-reload
    say "Removed $unit_path"
  fi
  cat <<EOF

Left in place: airspace.toml, web/.env.local, data/ and the kiosk Chromium profile in
${XDG_STATE_HOME:-$HOME/.local/state}/airspace-kiosk (delete it if you like).
Desktop autologin and screen blanking were not changed back; use sudo raspi-config
(System Options > Boot / Auto Login, Display Options > Screen Blanking).
EOF
}

case $action in
  print-unit) validate_settings; render_unit ;;
  print-autostart) validate_settings; render_desktop ;;
  install) install_all ;;
  uninstall) uninstall ;;
esac
