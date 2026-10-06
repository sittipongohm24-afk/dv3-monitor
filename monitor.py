"""Dragon Village 3 board -> Discord notifier (GitHub Actions)."""

import json
import os
import re
import sys
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright


BOARD_ID = "13"
URL = f"https://community.withhive.com/dv3/th/board/{BOARD_ID}"
WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL")

STATE = Path("seen.json")
MAX_PER_RUN = 10


def fetch_posts():
    """เปิดหน้า DV3 และดึง Post ID จาก onclick="detail('13', '4407')"."""

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        page = browser.new_page(
            locale="th-TH",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/140 Safari/537.36"
            ),
        )

        page.goto(URL, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(3000)

        items = page.locator("li[onclick*=\"detail('\"]").evaluate_all(
            """
            els => els.map(el => ({
                onclick: el.getAttribute("onclick") || "",
                title:
                    el.querySelector(".tit strong")?.innerText?.trim()
                    || el.querySelector("strong")?.innerText?.trim()
                    || el.innerText?.trim()
                    || "",
                date:
                    el.querySelector(".t_date")?.innerText?.trim()
                    || ""
            }))
            """
        )

        browser.close()

    return items


def extract_posts(items):
    """แปลงข้อมูลจากหน้าเว็บเป็น dictionary ของโพสต์."""

    posts = {}

    pattern = re.compile(
        r"""detail\(['"](\d+)['"]\s*,\s*['"](\d+)['"]\)"""
    )

    for item in items:
        onclick = item.get("onclick", "")
        match = pattern.search(onclick)

        if not match:
            continue

        board_id = match.group(1)
        post_id = match.group(2)

        title = item.get("title") or f"โพสต์ #{post_id}"
        date = item.get("date", "")

        post_url = (
            f"https://community.withhive.com/"
            f"dv3/th/board/{board_id}/{post_id}"
        )

        posts[post_id] = {
            "id": post_id,
            "title": title,
            "date": date,
            "url": post_url,
        }

    return posts

def send_discord(post):
    """ส่งประกาศเข้า Discord."""

    if not WEBHOOK:
        raise RuntimeError(
            "ไม่พบ DISCORD_WEBHOOK_URL ใน GitHub Secrets"
        )

    description = "มีประกาศใหม่ใน Dragon Village 3"

    if post.get("date"):
        description += f"\\n📅 {post['date']}"

    payload = {
        "username": "DV3 Update",
        "embeds": [
            {
                "title": post["title"][:250],
                "url": post["url"],
                "description": description,
                "color": 0xF5A623,
                "footer": {
                    "text": f"DV3 • Post ID {post['id']}"
                },
            }
        ],
    }

    req = urllib.request.Request(
        WEBHOOK,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "User-Agent": "dv3-monitor/2.0",
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=30) as response:
        response.read()


def load_seen():
    if not STATE.exists():
        return None

    try:
        data = json.loads(STATE.read_text(encoding="utf-8"))
        return set(str(x) for x in data)

    except Exception:
        return set()


def save_seen(seen):
    STATE.write_text(
        json.dumps(
            sorted(seen, key=int),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def main():

    # -------------------------
    # TEST DISCORD
    # -------------------------

    if "--test" in sys.argv:

        send_discord(
            {
                "id": "TEST",
                "title": "✅ DV3 Monitor ทำงานสำเร็จ",
                "date": "",
                "url": URL,
            }
        )

        print("ส่งข้อความทดสอบเข้า Discord สำเร็จ")
        return

    # -------------------------
    # FETCH DV3
    # -------------------------

    print("กำลังตรวจสอบ DV3...")

    items = fetch_posts()

    print(f"พบรายการ HTML: {len(items)}")

    posts = extract_posts(items)

    print(f"พบโพสต์ DV3: {len(posts)}")

    if not posts:
        print("ERROR: ไม่สามารถหา Post ID จากหน้า DV3 ได้")

        for item in items[:5]:
            print(
                "DEBUG:",
                item.get("onclick"),
                "|",
                item.get("title"),
            )

        sys.exit(1)

    # แสดงโพสต์ล่าสุดที่ตรวจพบ
    latest_ids = sorted(posts.keys(), key=int, reverse=True)[:5]

    print("โพสต์ล่าสุด:")

    for pid in latest_ids:
        print(
            pid,
            "|",
            posts[pid]["title"],
        )

    # -------------------------
    # LOAD STATE
    # -------------------------

    seen = load_seen()

    # ครั้งแรก
    if seen is None:

        save_seen(set(posts.keys()))

        print(
            f"รันครั้งแรก: บันทึก {len(posts)} โพสต์ "
            "โดยไม่ส่ง Discord"
        )

        return

    # -------------------------
    # FIND NEW POSTS
    # -------------------------

    new_ids = [
        pid
        for pid in posts
        if pid not in seen
    ]

    new_ids.sort(key=int)

    if not new_ids:
        print("ไม่มีประกาศใหม่")
        return

    print(f"พบประกาศใหม่ {len(new_ids)} รายการ")

    # ป้องกัน Discord ท่วม
    notify_ids = new_ids[-MAX_PER_RUN:]

    for pid in notify_ids:

        post = posts[pid]

        send_discord(post)

        print(
            "ส่ง Discord แล้ว:",
            pid,
            "|",
            post["title"],
        )

    # บันทึกทุกโพสต์ที่พบ
    seen.update(posts.keys())

    save_seen(seen)

    print("อัปเดต seen.json สำเร็จ")


if __name__ == "__main__":
    main()
