# Run Airspace as a Raspberry Pi kiosk

The kiosk runs Airspace as a systemd service and opens the dashboard in Chromium when the desktop starts. The service records traffic even if the desktop or browser fails. Start with a working manual install from the [Pi setup guide](pi-setup.md).

## Install

Stop any manually started `airspace serve` or `airspace collect` using the same data directory. In an SSH session on the Pi, run the installer as the desktop user.

```bash
cd ~/github/airspace
./scripts/install-pi-kiosk.sh
```

The installer uses `sudo` to install and start `airspace.service`, waits for the health endpoint, then sets up desktop autologin, Chromium autostart and screen blanking. It checks whether Wi-Fi and SSH are configured to start at boot. Rerun it to apply changes to the service template or autostart.

If the Pi is already logged into the desktop, start the kiosk now. Reboot to test unattended startup.

```bash
./scripts/airspace-kiosk start
./scripts/airspace-kiosk status
sudo reboot
```

Close any Chromium kiosk window started manually before launching the supervisor. The supervisor manages its own Chromium profile in `~/.local/state/airspace-kiosk/chromium`.

### Installer options

| Option | Purpose |
| --- | --- |
| `--no-autologin` | Keep the current boot and login settings |
| `--no-blanking` | Keep the current screen blanking setting |
| `--print-unit` | Preview the rendered systemd unit |
| `--print-autostart` | Preview the XDG desktop entry |

The defaults are the current checkout, service name `airspace` and port `8000`. Set `AIRSPACE_DIR`, `AIRSPACE_SERVICE` or `AIRSPACE_PORT` to override them. Use the same overrides for updates and uninstall. For a custom port, pass it to the kiosk commands that contact the backend, such as `./scripts/airspace-kiosk status --port 8010`.

`AIRSPACE_KIOSK_AUTOSTART` defaults to `auto`. Use `xdg` for a desktop entry under `~/.config/autostart/`, or `labwc` for an entry under `~/.config/labwc/autostart`. In automatic mode, the installer uses the labwc entry if it detects labwc with no XDG autostart handler.

## Operate the kiosk

Run these commands on the Pi. Use the [SSH tunnel](pi-setup.md#verify-the-dashboard) to view the dashboard from another computer.

| Task | Command |
| --- | --- |
| Check collector service | `systemctl status airspace` |
| Follow collector logs | `journalctl -u airspace -f` |
| Check collector and feed | `curl -fsS http://127.0.0.1:8000/api/health` |
| Restart collector | `sudo systemctl restart airspace` |
| Check kiosk | `./scripts/airspace-kiosk status` |
| Reload Chromium | `./scripts/airspace-kiosk restart` |
| Stop or start Chromium supervision | `./scripts/airspace-kiosk stop` or `./scripts/airspace-kiosk start` |
| Follow kiosk logs | `journalctl -t airspace-kiosk -f` |

The service starts after `network-online.target` and runs the checkout's `.venv/bin/airspace serve --no-access-log`. If the service exits with an error, systemd retries after 5 seconds and backs off to 2 minutes. Exit status 2 stops retries for configuration, catalog and boundary errors. A graceful service stop closes the recording and releases the collector lock.

The kiosk supervisor waits for the local health endpoint, finds the desktop display and relaunches Chromium after an exit. It starts with a 2-second delay and backs off to a minute for repeated crashes. Use `airspace-kiosk stop` to return to the desktop until the next login or kiosk start.

Use `.venv/bin/airspace report --session latest`, `sessions` and `export` while recording. Stop the service with `sudo systemctl stop airspace` before starting another collector in the same data directory.

## Update

```bash
cd ~/github/airspace
./scripts/update-pi.sh
```

The script pulls with `git pull --ff-only`, runs `uv sync --locked`, rebuilds with `npm ci` and `npm run build`, restarts the service and reloads Chromium. It keeps the enrichment extra when DuckDB is already installed. Use `--no-pull` to rebuild and restart the current checkout. Reinstall the kiosk if the service template or installer changed.

| When you change | Apply it |
| --- | --- |
| `airspace.toml` | Restart the service, then reload Chromium to pick up the starting display settings |
| `VITE_MAPBOX_TOKEN` or `VITE_MAPBOX_STYLE` | Run `./scripts/update-pi.sh --no-pull` |
| Code or frontend files in Git | Run `./scripts/update-pi.sh` |
| Systemd unit template or autostart settings | Rerun `./scripts/install-pi-kiosk.sh` |

For a wall display, add `tv_display = true` to the existing `[display]` section in the Pi's `airspace.toml`. The weather stays visible above the aircraft list, and the legend runs along the bottom. Set `view_radius_mi` to frame the area and `text_scale` to adjust readability from across the room. The live view uses the filters from `[filters]`. [Home, range and display settings](configuration.md#home-range-and-display)

## Troubleshoot

| Symptom | What to check |
| --- | --- |
| Service fails to start | Run `systemctl status airspace` and `journalctl -u airspace -b`. Fix any reported configuration, catalog or boundary error, then restart. |
| Another collector holds the lock | Check the PID in the error with `ps -p <pid> -o args=`. Stop that collector before starting the service. |
| Port 8000 already in use | Identify the listener with `sudo ss -ltnp 'sport = :8000'`. |
| Backend runs but the feed is unavailable | Check `/api/health`, the service journal and `nmcli device status`. A healthy HTTP endpoint can still report an upstream outage. |
| Dashboard has no basemap | Verify `web/.env.local` and rebuild. For a restricted token, check the browser's hostname and port against the allowed origin. Mapbox cannot allowlist a numeric IP or wildcard. [Mapbox token management](https://docs.mapbox.com/accounts/guides/tokens/#url-restrictions) |
| Screen shows a desktop | Run `./scripts/airspace-kiosk status`. If the backend responds but the supervisor isn't running, start it. If it doesn't appear at login, check the autostart entry, or rerun the installer with `AIRSPACE_KIOSK_AUTOSTART=labwc` on a labwc desktop. |
| Screen stops at the login prompt | Check desktop autologin in `sudo raspi-config` under System Options, Boot / Auto Login. |
| Screen goes blank | Check the Screen Blanking setting in `sudo raspi-config`, any running `swayidle` process and the monitor's own sleep timer. |
| Chromium keeps closing | Check `journalctl -t airspace-kiosk` and `~/.local/state/airspace-kiosk/chromium.log.1` for the last launch. |

Start the browser over SSH with `./scripts/airspace-kiosk start`. It finds the Pi user's Wayland or X11 display and session bus. A kiosk Chromium process has `--user-data-dir=.../airspace-kiosk/chromium` in its command line. Use `pgrep -af chromium` to distinguish it from a browser opened manually. The kiosk uses `--password-store=basic` for this dedicated profile, so keep saved credentials out of it.

If a first-run or restore prompt persists in the kiosk profile, stop the kiosk, move the profile directory aside and start the kiosk to create a fresh profile. In labwc mode, creating a user autostart file can stop the system autostart from launching the panel or wallpaper.

## Recover or roll back

- If an update breaks Airspace, find a working revision with `git log --oneline -10`, switch to it with `git switch --detach <commit>` and run `./scripts/update-pi.sh --no-pull`. Return to `main` with `git switch main`, then update. The local config, Mapbox token and recordings are outside Git.
- If the virtual environment is broken, move `.venv` aside from the checkout, then run `uv sync --locked` with `--extra enrich` if you use FAA lookups. Restart the service.
- If the desktop fails at startup, switch to console boot in `sudo raspi-config` under System Options, Boot / Auto Login. The backend still starts at boot.
- If the Pi loses network access, use Ethernet or connect a keyboard and monitor to run `sudo nmtui`. Back up `airspace.toml`, `web/.env.local` and `data/` before reimaging a readable card.
- If storage fills up, check `df -h` and `du -sh data`. Reduce `retention_days` under `[storage]` and restart the service to prune old recordings. To reclaim SQLite file space, stop the service, run `sqlite3 data/airspace.sqlite VACUUM` with the SQLite CLI installed, then start the service.

## Verify on the Pi

After installation or a deployment change, reboot and check the following on the Pi.

1. Chromium opens full screen without prompts and the display stays awake.
2. SSH reconnects. `systemctl is-enabled airspace` prints `enabled`, `systemctl is-active airspace` prints `active`, and the health endpoint shows a running collector and successful polls.
3. `./scripts/airspace-kiosk restart` reopens Chromium.

To test crash recovery when you can interrupt recording, run `sudo systemctl kill -s KILL airspace`. Check that systemd starts a new process and the dashboard resumes updating.

## Uninstall

```bash
cd ~/github/airspace
./scripts/install-pi-kiosk.sh --uninstall
```

This stops the service and kiosk and removes their startup entries. It keeps your local config, token, recordings and Chromium profile. Restore login and screen blanking preferences separately in `sudo raspi-config`.
