"""Google Maps: open a search, scroll its result list to the end, read place details.
No Google login is ever used: the browser runs on its own empty profile."""
from __future__ import annotations

import os
import random
import re
import time
from urllib.parse import parse_qs, quote_plus, unquote, urlencode, urlparse, urlunparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from common import (Box, Pacer, StopScraping, clean, format_phone, log, normalize_name, parse_count,
                    parse_rating, parse_rating_label, phone_digits)

MAPS_BASE = os.environ.get("DUBAI_LEADS_MAPS_BASE", "https://www.google.com/maps")
WINDOW = {"width": 1440, "height": 900}
MAP_AREA_PX = (1000, 840)        # part of the window showing the map (the results panel covers the rest)

# ───────────────────────────── page scripts ─────────────────────────────

STATE_JS = r"""
() => {
  const has = s => !!document.querySelector(s);
  const url = location.href;
  const feed = has('div[role="feed"]');
  let title = document.querySelector('h1.DUwDvf');
  if (!title) title = [...document.querySelectorAll('div[role="main"] h1')]
      .find(h => { const t = (h.textContent || '').trim(); return t && !/^results$/i.test(t); });
  const place = !!(title && title.textContent.trim());
  const all = document.body ? (document.body.innerText || '') : '';
  const text = (!feed && !place) ? all.slice(0, 6000) : '';
  return {
    url, feed, place,
    block: url.includes('/sorry/') || has('iframe[src*="recaptcha"]') || has('#captcha-form')
           || /unusual traffic|not a robot/i.test(text),
    consent: url.includes('consent.google.') || has('form[action*="consent.google"]'),
    limited: /limited view of Google Maps/i.test(all),
    signed_in: has('a[aria-label^="Google Account"]'),
    empty: /can.t find|No results found|didn.t match any/i.test(text),
  };
}
"""

FEED_STATUS_JS = r"""
() => {
  const feed = document.querySelector('div[role="feed"]');
  const block = location.href.includes('/sorry/');
  if (!feed) return {count: 0, end: false, feed: false, block};
  const hrefs = new Set();
  for (const a of feed.querySelectorAll('a[href*="/maps/place/"]')) hrefs.add(a.href);
  const end = /reached the end of the list/i.test(feed.textContent || '') || !!feed.querySelector('span.HlvSq');
  return {count: hrefs.size, end, feed: true, block};
}
"""

CARDS_JS = r"""
() => {
  const feed = document.querySelector('div[role="feed"]');
  if (!feed) return [];
  const out = [], seen = new Set();
  for (const a of feed.querySelectorAll('a[href*="/maps/place/"]')) {
    const href = a.href;
    if (!href || seen.has(href)) continue;
    seen.add(href);
    let card = a.closest('.Nv2PK') || a.parentElement;
    for (let i = 0; i < 4 && card && (card.innerText || '').trim().length < 3; i++) card = card.parentElement;
    if (!card) continue;
    const q = s => card.querySelector(s);
    const title = q('.fontHeadlineSmall') || q('.qBF1Pd');
    const stars = q('[role="img"][aria-label*="star" i]');
    let website = '';
    for (const w of card.querySelectorAll('a[href]')) {
      if (w === a) continue;
      const h = w.href || '';
      if (!/^https?:/i.test(h)) continue;
      if (/\/\/(www\.)?google\.[^/]+\/(maps|search|aclk|url)|\/maps\/|googleadservices/i.test(h)) continue;
      const label = (w.getAttribute('aria-label') || '') + ' ' + (w.getAttribute('data-value') || '');
      if (/website/i.test(label)) { website = h; break; }
      if (!website) website = h;
    }
    const phone = q('.UsdlK');
    out.push({
      href,
      name: (a.getAttribute('aria-label') || (title && title.textContent) || '').trim(),
      ratingLabel: stars ? (stars.getAttribute('aria-label') || '') : '',
      ratingText: (q('.MW4etd') || {}).textContent || '',
      reviewsText: (q('.UY7F9') || {}).textContent || '',
      phone: phone ? phone.textContent.trim() : '',
      website,
      lines: (card.innerText || '').split('\n').map(s => s.trim()).filter(Boolean).slice(0, 10),
    });
  }
  return out;
}
"""

DETAILS_JS = r"""
() => {
  const q = (s, r) => (r || document).querySelector(s);
  let title = q('h1.DUwDvf');
  if (!title) title = [...document.querySelectorAll('div[role="main"] h1')]
      .find(h => { const t = (h.textContent || '').trim(); return t && !/^results$/i.test(t); });
  const root = (title && title.closest('div[role="main"]')) || document;
  const attr = (el, n) => el ? (el.getAttribute(n) || '') : '';
  const cat = q('button[jsaction*="category"]', root) || q('button.DkEaL', root);
  const addr = q('[data-item-id="address"]', root);
  const web = q('a[data-item-id="authority"]', root);
  const phone = q('[data-item-id^="phone:tel:"]', root);
  let ratingText = '', reviewsText = '';
  const f7 = q('div.F7nice', root);
  if (f7) {
    const r = f7.querySelector('span[aria-hidden="true"]');
    ratingText = r ? r.textContent : '';
    const m = (f7.textContent || '').match(/\(([\d.,\s]+[KkMm]?)\)/);
    if (m) reviewsText = m[1];
  }
  if (!ratingText) { const img = q('[role="img"][aria-label*="star" i]', root); if (img) ratingText = attr(img, 'aria-label'); }
  if (!reviewsText) {
    for (const el of root.querySelectorAll('[aria-label]')) {
      const l = el.getAttribute('aria-label') || '';
      if (/^\s*[\d.,]+\s*[KkMm]?\s+reviews?\s*$/i.test(l)) { reviewsText = l; break; }
    }
  }
  const closed = ((root.innerText || '').match(/Permanently closed|Temporarily closed/i) || [''])[0];
  return {
    url: location.href,
    name: title ? title.textContent.trim() : '',
    category: cat ? cat.textContent.trim() : '',
    address: attr(addr, 'aria-label').replace(/^\s*Address:\s*/i, '').trim() || (addr ? addr.innerText.trim() : ''),
    website: web ? web.href : '',
    phoneTel: attr(phone, 'data-item-id').replace(/^phone:tel:/, ''),
    phoneLabel: attr(phone, 'aria-label').replace(/^\s*Phone:\s*/i, '').trim() || (phone ? phone.innerText.trim() : ''),
    ratingText, reviewsText, closed,
  };
}
"""

# ───────────────────────────── parsing ─────────────────────────────

PLACE_ID_RE = re.compile(r"!19s(ChIJ[\w-]+)")
FTID_RE = re.compile(r"!1s(0x[0-9a-f]+:0x[0-9a-f]+)", re.I)
COORDS_RE = re.compile(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)")
VIEWPORT_RE = re.compile(r"/@(-?\d+\.\d+),(-?\d+\.\d+)")
PHONE_IN_TEXT_RE = re.compile(r"(?:\+971|00971|\b0)[\s-]?\d{1,2}[\s-]?\d{3}[\s-]?\d{4}\b|\b[86]00[\s-]?\d{3,7}\b")
RATING_LINE_RE = re.compile(r"^(\d[.,]\d|no reviews)", re.I)
HOURS_RE = re.compile(r"^(open|closed|opens|closes|temporarily|permanently)\b", re.I)
ACTION_LINES = {"website", "directions", "sponsored", "order online", "reserve a table", "book online",
                "get online care", "call", "share", "save"}


def ids_from_url(url: str):
    u = unquote(url or "")
    pid = PLACE_ID_RE.search(u)
    ftid = FTID_RE.search(u)
    co = COORDS_RE.search(u) or VIEWPORT_RE.search(u)
    lat, lng = (float(co.group(1)), float(co.group(2))) if co else (None, None)
    return (pid.group(1) if pid else ""), (ftid.group(1).lower() if ftid else ""), lat, lng


def place_key(ftid: str, place_id: str, url: str, name: str, phone: str) -> str:
    if ftid:
        return ftid
    if place_id:
        return place_id
    if url:
        return "url:" + url.split("?")[0]
    return "np:" + normalize_name(name) + "|" + phone_digits(phone)


def clean_website(url: str) -> str:
    url = clean(url)
    if not url:
        return ""
    parsed = urlparse(url)
    if "google." in parsed.netloc and parsed.path.startswith("/url"):
        qs = parse_qs(parsed.query)
        url = (qs.get("q") or qs.get("url") or [""])[0]
        if not url:
            return ""
    if not re.match(r"^https?://", url, re.I):
        url = "http://" + url
    parsed = urlparse(url)
    query = [(k, v) for k, v in parse_qs(parsed.query, keep_blank_values=True).items()
             if not k.lower().startswith("utm_")]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True), fragment=""))


def maps_link(place_id: str, name: str, url: str) -> str:
    if place_id.startswith("ChIJ"):
        return ("https://www.google.com/maps/search/?api=1&query=" + quote_plus(name or "place")
                + "&query_place_id=" + place_id)
    return (url or "").split("?")[0]


def _category_and_address(lines: list[str], name: str) -> tuple[str, str]:
    """Card lines look like: name / '4.8(1,234)' / 'Dental clinic · · Bay Square 7' / 'Open · 04 123 4567'."""
    for line in lines:
        if line == name or RATING_LINE_RE.match(line) or line.lower() in ACTION_LINES:
            continue
        if line.startswith(("\"", "“")):          # review snippet
            continue
        parts = [p.strip() for p in line.split("·")]
        parts = [p for p in parts if re.search(r"\w", p)]
        if not parts or HOURS_RE.match(parts[0]) or PHONE_IN_TEXT_RE.fullmatch(parts[0]):
            return "", ""
        category = parts[0]
        address = parts[-1] if len(parts) > 1 else ""
        if re.fullmatch(r"[$€£]+|AED.*|\d+\s*[-–]\s*\d+.*", address):
            address = ""
        return category, address
    return "", ""


def parse_card(raw: dict) -> dict | None:
    href, name = raw.get("href") or "", clean(raw.get("name"))
    if not href or not name:
        return None
    place_id, ftid, lat, lng = ids_from_url(href)
    rating, reviews = parse_rating_label(raw.get("ratingLabel") or "")
    if rating is None:
        rating = parse_rating(raw.get("ratingText"))
    if reviews is None:
        reviews = parse_count(raw.get("reviewsText"))
    lines = [clean(x) for x in raw.get("lines") or []]
    if reviews is None and any(x.lower().startswith("no reviews") for x in lines):
        reviews = 0
    category, address = _category_and_address(lines, name)
    phone = raw.get("phone") or ""
    if not phone:
        m = PHONE_IN_TEXT_RE.search(" · ".join(lines[1:]))
        phone = m.group(0) if m else ""
    phone = format_phone(phone)
    url = href.split("?")[0]
    return {
        "place_key": place_key(ftid, place_id, url, name, phone),
        "place_id": place_id, "ftid": ftid, "maps_url": url, "name": name,
        "latitude": lat, "longitude": lng,
        "card_category": category, "card_address": address, "card_phone": phone,
        "card_website": clean_website(raw.get("website")),
        "card_rating": rating, "card_reviews": reviews,
    }


def parse_details(raw: dict) -> dict:
    place_id, ftid, lat, lng = ids_from_url(raw.get("url") or "")
    return {
        "name": clean(raw.get("name")),
        "google_category": clean(raw.get("category")),
        "address": clean(raw.get("address")),
        "phone": format_phone(raw.get("phoneTel") or raw.get("phoneLabel")),
        "website": clean_website(raw.get("website")),
        "rating": parse_rating(raw.get("ratingText")),
        "review_count": parse_count(raw.get("reviewsText")),
        "closed": clean(raw.get("closed")),
        "place_id": place_id, "ftid": ftid, "latitude": lat, "longitude": lng,
        "url": raw.get("url") or "",
    }


# ───────────────────────────── browser ─────────────────────────────

def launch_browser(playwright, out_dir, show_browser: bool, browser: str = "chrome"):
    """A dedicated, never-signed-in browser profile (kept between runs, like a normal visitor).
    browser="chrome": your installed Google Chrome (own separate profile) - sends the same headers as
    everyday Chrome. Falls back to the bundled Chromium when Chrome isn't installed."""
    options = dict(headless=not show_browser, locale="en-US", timezone_id="Asia/Dubai", viewport=WINDOW,
                   args=["--lang=en-US"], extra_http_headers={"Accept-Language": "en-US,en;q=0.9"})
    if str(browser).lower() == "chrome":
        try:
            context = playwright.chromium.launch_persistent_context(
                str(out_dir / "browser_profile_chrome"), channel="chrome", **options)
            log.info("Browser: your installed Google Chrome (separate profile, not signed in)")
            return context
        except PlaywrightError as e:
            log.info(f"Google Chrome could not be started ({clean(str(e)).split(' Call log')[0][:90]}) "
                     "- using the bundled Chromium instead")
    try:
        context = playwright.chromium.launch_persistent_context(str(out_dir / "browser_profile"), **options)
        log.info("Browser: bundled Chromium (separate profile, not signed in)")
        return context
    except PlaywrightError as e:
        if "Executable doesn't exist" in str(e) or "playwright install" in str(e):
            raise SystemExit("The browser isn't installed yet. Run:  python -m playwright install chromium")
        raise


LIMITED_ADVICE = ("Google is showing its 'limited view' of Maps (a reduced page for signed-out visitors that "
                  "hides review counts). It is usually triggered by the network - e.g. a busy office network - "
                  "or by the browser. Try running from another network (e.g. home). If you set "
                  "BROWSER = \"chromium\" in settings.py, also try BROWSER = \"chrome\" (and the other way round).")
CAPTCHA_ADVICE = "Wait a few hours before running again."


class MapsClient:
    def __init__(self, page, pacer: Pacer, needs_review_counts: bool = True):
        self.page = page
        self.pacer = pacer
        self.needs_review_counts = needs_review_counts
        self._limited_noted = False

    def _note_limited(self) -> None:
        if not self._limited_noted:
            self._limited_noted = True
            log.info("   ... Google shows its 'limited view' (signed-out Maps) - the fields needed are still "
                     "readable, continuing")

    # ── public ──
    def search(self, keyword: str, box: Box) -> list[dict] | None:
        """All result cards for `keyword` with the map showing `box`. None = the page failed."""
        lat, lng = box.center
        zoom = box.zoom(*MAP_AREA_PX)
        url = f"{MAPS_BASE}/search/{quote_plus(keyword)}/@{lat:.6f},{lng:.6f},{zoom:g}z?hl=en"
        for _ in range(3):
            state = self._open(url, want="search")
            if state is None:
                return None
            if state.get("feed"):
                if not self._scroll_to_end():
                    return None
                raw = self.page.evaluate(CARDS_JS)
                cards = [c for c in (parse_card(r) for r in raw) if c]
                if state.get("limited"):
                    # rated places without a count = counts hidden ('No reviews' cards don't count either way)
                    rated = [c for c in cards if c["card_rating"] is not None]
                    hidden = bool(rated) and all(c["card_reviews"] is None for c in rated)
                    if hidden and self.needs_review_counts:
                        self.pacer.page_problem("block", "limited view, review counts hidden", LIMITED_ADVICE)
                        continue                       # same search again after the backoff
                    self._note_limited()
                self.pacer.page_ok(state["took"])
                return cards
            self.pacer.page_ok(state["took"])
            if state.get("place"):                     # Google jumped straight to a single place
                d = self._read_details()
                url = d["url"].split("?")[0]
                card = {
                    "place_key": place_key(d["ftid"], d["place_id"], url, d["name"], d["phone"]),
                    "place_id": d["place_id"], "ftid": d["ftid"], "maps_url": url, "name": d["name"],
                    "latitude": d["latitude"], "longitude": d["longitude"],
                    "card_category": d["google_category"], "card_address": d["address"],
                    "card_phone": d["phone"], "card_website": d["website"],
                    "card_rating": d["rating"], "card_reviews": d["review_count"], "_details": d,
                }
                return [card] if card["name"] else []
            return []                                  # "Google Maps can't find ..."
        return None

    def details(self, maps_url: str) -> dict | None:
        url = maps_url if "hl=" in maps_url else maps_url + ("&" if "?" in maps_url else "?") + "hl=en"
        state = self._open(url, want="place")
        if not state or not state.get("place"):
            return None
        self.pacer.page_ok(state["took"])
        return self._read_details()

    # ── internals ──
    def _open(self, url: str, want: str) -> dict | None:
        for _ in range(3):
            self.pacer.before_page()
            started = time.time()
            try:
                self.page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                state = self._wait_for(want)
            except PlaywrightTimeout:
                state = {"timeout": True}
            except PlaywrightError as e:
                state = {"error": clean(str(e)).split(" Call log")[0][:100]}
            took = time.time() - started
            if state.get("signed_in"):
                raise StopScraping("A Google account is signed in inside the scraper's browser. Delete the "
                                   "browser_profile folders in output (or set BROWSER = \"chromium\" in settings.py) "
                                   "and run again.")
            if state.get("consent"):
                self._answer_consent()
                continue
            if state.get("block"):
                self.pacer.page_problem("block", "CAPTCHA / unusual traffic", CAPTCHA_ADVICE)
                continue
            if state.get("limited"):
                self._dismiss_popup()
                if state.get("timeout"):
                    self.pacer.page_problem("block", "limited view, no results shown", LIMITED_ADVICE)
                    continue
            if state.get("timeout") or state.get("error"):
                self.pacer.page_problem("error", state.get("error") or "page did not load")
                continue
            state["took"] = took
            return state
        return None

    def _wait_for(self, want: str, timeout: float = 25.0) -> dict:
        deadline = time.time() + timeout
        state: dict = {}
        while time.time() < deadline:
            try:
                state = self.page.evaluate(STATE_JS) or {}
            except PlaywrightError:
                state = {}                                   # page still navigating
            if state:
                if state["block"] or state["consent"] or state["signed_in"]:
                    return state
                if want == "place" and state["place"]:
                    return state
                if want == "search" and (state["feed"] or state["place"] or state["empty"]):
                    return state
            time.sleep(0.5)
        return {"block": False, "limited": False, **state, "timeout": True}

    def _dismiss_popup(self) -> None:
        try:
            self.page.keyboard.press("Escape")
        except PlaywrightError:
            pass

    def _answer_consent(self) -> None:
        for label in ("Reject all", "Accept all", "I agree"):
            try:
                button = self.page.get_by_role("button", name=label).first
                if button.count():
                    button.click(timeout=5_000)
                    self.page.wait_for_load_state("domcontentloaded", timeout=15_000)
                    time.sleep(2)
                    return
            except PlaywrightError:
                continue

    def _scroll_to_end(self) -> bool:
        """Scroll the results list until Google says it's the end (or it stops growing)."""
        last, stall, started = -1, 0, time.time()
        while time.time() - started < 360:
            info = self.page.evaluate(FEED_STATUS_JS)
            if info.get("block"):
                self.pacer.page_problem("block", "CAPTCHA while scrolling", CAPTCHA_ADVICE)
                return False
            if not info.get("feed"):
                return True
            if info["end"] or info["count"] >= 200:
                return True
            if info["count"] == last:
                stall += 1
                if stall >= 6:
                    return True
            else:
                stall, last = 0, info["count"]
            self._wheel(stall)
            self.pacer.scroll_pause(1 + 0.5 * min(stall, 4))
        return True

    def _wheel(self, stall: int) -> None:
        page = self.page
        try:
            box = page.locator('div[role="feed"]').first.bounding_box(timeout=3_000)
        except PlaywrightError:
            box = None
        if box:
            x = box["x"] + box["width"] * random.uniform(0.3, 0.7)
            y = box["y"] + box["height"] * random.uniform(0.5, 0.85)
            page.mouse.move(x, y, steps=random.randint(2, 6))
            page.mouse.wheel(0, random.randint(1600, 3200))
        if stall >= 2 or not box:
            page.evaluate("() => { const f = document.querySelector('div[role=\"feed\"]');"
                          " if (f) f.scrollTop = f.scrollHeight; }")

    def _read_details(self) -> dict:
        try:
            self.page.wait_for_selector('[data-item-id="address"], [data-item-id^="phone:tel:"], '
                                        'a[data-item-id="authority"], button[jsaction*="category"]',
                                        timeout=6_000)
        except PlaywrightError:
            pass
        time.sleep(random.uniform(0.6, 1.4))
        return parse_details(self.page.evaluate(DETAILS_JS))
