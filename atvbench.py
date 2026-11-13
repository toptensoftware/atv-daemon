#!/usr/bin/env python3
"""Time atvdaemon round trips (stdlib only; runs on Windows, the NAS or inside the container).

  python atvbench.py 10.1.1.2                    # 20 x "ping"
  python atvbench.py 10.1.1.2 -n 10 -c "key select"
  python atvbench.py 127.0.0.1 -n 1 -t 2         # exits 1 if no reply (usable as a health check)

"ping" times the network and the daemon only (it answers without talking to the Apple TV);
"key <name>" adds the pyatv round trip to the Apple TV. Run the same thing against the bare
daemon and the container to see what Docker costs.
"""

import argparse
import socket
import sys
import time


def summarize(times_ms, sent):
    """min / median / p95 (nearest rank) / max of the round trips, plus the loss count."""
    s = sorted(times_ms)
    n = len(s)
    out = {"sent": sent, "received": n, "lost": sent - n,
           "min": None, "median": None, "p95": None, "max": None}
    if n:
        mid = n // 2
        out["min"] = s[0]
        out["max"] = s[-1]
        out["median"] = s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2
        rank = -(-95 * n // 100)            # ceil(0.95 n), 1-based
        out["p95"] = s[rank - 1]
    return out


def run(host, port, command, count, timeout, gap):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    times = []
    try:
        for i in range(count):
            t0 = time.perf_counter()
            sock.sendto(command.encode("utf-8"), (host, port))
            try:
                reply, _ = sock.recvfrom(2048)
            except socket.timeout:
                print(f"{i + 1:3}: timeout")
                continue
            except ConnectionResetError:        # Windows: ICMP port unreachable
                print(f"{i + 1:3}: nothing listening")
                continue
            ms = (time.perf_counter() - t0) * 1000
            times.append(ms)
            print(f"{i + 1:3}: {ms:8.2f} ms  {reply.decode('utf-8', 'replace').strip()}")
            if gap and i + 1 < count:
                time.sleep(gap)
    finally:
        sock.close()
    return times


def main(argv=None):
    p = argparse.ArgumentParser(description="Time atvdaemon UDP round trips.")
    p.add_argument("host", help="daemon address (e.g. 10.1.1.2)")
    p.add_argument("-p", "--port", type=int, default=7777)
    p.add_argument("-c", "--command", default="ping", help='command to send (default "ping")')
    p.add_argument("-n", "--count", type=int, default=20)
    p.add_argument("-t", "--timeout", type=float, default=25.0,
                   help="seconds to wait per reply (key/app can take ~20 s while connecting)")
    p.add_argument("-g", "--gap", type=float, default=0.2, help="seconds between sends")
    args = p.parse_args(argv)

    times = run(args.host, args.port, args.command, args.count, args.timeout, args.gap)
    s = summarize(times, args.count)
    if s["received"]:
        print(f"{s['received']}/{s['sent']} replies, {s['lost']} lost | "
              f"min {s['min']:.2f}  median {s['median']:.2f}  p95 {s['p95']:.2f}  max {s['max']:.2f} ms")
    else:
        print(f"0/{s['sent']} replies")
    return 0 if s["received"] else 1


if __name__ == "__main__":
    sys.exit(main())
