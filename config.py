"""atvdaemon settings from environment variables (empty or unset = the default)."""

import os

DEFAULTS = {
    "id":   "2E:10:43:01:B6:8F",   # ATVD_ID: the Apple TV's pyatv identifier
    "host": "10.1.1.169",          # ATVD_HOST: the Apple TV's IP
    "bind": "0.0.0.0",             # ATVD_BIND: address the UDP server listens on
    "port": 7777,                  # ATVD_PORT: UDP port
}


def load_config(env=None, home=None):
    """Read ATVD_* from env (default os.environ). ATVD_STORAGE defaults to <home>/.pyatv.conf."""
    env = os.environ if env is None else env
    home = os.path.expanduser("~") if home is None else home

    def get(name, default):
        value = env.get(name, "").strip()
        return value if value else default

    port_text = get("ATVD_PORT", str(DEFAULTS["port"]))
    try:
        port = int(port_text)
    except ValueError:
        raise ValueError(f"ATVD_PORT must be a number, got {port_text!r}") from None
    if not 1 <= port <= 65535:
        raise ValueError(f"ATVD_PORT out of range: {port}")

    return {
        "id":      get("ATVD_ID", DEFAULTS["id"]),
        "host":    get("ATVD_HOST", DEFAULTS["host"]),
        "bind":    get("ATVD_BIND", DEFAULTS["bind"]),
        "port":    port,
        "storage": get("ATVD_STORAGE", os.path.join(home, ".pyatv.conf")),
    }
