import requests
import os
import logging
import threading
import time

logger = logging.getLogger("quadroPE")

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")


def send_alert(title: str, message: str, level: str = "warning"):
    if not DISCORD_WEBHOOK_URL:
        logger.warning("DISCORD_WEBHOOK_URL not set, skipping alert")
        return

    color = 16711680 if level == "critical" else 16776960  # red or yellow
    payload = {
        "embeds": [{
            "title": f"🚨 {title}",
            "description": message,
            "color": color
        }]
    }
    try:
        requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=5)
        logger.info(f"Alert sent: {title}")
    except Exception as e:
        logger.error(f"Failed to send alert: {e}")


def _monitor(app_url: str, interval: int, fail_threshold: int = 3):
    service_was_down = False
    consecutive_failures = 0
    consecutive_successes = 0
    while True:
        time.sleep(interval)
        try:
            resp = requests.get(f"{app_url}/health", timeout=5)
            healthy = resp.status_code == 200
        except Exception:
            healthy = False

        if not healthy:
            consecutive_failures += 1
            consecutive_successes = 0
        else:
            consecutive_successes += 1
            consecutive_failures = 0

        # Debounce: only alert after `fail_threshold` consecutive failures
        # so a single blip doesn't page (#145).
        if not healthy and not service_was_down and consecutive_failures >= fail_threshold:
            send_alert(
                "Service Down",
                f"`{app_url}` failed {consecutive_failures} consecutive health checks — the service may be down.",
                level="critical"
            )
            service_was_down = True
        elif healthy and service_was_down and consecutive_successes >= 2:
            send_alert(
                "Service Recovered",
                f"`{app_url}` is back online and passing health checks.",
                level="warning"
            )
            service_was_down = False


def start_alerting(app_url: str = "http://127.0.0.1:5000", interval: int = 60):
    for t in threading.enumerate():
        if t.name == "alert-monitor" and t.is_alive():
            logger.info("Alert monitor already running — skipping duplicate start")
            return t
    t = threading.Thread(target=_monitor, args=(app_url, interval), daemon=True, name="alert-monitor")
    t.start()
    logger.info(f"Alert monitor started — checking {app_url}/health every {interval}s")
    return t