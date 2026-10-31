# Set up Airspace on a Raspberry Pi 5

This guide records the steps used to get [Airspace](https://github.com/stiles/airspace) onto a Raspberry Pi and launch its dashboard on a directly connected monitor. The setup uses a Mac for SSH access, so the Pi does not need its own keyboard or mouse.

## Hardware and software

- Raspberry Pi 5 Model B, 8 GB RAM, ARM64 (`aarch64`)
- Raspberry Pi OS with Desktop, based on Debian 13 (Trixie)
- microSD card, power supply and HDMI monitor
- Mac on the same network
- Hostname: `raspberrypi` (accessible as `raspberrypi.local` when mDNS works)
- Username: `canterlot` (replace if using a different account)
- Airspace requires Python 3.12+, Node.js 20.19+ or 22.12+ and a Mapbox public token

## 1. Flash the microSD card

1. Install [Raspberry Pi Imager](https://www.raspberrypi.com/software/) on the Mac.
2. Insert the microSD card into the Mac.
3. Choose Raspberry Pi OS **with Desktop**, 64-bit, for the Pi 5. (Lite works for headless servers but adds work for an HDMI browser kiosk.)
4. In OS customization, set hostname `raspberrypi`, username `canterlot`, a new strong password, the **current** Wi-Fi SSID and password, locale/time zone and **enable SSH** with password authentication initially.
5. Write the image. **This erases the card.** Eject it, insert it into the Pi, connect the monitor and power it on. Wait for first boot.

If the Pi cannot be found, check that the Wi-Fi name and password match the current network. In this setup, the old Pi had been configured for an obsolete SSID.

## 2. Connect over SSH from the Mac

```bash
ping -c 4 raspberrypi.local
ssh canterlot@raspberrypi.local
```

Enter the password created in Imager. A `Connection refused` error means the hostname resolved but port 22 was not accepting connections; it does **not** necessarily mean the Pi was offline.

If SSH was not enabled, shut down and remove the microSD card, insert it into the Mac and create an empty `ssh` file on its FAT boot partition (typically mounted as `/Volumes/bootfs`):

```bash
touch /Volumes/bootfs/ssh
```

Eject, reinsert into the Pi, boot and retry SSH. This enables the SSH service but does not create a user account: the username/password must already have been configured. If the boot partition has another name, use that actual mount path.

If a reinstalled Pi triggers a host-key warning, **after verifying it is your Pi**, remove the stale entry and reconnect:

```bash
ssh-keygen -R raspberrypi.local
ssh canterlot@raspberrypi.local
```

## 3. Confirm the Pi model and OS

Run inside the Pi's SSH session:

```bash
cat /etc/os-release
cat /proc/device-tree/model; echo
uname -m
free -h
```

The tested device reported Raspberry Pi 5 Model B Rev 1.1, Debian 13 (Trixie), `aarch64` and approximately 8 GB RAM.

## 4. Install system packages

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y \
  git curl build-essential \
  chromium
```

pyproj installs from a prebuilt ARM64 wheel, so PROJ doesn't need to be installed separately. If `uv sync` ever tries to build pyproj from source, install `libproj-dev proj-data python3-dev` and retry.

## 5. Install uv and Airspace

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc
uv --version

mkdir -p ~/github
cd ~/github
git clone https://github.com/stiles/airspace.git
cd airspace
uv sync
```

The repository has `.python-version` set to Python 3.12; `uv` can provision it. Optional FAA registry enrichment can be installed later with `uv sync --extra enrich` and a configured hangarbay database.

## 6. Install Node.js

Node and npm were absent on the fresh Pi. Install Node.js 22 using NodeSource:

```bash
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt install -y nodejs
node --version
npm --version
```

Verify that Node is a compatible 22.x release.

## 7. Add the Mapbox token and build the dashboard

Vite embeds the token into the compiled frontend, so create `~/github/airspace/web/.env.local` before building:

```bash
cd ~/github/airspace
nano web/.env.local
```

```dotenv
VITE_MAPBOX_TOKEN=pk.YOUR_PUBLIC_TOKEN
```

Then build:

```bash
cd ~/github/airspace/web
npm install
npm run build
cd ..
```

The build completed successfully on the Pi 5. npm reported two low-severity audit findings; those did not block the build. Review with `npm audit` separately rather than blindly applying breaking dependency upgrades. If the token changes later, rebuild.

In your Mapbox account, restrict the token to `http://127.0.0.1:*`, the same origin the README uses. A `pk.` token is public, but should still be scoped.

## 8. Configure Airspace

```bash
uv run airspace init
```

Follow prompts for home coordinates and a display title. Defaults include a 5-mile visible radius, 10-nautical-mile collection radius, 10-second normal polling, 2-second fast polling for nearby helicopters and 7-day recording retention. `airspace.toml` is local and gitignored.

## 9. Start the collector and API

From the repository root:

```bash
cd ~/github/airspace
uv run airspace serve
```

Keep this SSH session open for the initial test. The backend serves the dashboard at `http://127.0.0.1:8000` **on the Pi**. The API is intentionally bound to localhost, not the LAN.

From another SSH session, check health:

```bash
ssh canterlot@raspberrypi.local
curl -s http://127.0.0.1:8000/api/health
```

To inspect the dashboard from the Mac without changing the API bind address, open a separate **Mac** terminal:

```bash
ssh -N -L 8000:127.0.0.1:8000 canterlot@raspberrypi.local
```

Then open `http://127.0.0.1:8000` **on the Mac**. Leave the tunnel running while testing.

## 10. Open the radar on the Pi's HDMI monitor

The Pi's desktop was visible on the monitor, but the browser did not open automatically. With the backend still running, open another SSH session and try:

```bash
DISPLAY=:0 chromium --kiosk http://127.0.0.1:8000
```

Chromium displayed a prompt asking for a password for a new keyring. For a dedicated browser with no saved credentials, first try canceling that prompt. If it persists, stop the browser with `Ctrl+C` in its launching terminal and retry:

```bash
DISPLAY=:0 chromium \
  --password-store=basic \
  --no-first-run \
  --kiosk \
  http://127.0.0.1:8000
```

`--password-store=basic` avoids the desktop keyring but does not provide encrypted browser credential storage. Use this only for a dedicated kiosk profile without stored passwords.

Raspberry Pi OS Trixie on a Pi 5 runs a Wayland desktop (the labwc compositor) by default. A process started over SSH doesn't inherit the session's Wayland socket or D-Bus environment, so `DISPLAY=:0` only works through XWayland and isn't reliable. To launch from SSH as a native Wayland client, point Chromium at the session's socket:

```bash
WAYLAND_DISPLAY=wayland-0 XDG_RUNTIME_DIR=/run/user/$(id -u) \
  chromium --ozone-platform=wayland \
  --password-store=basic --no-first-run --kiosk \
  http://127.0.0.1:8000
```

Check the socket name with `ls /run/user/$(id -u)/`. For the permanent setup, start Chromium from the desktop session itself rather than from SSH (see next steps).

**Status at the end of this session:** the frontend build succeeded and Chromium showed a keyring prompt. Successful dashboard rendering after dismissing the prompt was **not yet confirmed**.

## 11. Next steps (not completed yet)

- Confirm that the dashboard renders and updates on the HDMI monitor.
- Create a `systemd` service for `uv run airspace serve`, with the repository as its working directory and automatic restart on failure.
- Configure Chromium kiosk autostart **inside the graphical user session** by adding the launch command, with `--password-store=basic --no-first-run --kiosk`, to `~/.config/labwc/autostart`.
- Disable display blanking/screen sleep and configure desktop autologin if appropriate for the physical environment.
- Test a full reboot with no SSH interaction: backend starts, monitor displays Airspace and aircraft positions update.
- Optionally reserve the Pi's IP in the router, switch to SSH keys and monitor microSD write endurance.

## Useful commands

```bash
# Connect
ssh canterlot@raspberrypi.local

# Run Airspace manually
cd ~/github/airspace && uv run airspace serve

# Rebuild frontend after pulling updates
cd ~/github/airspace && git pull
cd web && npm install && npm run build

# Test API on Pi
curl -s http://127.0.0.1:8000/api/health

# Check memory
free -h
```

## Notes

- Airspace obtains live aircraft data from adsb.fi. Coverage and position freshness depend on volunteer receivers and the upstream service.
- The repository documents SQLite growth around 3–4 MB/hour and seven-day retention by default. Consider a high-endurance microSD card or SSD for sustained recording.
- Airspace's API is localhost-only by design. SSH tunneling is preferable to exposing it broadly for this personal installation.
- The Pi can be managed entirely over SSH after initial provisioning; a mouse is unnecessary for normal operation.
