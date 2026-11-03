"""Telegram notifier for new / closed positions.

Polls a JSON endpoint (the one the panel itself calls) and sends a Telegram
message when a position appears or disappears. Stdlib only.

Usage:
    python notifier.py --test    # send a test Telegram message and exit
    python notifier.py --once    # fetch once, print what it sees, no state change
    python notifier.py           # run forever
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "state.json")


def load_env(path):
    env = dict(os.environ)  # CI passes the config as environment variables
    if not os.path.exists(path):
        return env
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def telegram(cfg, text):
    url = f"https://api.telegram.org/bot{cfg['TELEGRAM_BOT_TOKEN']}/sendMessage"
    body = json.dumps({"chat_id": cfg["TELEGRAM_CHAT_ID"], "text": text}).encode()
    req = urllib.request.Request(url, body, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        r.read()


def fetch_positions(cfg):
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    if cfg.get("COOKIE"):
        headers["Cookie"] = cfg["COOKIE"]
    if cfg.get("AUTH_HEADER"):
        headers["Authorization"] = cfg["AUTH_HEADER"]
    req = urllib.request.Request(cfg["API_URL"], headers=headers)
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.loads(r.read().decode("utf-8"))
    path = cfg.get("POSITIONS_PATH", "")
    for part in [p for p in path.split(".") if p]:
        data = data[int(part)] if isinstance(data, list) else data[part]
    if not isinstance(data, list):
        raise ValueError("POSITIONS_PATH does not point to a list")
    return data


def key_of(item, key_fields):
    return "|".join(str(item.get(k, "")) for k in key_fields)


def describe(item, msg_fields):
    return "\n".join(f"{k}: {item.get(k, '?')}" for k in msg_fields)


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f)


def main():
    cfg = load_env(os.path.join(HERE, ".env"))
    key_fields = [x.strip() for x in cfg["KEY_FIELDS"].split(",")]
    msg_fields = [x.strip() for x in cfg["MSG_FIELDS"].split(",")]
    poll = int(cfg.get("POLL_SECONDS", "30"))

    if "--test" in sys.argv:
        telegram(cfg, "Test: bildirim botu calisiyor.")
        print("Test mesaji gonderildi.")
        return

    if "--once" in sys.argv:
        items = fetch_positions(cfg)
        print(f"{len(items)} pozisyon bulundu.")
        for it in items:
            print("-", key_of(it, key_fields), "\n", describe(it, msg_fields))
        return

    if "--run-once" in sys.argv:
        # One poll for schedulers (GitHub Actions). Errors propagate so the run fails visibly.
        known = load_state()
        current = {key_of(i, key_fields): describe(i, msg_fields) for i in fetch_positions(cfg)}
        if known is not None:
            for k, text in current.items():
                if k not in known:
                    telegram(cfg, "YENI ISLEM ACILDI\n" + text)
            for k, text in known.items():
                if k not in current:
                    telegram(cfg, "ISLEM KAPANDI\n" + text)
        save_state(current)
        print(f"{len(current)} pozisyon, durum kaydedildi.")
        return

    known = load_state()  # {key: text}; None on first run
    auth_alert_sent = False
    while True:
        try:
            items = fetch_positions(cfg)
            auth_alert_sent = False
            current = {key_of(i, key_fields): describe(i, msg_fields) for i in items}
            if known is None:
                known = current  # first run: remember, do not notify
            else:
                for k, text in current.items():
                    if k not in known:
                        telegram(cfg, "YENI ISLEM ACILDI\n" + text)
                for k, text in known.items():
                    if k not in current:
                        telegram(cfg, "ISLEM KAPANDI\n" + text)
                known = current
            save_state(known)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403) and not auth_alert_sent:
                try:
                    telegram(cfg, "Oturum suresi dolmus olabilir (HTTP %d). .env icindeki COOKIE/AUTH_HEADER'i yenile." % e.code)
                except Exception:
                    pass
                auth_alert_sent = True
            print("HTTP hatasi:", e.code)
        except Exception as e:  # keep running on transient errors
            print("Hata:", e)
        time.sleep(poll)


if __name__ == "__main__":
    main()
