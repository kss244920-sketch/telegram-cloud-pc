#!/usr/bin/env python3
"""
Render 24/7 Zero-Sleep Keep-Alive & Auto-Healer for Telegram Cloud PC Bot.
Pings the Render web service URL every 4-5 minutes to bypass Render's 15-minute inactivity spin-down.
If the service is down or unhealthy, automatically uses the Render REST API to restart it.
"""

import os
import sys
import json
import time
import datetime
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests library not found. Please install requests.")
    sys.exit(1)

BASE_DIR = Path(__file__).parent.parent.resolve()
CONFIG_FILE = BASE_DIR / "render_config.json"
LOG_FILE = BASE_DIR / "keep_alive_render.log"


def log(msg):
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{now_str}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def load_config():
    cfg = {}
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception as e:
            log(f"Error loading {CONFIG_FILE}: {e}")

    api_key = os.environ.get("RENDER_API_KEY") or cfg.get("api_key")
    owner_id = os.environ.get("RENDER_OWNER_ID") or cfg.get("owner_id")
    service_id = os.environ.get("RENDER_SERVICE_ID") or cfg.get("service_id")
    service_url = os.environ.get("RENDER_SERVICE_URL") or cfg.get("service_url")
    service_name = os.environ.get("RENDER_SERVICE_NAME") or cfg.get("service_name") or "telegram-cloud-pc-docker"

    return {
        "api_key": api_key,
        "owner_id": owner_id,
        "service_id": service_id,
        "service_url": service_url,
        "service_name": service_name,
    }


def save_config(cfg):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        log(f"Error saving config: {e}")


def discover_service(api_key, service_name):
    """Discovers service ID and public URL from Render API if not configured."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json"
    }
    try:
        r = requests.get("https://api.render.com/v1/services?limit=20", headers=headers, timeout=15)
        if r.status_code == 200:
            services = r.json()
            for s in services:
                svc = s.get("service", {})
                if svc.get("name") == service_name or service_name in svc.get("name", ""):
                    svc_id = svc.get("id")
                    svc_details = svc.get("serviceDetails", {})
                    svc_url = svc_details.get("url")
                    return svc_id, svc_url
            if services:
                first = services[0].get("service", {})
                return first.get("id"), first.get("serviceDetails", {}).get("url")
    except Exception as e:
        log(f"API discovery error: {e}")
    return None, None


def restart_service(api_key, service_id):
    """Triggers restart via Render REST API."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json"
    }
    url = f"https://api.render.com/v1/services/{service_id}/restart"
    try:
        r = requests.post(url, headers=headers, timeout=20)
        if r.status_code in (200, 201, 202):
            log(f"🔄 Successfully requested restart for service {service_id}")
            return True
        else:
            log(f"Restart failed with status {r.status_code}: {r.text}")
    except Exception as e:
        log(f"Restart request exception: {e}")
    return False


def ping_once():
    log("Running Render 24/7 Keep-Alive & Health Ping Check...")
    cfg = load_config()
    api_key = cfg.get("api_key")
    service_id = cfg.get("service_id")
    service_url = cfg.get("service_url")
    service_name = cfg.get("service_name", "telegram-cloud-pc-docker")

    if not api_key:
        log("ERROR: No RENDER_API_KEY found in config or environment.")
        return False

    if not service_url or not service_id:
        log(f"Discovering service info for '{service_name}' via Render API...")
        disc_id, disc_url = discover_service(api_key, service_name)
        if disc_id:
            service_id = disc_id
            cfg["service_id"] = disc_id
        if disc_url:
            service_url = disc_url
            cfg["service_url"] = disc_url
        if disc_id or disc_url:
            save_config(cfg)
            log(f"Discovered Service: ID={service_id}, URL={service_url}")

    if not service_url:
        log(f"Notice: No active Render service URL found yet for '{service_name}'.")
        return False

    # Ping the service health endpoint to keep it alive (with retries for transient DNS/network glitches)
    ping_url = service_url.rstrip("/") + "/health"
    log(f"📡 Sending Keep-Alive Ping to: {ping_url}")
    for attempt in range(1, 4):
        try:
            r = requests.get(ping_url, timeout=25)
            if r.status_code == 200:
                log(f"✅ Service is 100% HEALTHY & ALIVE! (HTTP {r.status_code}) - Inactivity timer reset.")
                return True
            else:
                log(f"⚠️ Health ping attempt {attempt} returned unexpected status: {r.status_code}")
        except requests.exceptions.RequestException as e:
            log(f"⚠️ Ping attempt {attempt} failed: {e}")
        if attempt < 3:
            time.sleep(3)

    # Self-heal if all 3 ping attempts fail
    if service_id:
        log("Attempting automatic service revival via Render API restart...")
        restart_service(api_key, service_id)
    return False


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Render 24/7 Keep-Alive & Auto-Healer")
    parser.add_argument("--loop", type=int, default=0, help="Run continuously for N minutes (pings every 4 mins)")
    parser.add_argument("--interval", type=int, default=240, help="Interval between pings in seconds (default 240)")
    args = parser.parse_args()

    if args.loop <= 0:
        ping_once()
        return

    end_time = time.time() + (args.loop * 60)
    log(f"🚀 Starting continuous Keep-Alive loop for {args.loop} minutes (ping interval: {args.interval}s)...")
    while time.time() < end_time:
        ping_once()
        remaining = end_time - time.time()
        if remaining <= 0:
            break
        sleep_dur = min(args.interval, remaining)
        time.sleep(sleep_dur)
    log("🏁 Continuous keep-alive session ended.")


if __name__ == "__main__":
    main()
