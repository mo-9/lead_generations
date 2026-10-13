"""Shared helpers: logging, text & phone clean-up, map boxes, request pacing."""
from __future__ import annotations

import logging
import math
import random
import re
import sys
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

log = logging.getLogger("dubai_leads")


class StopScraping(Exception):
    """Something needs the user's attention (CAPTCHA, repeated errors...). Progress is saved."""


# ───────────────────────────── logging ─────────────────────────────

def setup_logging(log_file: Path) -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # never crash on Arabic names in an old console
        except Exception:
            pass
    log.setLevel(logging.INFO)
    log.propagate = False
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S"))
    logfile = logging.FileHandler(log_file, encoding="utf-8")
    logfile.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%Y-%m-%d %H:%M:%S"))
    log.handlers[:] = [console, logfile]


def fmt_duration(seconds: float) -> str:
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


# ───────────────────────────── text ─────────────────────────────

def clean(text) -> str:
    if text is None:
        return ""
    return re.sub(r"\s+", " ", str(text)).strip()


def parse_rating(text) -> float | None:
    m = re.search(r"(\d(?:[.,]\d)?)", text or "")
    if not m:
        return None
    value = float(m.group(1).replace(",", "."))
    return value if 0 < value <= 5 else None


def parse_count(text) -> int | None:
    """'(1,234)' -> 1234 · '1.2K reviews' -> 1200 · '56 reviews' -> 56"""
    m = re.search(r"(\d[\d,.   ]*)\s*([KkMm])?\b", text or "")
    if not m:
        return None
    number, suffix = m.group(1).strip(), m.group(2)
    if suffix:
        try:
            return int(float(number.replace(",", ".").replace(" ", "")) * (1000 if suffix in "Kk" else 1_000_000))
        except ValueError:
            return None
    digits = re.sub(r"\D", "", number)
    return int(digits) if digits else None


def parse_rating_label(label: str) -> tuple[float | None, int | None]:
    """'4.8 stars 1,234 Reviews' -> (4.8, 1234)"""
    label = label or ""
    rating = parse_rating(label.split("star")[0]) if "star" in label.lower() else None
    reviews = None
    m = re.search(r"([\d,.]+\s*[KkMm]?)\s*review", label, re.I)
    if m:
        reviews = parse_count(m.group(1))
    return rating, reviews


def contains_phrase(text: str, phrase: str) -> bool:
    """Whole-word, case-insensitive match ('spa' does not match 'space', 'Real-Estate' matches 'real estate')."""
    def words(s):
        return " ".join(re.findall(r"[^\W_]+", (s or "").lower()))
    phrase = words(phrase)
    return bool(phrase) and re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", words(text)) is not None


def excel_safe(value):
    """Stop Excel from treating text that starts with = + - @ as a formula."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@"):
        return " " + value
    return value


# ───────────────────────────── phones ─────────────────────────────

def format_phone(raw: str) -> str:
    """UAE numbers in local style ('04 123 4567', '050 123 4567', '800 1234').
    The spaces keep Excel from turning them into numbers and dropping the leading 0."""
    raw = clean(raw)
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return ""
    international = raw.startswith("+") or raw.startswith("00")
    if raw.startswith("00"):
        digits = digits[2:]
    if international and not digits.startswith("971"):
        return "00" + digits[:4] + " " + digits[4:]          # foreign number
    if digits.startswith("971"):
        rest = digits[3:]
    elif digits.startswith("0"):
        rest = digits[1:]
    else:
        rest = digits
    if rest.startswith(("800", "600")) and 6 <= len(rest) <= 12:
        return f"{rest[:3]} {rest[3:]}"                       # toll-free / call-centre
    if rest.startswith("5") and len(rest) == 9:
        return f"0{rest[:2]} {rest[2:5]} {rest[5:]}"          # mobile
    if len(rest) == 8:
        return f"0{rest[0]} {rest[1:4]} {rest[4:]}"           # landline
    return f"0{rest[:2]} {rest[2:]}" if len(rest) > 2 else raw


def phone_digits(phone: str) -> str:
    """Comparable form of a phone number ('+971 4 123 4567' and '04 1234567' -> '041234567')."""
    return re.sub(r"\D", "", format_phone(phone or ""))


# ───────────────────────────── names ─────────────────────────────

_LEGAL_WORDS = (r"l\.?l\.?c|fz[- ]?llc|fz[- ]?co|fzco|fze|dmcc|est|establishment|co|company|ltd|"
                r"limited|inc|plc|llp|branch|br|sole proprietorship")


def area_name_variants(area_names) -> list[str]:
    """'Dubai Marina' -> ['dubai marina', 'marina'] etc. Used to strip branch names."""
    variants = {"dubai", "uae", "branch"}
    for name in area_names:
        for part in re.split(r"/", name.lower()):
            part = re.sub(r"[^\w\s]", " ", part)
            part = re.sub(r"\s+", " ", part).strip()
            if not part:
                continue
            variants.add(part)
            for prefix in ("al ", "dubai "):
                if part.startswith(prefix):
                    variants.add(part[len(prefix):])
    return sorted(variants, key=len, reverse=True)


def normalize_name(name: str, area_variants=()) -> str:
    """'Aster Clinic - Al Barsha (Branch) LLC' -> 'aster clinic'. Used for chains + de-duplication."""
    s = unicodedata.normalize("NFKC", name or "").lower()
    s = re.split(r"\s+[-–—|@]\s+|\s*\(|\s*\|", s)[0]
    s = re.sub(rf"(?<!\w)(?:{_LEGAL_WORDS})(?!\w)\.?", " ", s)
    s = re.sub(r"[^\w\s]", " ", s)          # \w keeps Arabic letters
    s = re.sub(r"\s+", " ", s).strip()
    for _ in range(3):                       # e.g. '... business bay dubai uae', '... al barsha 1'
        for area in area_variants:
            m = re.search(rf"\s{re.escape(area)}(?:\s\d{{1,2}})?$", s)
            if m:
                rest = s[: m.start()].strip()
                if len(rest.split()) >= 2:
                    s = rest
                    break
    return s or clean(name).lower()


# ───────────────────────────── web ─────────────────────────────

def host_of(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
    except ValueError:
        return ""
    host = host.split("@")[-1].split(":")[0]
    return host[4:] if host.startswith("www.") else host


def domain_in(host: str, domains) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


# ───────────────────────────── map boxes ─────────────────────────────

@dataclass(frozen=True)
class Box:
    south: float
    north: float
    west: float
    east: float
    depth: int = 0

    @property
    def center(self) -> tuple[float, float]:
        return (self.south + self.north) / 2, (self.west + self.east) / 2

    def size_m(self) -> tuple[float, float]:
        """(height, width) in metres."""
        lat = math.radians(self.center[0])
        return (self.north - self.south) * 111_320, (self.east - self.west) * 111_320 * math.cos(lat)

    def split(self) -> list["Box"]:
        mlat, mlng = self.center
        d = self.depth + 1
        return [Box(mlat, self.north, self.west, mlng, d), Box(mlat, self.north, mlng, self.east, d),
                Box(self.south, mlat, self.west, mlng, d), Box(self.south, mlat, mlng, self.east, d)]

    def contains(self, lat: float, lng: float, margin: float = 0.0) -> bool:
        dlat = (self.north - self.south) * margin
        dlng = (self.east - self.west) * margin
        return (self.south - dlat <= lat <= self.north + dlat) and (self.west - dlng <= lng <= self.east + dlng)

    def key(self) -> str:
        return f"{self.south:.5f},{self.north:.5f},{self.west:.5f},{self.east:.5f}"

    def zoom(self, map_width_px: int, map_height_px: int) -> float:
        """Google Maps zoom level at which this box fills the visible map."""
        height, width = self.size_m()
        metres_per_px = max(width / map_width_px, height / map_height_px, 0.5)
        z = math.log2(156_543.03392 * math.cos(math.radians(self.center[0])) / metres_per_px)
        return max(11.0, min(19.0, math.floor(z * 4) / 4))


def box_from_setting(value) -> Box:
    """(south, north, west, east) or (center_lat, center_lng, radius_km) -> Box"""
    nums = [float(v) for v in value]
    if len(nums) == 4:
        s, n, w, e = nums
        return Box(min(s, n), max(s, n), min(w, e), max(w, e))
    if len(nums) == 3:
        lat, lng, r = nums
        dlat = r / 111.32
        dlng = r / (111.32 * math.cos(math.radians(lat)))
        return Box(lat - dlat, lat + dlat, lng - dlng, lng + dlng)
    raise ValueError("an area must be (south, north, west, east) or (lat, lng, radius_km)")


# ───────────────────────────── pacing ─────────────────────────────

class Pacer:
    """Randomised, adaptive delays and exponential backoff for every Google page load."""

    MAX_FACTOR = 4.0

    def __init__(self, s):
        self.s = s
        self.factor = 1.0            # >1 = slowed down because Google looked unhappy
        self.problems_in_row = 0
        self.blocks_in_row = 0
        self.loads = 0
        self._next_break = random.randint(*s.LONG_BREAK_EVERY)

    @staticmethod
    def sleep(seconds: float, why: str = "") -> None:
        if seconds <= 0:
            return
        if why:
            log.info(f"   ... {why} - waiting {fmt_duration(seconds)}")
        time.sleep(seconds)

    def before_page(self) -> None:
        if self.loads and self.loads >= self._next_break:
            self.sleep(random.uniform(*self.s.LONG_BREAK_SECONDS), "long break")
            self._next_break = self.loads + random.randint(*self.s.LONG_BREAK_EVERY)
        if self.loads:
            self.sleep(random.uniform(*self.s.DELAY_BETWEEN_PAGES) * self.factor)
        self.loads += 1

    def scroll_pause(self, extra: float = 1.0) -> None:
        self.sleep(random.uniform(*self.s.SCROLL_PAUSE) * self.factor * extra)

    def page_ok(self, seconds: float) -> None:
        self.problems_in_row = 0
        self.blocks_in_row = 0
        if seconds > self.s.SLOW_PAGE_SECONDS:
            self.factor = min(self.MAX_FACTOR, self.factor * 1.5)
            log.info(f"   ... Google answered slowly ({seconds:.0f}s) - slowing down (delays x{self.factor:.1f})")
        else:
            self.factor = max(1.0, self.factor * 0.93)

    def page_problem(self, kind: str, detail: str, advice: str = "") -> None:
        """kind = 'block' (CAPTCHA / unusual traffic / limited view) or 'error' (timeout, failed load)."""
        self.problems_in_row += 1
        if kind == "block":
            self.blocks_in_row += 1
            if self.blocks_in_row >= self.s.STOP_AFTER_BLOCKS:
                raise StopScraping(f"Google pushed back ({detail}) {self.blocks_in_row} times in a row. {advice}")
        if self.problems_in_row >= self.s.STOP_AFTER_ERRORS:
            raise StopScraping(f"{self.problems_in_row} Google page loads failed in a row (last: {detail}). "
                               "Check the internet connection, then run again.")
        self.factor = min(self.MAX_FACTOR, self.factor * 2)
        wait = self.s.BACKOFF_START_SECONDS * 2 ** (self.problems_in_row - 1)
        if kind == "block":
            wait = max(wait, 2 * self.s.BACKOFF_START_SECONDS * 2 ** (self.blocks_in_row - 1))
        wait = min(self.s.BACKOFF_MAX_SECONDS, wait) * random.uniform(0.85, 1.25)
        label = "Google block page" if kind == "block" else "Page problem"
        self.sleep(wait, f"{label} ({detail}), backing off")
