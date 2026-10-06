"""Dragon Village 3 board -> Discord notifier (runs on GitHub Actions)."""
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = "https://community.withhive.com/dv3/th/board/13"
# ลิงก์โพสต์แต่ละอันต้องมีรูปแบบนี้ (ปรับได้ถ้าเว็บใช้รูปแบบอื่น)
POST_PATTERN = re.compile(r"/board/13/(\d+)")
WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL")
STATE = Path("seen.json")
MAX_PER_RUN = 10


def fetch_links():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(locale="th-TH")

        page.goto(URL, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(3000)

        # DEBUG: ตรวจ element ของรายการประกาศที่เว็บใช้ javascript เปิดโพสต์
        debug_items = page.locator('a[href="javascript:;"]').evaluate_all("""
        els => els.map(e => ({
            text: e.innerText.trim(),
            html: e.outerHTML,
            onclick: e.getAttribute('onclick'),
            data: {...e.dataset},
            parent: e.parentElement ? e.parentElement.outerHTML : ''
        }))
        """)

        print("===== DV3 DEBUG =====")
        for item in debug_items:
            if item["text"]:
                print("TEXT:", item["text"][:120])
                print("HTML:", item["html"][:1000])
                print("ONCLICK:", item["onclick"])
                print("DATA:", item["data"])
                print("PARENT:", item["parent"][:1500])
                print("----------------------")

        links = page.eval_on_selector_all(
            "a[href]",
            "els => els.map(e => ({href: e.href, text: e.innerText.trim()}))",
        )

        browser.close()

    return links


def extract_posts(links):
    posts = {}
    for link in links:
        m = POST_PATTERN.search(link["href"])
        if not m:
            continue
        lines = [l.strip() for l in link["text"].splitlines() if l.strip()]
        title = lines[0] if lines else f"โพสต์ #{m.group(1)}"
        posts.setdefault(m.group(1), {"id": m.group(1), "title": title, "url": link["href"]})
    return posts


def send_discord(post):
    payload = {
        "username": "DV3 Update",
        "embeds": [{
            "title": post["title"][:250],
            "url": post["url"],
            "description": "มีโพสต์ใหม่ในบอร์ดอัพเดท Dragon Village 3",
            "color": 0xF5A623,
        }],
    }
    req = urllib.request.Request(
        WEBHOOK,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "dv3-monitor/1.0"},
    )
    urllib.request.urlopen(req, timeout=30).read()


def main():
    if "--test" in sys.argv:
        send_discord({"title": "ทดสอบการแจ้งเตือน", "url": URL})
        print("ส่งข้อความทดสอบแล้ว")
        return

    links = fetch_links()
    posts = extract_posts(links)

    if not posts:
        print("ไม่พบลิงก์โพสต์ที่ตรงกับ POST_PATTERN ลิงก์ทั้งหมดที่เจอ:")
        for l in links:
            print(" ", l["href"], "|", l["text"][:60].replace("\n", " "))
        sys.exit(1)

    seen = set(json.loads(STATE.read_text())) if STATE.exists() else None

    if seen is None:
        # รันครั้งแรก: บันทึกโพสต์ที่มีอยู่แล้วโดยไม่แจ้งเตือน กันข้อความท่วมช่อง
        STATE.write_text(json.dumps(sorted(posts), indent=1))
        print(f"รันครั้งแรก บันทึก {len(posts)} โพสต์ ไม่ส่งแจ้งเตือน")
        return

    new_ids = sorted((i for i in posts if i not in seen), key=int)
    if not new_ids:
        print("ไม่มีโพสต์ใหม่")
        return

    for pid in new_ids[-MAX_PER_RUN:]:
        send_discord(posts[pid])
        print("ส่งแล้ว:", posts[pid]["title"])

    STATE.write_text(json.dumps(sorted(seen | set(posts), key=int), indent=1))


if __name__ == "__main__":
    main()
