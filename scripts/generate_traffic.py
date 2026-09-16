"""Generate realistic traffic against the running app to populate Grafana dashboards."""

import random
import time
import requests

BASE = "http://localhost"


def health_checks(session, n=5):
    for _ in range(n):
        session.get(f"{BASE}/health")


def browse_users(session):
    r = session.get(f"{BASE}/users")
    data = r.json()
    total = data.get("total_items", 0)
    if total > 0:
        for page in range(1, min(4, (total // 10) + 1)):
            session.get(f"{BASE}/users?page={page}&per_page=10")

    for uid in random.sample(range(1, min(total, 400) + 1), min(10, total)):
        session.get(f"{BASE}/users/{uid}")


def browse_urls(session):
    r = session.get(f"{BASE}/urls")
    data = r.json()
    total = data.get("total_items", 0)

    session.get(f"{BASE}/urls?user_id={random.randint(1, 50)}")
    session.get(f"{BASE}/urls?is_active=true")

    if total > 0:
        sample_size = min(8, total)
        for uid in random.sample(range(1, min(total, 200) + 1), sample_size):
            session.get(f"{BASE}/urls/{uid}")


def create_users(session, n=5):
    for i in range(n):
        session.post(
            f"{BASE}/users",
            json={
                "username": f"loadtest_user_{int(time.time())}_{i}",
                "email": f"loadtest_{int(time.time())}_{i}@test.com",
            },
        )


def create_urls(session, n=5):
    for i in range(n):
        session.post(
            f"{BASE}/urls",
            json={
                "user_id": random.randint(1, 50),
                "original_url": f"https://example.com/load-test/{int(time.time())}/{i}",
                "title": f"Load Test URL {i}",
            },
        )


def list_events(session):
    session.get(f"{BASE}/events")
    session.get(f"{BASE}/events?event_type=created")
    session.get(f"{BASE}/events?user_id={random.randint(1, 20)}")


def generate_errors(session):
    session.post(f"{BASE}/users", json={"bad": True})
    session.post(f"{BASE}/urls", json={"user_id": "not_int"})
    session.post(f"{BASE}/urls", data="not json")
    session.get(f"{BASE}/users/999999")
    session.get(f"{BASE}/urls/999999")
    session.get(f"{BASE}/nonexistent-route")
    session.post(f"{BASE}/users", json={"username": "", "email": "x@y.com"})
    session.post(
        f"{BASE}/urls", json={"user_id": 999999, "original_url": "https://x.com", "title": "t"}
    )


def run_round(session, round_num):
    print(f"  Round {round_num}: ", end="", flush=True)

    health_checks(session, random.randint(2, 5))
    print("health ", end="", flush=True)

    browse_users(session)
    print("users ", end="", flush=True)

    browse_urls(session)
    print("urls ", end="", flush=True)

    list_events(session)
    print("events ", end="", flush=True)

    if random.random() < 0.6:
        create_users(session, random.randint(2, 5))
        print("create-users ", end="", flush=True)

    if random.random() < 0.6:
        create_urls(session, random.randint(2, 4))
        print("create-urls ", end="", flush=True)

    if random.random() < 0.4:
        generate_errors(session)
        print("errors ", end="", flush=True)

    print("done")


def main():
    print("=== Traffic Generator ===")
    print(f"Target: {BASE}")
    print()

    session = requests.Session()

    try:
        r = session.get(f"{BASE}/health", timeout=3)
        r.raise_for_status()
    except Exception as e:
        print(f"Cannot reach {BASE}/health — is the app running? ({e})")
        print("If running locally without Docker, change BASE to http://localhost:5000")
        return

    rounds = 30
    print(f"Running {rounds} rounds of traffic...\n")

    for i in range(1, rounds + 1):
        run_round(session, i)
        time.sleep(random.uniform(0.5, 2.0))

    print("\nDone! Open Grafana at http://localhost:3000 to see the data.")


if __name__ == "__main__":
    main()
