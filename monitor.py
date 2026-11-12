"""Dragon Village 3 boards -> Discord notifier."""

import json
import os
import re
import sys
import urllib.request
import urllib.error
import time
from pathlib import Path

from playwright.sync_api import sync_playwright


BASE_URL = "https://community.withhive.com/dv3/th/board"

UPDATE_WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL")
EVENT_WEBHOOK = os.environ.get("DISCORD_EVENT_WEBHOOK_URL")

STATE = Path("seen.json")

# จำนวนโพสต์ใหม่สูงสุดที่จะส่งต่อการรัน 1 ครั้ง
MAX_PER_RUN = 10

# Discord embed description จำกัด 4096 ตัวอักษร
# ใช้น้อยกว่านั้นเพื่อเผื่อข้อความอื่น
CHUNK_SIZE = 1800

# จำกัดจำนวนส่วนต่อโพสต์ ป้องกันกรณีเว็บผิดปกติ
MAX_PARTS = 20

BOARDS = {
    "13": {
        "name": "อัปเดตเกม",
        "emoji": "🔧",
        "webhook": UPDATE_WEBHOOK,
        "color": 0x3498DB,
    },
    "5": {
        "name": "กิจกรรม",
        "emoji": "🎉",
        "webhook": EVENT_WEBHOOK,
        "color": 0x9B59B6,
    },
}


def fetch_posts(page, board_id):
    """อ่านรายการโพสต์จากหน้า Hive โดยรองรับโครงสร้าง DOM หลายรูปแบบ"""

    url = f"{BASE_URL}/{board_id}"

    print(f"\nกำลังตรวจ Board {board_id}...")
    print("URL:", url)

    page.goto(
        url,
        wait_until="domcontentloaded",
        timeout=60000,
    )

    # Hive อาจโหลดรายการโพสต์ภายหลังจาก HTML หลัก
    page.wait_for_timeout(5000)

    # รอรายการแบบเดิมหรือ link ของหน้าโพสต์แบบใหม่
    try:
        page.wait_for_function(
            """boardId => {
                const escaped = String(boardId);
                return document.querySelector(
                    'li[onclick*="goToPost"], [onclick*="goToPost"], ' +
                    'a[href*="/board/' + escaped + '/"]'
                ) !== null;
            }""",
            arg=board_id,
            timeout=12000,
        )
    except Exception:
        # ไม่สรุปว่าเว็บล้มเหลวตรงนี้ ให้ลองอ่าน DOM และรายงานสาเหตุด้านล่าง
        pass

    print("URL หลังโหลด:", page.url)
    print("TITLE:", page.title())

    # อ่านข้อมูลจาก onclick, href และ data attributes
    # ไม่ผูกกับ tag <li> เพียงชนิดเดียว
    candidates = page.locator("body").evaluate(
        """(body) => {
            const result = [];
            const seen = new Set();
            const nodes = body.querySelectorAll("*");

            for (const el of nodes) {
                const attrs = {};
                for (const attr of el.attributes || []) {
                    attrs[attr.name.toLowerCase()] = attr.value || "";
                }

                const onclick = attrs.onclick || "";
                const href = attrs.href || "";
                const dataId =
                    attrs["data-post-id"] ||
                    attrs["data-article-id"] ||
                    attrs["data-id"] || "";

                const source = onclick + " " + href;
                const detailMatches = [...source.matchAll(
                    /detail\s*\(\s*['"]?(\d+)['"]?\s*,\s*['"]?(\d+)['"]?\s*\)/gi
                )];

                // Hive Thai board ปัจจุบันใช้ onclick="goToPost('FULL_URL')"
                // ดังนั้นต้องค้นหา URL จากทั้ง onclick และ href ไม่ใช่ href อย่างเดียว
                const pathMatches = [...source.matchAll(
                    /\/board\/(\d+)\/(\d+)(?:[/?#'"]|$)/g
                )];

                const ids = [];
                for (const m of detailMatches) {
                    ids.push({board: m[1], id: m[2]});
                }
                for (const m of pathMatches) {
                    ids.push({board: m[1], id: m[2]});
                }

                // รองรับ data-id เมื่อมี element ระบุ board ชัดเจน
                const dataBoard =
                    attrs["data-board-id"] ||
                    attrs["data-board"] || "";
                if (/^\d+$/.test(dataId) && dataBoard) {
                    ids.push({board: dataBoard, id: dataId});
                }

                for (const item of ids) {
                    const key = item.board + ":" + item.id;
                    if (seen.has(key)) continue;
                    seen.add(key);

                    const container =
                        el.closest("li") ||
                        el.closest("article") ||
                        el.closest("[class*='item']") ||
                        el;

                    const titleNode =
                        container.querySelector(".title") ||
                        container.querySelector(".tit strong") ||
                        container.querySelector(".tit") ||
                        container.querySelector("strong") ||
                        container.querySelector("a") ||
                        container;

                    const dateNode =
                        container.querySelector(".date") ||
                        container.querySelector(".t_date") ||
                        container.querySelector("time") ||
                        container.querySelector("[class*='date']");

                    result.push({
                        board: item.board,
                        id: item.id,
                        title: (titleNode?.innerText || "").trim(),
                        date: (dateNode?.innerText || dateNode?.getAttribute("datetime") || "").trim(),
                        source: href ? "href" : "onclick"
                    });
                }
            }
            return result;
        }"""
    )

    posts = {}

    for item in candidates:
        if str(item.get("board", "")) != str(board_id):
            continue

        post_id = str(item.get("id", "")).strip()
        if not post_id.isdigit():
            continue

        title = item.get("title") or f"โพสต์ #{post_id}"

        posts[post_id] = {
            "id": post_id,
            "board": str(board_id),
            "title": title,
            "date": item.get("date", ""),
            "url": f"{BASE_URL}/{board_id}/{post_id}",
        }

    print(f"พบ candidate จาก DOM: {len(candidates)}")
    print(f"พบโพสต์ใน Board {board_id}: {len(posts)}")

    latest = sorted(posts.keys(), key=int, reverse=True)[:5]
    print("โพสต์ล่าสุดที่ตรวจพบ:")
    for pid in latest:
        print(f"  {pid} | {posts[pid]['title']}")

    if not posts:
        body_text = page.locator("body").inner_text()[:2500]
        html_length = page.locator("html").evaluate("(el) => el.outerHTML.length")
        print("HTML length:", html_length)
        print("BODY diagnostic:", body_text[:2500].replace("\n", " | "))

        lower_text = body_text.lower()
        if any(term in lower_text for term in (
            "captcha", "access denied", "cloudflare",
            "unusual traffic", "잠시 후", "접근이"
        )):
            reason = "หน้า Hive อาจแสดงระบบป้องกันบอตหรือปฏิเสธการเข้าถึง"
        elif html_length < 1500:
            reason = "หน้า Hive ส่ง HTML กลับมาน้อยผิดปกติ"
        else:
            reason = "ไม่พบตัวระบุโพสต์ใน DOM ที่โหลดได้ อาจมีการเปลี่ยนโครงสร้างหน้าเว็บ"

        raise RuntimeError(
            f"Board {board_id}: {reason}. "
            f"title={page.title()!r}, url={page.url!r}, "
            f"html_length={html_length}, candidates={len(candidates)}"
        )

    return posts

def clean_content(text, post):
    """ทำความสะอาดข้อความจากหน้าประกาศ"""

    if not text:
        return ""

    text = text.replace("\r", "")

    lines = [
        line.strip()
        for line in text.split("\n")
    ]

    # ตัดข้อมูลหัวโพสต์ที่ซ้ำกับ Embed
    title = post.get("title", "")

    cleaned = []
    title_removed = False

    for line in lines:

        if not line:
            # รักษาบรรทัดว่าง แต่ไม่ให้ซ้ำเยอะ
            if cleaned and cleaned[-1] != "":
                cleaned.append("")
            continue

        if line == "แชร์":
            continue

        # ตัดชื่อหัวข้อที่ซ้ำครั้งแรก
        if (
            not title_removed
            and title
            and line == title
        ):
            title_removed = True
            continue

        cleaned.append(line)

    text = "\n".join(cleaned)

    # ลดบรรทัดว่างซ้ำ
    text = re.sub(
        r"\n{3,}",
        "\n\n",
        text,
    )

    return text.strip()


def fetch_post_detail(page, post):
    """
    เปิดโพสต์จริงและอ่าน ARTICLE.board_detail
    พร้อมดึงรูปภาพภายในโพสต์
    """

    print(
        f"กำลังอ่านรายละเอียด Post "
        f"{post['id']}..."
    )

    page.goto(
        post["url"],
        wait_until="networkidle",
        timeout=60000,
    )

    page.wait_for_timeout(2500)

    article = page.locator(
        "article.board_detail"
    )

    if article.count() == 0:
        print(
            "⚠️ ไม่พบ article.board_detail "
            f"ใน Post {post['id']}"
        )

        return {
            "content": "",
            "images": [],
        }

    raw_text = article.first.inner_text()

    content = clean_content(
        raw_text,
        post,
    )

    images = article.first.locator(
        "img"
    ).evaluate_all(
        """
        imgs => imgs.map(img => ({
            src:
                img.currentSrc
                || img.src
                || img.getAttribute("data-src")
                || ""
        }))
        .map(x => x.src)
        .filter(src =>
            src &&
            src.startsWith("http")
        )
        """
    )

    # ลบรูปซ้ำ
    unique_images = []

    for image in images:
        if image not in unique_images:
            unique_images.append(image)

    print(
        f"อ่านข้อความได้ {len(content)} ตัวอักษร "
        f"/ พบรูป {len(unique_images)} รูป"
    )

    return {
        "content": content,
        "images": unique_images,
    }


def split_text(text, limit=CHUNK_SIZE):
    """
    แบ่งข้อความยาวเป็นหลายส่วน
    โดยพยายามแบ่งตามย่อหน้า
    """

    if not text:
        return []

    if len(text) <= limit:
        return [text]

    paragraphs = text.split("\n\n")

    chunks = []
    current = ""

    for paragraph in paragraphs:

        paragraph = paragraph.strip()

        if not paragraph:
            continue

        candidate = (
            paragraph
            if not current
            else current + "\n\n" + paragraph
        )

        if len(candidate) <= limit:
            current = candidate
            continue

        if current:
            chunks.append(current)
            current = ""

        # ย่อหน้าเดียวยาวเกิน limit
        while len(paragraph) > limit:

            cut = paragraph.rfind(
                "\n",
                0,
                limit,
            )

            if cut < limit // 2:
                cut = paragraph.rfind(
                    " ",
                    0,
                    limit,
                )

            if cut < limit // 2:
                cut = limit

            chunks.append(
                paragraph[:cut].strip()
            )

            paragraph = (
                paragraph[cut:].strip()
            )

        current = paragraph

    if current:
        chunks.append(current)

    return chunks[:MAX_PARTS]


def discord_request(webhook, payload):
    """ส่ง Discord พร้อมจัดการ Rate Limit อัตโนมัติ"""

    data = json.dumps(
        payload,
        ensure_ascii=False,
    ).encode("utf-8")

    print(
        f"กำลังส่ง Discord: "
        f"{len(data)} bytes"
    )

    max_retries = 10

    for attempt in range(max_retries):

        req = urllib.request.Request(
            webhook,
            data=data,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "dv3-monitor/5.2",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(
                req,
                timeout=30,
            ) as response:
                response.read()

            # เว้นระยะทุกข้อความ
            # ป้องกันยิง Discord เร็วเกินไป
            time.sleep(0.8)

            return

        except urllib.error.HTTPError as e:

            error_body = e.read().decode(
                "utf-8",
                errors="replace",
            )

            # Discord Rate Limit
            if e.code == 429:

                retry_after = 1.0

                try:
                    error_data = json.loads(
                        error_body
                    )

                    retry_after = float(
                        error_data.get(
                            "retry_after",
                            1.0,
                        )
                    )

                except Exception:
                    pass

                # เผื่อเวลาเพิ่มเล็กน้อย
                wait_time = max(
                    retry_after + 0.5,
                    1.0,
                )

                print(
                    "⏳ Discord Rate Limit "
                    f"รอ {wait_time:.2f} วินาที "
                    f"แล้วลองใหม่ "
                    f"({attempt + 1}/{max_retries})"
                )

                time.sleep(wait_time)

                continue

            print(
                f"❌ Discord HTTP {e.code}"
            )

            print(
                "Discord response:",
                error_body[:1000],
            )

            raise

    raise RuntimeError(
        "Discord ยัง Rate Limit "
        "หลังจากลองใหม่หลายครั้ง"
    )


def send_post_to_discord(
    post,
    detail,
    config,
):
    """
    ส่งรายละเอียดโพสต์ทั้งหมดเข้า Discord
    ถ้ายาวจะแบ่งหลาย Embed
    """

    webhook = config["webhook"]

    if not webhook:
        raise RuntimeError(
            "ไม่พบ Webhook สำหรับ "
            f"Board {post['board']}"
        )

    content = detail.get(
        "content",
        "",
    )

    images = detail.get(
        "images",
        [],
    )

    chunks = split_text(content)

    # กรณีอ่านรายละเอียดไม่ได้
    if not chunks:
        chunks = [
            "ไม่สามารถอ่านข้อความภายใน"
            "ประกาศได้ในขณะนี้\n\n"
            "สามารถกดลิงก์ด้านล่าง"
            "เพื่ออ่านประกาศต้นฉบับ"
        ]

    total = len(chunks)

    for index, chunk in enumerate(
        chunks,
        start=1,
    ):

        if total > 1:
            part_text = (
                f" • ตอนที่ {index}/{total}"
            )
        else:
            part_text = ""

        if index == 1:
            title = (
                f"{config['emoji']} "
                f"{post['title']}"
            )
        else:
            title = (
                f"{config['emoji']} "
                f"{post['title']} "
                f"({index}/{total})"
            )

        embed = {
            "title": title[:250],
            "url": post["url"],
            "description": chunk,
            "color": config["color"],
            "footer": {
                "text": (
                    f"DV3 Monitor"
                    f"{part_text} • "
                    f"Board {post['board']} • "
                    f"Post {post['id']}"
                )
            },
        }

        # แสดงข้อมูลวันที่เฉพาะส่วนแรก
        if index == 1:

            embed["author"] = {
                "name": (
                    "Dragon Village 3 • "
                    f"{config['name']}"
                )
            }

            if post.get("date"):
                embed["fields"] = [{
                    "name":
                        "📅 วันที่ประกาศ",
                    "value":
                        post["date"],
                    "inline":
                        False,
                }]

        payload = {
            "username": "DV3 Monitor",
            "embeds": [embed],
        }

        discord_request(
            webhook,
            payload,
        )

    # --------------------------
    # ส่งรูปภาพ
    # --------------------------

    # จำกัด 10 รูปต่อโพสต์
    # ป้องกัน Discord ถูกยิงข้อความมากเกินไป
    for image_number, image_url in enumerate(
        images[:10],
        start=1,
    ):

        payload = {
            "username": "DV3 Monitor",
            "embeds": [{
                "title": (
                    f"🖼️ รูปประกอบ "
                    f"{image_number}"
                ),
                "url": post["url"],
                "image": {
                    "url": image_url
                },
                "color": config["color"],
                "footer": {
                    "text": (
                        f"{config['name']} • "
                        f"Post {post['id']}"
                    )
                },
            }],
        }

        discord_request(
            webhook,
            payload,
        )

    # ข้อความปิดท้าย
    payload = {
        "username": "DV3 Monitor",
        "embeds": [{
            "description": (
                "🔗 **อ่านประกาศต้นฉบับ**\n"
                f"{post['url']}"
            ),
            "color": config["color"],
        }],
    }

    discord_request(
        webhook,
        payload,
    )


def load_state():
    """
    รองรับ seen.json เวอร์ชันเก่า
    และเวอร์ชันใหม่ที่แยก Board
    """

    if not STATE.exists():
        return {}

    try:

        data = json.loads(
            STATE.read_text(
                encoding="utf-8"
            )
        )

        # รูปแบบเดิม
        if isinstance(data, list):

            print(
                "พบ seen.json "
                "เวอร์ชันเดิม"
            )

            return {
                "13": set(
                    str(x)
                    for x in data
                )
            }

        # รูปแบบใหม่
        if isinstance(data, dict):

            return {
                str(board): set(
                    str(x)
                    for x in ids
                )
                for board, ids
                in data.items()
            }

    except Exception as e:

        print(
            "อ่าน seen.json "
            "ไม่สำเร็จ:",
            e,
        )

    return {}


def save_state(state):
    """บันทึกโพสต์ที่เคยเห็น"""

    output = {}

    for board_id, ids in state.items():

        output[board_id] = sorted(
            ids,
            key=int,
        )

    STATE.write_text(
        json.dumps(
            output,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def test_webhooks():
    """ทดสอบ Webhook สองห้อง"""

    for board_id, config in BOARDS.items():

        webhook = config["webhook"]

        if not webhook:
            raise RuntimeError(
                "ไม่พบ Webhook "
                f"Board {board_id}"
            )

        payload = {
            "username": "DV3 Monitor",
            "embeds": [{
                "title": (
                    f"{config['emoji']} "
                    "ทดสอบระบบแจ้งเตือน"
                ),
                "description": (
                    f"ห้อง **{config['name']}** "
                    "เชื่อมต่อสำเร็จ ✅\n\n"
                    "เมื่อมีโพสต์ใหม่ "
                    "ระบบจะนำรายละเอียด"
                    "จากประกาศมาแสดง"
                    "ใน Discord อัตโนมัติ"
                ),
                "color":
                    config["color"],
            }],
        }

        discord_request(
            webhook,
            payload,
        )

        print(
            f"✅ Board {board_id} "
            f"ส่งห้อง {config['name']} "
            "สำเร็จ"
        )


def main():

    if "--test" in sys.argv:
        test_webhooks()
        return

    if "--test-full" in sys.argv:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)

            page = browser.new_page(
                locale="th-TH",
                user_agent=(
                    "Mozilla/5.0 "
                    "(Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 "
                    "Chrome/140 Safari/537.36"
                ),
            )

            post = {
                "id": "4407",
                "board": "13",
                "title": "อัปเดตประกาศการบํารุงรักษา - 2026.09.30 (ทดสอบระบบ)",
                "date": "29-09-2026 21:30",
                "url": f"{BASE_URL}/13/4407",
            }

            detail = fetch_post_detail(page, post)

            send_post_to_discord(
                post,
                detail,
                BOARDS["13"],
            )

            browser.close()

        print("✅ ทดสอบส่งประกาศฉบับเต็มสำเร็จ")
        return
        
    state = load_state()

    with sync_playwright() as p:

        browser = p.chromium.launch(
            headless=True
        )

        page = browser.new_page(
            locale="th-TH",
            user_agent=(
                "Mozilla/5.0 "
                "(Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "Chrome/140 Safari/537.36"
            ),
        )

        for board_id, config in BOARDS.items():

            posts = fetch_posts(
                page,
                board_id,
            )

            if not posts:
                browser.close()

                raise RuntimeError(
                    "ไม่พบโพสต์ใน "
                    f"Board {board_id}"
                )

            # --------------------------
            # บอร์ดที่เพิ่งเพิ่มครั้งแรก
            # --------------------------

            if board_id not in state:

                state[board_id] = set(
                    posts.keys()
                )

                print(
                    f"Board {board_id} "
                    "รันครั้งแรก: "
                    f"บันทึก {len(posts)} "
                    "โพสต์โดยไม่ส่ง Discord"
                )

                continue

            seen = state[board_id]

            new_ids = [
                pid
                for pid in posts
                if pid not in seen
            ]

            new_ids.sort(
                key=int
            )

            if not new_ids:

                print(
                    f"Board {board_id}: "
                    "ไม่มีประกาศใหม่"
                )

                continue

            print(
                f"Board {board_id}: "
                f"พบใหม่ "
                f"{len(new_ids)} รายการ"
            )

            # --------------------------
            # อ่านโพสต์ใหม่จริง
            # --------------------------

            for pid in new_ids[
                -MAX_PER_RUN:
            ]:

                post = posts[pid]

                detail = fetch_post_detail(
                    page,
                    post,
                )


                send_post_to_discord(
                    post,
                    detail,
                    config,
                )

                # บันทึกทันทีหลังส่ง Discord สำเร็จ
                seen.add(pid)
                save_state(state)

                print(
                    "✅ ส่งรายละเอียดแล้ว + บันทึก seen:",
                    pid,
                    "|",
                    post["title"],
                )

        browser.close()

    save_state(state)

    print(
        "\n✅ อัปเดต seen.json สำเร็จ"
    )


if __name__ == "__main__":
    main()
