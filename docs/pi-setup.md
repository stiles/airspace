# Set up Airspace on a Raspberry Pi 5

This guide uses 64-bit Raspberry Pi OS with Desktop (Debian 13 Trixie). Connect the Pi to an HDMI monitor for the kiosk. You can install and manage it over SSH from another computer.

## Prepare the Pi

1. In [Raspberry Pi Imager](https://www.raspberrypi.com/software/), choose Raspberry Pi OS with Desktop, 64-bit. Use the OS customization screen to set a username, network, hostname, time zone and SSH access.
2. Write the image to a microSD card. Imager erases the card.
3. Insert the card, connect the monitor and power on the Pi.

Connect from another computer. The examples use `pi` as the account and `raspberrypi` as the hostname. Substitute the values you set in Imager.

```bash
ssh pi@raspberrypi.local
```

If the hostname doesn't resolve, look up the Pi's address in your router and use that address with SSH. If SSH refuses the connection, check that the Pi has finished its first boot and that SSH is enabled. After reinstalling the OS, verify that the new server key belongs to your Pi before removing the old entry with `ssh-keygen -R raspberrypi.local`. [Raspberry Pi remote access](https://www.raspberrypi.com/documentation/computers/remote-access.html#enable-the-ssh-server)

## Install Airspace

Run these on the Pi as your desktop user.

```bash
sudo apt update
sudo apt install -y git curl chromium util-linux nodejs npm
node --version
npm --version
```

Use Node.js 20.19 or later in the 20.x series, 22.13 or later in the 22.x series, or 24 or later. Debian Trixie's packaged ARM64 Node.js meets that requirement. [Debian Node.js package](https://packages.debian.org/trixie/arm64/nodejs)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"

mkdir -p ~/github
cd ~/github
git clone https://github.com/stiles/airspace.git
cd airspace
uv sync --locked
uv run airspace init
```

`uv` can install the repository's requested Python 3.12. If you have a hangarbay registry, use `uv sync --locked --extra enrich` to enable optional FAA lookups. The home coordinates and display title go in `airspace.toml`, which Git ignores.

## Build the dashboard

Create `web/.env.local` in the checkout with a dedicated Mapbox public token.

```dotenv
VITE_MAPBOX_TOKEN=pk.YOUR_PUBLIC_TOKEN
```

Vite embeds the token in the browser bundle. Use a public token without URL restrictions for the default numeric loopback address. Mapbox URL restrictions support hostnames and explicit ports, and reject numeric IP addresses and wildcards. [Mapbox token management](https://docs.mapbox.com/accounts/guides/tokens/#url-restrictions)

```bash
cd web
npm ci
npm run build
cd ..
```

## Verify the dashboard

Start Airspace on the Pi from the repository root.

```bash
uv run airspace serve
```

From a second SSH session, check the backend and collector.

```bash
curl -fsS http://127.0.0.1:8000/api/health
```

Look for `"collector_running":true` and a live feed after the first successful poll. For a visual check, open `http://127.0.0.1:8000` in Chromium on the Pi. To view it on another computer, forward the port from that computer and open the same URL there.

```bash
ssh -N -L 127.0.0.1:8000:127.0.0.1:8000 pi@raspberrypi.local
```

If port 8000 is in use on your computer, change the first port to 8001 and open `http://127.0.0.1:8001`.

Stop the manual collector with `Ctrl+C` before following the [kiosk guide](raspberry-pi-kiosk.md). The Pi's kiosk browser starts with the desktop session and uses its own Chromium profile.
