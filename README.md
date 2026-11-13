# atvdaemon

A UDP daemon that controls an Apple TV via [pyatv](https://pyatv.dev). Keeps a persistent connection to the device and accepts simple text commands over UDP, making it easy to drive from any language.

## Setup

Pair your Apple TV with pyatv first if you haven't already:

```bash
atvremote --id <device-id> --scan-hosts <ip> pair
```

Credentials are stored in `~/.pyatv.conf`.

## Configuration

Environment variables (unset or empty = the default, in `config.py`):

| Variable | Default | |
|---|---|---|
| `ATVD_ID` | `2E:10:43:01:B6:8F` | Apple TV identifier |
| `ATVD_HOST` | `10.1.1.169` | Apple TV IP (give it a DHCP reservation) |
| `ATVD_PORT` | `7777` | UDP port |
| `ATVD_BIND` | `0.0.0.0` | Listen address |
| `ATVD_STORAGE` | `~/.pyatv.conf` | pyatv credentials file (`/data/pyatv.conf` in the container) |

## Running in Docker (the NAS)

```bash
git clone https://github.com/toptensoftware/atv-daemon.git
cd atv-daemon
mkdir -p data
cp ~pi/.pyatv.conf data/pyatv.conf     # the Pi's existing pairing, or pair below
chown -R 1000:1000 data                # the container's user (see below)
docker compose up -d --build
docker compose logs -f                 # "Connected to <name>" once it reaches the ATV
```

To pair from the container instead (tvOS 15+ wants AirPlay and Companion; enter the PIN the TV shows):

```bash
docker compose run --rm atvdaemon atvremote --storage-filename /data/pyatv.conf \
    --id 2E:10:43:01:B6:8F --scan-hosts 10.1.1.169 --protocol airplay pair
docker compose run --rm atvdaemon atvremote --storage-filename /data/pyatv.conf \
    --id 2E:10:43:01:B6:8F --scan-hosts 10.1.1.169 --protocol companion pair
```

The container uses host networking (`compose.yaml`): no NAT or port mapping between the network and the daemon,
and pyatv's unicast mDNS scan of the Apple TV behaves as it does bare.

**Open the port in the host's firewall.** With host networking Docker adds no firewall rules, so the host's own
firewall decides: a `ping` to `127.0.0.1` answers but one from another machine gets nothing. The daemon logs every
datagram it receives (`← (addr, port) 'ping'`), so no such line in `docker compose logs` means the packet never
arrived.

```bash
sudo ufw allow 7777/udp                     # ufw
sudo firewall-cmd --permanent --add-port=7777/udp && sudo firewall-cmd --reload   # firewalld
``` Settings go in `compose.yaml`'s
`environment`. After editing the daemon, `docker compose up -d --build`.

The container runs as UID:GID 1000:1000, not root (nothing needs root: the port is above 1024 and the
credentials are in `/data`). If your user on the NAS isn't 1000 (`id -u`, `id -g`), set `ATVD_UID` and
`ATVD_GID` in a `.env` file beside `compose.yaml` and `chown` `data` to match. Pairing with
`docker compose run` runs as the same user, so the file it writes stays owned by you.

## Running bare

```bash
./start.sh
```

`start.sh` creates a virtualenv at `~/.venv/pyatv`, installs `requirements.txt` if needed, then launches the daemon.

## Timing

`atvbench.py` (Python 3, stdlib only) sends a command repeatedly and prints each round trip and min/median/p95/max:

```bash
python atvbench.py 10.1.1.2                     # 20 x ping: network + daemon only
python atvbench.py 10.1.1.2 -n 10 -c "key select"   # adds the pyatv round trip to the ATV
```

Run it from a machine on Wi-Fi (the ESP32's side of the network) against the bare daemon and then the container:
the difference is what Docker costs. Inside the container: `docker exec atvdaemon python atvbench.py 127.0.0.1`.

## Tests

`python -m unittest` in this folder (config and the bench; no pyatv needed).

## UDP Protocol

Send UTF-8 text commands to port `7777`. Each command gets a single-line response.

| Command | Response | Description |
|---|---|---|
| `ping` | `pong` / `connecting` | `pong` if connected to ATV, `connecting` if not |
| `key <name>` | `ok` / `error: ...` | Press a remote control key |
| `app <bundle-id>` | `ok` / `error: ...` | Launch an app by bundle ID |
| `disconnect` | `ok` | Drop the ATV connection |

### Key names

`up` `down` `left` `right` `select` `menu` `home` `home_hold` `play_pause` `play` `pause` `next` `previous` `stop` `volume_up` `volume_down` `top_menu` `channel_up` `channel_down` `screensaver`

### Quick test

```bash
echo "ping" | nc -u -w2 127.0.0.1 7777
echo "key menu" | nc -u -w2 127.0.0.1 7777
echo "app com.netflix.Netflix" | nc -u -w2 127.0.0.1 7777
```

## Node.js client

```js
import { atv } from "./atv.js";

// Wait for ATV to be ready (polls ping, up to 20s)
await atv.connect();

// Send keys
await atv.key("select");
await atv.key("volume_up");

// Launch an app
await atv.app("com.netflix.Netflix");

// When turning the ATV off manually
await atv.disconnect();
```

### Typical power-on flow

```js
// 1. Power on via IR blaster
// 2. Wait for daemon to establish connection
await atv.connect(20000);  // throws if not connected within 20s

// 3. Send commands
await atv.key("home");
```

### Typical power-off flow

```js
// 1. Power off via IR blaster
// 2. Tell the daemon to drop its connection immediately
await atv.disconnect();
// Daemon will also detect passive disconnects (e.g. ATV auto-sleep) via background health check
```

## Connection behaviour

- On startup the daemon connects immediately; if that fails it retries on the first command.
- `key`, `app` and `text_*` wait for the connection: if not connected they retry every 2 s (`CONNECT_RETRY`) for up to ~20 s (`CONNECT_WAIT`), waiting on a background connect started by `ping` rather than starting a second one. So a command sent while the ATV wakes replies `ok` once connected; one that can't connect replies `error: ...` after ~20 s. Callers don't need to poll `ping` first.
- A background health check runs every 5 seconds. If the ATV stops responding on the network the connection is dropped and `ping` starts returning `connecting`.
- On any command failure the connection is invalidated and re-established on the next command.
- `ping` returning `connecting` also triggers a reconnection attempt in the background.
