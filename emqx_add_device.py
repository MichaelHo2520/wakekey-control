"""WakeKey：在 EMQX Serverless 新增／列出／移除設備帳號。

Standalone: needs only Python 3 (and paho-mqtt to also update the control page's
device list). Published next to the control page so anyone, or an AI assistant,
can download it:
  https://michaelho2520.github.io/wakekey-control/emqx_add_device.py
Guide: https://michaelho2520.github.io/wakekey-control/guide.html#ai

usage:
  python emqx_add_device.py init                    建立設定檔範本 emqx.txt（自己填入帳密）
  python emqx_add_device.py check                   確認設定檔和 API Key 可以用
  python emqx_add_device.py add <代號> [--name 名稱]  建立設備帳號，印出配對碼
  python emqx_add_device.py list                    列出 EMQX 裡的帳號
  python emqx_add_device.py remove <代號>            刪除設備帳號並從控制網頁清單移除
  --config <檔案>  指定設定檔（預設：WakeKey 專案的 secrets/emqx.txt，或這支程式旁邊的 emqx.txt）

add creates the MQTT user <id> with a random password, prints the pairing code
(wk1:...) for the board, and adds the device to the control page's list (retained
wakekey/_admin/devices). The password only appears inside the pairing code and is
never written to disk. The broker ACL already limits every user to
wakekey/<its name>/# (guide section 5), so a new device needs no rule change.
"""
import argparse
import base64
import json
import os
import re
import secrets as rnd
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

AUTHN = "password_based:built_in_database"
REG_TOPIC = "wakekey/_admin/devices"
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,15}$")     # same rule as the control page
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"
HERE = os.path.dirname(os.path.abspath(__file__))
GUIDE = "https://michaelho2520.github.io/wakekey-control/guide.html"

TEMPLATE = """# WakeKey 的 EMQX 設定（{guide}）
# 這個檔案有密碼：不要上傳、不要傳給別人。每行「名稱=值」，值前後不要加引號。

# EMQX 控制台 → 部署 → Deployment Overview → Connection Information 的 Address（只要主機名稱）
broker_host=
mqtts_port=8883

# 控制網頁登入用的 wk-admin 密碼（用來把新設備加進控制網頁的清單）
wk-admin=

# EMQX 控制台 → 部署 → Deployment Overview → Deployment API Key → + New API Key
api_app_id=
api_app_secret=

# API 位址；留空＝https://<broker_host>:8443/api/v5
api_url=
"""


def default_config():
    for p in (os.environ.get("WAKEKEY_EMQX"),
              os.path.join(HERE, "..", "secrets", "emqx.txt"),     # inside the WakeKey repo
              os.path.join(HERE, "emqx.txt"),
              "emqx.txt"):
        if p and os.path.isfile(p):
            return p
    return os.path.join(HERE, "emqx.txt")


def load(path):
    if not os.path.isfile(path):
        sys.exit(f"找不到設定檔 {path}。先執行：python {os.path.basename(__file__)} init")
    cfg = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip().strip('"')
    if cfg.get("broker_host"):
        cfg["broker_host"] = re.sub(r"^[a-z]+://|[:/].*$", "", cfg["broker_host"])
    if not cfg.get("api_url") and cfg.get("broker_host"):
        # where the deployment answers (GET without credentials -> 403), probed 2026-10-08
        cfg["api_url"] = f"https://{cfg['broker_host']}:8443/api/v5"
    return cfg


def need(cfg, *keys):
    missing = [k for k in keys if not cfg.get(k)]
    if missing:
        sys.exit("設定檔還沒填：" + ", ".join(missing))


def api(cfg, method, path, body=None):
    need(cfg, "broker_host", "api_app_id", "api_app_secret")
    url = cfg["api_url"].rstrip("/") + path
    auth = base64.b64encode(f"{cfg['api_app_id']}:{cfg['api_app_secret']}".encode()).decode()
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": "Basic " + auth, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            text = r.read().decode()
            return r.status, json.loads(text) if text else None
    except urllib.error.HTTPError as e:
        text = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(text)
        except ValueError:
            return e.code, {"message": text[:300]}
    except (urllib.error.URLError, OSError) as e:
        sys.exit(f"連不到 EMQX API（{cfg['api_url']}）：{e}")


def explain(code, body):
    msg = (body or {}).get("message") or (body or {}).get("code") or ""
    hint = {
        401: "API Key 不對：檢查 api_app_id / api_app_secret（App Secret 只在建立時顯示一次，忘了就新建一組）",
        403: "API Key 不對（EMQX 對錯的 Key 也回 403）：檢查 api_app_id / api_app_secret；都對的話可能是 api_url 不對",
        404: "EMQX 找不到這個功能：這個方案可能不支援用 API 管理帳號，請改用手動步驟 " + GUIDE + "#device",
    }.get(code, "")
    return f"EMQX 回應 {code} {msg}".strip() + (f"\n→ {hint}" if hint else "")


def users_path(user=None):
    p = "/authentication/" + urllib.parse.quote(AUTHN, safe="") + "/users"
    return p + ("/" + urllib.parse.quote(user, safe="") if user else "")


def pairing_code(cfg, user, pw):
    raw = json.dumps({"h": cfg["broker_host"], "p": int(cfg.get("mqtts_port") or 8883), "u": user, "pw": pw},
                     separators=(",", ":")).encode()
    return "wk1:" + base64.urlsafe_b64encode(raw).decode().rstrip("=")


def update_registry(cfg, change, clear_retained=None):
    """Apply change(list) -> list to the control page's device list. False if it cannot."""
    if not cfg.get("wk-admin"):
        print("（設定檔沒有 wk-admin 密碼，略過控制網頁清單；可在控制網頁「新增裝置」選「已經配對過」自己加）")
        return False
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        print("（沒有安裝 paho-mqtt，略過控制網頁清單：python -m pip install paho-mqtt）")
        return False
    got = {}

    def on_connect(c, userdata, flags, rc, props=None):
        c.subscribe(REG_TOPIC, qos=1)

    def on_message(c, userdata, msg):
        try:
            got["list"] = json.loads(msg.payload or b"{}").get("devices", [])
        except ValueError:
            got["list"] = []

    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="wkadm-" + rnd.token_hex(4))
    c.username_pw_set("wk-admin", cfg["wk-admin"])
    c.tls_set(cert_reqs=ssl.CERT_REQUIRED)
    c.on_connect, c.on_message = on_connect, on_message
    try:
        c.connect(cfg["broker_host"], int(cfg.get("mqtts_port") or 8883))
    except OSError as e:
        print(f"（連不上 broker，略過控制網頁清單：{e}）")
        return False
    c.loop_start()
    time.sleep(3)               # the retained list arrives right after SUBACK, or there is none
    new = change(list(got.get("list", [])))
    c.publish(REG_TOPIC, json.dumps({"v": 1, "devices": new}, ensure_ascii=False), qos=1, retain=True).wait_for_publish(10)
    for t in clear_retained or []:          # a removed device would still show on the page
        c.publish(t, b"", qos=1, retain=True).wait_for_publish(10)
    c.loop_stop()
    c.disconnect()
    return True


def cmd_init(path):
    if os.path.exists(path):
        print(f"設定檔已經存在：{path}（沒有覆蓋）")
        return
    with open(path, "w", encoding="utf-8") as f:
        f.write(TEMPLATE.format(guide=GUIDE + "#ai"))
    print(f"已建立設定檔範本：{path}")
    print("請用記事本打開，自己填入 broker_host、wk-admin、api_app_id、api_app_secret 後存檔。")


def cmd_check(cfg):
    need(cfg, "broker_host", "api_app_id", "api_app_secret")
    code, body = api(cfg, "GET", users_path() + "?page=1&limit=1")
    if code != 200:
        sys.exit(explain(code, body))
    print(f"API 可以用（{cfg['api_url']}）")
    print("wk-admin 密碼：" + ("已填" if cfg.get("wk-admin") else "沒填（新設備不會自動加進控制網頁清單）"))


def cmd_add(cfg, dev, name):
    pw = "".join(rnd.choice(ALPHABET) for _ in range(24))
    code, body = api(cfg, "POST", users_path(), {"user_id": dev, "password": pw, "is_superuser": False})
    if code == 409:
        sys.exit(f"EMQX 裡已經有帳號「{dev}」。換一個代號，或先 remove {dev}")
    if code not in (200, 201):
        sys.exit(explain(code, body))
    print(f"已在 EMQX 建立帳號「{dev}」")
    if update_registry(cfg, lambda lst: [d for d in lst if d.get("id") != dev] + [{"id": dev, "name": name or dev}]):
        print(f"已加進控制網頁的清單（名稱：{name or dev}）")
    print("\n配對碼（含這顆設備的密碼，當成密碼保管）：")
    print(pairing_code(cfg, dev, pw))
    print("\n貼到板子網頁「設定 → 雲端」，或設定熱點頁的「雲端配對碼」。")


def cmd_list(cfg):
    code, body = api(cfg, "GET", users_path() + "?page=1&limit=100")
    if code != 200:
        sys.exit(explain(code, body))
    for u in (body or {}).get("data", []):
        print(u.get("user_id"), "（superuser）" if u.get("is_superuser") else "")


def cmd_remove(cfg, dev):
    code, body = api(cfg, "DELETE", users_path(dev))
    if code == 404 and "NOT_FOUND" in json.dumps(body or {}):
        print(f"EMQX 裡本來就沒有帳號「{dev}」")
    elif code not in (200, 204):
        sys.exit(explain(code, body))
    else:
        print(f"已刪除 EMQX 帳號「{dev}」")
    if update_registry(cfg, lambda lst: [d for d in lst if d.get("id") != dev],
                       [f"wakekey/{dev}/status", f"wakekey/{dev}/ui"]):
        print("已從控制網頁清單移除；板子本身要清除的話，到板子網頁「設定 → 雲端 → 清除雲端配對」")


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    ap = argparse.ArgumentParser(description="WakeKey：管理 EMQX 上的設備帳號（說明：" + GUIDE + "#ai）")
    ap.add_argument("--config", default=None, help="設定檔路徑")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="建立設定檔範本")
    sub.add_parser("check", help="確認設定檔和 API Key")
    a = sub.add_parser("add", help="新增設備")
    a.add_argument("id")
    a.add_argument("--name", default="")
    sub.add_parser("list", help="列出帳號")
    r = sub.add_parser("remove", help="移除設備")
    r.add_argument("id")
    args = ap.parse_args()
    path = args.config or default_config()
    if args.cmd == "init":
        cmd_init(path)
        return
    cfg = load(path)
    if args.cmd in ("add", "remove") and (not ID_RE.match(args.id) or args.id == "wk-admin"):
        sys.exit("代號只能用英文小寫、數字和 -，2–16 字，開頭不能是 -（也不能是 wk-admin）")
    {"check": lambda: cmd_check(cfg), "list": lambda: cmd_list(cfg),
     "add": lambda: cmd_add(cfg, args.id, args.name), "remove": lambda: cmd_remove(cfg, args.id)}[args.cmd]()


if __name__ == "__main__":
    main()
