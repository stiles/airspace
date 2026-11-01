# Run Airspace as a Raspberry Pi kiosk

This guide turns a Raspberry Pi with an HDMI monitor into a dedicated Airspace display. After power-on, the Pi joins Wi-Fi, starts the backend, logs in to the desktop and opens the dashboard full screen in Chromium. Nothing needs a keyboard, mouse or SSH session, and SSH stays available for administration.

[pi-setup.md](pi-setup.md) covers getting Airspace onto the Pi in the first place. This guide picks up once `uv run airspace serve` works by hand.

## How it fits together

| Piece | What it does |
| --- | --- |
| `airspace.service` | A systemd service that runs `.venv/bin/airspace serve` as your user at boot, restarts it if it crashes and logs to journald. Rendered from [deploy/airspace.service.in](../deploy/airspace.service.in) |
| `scripts/airspace-kiosk` | Waits for the backend, opens Chromium in kiosk mode and reopens it if it exits. Started by the desktop session, and controllable over SSH |
| `~/.config/autostart/airspace-kiosk.desktop` | Starts `airspace-kiosk` when the desktop session starts |
| `raspi-config` | Boots to the desktop with your user logged in and turns off screen blanking |
| `scripts/install-pi-kiosk.sh` | Installs all of the above. Safe to rerun |
| `scripts/update-pi.sh` | Pulls, rebuilds and restarts |

The backend and the browser are separate. The backend starts at boot whether or not anyone is logged in, so it records even if the desktop fails. The browser belongs to the desktop session, because that's where the Wayland display and its permissions are.

Raspberry Pi OS Trixie on a Pi 5 uses Wayland with the labwc compositor. The kiosk script doesn't assume `DISPLAY=:0`: started from the desktop, it uses the session's own environment, and started from SSH, it finds the session's Wayland socket in `/run/user/<uid>/`. On an X11 desktop it uses `DISPLAY` instead.

## Prerequisites

- Raspberry Pi OS with Desktop (Debian 13 Trixie), with SSH enabled and Wi-Fi configured, as in [pi-setup.md](pi-setup.md)
- The repository at `~/github/airspace`, with `uv sync` done
- A valid `airspace.toml` (`uv run airspace init`)
- `web/.env.local` with `VITE_MAPBOX_TOKEN`, and a built dashboard (`cd web && npm ci && npm run build`)
- `chromium` and `curl` installed (`sudo apt install -y chromium curl`)
- Nothing else running `airspace serve` or `airspace collect`. Stop any copy started by hand with `Ctrl+C` in its terminal

## Install

Over SSH, as the desktop user (`canterlot`), not root:

```bash
cd ~/github/airspace
./scripts/install-pi-kiosk.sh
```

The script asks for your password when it uses `sudo`. It:

1. Checks the config, the virtual environment, the dashboard build and the Mapbox token file.
2. Reports which compositor the desktop uses.
3. Installs and starts `airspace.service`, then waits for `http://127.0.0.1:8000/api/health`. It stops with an error if another collector holds the lock.
4. Adds the kiosk to the desktop autostart.
5. Sets the Pi to boot to the desktop with your user logged in and turns off screen blanking, through `raspi-config`.
6. Checks that Wi-Fi connects at boot and that SSH is enabled.

It doesn't write `airspace.toml`, `web/.env.local` or `data/`. Rerunning it only changes what differs.

Then open the kiosk without rebooting, or reboot to test the whole sequence:

```bash
./scripts/airspace-kiosk start
sudo reboot
```

If a Chromium window opened by hand over SSH is still showing, close it first (`pkill chromium`). The kiosk uses its own profile in `~/.local/state/airspace-kiosk/chromium`, so it would otherwise open a second window.

### Options

| Setting | Default | Notes |
| --- | --- | --- |
| `--no-autologin` | off | Leave boot and login settings alone, e.g. if you set up autologin yourself |
| `--no-blanking` | off | Leave screen blanking alone |
| `AIRSPACE_DIR` | this checkout | Repository path |
| `AIRSPACE_PORT` | `8000` | Port on 127.0.0.1 for the service and the kiosk |
| `AIRSPACE_SERVICE` | `airspace` | systemd service name |
| `AIRSPACE_KIOSK_AUTOSTART` | `auto` | `xdg` uses `~/.config/autostart`; `labwc` adds a line to `~/.config/labwc/autostart`. `auto` picks `xdg` unless labwc has no XDG autostart handler |

For example: `AIRSPACE_PORT=8010 ./scripts/install-pi-kiosk.sh`. `--print-unit` and `--print-autostart` show what would be installed without changing anything.

## What happens at boot

1. NetworkManager connects to the saved Wi-Fi network. Connections set up in Raspberry Pi Imager are system connections, so this doesn't wait for a login.
2. `airspace.service` starts after `network-online.target`. It runs `/home/canterlot/github/airspace/.venv/bin/airspace serve --port 8000 --no-access-log` directly, with no shell, and reads `airspace.toml` from the repository. If Wi-Fi is slow, the first polls fail and the collector backs off and retries, as it would on a laptop.
3. The display manager logs `canterlot` in to the desktop.
4. The desktop session runs `airspace-kiosk run`, which waits until `/api/health` responds, then opens Chromium full screen at `http://127.0.0.1:8000`.

Recovery:

- **Backend crash:** systemd restarts the service 5 seconds after a failure, backing off to 2 minutes if it keeps failing. It never gives up, except when `airspace.toml` is missing or invalid (exit status 2), since retrying won't fix that. The dashboard shows the outage in red and recovers on its own when the backend returns.
- **Browser crash or close:** the kiosk script reopens Chromium, after 2 seconds the first time and backing off to 1 minute if it keeps crashing. Pressing `Alt+F4` on the Pi also just reopens it.
- **Duplicates:** systemd runs one copy of the service and the collector lock refuses a second collector. The kiosk script holds a lock so only one supervisor runs, and it won't open a second window while a kiosk Chromium is already open.

The request log is off in the service because the dashboard polls every 2 seconds, which would add about 43,000 lines a day to the journal. Collector messages, warnings and errors are still logged.

## Everyday commands

Backend:

```bash
sudo systemctl start airspace
sudo systemctl stop airspace
sudo systemctl restart airspace      # reloads airspace.toml
systemctl status airspace
journalctl -u airspace -f            # follow logs
journalctl -u airspace -b            # everything since this boot
journalctl -u airspace -b -1         # the previous boot, if the journal is persistent
```

`sudo systemctl stop airspace` closes the session and releases the collector lock before it exits. To stop it from starting at boot, `sudo systemctl disable airspace`.

Kiosk, from SSH or a terminal on the Pi:

```bash
cd ~/github/airspace
./scripts/airspace-kiosk status      # supervisor, Chromium and backend
./scripts/airspace-kiosk restart     # close Chromium; it reopens and reloads the dashboard
./scripts/airspace-kiosk stop        # close it until the next login or `start`
./scripts/airspace-kiosk start       # open it in the desktop session
journalctl -t airspace-kiosk -f      # kiosk messages
less ~/.local/state/airspace-kiosk/chromium.log   # Chromium's own output for the current launch
```

The API is still at `http://127.0.0.1:8000` on the Pi only. To see the dashboard from a Mac, use an SSH tunnel rather than changing the bind address:

```bash
ssh -N -L 8000:127.0.0.1:8000 canterlot@raspberrypi.local
```

## Update the application

```bash
cd ~/github/airspace
./scripts/update-pi.sh
```

This runs the same steps you can run by hand:

```bash
git pull --ff-only
uv sync --locked                      # adds --extra enrich if FAA registry lookups are installed
cd web && npm ci && npm run build && cd ..
sudo systemctl restart airspace
./scripts/airspace-kiosk restart      # load the new dashboard build
```

Use the same extras every time: a plain `uv sync` removes DuckDB if it was installed with `--extra enrich`. The update script checks for it. None of these steps touch `airspace.toml`, `web/.env.local` or `data/`, which are gitignored.

Rerun `./scripts/install-pi-kiosk.sh` after an update only if `deploy/airspace.service.in` or the install script changed (`git log -- deploy scripts`). Rerunning it is harmless otherwise.

### When the dashboard needs a rebuild

Vite builds the Mapbox token and style into the JavaScript bundle, and `web/dist/` isn't tracked in Git. Rebuild after:

- any `git pull` that changes `web/`
- changing `VITE_MAPBOX_TOKEN` or `VITE_MAPBOX_STYLE` in `web/.env.local`

To rebuild without pulling, run `./scripts/update-pi.sh --no-pull`. `airspace serve` logs a warning when the build is missing or older than its source (`journalctl -u airspace | grep Warning`). Changing `airspace.toml` never needs a rebuild.

## Change the home coordinates

Edit `[home]` in `airspace.toml`, then restart the backend and reload the browser:

```bash
nano ~/github/airspace/airspace.toml          # latitude, longitude, label, title
sudo systemctl restart airspace
curl -s http://127.0.0.1:8000/api/state | head -c 400; echo
~/github/airspace/scripts/airspace-kiosk restart
```

The dashboard reads the home point from the API, so it doesn't need a rebuild. If the file is invalid, the service stops instead of restarting. `systemctl status airspace` shows the validation error. Fix the file and run `sudo systemctl restart airspace` again.

## Change the polling intervals

The `[collector]` section of `airspace.toml` controls polling:

```toml
[collector]
poll_interval_s = 10      # normal rate
fast_interval_s = 2       # while a helicopter or public-safety aircraft is nearby; 0 turns it off
fast_hold_s = 60          # how long fast polling continues after the last one
```

adsb.fi allows one request per second, so neither interval can go below 1. Restart the backend afterward with `sudo systemctl restart airspace`. The browser doesn't need a reload. The dashboard's own 2-second refresh from the local API is separate and doesn't cause provider requests.

## Run other Airspace commands

`airspace report`, `sessions` and `export` read the database and work while the service runs:

```bash
cd ~/github/airspace
.venv/bin/airspace report --session latest
```

`airspace collect` and a second `airspace serve` are collectors and refuse to start while the service holds the lock. Stop the service first (`sudo systemctl stop airspace`) and start it again afterward.

## Troubleshooting

### The backend

- `systemctl status airspace` shows the state, the last exit status and recent log lines.
- `another collector (pid N) is using data`: something outside the service is collecting. Check with `ps -p N -o args=` and stop it. The service retries on its own and takes over once the lock is free.
- `airspace.toml not found` or `Invalid configuration`: the service has stopped retrying. Fix the file, then `sudo systemctl restart airspace`.
- `address already in use`: something else is on port 8000, e.g. `airspace replay`. Find it with `sudo ss -ltnp 'sport = :8000'`.
- The first polls after boot fail with transport errors: Wi-Fi wasn't up yet. They recover on their own. If they don't, check `nmcli device status`.

### Chromium

- **A keyring prompt appears:** the kiosk passes `--password-store=basic`, so the prompt is probably from a Chromium started some other way. `pgrep -af chromium` lists them; the kiosk's has `--user-data-dir=...airspace-kiosk...`.
- **"Restore pages?" or a first-run page appears:** the kiosk marks the previous exit as clean before each launch and passes `--no-first-run`. If it still appears, run `./scripts/airspace-kiosk stop`, delete `~/.local/state/airspace-kiosk/chromium` and `start` again.
- **The screen shows the desktop, not the dashboard:** run `./scripts/airspace-kiosk status`. If the supervisor is waiting for the backend, look at the backend logs. If it isn't running, the autostart didn't fire; see below.
- **The dashboard loads but the map is blank:** the token is missing from the build or the Mapbox token's URL restrictions don't include `http://127.0.0.1:*`. Check `web/.env.local` and rebuild.
- **Chromium keeps crashing:** `journalctl -t airspace-kiosk` shows each exit status and `~/.local/state/airspace-kiosk/chromium.log.1` has the previous launch's output.

### Wayland and the desktop

- **Which compositor is running:** `pgrep -a labwc` (or `wayfire`, `Xorg`). `ls /run/user/$(id -u)/` shows a `wayland-0` socket under Wayland.
- **Starting Chromium from SSH:** use `./scripts/airspace-kiosk start`, which finds the Wayland socket and the session's D-Bus. A bare `DISPLAY=:0 chromium` only works through XWayland, and with SSH X forwarding `DISPLAY` points at your Mac.
- **The kiosk doesn't open at login:** check `~/.config/autostart/airspace-kiosk.desktop` exists. Some labwc setups don't run XDG autostart entries; rerun the installer with `AIRSPACE_KIOSK_AUTOSTART=labwc`, which adds the command to `~/.config/labwc/autostart` between `# >>> airspace kiosk >>>` markers. Creating that file can stop labwc from reading `/etc/xdg/labwc/autostart`, which starts the panel and wallpaper. A kiosk doesn't need either.
- **The Pi stops at a login screen:** autologin isn't set. Run `sudo raspi-config nonint do_boot_behaviour B4` (desktop autologin) or choose System Options, Boot / Auto Login in `sudo raspi-config`.
- **The screen still blanks:** run `sudo raspi-config nonint do_blanking 1` and reboot, or choose Display Options, Screen Blanking, No. `pgrep -a swayidle` should then show nothing. Check the monitor's own power-saving settings too: some monitors sleep on their own timer.

### SSH

- The kiosk setup doesn't change SSH. If `raspberrypi.local` doesn't resolve, mDNS is failing on your network; find the Pi's address in the router and use `ssh canterlot@<address>`. Reserving that address in the router helps.
- A host-key warning after reinstalling the OS: once you're sure it's your Pi, `ssh-keygen -R raspberrypi.local`.
- SSH lags or drops on Wi-Fi: Wi-Fi power saving can cause this. To turn it off for one connection: `sudo nmcli connection modify "<name>" 802-11-wireless.powersave 2`, then reconnect. Leave it on unless you see the problem.

## Recover from a failed boot or a broken deployment

Everything can be done over SSH. Start by finding which part failed:

```bash
systemctl status airspace
journalctl -u airspace -b --no-pager | tail -50
journalctl -t airspace-kiosk -b --no-pager
```

**An update broke the backend or the dashboard.** Go back to the last version that worked and rebuild:

```bash
cd ~/github/airspace
git log --oneline -10
git checkout <commit>                  # the last good commit
./scripts/update-pi.sh --no-pull
```

Run `git checkout main` when a fix is available, then `./scripts/update-pi.sh`. Your config, token and recordings aren't in Git, so switching commits doesn't touch them.

**The virtual environment is broken** (`ModuleNotFoundError`, or `.venv/bin/airspace` is missing): `rm -rf .venv && uv sync` (with `--extra enrich` if you use it), then `sudo systemctl restart airspace`.

**The browser shows something wrong and you want the plain desktop:** `./scripts/airspace-kiosk stop`. To skip the kiosk at the next boots too, move the autostart entry aside: `mv ~/.config/autostart/airspace-kiosk.desktop ~/airspace-kiosk.desktop.off`. Rerun the installer to restore it.

**You need the Pi to stop booting into the desktop:** `sudo raspi-config nonint do_boot_behaviour B1` boots to a console login. The backend still starts, since it doesn't depend on the desktop.

**The Pi doesn't come back on the network after a Wi-Fi change:** connect a keyboard and monitor, log in and run `sudo nmtui`, or connect Ethernet and SSH in. Raspberry Pi Imager can also write a new card with the right Wi-Fi details. Back up `airspace.toml`, `web/.env.local` and `data/` first if the old card is readable.

**The SD card is full:** recordings are pruned after `retention_days` (7 by default; about 600 MB a week). Check with `df -h` and `du -sh ~/github/airspace/data`, lower `retention_days` and restart the backend. To shrink the database file, stop the service, run `sqlite3 data/airspace.sqlite VACUUM` and start it again.

## Verify on the Pi

Run these after installing and again after any change to the deployment files.

1. **Boot:** `sudo reboot`. Without touching the Pi, the dashboard should appear full screen within a minute or two, with no keyring, first-run or restore prompts, and aircraft should update.
2. **Service:** `systemctl is-enabled airspace` prints `enabled`, `systemctl is-active airspace` prints `active` and `curl -s http://127.0.0.1:8000/api/health` shows `"collector_running":true`.
3. **One collector:** `pgrep -af 'airspace (serve|collect)'` lists one process, and `.venv/bin/airspace collect --duration 1m` refuses to start.
4. **Backend crash:** `sudo systemctl kill -s KILL airspace`. Within about 10 seconds `systemctl status airspace` shows a new PID, and the dashboard goes red, then live again.
5. **Browser crash:** `pkill -f airspace-kiosk/chromium`. Chromium reopens within a few seconds, with no restore prompt.
6. **Config reload:** change `title` in `airspace.toml`, `sudo systemctl restart airspace`, `./scripts/airspace-kiosk restart`. The new title shows. Change it back.
7. **Screen stays on:** leave the Pi alone for longer than the old blanking timeout (10 minutes by default). The dashboard should still be showing.
8. **SSH:** you can still connect after the reboot.
9. **Files kept:** `airspace.toml`, `web/.env.local` and `data/` are unchanged (`ls -l` before and after, or `git status --ignored`).

## Uninstall

```bash
cd ~/github/airspace
./scripts/install-pi-kiosk.sh --uninstall
```

This stops and removes `airspace.service`, stops the kiosk and removes its autostart entry. It leaves `airspace.toml`, `web/.env.local`, `data/` and the kiosk's Chromium profile in `~/.local/state/airspace-kiosk`. It doesn't change autologin or screen blanking back; use `sudo raspi-config` (System Options, Boot / Auto Login and Display Options, Screen Blanking) for those. After uninstalling, `uv run airspace serve` works by hand as before.
