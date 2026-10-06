"""Dragon Village 3 boards -> Discord notifier."""

import json
import os
import re
import sys
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright


BASE_URL = "https://community.withhive.com/dv3/th/board"

UPDATE_WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL")
EVENT_WEBHOOK = os.environ.get("DISCORD_EVENT_WEBHOOK_URL")

STATE = Path("seen.json")
MAX_PER_RUN = 10

BOARDS = {
    "13": {
        "name": "อัปเดตเกม",
        "emoji": "🔧",
        "webhook": UPDATE_WEBHOOK,
    },
    "5": {
        "name": "กิจกรรม",
        "emoji": "🎉",
        "webhook": EVENT_WEBHOOK,
    },
}


def fetch_posts(page, board_id):
    """ดึงรายการโพสต์จากบอร์ดที่กำหนด"""

    url = f"{BASE_URL}/{board_id}"

    print(f"\nกำลังตรวจ Board {board_id}...")

    page.goto(url, wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(3000)

    items = page.locator(
        "li[onclick*=\"detail('\"]"
    ).evaluate_all(
        """
        els => els.map(el => ({
            onclick: el.getAttribute("onclick") || "",
            title:
                el.querySelector(".tit strong")?.innerText?.trim()
                || el.querySelector("strong")?.innerText?.trim()
                || "",
            date:
                el.querySelector(".t_date")?.innerText?.trim()
                || ""
        }))
        """
    )

    pattern = re.compile(
        r"""detail\(['"](\d+)['"]\s*,\s*['"](\d+)['"]\)"""
    )

    posts = {}

    for item in items:
        match = pattern.search(item.get("onclick", ""))

        if not match:
            continue

        found_board = match.group(1)
        post_id = match.group(2)

        # ป้องกันการอ่านโพสต์จากบอร์ดอื่น
        if found_board != board_id:
            continue

        title = item.get("title") or f"โพสต์ #{post_id}"

        posts[post_id] = {
            "id": post_id,
            "board": board_id,
            "title": title,
            "date": item.get("date", ""),
            "url": f"{BASE_URL}/{board_id}/{post_id}",
        }

    print(f"พบรายการ HTML: {len(items)}")
    print(f"พบโพสต์: {len(posts)}")

    latest = sorted(posts.keys(), key=int, reverse=True)[:3]

    for pid in latest:
        print(f"  {pid} | {posts[pid]['title']}")

    return posts


def send_discord(post, board_config):
    """ส่งโพสต์ไปยัง Discord ของบอร์ดนั้น"""

    webhook = board_config["webhook"]

    if not webhook:
        raise RuntimeError(
            f"ไม่พบ Webhook สำหรับ Board {post['board']}"
        )

    emoji = board_config["emoji"]
    board_name = board_config["name"]

    fields = []

    if post.get("date"):
        fields.append({
            "name": "📅 วันที่ประกาศ",
            "value": post["date"],
            "inline": True,
        })

    fields.append({
        "name": "🔎 Post ID",
        "value": str(post["id"]),
        "inline": True,
    })

    if post["board"] == "5":
        description = (
            "ตรวจพบ **กิจกรรมใหม่** จาก Dragon Village 3 🎉\n\n"
            "กดที่หัวข้อด้านบนเพื่ออ่านรายละเอียดกิจกรรมฉบับเต็ม"
        )
        color = 0x9B59B6

    else:
        description = (
            "ตรวจพบ **ประกาศอัปเดตใหม่** จาก Dragon Village 3 🔧\n\n"
            "กดที่หัวข้อด้านบนเพื่ออ่านประกาศฉบับเต็ม"
        )
        color = 0x3498DB

    payload = {
        "username": "DV3 Monitor",
        "embeds": [{
            "author": {
                "name": f"Dragon Village 3 • {board_name}"
            },
            "title": f"{emoji} {post['title'][:240]}",
            "url": post["url"],
            "description": description,
            "color": color,
            "fields": fields,
            "footer": {
                "text": (
                    f"DV3 Monitor • Board {post['board']} "
                    f"• Post {post['id']}"
                )
            },
        }],
    }

    req = urllib.request.Request(
        webhook,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "User-Agent": "dv3-monitor/4.0",
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=30) as response:
        response.read()


def load_state():
    """
    รองรับ seen.json แบบเก่าที่เป็น list ของ Board 13
    และแบบใหม่ที่แยก Board 13 / Board 5
    """

    if not STATE.exists():
        return {}

    try:
        data = json.loads(
            STATE.read_text(encoding="utf-8")
        )

        # seen.json เวอร์ชันเก่า
        if isinstance(data, list):
            print("พบ seen.json เวอร์ชันเดิม")
            return {
                "13": set(str(x) for x in data)
            }

        # seen.json เวอร์ชันใหม่
        if isinstance(data, dict):
            return {
                str(board): set(str(x) for x in ids)
                for board, ids in data.items()
            }

    except Exception as e:
        print("อ่าน seen.json ไม่สำเร็จ:", e)

    return {}


def save_state(state):
    """บันทึกประวัติแยกตาม Board"""

    output = {}

    for board_id, ids in state.items():
        output[board_id] = sorted(ids, key=int)

    STATE.write_text(
        json.dumps(
            output,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def test_webhooks():
    """ทดสอบ Webhook ทั้งสองห้อง"""

    print("กำลังทดสอบห้องอัปเดตเกม...")

    send_discord(
        {
            "id": "999999",
            "board": "13",
            "title": "ทดสอบระบบแจ้งเตือนอัปเดตเกม",
            "date": "",
            "url": f"{BASE_URL}/13",
        },
        BOARDS["13"],
    )

    print("✅ ส่งห้องอัปเดตเกมสำเร็จ")

    print("กำลังทดสอบห้องกิจกรรม...")

    send_discord(
        {
            "id": "999999",
            "board": "5",
            "title": "ทดสอบระบบแจ้งเตือนกิจกรรม",
            "date": "",
            "url": f"{BASE_URL}/5",
        },
        BOARDS["5"],
    )

    print("✅ ส่งห้องกิจกรรมสำเร็จ")


def main():

    if "--test" in sys.argv:
        test_webhooks()
        return

    state = load_state()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        page = browser.new_page(
            locale="th-TH",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/140 Safari/537.36"
            ),
        )

        for board_id, config in BOARDS.items():

            posts = fetch_posts(page, board_id)

            if not posts:
                browser.close()
                raise RuntimeError(
                    f"ไม่พบโพสต์ใน Board {board_id}"
                )

            # Board ที่ยังไม่เคยตรวจมาก่อน
            if board_id not in state:

                state[board_id] = set(posts.keys())

                print(
                    f"Board {board_id} รันครั้งแรก: "
                    f"บันทึก {len(posts)} โพสต์ "
                    "โดยไม่ส่ง Discord"
                )

                continue

            seen = state[board_id]

            new_ids = [
                pid
                for pid in posts
                if pid not in seen
            ]

            new_ids.sort(key=int)

            if not new_ids:
                print(
                    f"Board {board_id}: ไม่มีประกาศใหม่"
                )
                continue

            print(
                f"Board {board_id}: "
                f"พบใหม่ {len(new_ids)} รายการ"
            )

            for pid in new_ids[-MAX_PER_RUN:]:

                send_discord(
                    posts[pid],
                    config,
                )

                print(
                    "ส่ง Discord แล้ว:",
                    pid,
                    "|",
                    posts[pid]["title"],
                )

            seen.update(posts.keys())

        browser.close()

    save_state(state)

    print("\n✅ อัปเดต seen.json สำเร็จ")


if __name__ == "__main__":
    main()
