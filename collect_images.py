#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""从 Wikimedia Commons 按类别采集图片到本地，生成带 ground truth 标注的清单。

用法：python collect_images.py <输出目录> [每类张数]
输出：
    <输出目录>/<类名>/<文件名>         下载的图片
    <输出目录>/manifest.jsonl          每行一条 {"path","main","sub"}

类别映射（Wikimedia 类别名 -> 我们的 大类/小类）：
    person.real     -> Category:People, Portrait photographs, Selfies
    person.group    -> Category:Group photographs
    person.anime    -> Category:Anime, Manga illustrations
    animal          -> Category:Animals, Cats, Dogs, Birds
    document.report -> Category:Reports, Documents
    document.table  -> Category:Tables, Data tables
    document.code   -> Category:Computer programming, Source code
    chart           -> Category:Charts, Line charts, Bar charts, Pie charts, Scatter plots
    generic.landscape -> Category:Landscapes, Mountains
    generic.object  -> Category:Objects
    generic.screenshot -> Category:Screenshots
    generic.meme    -> Category:Internet memes

网络：直连失败时回退到本地代理 127.0.0.1:7897。
"""
import base64
import json
import os
import sys
import time
import urllib.request
import urllib.parse

API = "https://commons.wikimedia.org/w/api.php"
PROXY = "http://127.0.0.1:7897"

# 类别映射：Wikimedia 类别名 -> (大类, 小类)（v2 场景体系，F6）
CATEGORY_MAP = {
    "People": ("person", "real_single"),
    "Portrait_photographs": ("person", "real_single"),
    "Selfies": ("person", "real_single"),
    "Group_photographs": ("person", "real_group"),
    "Anime": ("person", "anime_character"),
    "Manga_illustrations": ("person", "anime_character"),
    "Animals": ("animal", ""),
    "Cats": ("animal", "mammal"),
    "Dogs": ("animal", "mammal"),
    "Birds": ("animal", "bird"),
    "Reports": ("document", "report"),
    "Documents": ("document", "report"),
    "Tables": ("document", "table"),
    "Data_tables": ("document", "table"),
    "Computer_programming": ("document", "code"),
    "Source_code": ("document", "code"),
    "Charts": ("chart", ""),
    "Line_charts": ("chart", ""),
    "Bar_charts": ("chart", ""),
    "Pie_charts": ("chart", ""),
    "Scatter_plots": ("chart", ""),
    "Landscapes": ("scene", "landscape"),
    "Mountains": ("scene", "landscape"),
    "Objects": ("object", ""),
    "Screenshots": ("screenshot", ""),
    "Internet_memes": ("meme", ""),
}


def fetch(url: str, timeout: int = 30) -> bytes:
    """带代理回退的下载。"""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (vision-eyes corpus)"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except Exception:
        # 回退本地代理
        proxy_h = urllib.request.ProxyHandler({"http": PROXY, "https": PROXY})
        opener = urllib.request.build_opener(proxy_h)
        req2 = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (vision-eyes corpus)"})
        with opener.open(req2, timeout=timeout) as r:
            return r.read()


def list_category_images(category: str, limit: int = 10) -> list[str]:
    """列出某类别的图片文件 URL（跳过 svg/pdf/大文件）。"""
    params = {
        "action": "query",
        "generator": "categorymembers",
        "gcmtitle": f"Category:{category}",
        "gcmtype": "file",
        "gcmlimit": str(min(limit * 3, 100)),
        "prop": "imageinfo",
        "iiprop": "url|size",
        "iiurlwidth": "512",
        "format": "json",
    }
    url = API + "?" + urllib.parse.urlencode(params)
    data = json.loads(fetch(url).decode("utf-8", "replace"))
    pages = data.get("query", {}).get("pages", {})
    urls = []
    for page in pages.values():
        info = page.get("imageinfo", [{}])
        if not info:
            continue
        ii = info[0]
        name = page.get("title", "").lower()
        if any(x in name for x in (".svg", ".pdf", ".tif", ".tiff")):
            continue
        w = ii.get("width", 0) or 0
        h = ii.get("height", 0) or 0
        if w < 100 or h < 100:
            continue
        # 优先缩略图（512px），避免超大文件
        thumb = ii.get("thumburl")
        urls.append(thumb or ii.get("url"))
    return urls


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    outdir = sys.argv[1]
    per_cat = int(sys.argv[2]) if len(sys.argv) > 2 else 5

    os.makedirs(outdir, exist_ok=True)
    # 追加模式：读已有 manifest，避免覆盖已采记录
    manifest = []
    man_path = os.path.join(outdir, "manifest.jsonl")
    if os.path.exists(man_path):
        try:
            manifest = [json.loads(l) for l in open(man_path, encoding="utf-8") if l.strip()]
        except Exception:
            manifest = []
    # 已存在的文件名集合，避免重复下载
    existing = {os.path.basename(m["path"]) for m in manifest}
    for cat, (main, sub) in CATEGORY_MAP.items():
        subdir = os.path.join(outdir, main + (f"_{sub}" if sub else ""))
        os.makedirs(subdir, exist_ok=True)
        try:
            urls = list_category_images(cat, per_cat)
        except Exception as e:
            print(f"[{cat}] ERROR list: {e}", flush=True)
            continue
        got = 0
        for i, u in enumerate(urls):
            if got >= per_cat:
                break
            # 从 URL 推导扩展名
            ext = os.path.splitext(urllib.parse.urlparse(u).path)[1] or ".jpg"
            if ext.lower() not in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
                ext = ".jpg"
            fp = os.path.join(subdir, f"{cat}_{i}{ext}")
            if os.path.basename(fp) in existing:
                continue  # 已下载过
            try:
                blob = fetch(u, timeout=40)
                if len(blob) < 2000:
                    continue
                with open(fp, "wb") as f:
                    f.write(blob)
                manifest.append({"path": fp, "main": main, "sub": sub})
                got += 1
                print(f"[{cat}] saved {os.path.basename(fp)}", flush=True)
            except Exception as e:
                print(f"[{cat}] {i} ERROR dl: {e}", flush=True)
                continue
            time.sleep(1.5)  # 降速避 429

    with open(os.path.join(outdir, "manifest.jsonl"), "w", encoding="utf-8") as f:
        for m in manifest:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")
    print(f"\nDONE: {len(manifest)} images -> {outdir}/manifest.jsonl", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
