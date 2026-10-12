"""Website enrichment: visit only the homepage and the contact / about / team pages
(robots.txt respected) and pull out the business email, LinkedIn company page,
Instagram, Facebook and - when the site names one publicly - the owner / founder /
CEO / managing director / lead doctor / partner."""
from __future__ import annotations

import json
import random
import re
import threading
import time
from collections import Counter
from urllib import robotparser
from urllib.parse import unquote, urldefrag, urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Comment, Doctype, NavigableString

from common import clean, domain_in, host_of

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
           "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
TIMEOUT = 20
MAX_BYTES = 3_000_000
PAGE_DELAY = (1.5, 3.5)          # pause between pages of the same website

# ───────────────────────────── fetching ─────────────────────────────

_local = threading.local()


def _session() -> requests.Session:
    if not hasattr(_local, "session"):
        _local.session = requests.Session()
        _local.session.headers.update(HEADERS)
    return _local.session


class Robots:
    """robots.txt cache shared by all worker threads."""

    def __init__(self):
        self._cache: dict = {}
        self._lock = threading.Lock()

    def check(self, url: str) -> tuple[bool | None, float]:
        p = urlparse(url)
        origin = f"{p.scheme}://{p.netloc}"
        with self._lock:
            cached = origin in self._cache
            rules = self._cache.get(origin)
        if not cached:
            rules = self._load(origin)
            with self._lock:
                self._cache[origin] = rules
        if rules is None:                      # robots.txt unreadable: no crawling this run, the next run asks again
            return None, 0.0
        if rules is True or rules is False:
            return rules, 0.0
        return rules.can_fetch(USER_AGENT, url), float(rules.crawl_delay(USER_AGENT) or 0)

    @staticmethod
    def _load(origin: str):
        try:
            r = _session().get(origin + "/robots.txt", timeout=TIMEOUT, allow_redirects=True)
        except requests.RequestException:
            return None                        # can't read robots.txt -> don't crawl now (RFC 9309), retry later
        if r.status_code >= 500 or r.status_code == 429:
            return None
        if r.status_code in (401, 403):
            return False
        if r.status_code >= 400:
            return True                        # no robots.txt -> everything allowed
        rules = robotparser.RobotFileParser()
        rules.parse(r.text.splitlines())
        return rules


MAX_REDIRECTS = 10


def fetch_static(url: str, allow=None) -> tuple[str | None, str]:
    """(final_url, html) or (None, reason). Each redirect target is first checked with allow(url):
    True = follow, False = 'robots' (may not visit), None = 'unreachable' (can't tell now)."""
    for _ in range(MAX_REDIRECTS + 1):
        try:
            r = _session().get(url, timeout=TIMEOUT, allow_redirects=False, stream=True)
        except requests.exceptions.SSLError:
            return None, "SSL error"
        except requests.RequestException:
            return None, "unreachable"
        target = _session().get_redirect_target(r)
        if not target:
            break
        r.close()
        url = urldefrag(urljoin(url, target))[0]
        ok = allow(url) if allow else True
        if not ok:
            return None, "robots" if ok is False else "unreachable"
    else:
        return None, "too many redirects"
    with r:
        if r.status_code != 200:
            return None, f"HTTP {r.status_code}"
        ctype = r.headers.get("Content-Type", "").lower()
        if ctype and "html" not in ctype and "xml" not in ctype:
            return None, "not a web page"
        data = b""
        try:
            for chunk in r.iter_content(65536):
                data += chunk
                if len(data) > MAX_BYTES:
                    break
        except requests.RequestException:                   # cut-off / broken body
            return None, "unreachable"
        encoding = r.encoding if r.encoding and r.encoding.lower() != "iso-8859-1" else None
    m = re.search(rb"<meta[^>]+charset=[\"']?([\w-]+)", data[:4096], re.I)
    if m:
        encoding = m.group(1).decode("ascii", "ignore")
    try:
        return r.url, data.decode(encoding or "utf-8", errors="replace")
    except LookupError:
        return r.url, data.decode("utf-8", errors="replace")


def browser_fetcher(page):
    """Fetch pages with the real browser - only for sites whose content is drawn by JavaScript."""
    def fetch(url: str, allow=None) -> tuple[str | None, str]:
        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            if response is None or response.status != 200:
                return None, f"HTTP {response.status if response else '?'}"
            page.wait_for_timeout(2500)
            if allow and page.url.rstrip("/") != url.rstrip("/"):      # redirected: same check as fetch_static
                ok = allow(page.url)
                if not ok:
                    return None, "robots" if ok is False else "unreachable"
            return page.url, page.content()
        except Exception:
            return None, "unreachable"
    return fetch


# ───────────────────────────── which pages ─────────────────────────────

TEAM_WORD = (r"(?:our|the|meet)*(?:team|leadership|leaders|management|founders?|people|doctors|dentists|physicians|"
             r"specialists|lawyers|attorneys|partners|experts|staff|board|directors|members|فريق|فريقنا|الأطباء|"
             r"أطباؤنا)s?")
FILLER_WORD = r"our|the|meet|us|en|ar|of|and|page|html?|php|aspx|العمل"
PAGE_KINDS = [   # (kind, pattern, whole): whole = every word of the slug / link text must fit the pattern
    ("contact", re.compile(r"contact(?![-_ ]?lens)|get[-_ ]?in[-_ ]?touch|reach[-_ ]?us|enquir|inquir|اتصل|تواصل", re.I),
     False),
    ("about", re.compile(r"about|who[-_ ]?we[-_ ]?are|our[-_ ]?story|company[-_ ]?profile|من[-_ ]?نحن|عن[-_ ]", re.I), False),
    # 'our-team', 'partners', 'board-of-directors' - not 'wealth-management', 'insurance-partners', 'dashboard'
    ("team", re.compile(rf"(?:(?:{FILLER_WORD}) )*{TEAM_WORD}(?: (?:{FILLER_WORD}|{TEAM_WORD}))*", re.I), True),
]


def _kind_match(rx, whole: bool, text: str) -> bool:
    if whole:
        return rx.fullmatch(" ".join(w for w in re.split(r"[\W_]+", text.lower()) if w)) is not None
    return rx.search(text) is not None
SKIP_PATH = re.compile(r"\.(pdf|jpe?g|png|gif|webp|svg|zip|docx?|xlsx?|pptx?|mp4|mp3)$", re.I)


def same_site(url: str, site: str) -> bool:
    """url is on the website `site` (a host, 'www.' removed) or one of its subdomains."""
    host = host_of(url)
    return bool(host) and (host == site or host.endswith("." + site) or site.endswith("." + host))


def pick_pages(soup: BeautifulSoup, base_url: str) -> dict[str, str]:
    """Best contact / about / team link on the homepage (same website only)."""
    site = host_of(base_url)
    best: dict[str, tuple] = {}
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.lower().startswith(("mailto:", "tel:", "javascript:", "#", "whatsapp:", "sms:")):
            continue
        url = urldefrag(urljoin(base_url, href))[0]
        if not url.startswith(("http://", "https://")):
            continue
        if not same_site(url, site):
            continue
        path = urlparse(url).path
        segments = [s for s in unquote(path).split("/") if s]
        if not segments or SKIP_PATH.search(path):
            continue
        text = clean(a.get_text(" "))
        text = text if len(text.split()) <= 4 else ""
        for kind, rx, whole in PAGE_KINDS:
            if _kind_match(rx, whole, segments[-1]) or (text and _kind_match(rx, whole, text)):
                score = (len(segments), len(path))
                if kind not in best or score < best[kind][0]:
                    best[kind] = (score, url)
                break
    return {kind: url for kind, (score, url) in best.items()}


# ───────────────────────────── emails ─────────────────────────────

EMAIL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._%+-]{0,63}@(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
                      r"[A-Za-z]{2,24}")
JUNK_EMAIL_RE = re.compile(
    r"example\.|domain\.com|yourdomain|yoursite|your-?email|sentry|wixpress|@wix\.|"
    r"\.(png|jpe?g|gif|webp|svg|css|js)$|^(no-?reply|donotreply|do-not-reply|user|username|name|test|email|"
    r"your|yourname|someone|john\.?doe)@|@(example|test|email|domain)\.(com|org|net)$", re.I)
GOOD_PREFIXES = ("info", "contact", "hello", "hi", "enquiries", "enquiry", "inquiries", "inquiry", "admin",
                 "office", "reception", "appointments", "appointment", "booking", "bookings", "sales", "ceo",
                 "founder", "director", "md", "partner", "owner", "management", "dubai", "uae")
BAD_PREFIXES = ("careers", "career", "jobs", "job", "hr", "recruit", "cv", "resume", "privacy", "dpo",
                "abuse", "webmaster", "postmaster", "newsletter", "unsubscribe", "noreply")
WEAK_PREFIXES = ("billing", "accounts", "invoice", "support", "complaint", "feedback", "press", "media")


def _cf_decode(hexstr: str) -> str:
    """Cloudflare 'email protection' encoding."""
    try:
        key = int(hexstr[:2], 16)
        return "".join(chr(int(hexstr[i:i + 2], 16) ^ key) for i in range(2, len(hexstr), 2))
    except ValueError:
        return ""


def emails_from(soup: BeautifulSoup, text: str) -> list[str]:
    found = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.lower().startswith("mailto:"):
            found += re.split(r"[,;]", unquote(href[7:].split("?")[0]))
        elif "/cdn-cgi/l/email-protection#" in href:
            found.append(_cf_decode(href.split("#", 1)[1]))
    for el in soup.select("[data-cfemail]"):
        found.append(_cf_decode(el.get("data-cfemail", "")))
    text = re.sub(r"\s*[\[({]\s*at\s*[\])}]\s*", "@", text, flags=re.I)
    text = re.sub(r"\s*[\[({]\s*dot\s*[\])}]\s*", ".", text, flags=re.I)
    found += EMAIL_RE.findall(text)
    out = []
    for e in found:
        e = e.strip().strip(".").lower()
        if EMAIL_RE.fullmatch(e) and not JUNK_EMAIL_RE.search(e) and e not in out:
            out.append(e)
    return out


def best_email(emails: list[str], site_host: str, person: str = "") -> str:
    first_names = [t.lower() for t in re.findall(r"[A-Za-z]{3,}", person or "")
                   if t.lower() not in ("dr", "prof", "eng", "mr", "mrs", "ms", "sheikh")][:2]

    def score(e: str) -> int:
        local, _, dom = e.partition("@")
        s = 0
        if site_host and (dom == site_host or dom.endswith("." + site_host) or site_host.endswith("." + dom)):
            s += 3
        if local in GOOD_PREFIXES or local.split(".")[0] in GOOD_PREFIXES:
            s += 2
        if local.startswith(BAD_PREFIXES):
            s -= 4
        elif local.startswith(WEAK_PREFIXES):
            s -= 1
        if any(n in local if len(n) > 3 else re.search(rf"(?:^|[._+-]){n}(?:$|[._+-])", local)
               for n in first_names):                  # 'ali' must not match 'khalid@' or 'quality@'
            s += 4
        return s

    return max(emails, key=score) if emails else ""


# ───────────────────────────── social links ─────────────────────────────

IG_RESERVED = {"p", "reel", "reels", "tv", "explore", "accounts", "stories", "about", "developer", "legal",
               "direct", "share", "sharer", "web", "privacy", "help"}
FB_RESERVED = {"sharer", "sharer.php", "share", "share.php", "dialog", "plugins", "tr", "login", "login.php",
               "help", "policies", "privacy", "watch", "hashtag", "events", "groups", "gaming", "marketplace",
               "photo.php", "photo", "story.php", "permalink.php", "home.php", "l.php", "ads", "business"}


def socials_from(urls) -> dict[str, list[str]]:
    out = {"linkedin": [], "instagram": [], "facebook": []}
    for u in urls:
        u = (u or "").strip()
        m = re.search(r"linkedin\.com/(company|school|showcase)/([^/?#\s\"'<>]+)", u, re.I)
        if m:
            out["linkedin"].append(f"https://www.linkedin.com/{m.group(1).lower()}/{m.group(2)}/")
            continue
        m = re.search(r"instagram\.com/([A-Za-z0-9._]{1,30})(?:[/?#]|$)", u, re.I)
        if m and m.group(1).lower() not in IG_RESERVED:
            out["instagram"].append(f"https://www.instagram.com/{m.group(1)}/")
            continue
        m = re.search(r"(?:facebook|fb)\.com/([^\s\"'<>]+)", u, re.I)
        if m:
            path = m.group(1)
            if path.lower().startswith("profile.php"):
                pid = re.search(r"id=(\d+)", path)
                if pid:
                    out["facebook"].append(f"https://www.facebook.com/profile.php?id={pid.group(1)}")
                continue
            path = re.split(r"[?#]", path)[0].strip("/")
            if path and path.split("/")[0].lower() not in FB_RESERVED:
                out["facebook"].append(f"https://www.facebook.com/{path}")
    return out


def social_from_website(url: str) -> dict[str, str]:
    """When Google's 'website' is itself an Instagram / Facebook / LinkedIn page."""
    found = socials_from([url])
    return {k: v[0] for k, v in found.items() if v}


# ───────────────────────────── decision-makers ─────────────────────────────

TITLE_TIERS = [
    (1, r"co[- ]?founder|founding partner|founder|owner|proprietor|chairman|chairwoman|chairperson|"
        r"chief executive(?: officer)?|ceo|managing director|managing partner|president"),
    (2, r"general manager|medical director|clinical director|dental director|chief medical officer|"
        r"senior partner|executive director|principal(?: architect| designer| partner)?|"
        r"chief [a-z]+ officer|coo|cfo|cto|cmo|cco"),
    (3, r"partner|director|head of [a-z&,/ ]{3,40}|vice[- ]president|vp|"
        r"lead (?:dentist|doctor|surgeon|physician|orthodontist|dermatologist|consultant)|"
        r"head (?:dentist|doctor|surgeon|physician)"),
]
TITLE_RES = [(t, re.compile(rf"(?<![A-Za-z])(?:{rx})(?![A-Za-z])", re.I)) for t, rx in TITLE_TIERS]
VICE_RE = re.compile(r"vice[- ]president|(?<![A-Za-z])vp(?![A-Za-z])|deputy|assistant|associate", re.I)
NOT_A_TITLE_RE = re.compile(r"former|(?<![A-Za-z])ex-|junior|assistant to|our partners|partner with|patronage|"
                            r"partnership|directory|directions|^\s*(?:our\s+|the\s+)?board\s+of\s+directors\s*$",
                            re.I)
# 'CEO at Acme Holding' in a testimonial is a client, not this business's decision-maker
EXTERNAL_TITLE_RE = re.compile(r"(?:\bat\b|@|\bof\b)\s+(.*\b(?:llc|inc|ltd|limited|group|holding|holdings|corp|"
                               r"corporation|company|co\.|fz-?llc|fze|dmcc|bank|properties|real estate)\b.*)$", re.I)
AR_TITLES = [(1, r"الشريك المؤسس|المؤسس|مؤسس|المالك|الرئيس التنفيذي|المدير التنفيذي|رئيس مجلس الإدارة|العضو المنتدب"),
             (2, r"المدير العام|المدير الطبي|الشريك الإداري|الشريك الأول"),
             (3, r"شريك|مدير|رئيس قسم")]
AR_TITLE_RES = [(t, re.compile(rx)) for t, rx in AR_TITLES]


# a title word right after these is not a job title ('Google Partner', 'Become a Partner', 'Business Owner')...
TITLE_BEFORE_NOT = {"a", "an", "become", "your", "business", "google", "certified", "official", "premier",
                    "preferred", "trusted", "strategic", "channel"}
# ...nor one followed by another word, unless it's one of these ('Partner Visa', 'Partner Hospitals')
TITLE_AFTER_OK = {"and", "of", "at", "for", "in", "uae", "dubai", "mena", "gcc", "middle", "emea", "ksa",
                  "international", "group", "general", "emeritus", "founder", "cofounder", "owner", "ceo",
                  "director", "partner", "chairman", "president", "manager", "md"}


def _title_in_context(text: str, m) -> bool:
    before = re.findall(r"[\w'’]+", text[:m.start()])
    after = re.match(r"\s*([\w'’]+)", text[m.end():])        # punctuation next ('Partner - Tax') is fine
    if before and before[-1].lower() in TITLE_BEFORE_NOT:
        return False
    return not after or not re.match(r"\w", after.group(1)) or after.group(1).lower() in TITLE_AFTER_OK


def title_tier(text: str) -> int:
    """1 = owner level, 2 = senior, 3 = director / VP / head / partner, 0 = not a leadership title."""
    if not text or len(text) > 90 or NOT_A_TITLE_RE.search(text) or text.rstrip().endswith("?"):
        return 0
    tiers = ([t for t, rx in TITLE_RES if any(_title_in_context(text, m) for m in rx.finditer(text))]
             + [t for t, rx in AR_TITLE_RES if rx.search(text)])
    if not tiers:
        return 0
    tier = min(tiers)
    if VICE_RE.search(text):
        tier = max(tier, 3)
    return tier


UP, LOW = "A-ZÀ-ÖØ-Þ", "a-zß-öø-ÿ"
TOKEN = rf"(?:[{UP}][{UP}{LOW}'’\-]*[{UP}{LOW}]|[{UP}]\.)"
CONNECT = r"(?:al|el|bin|bint|ibn|abu|abd|abdul|van|von|de|del|der|da|di|du|la|le)"
HONORIFIC = r"(?i:dr|prof|professor|eng|engr|mr|mrs|ms|miss|sheikh|shaikh|h\.?\s?e)\.?\s+"
NAME_CORE = rf"{TOKEN}(?:[\s\-]+(?:{CONNECT}[\s\-]+)?{TOKEN}){{1,3}}"
NAME_FULL_RE = re.compile(rf"(?:{HONORIFIC})?{NAME_CORE}")
LEAD_IN_RE = re.compile(r"^(?:meet|about|by|message from|a message from|a word from|word from|letter from|"
                        r"note from|introducing|welcome from)\s+", re.I)
CREDENTIALS_RE = re.compile(
    r"(?:[,\s]+(?:BDS|DDS|DMD|MBBS|MBChB|MD|DO|PhD|MSc|MS|MA|BA|BSc|MBA|MPH|FRCS|MRCS|MFDS|MJDF|FDS|"
    r"DClinDent|MClinDent|LLB|LLM|JD|CPA|ACCA|FCCA|CFA|CA|PE|RIBA|PMP|Esq|FRCP|MRCP|DDS)\.?(?:\s*\([^)]*\))?)+\s*$")
NAME_STOP = set("""
about us our the team meet contact home read more view profile learn book appointment call email phone
address location follow copyright rights reserved privacy policy terms welcome message mission vision values
why choose services service solutions company group clinic clinics dental medical center centre hospital
pharmacy health healthcare care dubai uae emirates dhabi sharjah ajman llc fz fze fzco dmcc ltd limited inc
international global middle east gulf agency digital marketing media studio studios consulting consultancy
consultants management partners associates advocates legal law firm chambers office offices business setup
trading real estate properties director founder ceo chairman owner partner manager managing general executive
chief officer head senior lead specialist consultant doctor dentist orthodontist surgeon physician
dermatologist practitioner board members member staff people leadership story history testimonials reviews
faq blog news events careers gallery portfolio projects clients award awards english arabic menu close open
and of for with in at to by from your we is are our get started now today here new top best world group
investment investments capital financial finance wealth insurance accounting audit tax design interior
architecture engineering logistics shipping cargo technology technologies tech software it systems
restaurant cafe coffee salon spa beauty fitness academy institute school nursery training travel tours
yacht yachts luxury jewellery jewelry boutique gallery motors cars
established founded started joined leads led has have holds brings believes graduated received completed
specializes specialises offers provides built created opened launched works worked studied trained qualified
comes came began since over years experience is was as an a his her he she they their who which that this
these those also all every very click discover explore find see more call send submit request free our
""".split())
AR_HONORIFIC = r"(?:د\.?\s*|الدكتور\s+|الدكتورة\s+|المهندس\s+|المهندسة\s+|الأستاذ\s+|الأستاذة\s+|السيد\s+|السيدة\s+|الشيخ\s+)"
AR_NAME_RE = re.compile(rf"^({AR_HONORIFIC})?([\u0621-\u064A]{{2,}}(?:\s+[\u0621-\u064A]{{2,}}){{1,3}})$")
AR_STOP = set("من نحن عن اتصل بنا فريق فريقنا الشركة شركة الإدارة إدارة مجلس عيادة مركز دبي الإمارات خدمات "
              "خدماتنا رسالة رؤية رؤيتنا قيمنا كلمة المدير مدير المؤسس الرئيس التنفيذي العام الطبي".split())


HISTORIC_RE = re.compile(r"zayed bin sultan|rashid bin saeed", re.I)   # founders of the UAE / modern Dubai


def as_name(text: str, allow_arabic_without_title: bool = True) -> str:
    """Return a clean person name if `text` is one (optionally with 'Dr.' and degrees), else ''."""
    text = clean(text).strip(" ,.:;|-–—")
    if not text or len(text) > 60:
        return ""
    text = LEAD_IN_RE.sub("", text)
    text = CREDENTIALS_RE.sub("", text).strip(" ,")
    if HISTORIC_RE.search(text):                     # 'the vision of our Founder, Sheikh Zayed bin Sultan'
        return ""
    m = AR_NAME_RE.match(text)
    if m:
        words = m.group(2).split()
        if any(w in AR_STOP for w in words) or (not m.group(1) and not allow_arabic_without_title):
            return ""
        return text
    if not NAME_FULL_RE.fullmatch(text):
        return ""
    words = re.sub(rf"^{HONORIFIC}", "", text).replace("-", " ").split()
    core = [w for w in words if not re.fullmatch(CONNECT, w, re.I)]
    if len(core) < 2 or any(w.lower().strip("'’") in NAME_STOP for w in core):
        return ""
    if text.isupper():
        text = " ".join(_cap(w) for w in text.split())
    return text


ACRONYMS = {"ceo", "coo", "cfo", "cto", "cmo", "cco", "md", "vp", "gm", "it", "hr", "pr", "uae", "llc"}


def _cap(word: str) -> str:
    """'AL-MANSOORI' -> 'Al-Mansoori', "O'BRIEN" -> "O'Brien" """
    return "-".join("'".join(x.capitalize() for x in part.split("'")) for part in word.split("-"))


def tidy_title(text: str) -> str:
    text = clean(text).strip(" ,.:;|-–—()")
    text = re.sub(r"^(?:our|the|meet (?:our|the))\s+", "", text, flags=re.I)
    if text.isupper() or text.islower():
        text = " ".join(w.upper() if w.lower() in ACRONYMS else (w.lower() if w.lower() in ("of", "and", "&")
                        else _cap(w)) for w in text.split())
    return text[:80]


SEP_RE = re.compile(r"\s*(?:,|–|—|\s-\s|\||:|/|·|•|\()\s*")
SENTENCE_TITLES = (r"(?i:founder|co-founder|ceo|chief executive officer|managing director|medical director|"
                   r"owner|managing partner|chairman|clinical director|general manager)")
SENTENCE_RES = [   # (pattern, name group, title group or fixed title)
    (re.compile(rf"(?i:\bfounded)\s+(?:(?i:in)\s+\d{{4}}\s+)?(?i:by)\s+((?:{HONORIFIC})?{NAME_CORE})"), 1, "Founder"),
    (re.compile(rf"(?i:\b(?:our|the))\s+({SENTENCE_TITLES})\s*,?\s+((?:{HONORIFIC})?{NAME_CORE})"), 2, 1),
    (re.compile(rf"((?:{HONORIFIC})?{NAME_CORE})\s*,\s*(?i:our|the)\s+({SENTENCE_TITLES})\b"), 1, 2),
    (re.compile(rf"((?:{HONORIFIC})?{NAME_CORE})\s+(?i:is|was)\s+(?i:the|our)\s+({SENTENCE_TITLES})\b"), 1, 2),
]


def _trim_name(candidate: str) -> str:
    """'Dr. Sara Ahmed In' -> 'Dr. Sara Ahmed' (drop trailing words until it's a valid name)."""
    words = candidate.split()
    while len(words) >= 2:
        name = as_name(" ".join(words))
        if name:
            return name
        words = words[:-1]
    return ""


def people_from_lines(lines: list[str]) -> list[tuple[int, str, str, int]]:
    """(tier, name, title, position) candidates from a page's text lines."""
    found = []
    for i, line in enumerate(lines):
        if len(line) <= 140:
            sep = SEP_RE.search(line)
            if sep:                                               # 'Name, Title' or 'Title: Name'
                parts = (line[:sep.start()], line[sep.end():])
                orders = [parts] if sep.group().strip() in (",", "(") else [parts, parts[::-1]]
                for name_part, title_part in orders:              # not 'Partner, Dispute Resolution' reversed
                    tier = title_tier(title_part)
                    name = as_name(name_part) if tier else ""
                    if name and len(title_part) <= 80:
                        found.append((tier, name, tidy_title(title_part), i))
            tier = title_tier(line)
            if tier and len(line) <= 80 and not as_name(line):   # title line next to a name line
                for j in (i - 1, i + 1, i - 2):
                    if 0 <= j < len(lines):
                        name = as_name(lines[j], allow_arabic_without_title=False)
                        if name:
                            found.append((tier, name, tidy_title(line), i))
                            break
        for rx, name_group, title in SENTENCE_RES:
            for m in rx.finditer(line):
                raw_name = m.group(name_group)
                title_text = title if isinstance(title, str) else m.group(title)
                name = _trim_name(raw_name)
                if name:
                    found.append((title_tier(title_text) or 1, name, tidy_title(title_text), i))
    return found


def jsonld_data(soup: BeautifulSoup):
    """People, emails and social links from schema.org JSON-LD blocks."""
    people, emails, links = [], [], []

    def walk(node):
        if isinstance(node, list):
            for x in node:
                walk(x)
            return
        if not isinstance(node, dict):
            return
        if isinstance(node.get("email"), str):
            emails.append(node["email"].replace("mailto:", ""))
        same = node.get("sameAs")
        same = same if isinstance(same, list) else [same]
        links.extend(x for x in same if isinstance(x, str))
        for key, default in (("founder", "Founder"), ("founders", "Founder"), ("employee", None),
                             ("employees", None), ("member", None), ("members", None)):
            value = node.get(key)
            for person in value if isinstance(value, list) else [value]:
                if isinstance(person, dict):
                    name, title = person.get("name"), person.get("jobTitle") or default
                elif isinstance(person, str):
                    name, title = person, default
                else:
                    continue
                title = title if isinstance(title, str) else default
                tier = title_tier(title or "")
                name = as_name(name if isinstance(name, str) else "")
                if name and tier:
                    people.append((tier, name, tidy_title(title), -1))
        for v in node.values():
            if isinstance(v, (dict, list)):
                walk(v)

    for script in soup.find_all("script", type=re.compile(r"ld\+json", re.I)):
        try:
            walk(json.loads(script.string or script.get_text() or ""))
        except (ValueError, TypeError):
            continue
    return people, emails, links


BLOCK_TAGS = ["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "td", "th", "dt", "dd", "figcaption", "blockquote",
              "div", "section", "article", "header", "footer", "aside", "address", "main", "nav", "ul", "ol",
              "table", "tr", "form", "label", "figure"]
BLOCK_SET = set(BLOCK_TAGS)
TESTIMONIAL_RE = re.compile(r"(?<![a-z])(?:testimonial|review|feedback|quote)|what.?(?:our|clients|patients).?say", re.I)


def text_lines(soup: BeautifulSoup) -> list[str]:
    """Visible text as short lines. Each block is added whole ('Dr. Sara Ahmed, Founder') and,
    when it has several text pieces, piece by piece too ('Sara Ahmed' / 'Founder & CEO')."""
    for br in soup.find_all("br"):
        br.replace_with("\n")
    lines: list[str] = []

    def add(text) -> None:
        for part in str(text).split("\n"):
            part = clean(part)
            if part and (not lines or lines[-1] != part):
                lines.append(part)

    for el in soup.find_all(BLOCK_TAGS):
        if el.find(BLOCK_TAGS):                       # has inner blocks: only its own loose text
            for child in el.children:
                if isinstance(child, NavigableString):
                    if not isinstance(child, (Comment, Doctype)):
                        add(child)
                elif child.name not in BLOCK_SET:
                    add(child.get_text(" "))
            continue
        add(el.get_text(" "))
        pieces = list(el.stripped_strings)
        if len(pieces) > 1:
            for piece in pieces:
                add(piece)
    return lines


def _drop_testimonials(soup: BeautifulSoup) -> None:
    """Client quotes ('Great work! - John, CEO of Acme') are not this business's people."""
    for el in soup.find_all(True):
        if el.decomposed or el.name in ("html", "body", "main"):
            continue
        marker = " ".join(el.get("class") or []) + " " + (el.get("id") or "")
        if TESTIMONIAL_RE.search(marker):
            el.decompose()


def _is_external(title: str, business_name: str) -> bool:
    m = EXTERNAL_TITLE_RE.search(title or "")
    if not m or re.fullmatch(r"(?:the\s+)?(?:company|group|firm|clinic|practice)\.?", m.group(1).strip(), re.I):
        return False                                   # 'Founder of the Group' = this business
    own = {w for w in re.findall(r"\w{4,}", (business_name or "").lower())}
    return not own & set(re.findall(r"\w{4,}", m.group(1).lower()))


# ───────────────────────────── one website ─────────────────────────────

def analyse(pages: list[tuple[str, str, str]], site_host: str, business_name: str = "") -> dict:
    """pages = [(kind, url, html)] -> contacts found on them."""
    emails, links, people = [], [], []
    page_rank = {"team": 0, "about": 1, "home": 2, "contact": 3}
    for kind, url, html in pages:
        soup = BeautifulSoup(html, "html.parser")
        rank = page_rank.get(kind, 4)
        found, mails, same_as = jsonld_data(soup)
        people += [(t, n, ti, rank, pos) for t, n, ti, pos in found]
        emails += mails
        links += [a["href"] for a in soup.find_all("a", href=True)] + same_as
        for tag in soup(["script", "style", "noscript", "template", "svg"]):
            tag.decompose()
        emails += emails_from(soup, soup.get_text(" "))
        _drop_testimonials(soup)
        people += [(t, n, ti, rank, pos) for t, n, ti, pos in people_from_lines(text_lines(soup))]

    people = [p for p in people if not _is_external(p[2], business_name)]
    emails = list(dict.fromkeys(x.strip().lower() for x in emails if EMAIL_RE.fullmatch(x.strip())
                                and not JUNK_EMAIL_RE.search(x.strip())))
    person = min(people, key=lambda c: (c[0], c[3], c[4])) if people else None
    socials = socials_from(links)

    def most_common(items):
        return Counter(items).most_common(1)[0][0] if items else ""

    return {
        "email": best_email(emails, site_host, person[1] if person else ""),
        "emails_all": "; ".join(emails[:10]),
        "decision_maker_name": person[1] if person else "",
        "decision_maker_title": person[2] if person else "",
        "linkedin": most_common(socials["linkedin"]),
        "instagram": most_common(socials["instagram"]),
        "facebook": most_common(socials["facebook"]),
    }


def looks_empty(html: str) -> bool:
    """True when a page is an empty shell that JavaScript fills in later."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()
    return len(clean(soup.get_text(" "))) < 300 and len(soup.find_all("a", href=True)) < 5


def enrich_website(website: str, business_name: str, robots: Robots, skip_domains,
                   fetch=fetch_static) -> tuple[str, dict]:
    """Visit the homepage + contact / about / team pages. Returns (status, data).
    status: ok | nothing | skipped | robots | unreachable | needs_js"""
    host = host_of(website)
    social = social_from_website(website)
    if social:
        return "skipped", social
    if not host or domain_in(host, skip_domains):
        return "skipped", {}
    allowed, delay = robots.check(website)
    if allowed is None:
        return "unreachable", {}
    if not allowed:
        return "robots", {}
    final_url, html = fetch(website, lambda u: robots.check(u)[0])     # a moved homepage: its robots.txt rules
    if final_url is None:
        return ("robots" if html == "robots" else "unreachable"), {}
    if fetch is fetch_static and looks_empty(html):
        return "needs_js", {}
    pages = [("home", final_url, html)]
    links = pick_pages(BeautifulSoup(html, "html.parser"), final_url)
    visited = {final_url.rstrip("/")}
    for kind in ("contact", "about", "team"):
        url = links.get(kind)
        if not url or url.rstrip("/") in visited:
            continue
        visited.add(url.rstrip("/"))
        allowed, delay = robots.check(url)
        if not allowed:
            continue
        if delay > 60:                                  # the site asks for very slow visits: homepage only
            break
        time.sleep(max(random.uniform(*PAGE_DELAY), delay))
        site = host_of(final_url)
        page_url, page_html = fetch(url, lambda u: robots.check(u)[0] if same_site(u, site) else False)
        if page_url:
            pages.append((kind, page_url, page_html))
    data = analyse(pages, host_of(final_url) or host, business_name)
    found = any(data[k] for k in ("email", "linkedin", "instagram", "facebook", "decision_maker_name"))
    return ("ok" if found else "nothing"), data
