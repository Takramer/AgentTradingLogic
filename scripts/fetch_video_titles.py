"""fetch_video_titles.py — the intake tool for video/course batches.

Video intakes keep arriving (28 triaged on 2026-07-31 and 2026-08-14, then a
361-video batch the same day) and the fetch is fiddly enough to be worth a
script rather than a fresh scrape each time.

**What is and is not reachable.** YouTube renders client-side and exposes no
transcript endpoint, so **only the title and channel are obtainable.** Nothing
here reads a video. That is a hard constraint on every intake doc: cluster and
judge by title, channel and the channel's outside record, and say plainly that
you could not watch it. See `docs/CLAUDE-RESEARCH-RULES.md` — a video is not
evidence in the first place, so the missing transcript costs less than it looks.

Two mechanisms:

* **single videos** — the public oEmbed endpoint, which does resolve and returns
  title + author.
* **playlists** — scrape `ytInitialData` from the playlist page. YouTube moved
  playlist items from `playlistVideoRenderer` to `lockupViewModel` (this broke
  every older scraper), and pages past the first need a continuation token
  posted to the innertube browse endpoint. Both are handled. `yt-dlp` is not
  installed in this environment; if it ever is, prefer it.

Usage:

    py scripts/fetch_video_titles.py --out research_data/video_corpus.tsv \\
        https://youtu.be/XXXXXXXXXXX \\
        https://www.youtube.com/playlist?list=PLxxxxxxxx
    py scripts/fetch_video_titles.py --ids-file urls.txt --format text

Output is TSV: video_id, channel, title, source (playlist id or LOOSE),
source_name. Duplicates across playlists are dropped, first occurrence wins.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
TIMEOUT = 60


# ------------------------------------------------------------------- transport
def _get(url: str, data: bytes | None = None, headers: dict | None = None) -> str:
    h = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read().decode("utf-8", "replace")


def _brace_json(html: str, marker: str):
    """Extract the first balanced {...} following `marker`."""
    i = html.find(marker)
    if i < 0:
        return None
    i = html.find("{", i)
    depth = j = 0
    instr = esc = False
    j = i
    while j < len(html):
        c = html[j]
        if instr:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                instr = False
        elif c == '"':
            instr = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return json.loads(html[i:j + 1])
        j += 1
    return None


def _first(node, key):
    if isinstance(node, dict):
        if key in node:
            return node[key]
        for v in node.values():
            r = _first(v, key)
            if r is not None:
                return r
    elif isinstance(node, list):
        for v in node:
            r = _first(v, key)
            if r is not None:
                return r
    return None


# ----------------------------------------------------------------- URL parsing
VID_RE = re.compile(r"(?:youtu\.be/|[?&]v=|/embed/|/shorts/)([\w-]{11})")
PL_RE = re.compile(r"[?&]list=(PL[\w-]+|UU[\w-]+|OL[\w-]+)")


def classify(arg: str) -> tuple[str, str]:
    """-> ('playlist'|'video', id). A bare 11-char token is a video id."""
    m = PL_RE.search(arg)
    if m:
        return "playlist", m.group(1)
    if arg.startswith("PL") and "/" not in arg:
        return "playlist", arg
    m = VID_RE.search(arg)
    if m:
        return "video", m.group(1)
    if re.fullmatch(r"[\w-]{11}", arg):
        return "video", arg
    raise ValueError(f"cannot parse as a video or playlist: {arg!r}")


# ---------------------------------------------------------------- single video
def fetch_video(vid: str) -> tuple[str, str, str]:
    url = (f"https://www.youtube.com/oembed?url="
           f"https://www.youtube.com/watch?v={vid}&format=json")
    try:
        d = json.loads(_get(url))
    except Exception as e:                      # private, deleted, region-locked
        return vid, "", f"<UNRESOLVED: {e.__class__.__name__}>"
    return vid, d.get("author_name", ""), d.get("title", "")


# -------------------------------------------------------------------- playlist
def _parse_lockup(lk: dict):
    vid = lk.get("contentId", "")
    if not vid or len(vid) != 11:
        return None
    meta = _first(lk, "lockupMetadataViewModel") or {}
    title = ""
    t = meta.get("title")
    if isinstance(t, dict):
        title = t.get("content", "")
    if not title:
        t2 = _first(lk, "title")
        if isinstance(t2, dict):
            title = t2.get("content", "") or t2.get("simpleText", "")
    channel = ""
    for row in (_first(meta, "metadataRows") or []):
        for part in row.get("metadataParts", []):
            txt = (part.get("text") or {}).get("content", "")
            if txt and "ago" not in txt and not re.match(
                    r"^[\d.,]+\s*[KMB]?\s*views?$", txt):
                channel = txt
                break
        if channel:
            break
    return vid, channel, title


def _collect(data, items: list, seen: set, toks: list) -> int:
    lockups, conts = [], []

    def walk(n):
        if isinstance(n, dict):
            lk = n.get("lockupViewModel")
            if isinstance(lk, dict):
                lockups.append(lk)
            cc = n.get("continuationCommand")
            if isinstance(cc, dict) and cc.get("token"):
                conts.append(cc["token"])
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)

    walk(data)
    added = 0
    for lk in lockups:
        p = _parse_lockup(lk)
        if p and p[0] not in seen:
            seen.add(p[0])
            items.append(p)
            added += 1
    for t in conts:
        if t not in toks:
            toks.append(t)
    return added


def fetch_playlist(pid: str, max_pages: int = 60):
    html = _get(f"https://www.youtube.com/playlist?list={pid}&hl=en&gl=US")
    data = _brace_json(html, "var ytInitialData =")
    if data is None:
        return pid, [], "ytInitialData not found (layout changed, or blocked)"

    name = (_first(data, "playlistMetadataRenderer") or {}).get("title", "")
    if not name:
        m = re.search(r"<title>(.*?)</title>", html, re.S)
        name = m.group(1).replace(" - YouTube", "").strip() if m else pid

    key = (re.findall(r'"INNERTUBE_API_KEY":"([^"]+)"', html) or [None])[0]
    ver = (re.findall(r'"clientVersion":"([\d.]+)"', html) or ["2.20240101"])[0]

    items, seen, toks = [], set(), []
    _collect(data, items, seen, toks)

    pages = 0
    while toks and key and pages < max_pages:
        pages += 1
        body = json.dumps({
            "context": {"client": {"clientName": "WEB", "clientVersion": ver,
                                   "hl": "en", "gl": "US"}},
            "continuation": toks.pop(0)}).encode()
        try:
            raw = _get(f"https://www.youtube.com/youtubei/v1/browse"
                       f"?key={key}&prettyPrint=false", data=body,
                       headers={"Content-Type": "application/json",
                                "X-Youtube-Client-Name": "1",
                                "X-Youtube-Client-Version": ver,
                                "Origin": "https://www.youtube.com"})
        except urllib.error.HTTPError as e:
            sys.stderr.write(f"  {pid}: continuation HTTP {e.code}, stopping\n")
            break
        if _collect(json.loads(raw), items, seen, toks) == 0:
            break
        time.sleep(0.3)
    return name, items, None


# ------------------------------------------------------------------------ main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("urls", nargs="*", help="video or playlist URLs / ids")
    ap.add_argument("--ids-file", help="file with one URL or id per line")
    ap.add_argument("--out", help="write TSV here (default: stdout)")
    ap.add_argument("--format", choices=("tsv", "text"), default="tsv")
    a = ap.parse_args(argv)

    args = list(a.urls)
    if a.ids_file:
        with open(a.ids_file, encoding="utf-8") as fh:
            args += [ln.strip() for ln in fh if ln.strip()
                     and not ln.startswith("#")]
    if not args:
        ap.error("give at least one URL/id, or --ids-file")

    rows, seen = [], set()
    for arg in args:
        try:
            kind, ident = classify(arg)
        except ValueError as e:
            sys.stderr.write(f"skip: {e}\n")
            continue
        if kind == "video":
            vid, ch, title = fetch_video(ident)
            if vid not in seen:
                seen.add(vid)
                rows.append((vid, ch, title, "LOOSE", "loose"))
            sys.stderr.write(f"video    {vid}  {title[:60]}\n")
        else:
            name, items, err = fetch_playlist(ident)
            if err:
                sys.stderr.write(f"playlist {ident}: ERROR {err}\n")
                continue
            new = 0
            for vid, ch, title in items:
                if vid in seen:
                    continue
                seen.add(vid)
                rows.append((vid, ch, title, ident, name))
                new += 1
            sys.stderr.write(
                f"playlist {ident}  {len(items):>4} items ({new} new)  {name}\n")

    out = open(a.out, "w", encoding="utf-8", newline="\n") if a.out else sys.stdout
    try:
        if a.format == "tsv":
            out.write("video_id\tchannel\ttitle\tsource_playlist\tsource_name\n")
            for r in rows:
                out.write("\t".join(x.replace("\t", " ") for x in r) + "\n")
        else:
            for n, (vid, ch, title, p, name) in enumerate(rows, 1):
                out.write(f"{n:>4}. [{vid}] {ch} | {title}\n")
    finally:
        if a.out:
            out.close()
    sys.stderr.write(f"\n{len(rows)} unique videos\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
