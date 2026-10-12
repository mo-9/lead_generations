"""Dubai Leads - Google Maps lead list of business owners and high-earning professionals.

Run:   python run.py          (all settings are in settings.py)
Stop:  Ctrl+C at any time - progress is saved and the next run continues where it stopped.
"""
from __future__ import annotations

import csv
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import settings as S
from common import (Pacer, StopScraping, area_name_variants, box_from_setting, contains_phrase, excel_safe,
                    fmt_duration, log, normalize_name, phone_digits, setup_logging)
from store import Store

try:
    from playwright.sync_api import sync_playwright
    from enrich import Robots, browser_fetcher, enrich_website
    from maps import MapsClient, launch_browser, maps_link
except ImportError as missing:
    sys.exit(f"Missing package ({missing.name}). Run:  python -m pip install -r requirements.txt")

AREA_MARGIN = 0.05            # accept places up to 5% outside an area's box
DETAILS_MAX_ATTEMPTS = 2
ENRICH_MAX_ATTEMPTS = 2
ENRICH_WORKERS = 4            # websites visited in parallel (different sites, so each site sees one visitor)

CSV_COLUMNS = ["persona", "search_keyword", "business_name", "google_category", "area", "address", "phone",
               "website", "email", "decision_maker_name", "decision_maker_title", "linkedin", "instagram",
               "facebook", "rating", "review_count", "is_chain", "google_maps_url", "place_id", "latitude",
               "longitude", "date_collected"]
OTHER_EMIRATES = ("sharjah", "abu dhabi", "ajman", "ras al khaimah", "fujairah", "umm al quwain", "al ain",
                  "الشارقة", "أبوظبي", "عجمان", "رأس الخيمة", "الفجيرة", "أم القيوين", "العين")


# ───────────────────────────── settings → plan ─────────────────────────────

def build_plan():
    """[(persona, keyword)], [(area_name, Box)] from settings.py, or exit with a clear message."""
    problems = []
    personas = {name.lower(): name for name in S.PERSONAS}
    catalogue = {}
    for persona, categories in S.PERSONAS.items():
        for c in categories:
            catalogue.setdefault(c.strip().lower(), (persona, c.strip()))

    chosen: dict[str, tuple[str, str]] = {}
    wanted = [str(x).strip() for x in S.SEARCH_PERSONAS if str(x).strip()]
    if any(w.upper() == "ALL" for w in wanted):
        wanted = list(S.PERSONAS)
    for w in wanted:
        persona = personas.get(w.lower())
        if not persona:
            problems.append(f'Unknown persona "{w}". Choose from: {", ".join(S.PERSONAS)}')
            continue
        for c in S.PERSONAS[persona]:
            chosen.setdefault(c.strip().lower(), (persona, c.strip()))
    for c in S.SEARCH_CATEGORIES:
        c = str(c).strip()
        if c:
            chosen.setdefault(c.lower(), catalogue.get(c.lower(), ("Other", c)))

    area_names = {name.lower(): name for name in S.AREAS}
    wanted_areas = [str(x).strip() for x in S.SEARCH_AREAS if str(x).strip()]
    if any(w.upper() == "ALL" for w in wanted_areas):
        wanted_areas = list(S.AREAS)
    areas = []
    for w in wanted_areas:
        name = area_names.get(w.lower())
        if not name:
            problems.append(f'Unknown area "{w}". Choose from: {", ".join(S.AREAS)}')
            continue
        try:
            box = box_from_setting(S.AREAS[name])
        except (TypeError, ValueError) as e:
            problems.append(f'Area "{name}": {e}')
            continue
        if name not in [a for a, _ in areas]:
            areas.append((name, box))

    if S.WEBSITE not in ("must_have", "must_not_have", "either"):
        problems.append('WEBSITE must be "must_have", "must_not_have" or "either"')
    if S.PHONE not in ("must_have", "either"):
        problems.append('PHONE must be "must_have" or "either"')
    if not chosen:
        problems.append("Nothing to search: put at least one persona or category in SEARCH_PERSONAS / SEARCH_CATEGORIES")
    if not areas and not any("Unknown area" in p for p in problems):
        problems.append("No areas selected in SEARCH_AREAS")
    if problems:
        print("\nPlease fix settings.py:\n  - " + "\n  - ".join(problems))
        sys.exit(1)
    return list(chosen.values()), areas


# ───────────────────────────── filters ─────────────────────────────

def is_excluded(name: str, category: str) -> bool:
    category = (category or "").lower()
    return (any(k.lower() in category for k in S.EXCLUDE_CATEGORY_KEYWORDS)
            or any(contains_phrase(name, k) for k in S.EXCLUDE_NAME_KEYWORDS))


def outside_dubai(address: str) -> bool:
    a = (address or "").lower()
    return any(e in a for e in OTHER_EMIRATES) and "dubai" not in a and "دبي" not in a


def card_ok(row) -> bool:
    """Quick check on what the results list shows, before opening the place (saves Google requests)."""
    rating, reviews = row["card_rating"], row["card_reviews"]
    if S.MIN_RATING and rating is not None and rating < S.MIN_RATING:
        return False
    if reviews is not None:
        if reviews < (S.MIN_REVIEWS or 0):
            return False
        if S.MAX_REVIEWS is not None and reviews > S.MAX_REVIEWS:
            return False
    return not is_excluded(row["name"], row["card_category"])


def lead_problem(row) -> str:
    """'' if the place is a lead under the current filters, else the reason it isn't."""
    if row["details_status"] != "ok":
        return "details not loaded"
    if (row["closed"] or "").lower().startswith("permanently"):
        return "permanently closed"
    rating = row["rating"] if row["rating"] is not None else row["card_rating"]
    reviews = row["review_count"] if row["review_count"] is not None else row["card_reviews"]
    if S.MIN_RATING and (rating is None or rating < S.MIN_RATING):
        return f"rating {rating or 'none'}"
    if S.MIN_REVIEWS and (reviews is None or reviews < S.MIN_REVIEWS):
        return f"{reviews or 0} reviews"
    if S.MAX_REVIEWS is not None and reviews is not None and reviews > S.MAX_REVIEWS:
        return f"{reviews} reviews (above max)"
    if S.WEBSITE == "must_have" and not row["website"]:
        return "no website"
    if S.WEBSITE == "must_not_have" and row["website"]:
        return "has a website"
    if S.PHONE == "must_have" and not row["phone"]:
        return "no phone"
    if is_excluded(row["name"], f'{row["google_category"] or ""} {row["card_category"] or ""}'):
        return "excluded category/name"
    if outside_dubai(row["address"]):
        return "outside Dubai"
    return ""


def describe(row) -> str:
    rating = row["rating"] if row["rating"] is not None else row["card_rating"]
    reviews = row["review_count"] if row["review_count"] is not None else row["card_reviews"]
    category = row["google_category"] or row["card_category"] or "?"
    return f'{row["name"]} | {category} | {rating or "-"} ({reviews or 0})'


def log_place(row, prefix="   ") -> bool:
    problem = lead_problem(row)
    log.info(f"{prefix} {'LEAD' if not problem else 'skip'}  {describe(row)}" + (f"  [{problem}]" if problem else ""))
    return not problem


# ───────────────────────────── Google Maps ─────────────────────────────

_warned = set()


def layout_warning(kind: str, message: str) -> None:
    if kind not in _warned:
        _warned.add(kind)
        log.info(f"   WARNING: {message}")


def check_layout(cards) -> None:
    """Results load but nothing can be read from them -> Google changed its page, say so clearly."""
    if len(cards) >= 10 and not any(c.get("card_rating") or c.get("card_category") for c in cards):
        layout_warning("cards", "Google Maps results load, but their ratings/categories can't be read. "
                                "Google may have changed its page layout - the scraper may need an update.")


def run_searches(store: Store, maps: MapsClient, plan, areas) -> None:
    total, n = len(plan) * len(areas), 0
    for area_name, area_box in areas:
        for persona, keyword in plan:
            n += 1
            queue = [area_box]
            while queue:
                box = queue.pop(0)
                task_id = f"{keyword.lower()}|{area_name}|{box.key()}"
                status = store.task_status(task_id)
                if status == "done":
                    continue
                if status == "split":
                    queue.extend(box.split())
                    continue
                part = f" (smaller part, level {box.depth})" if box.depth else ""
                log.info(f"[{n}/{total}] {area_name} - {keyword}{part}")
                cards = maps.search(keyword, box)
                if cards is None:
                    store.set_task(task_id, keyword, area_name, box, "failed", 0)
                    log.info("   could not load this search - it will be retried on the next run")
                    continue
                check_layout(cards)
                new, leads = handle_cards(store, maps, cards, persona, keyword, area_name, area_box)
                capped = len(cards) >= S.CAP_THRESHOLD
                if capped and box.depth < S.MAX_SPLIT_DEPTH:
                    store.set_task(task_id, keyword, area_name, box, "split", len(cards))
                    queue.extend(box.split())
                    note = " -> list cut off by Google, searching 4 smaller parts"
                else:
                    store.set_task(task_id, keyword, area_name, box, "done", len(cards))
                    note = " -> still cut off at the smallest size; some places may be missed" if capped else ""
                log.info(f"   {len(cards)} results, {new} new, {leads} leads here{note}")


_empty_details_in_row = 0


def save_details(store: Store, key: str, details) -> None:
    global _empty_details_in_row
    if details and not any(details.get(f) for f in ("google_category", "address", "phone", "website")):
        _empty_details_in_row += 1
        if _empty_details_in_row >= 5:
            layout_warning("details", "Place pages open, but their address/phone/website can't be read. "
                                      "Google may have changed its page layout - the scraper may need an update.")
    elif details:
        _empty_details_in_row = 0
    store.save_details(key, details)


def handle_cards(store: Store, maps: MapsClient, cards, persona, keyword, area_name, area_box):
    new = leads = 0
    for card in cards:
        new += store.save_card(card)
        lat, lng = card.get("latitude"), card.get("longitude")
        if lat is not None and lng is not None and not area_box.contains(lat, lng, AREA_MARGIN):
            continue                                # Google pads results with places outside the area
        key = card["place_key"]
        store.add_hit(key, keyword, persona, area_name)
        row = store.get(key)
        if row["details_status"] == "ok":
            leads += not lead_problem(row)
            continue
        if row["details_attempts"] >= DETAILS_MAX_ATTEMPTS or not card_ok(row):
            continue
        save_details(store, key, card.get("_details") or maps.details(row["maps_url"]))
        leads += log_place(store.get(key))
    return new, leads


def selected_hits(store: Store, plan, areas) -> dict:
    """place_key -> first search (in the current selection) that found it."""
    keywords = {k.lower() for _, k in plan}
    names = {a for a, _ in areas}
    first = {}
    for h in store.hits():
        if h["keyword"].lower() in keywords and h["area"] in names and h["place_key"] not in first:
            first[h["place_key"]] = h
    return first


def catch_up_details(store: Store, maps: MapsClient, plan, areas) -> None:
    """Places found earlier whose details are still missing (interrupted run, changed filters)."""
    hits = selected_hits(store, plan, areas)
    todo = [r for r in store.places() if r["place_key"] in hits and r["details_status"] != "ok"
            and r["details_attempts"] < DETAILS_MAX_ATTEMPTS and card_ok(r)]
    if todo:
        log.info(f"Opening {len(todo)} places that still need their details...")
    for i, row in enumerate(todo, 1):
        save_details(store, row["place_key"], maps.details(row["maps_url"]))
        log_place(store.get(row["place_key"]), prefix=f"   [{i}/{len(todo)}]")


# ───────────────────────────── websites ─────────────────────────────

def log_enrich(i, total, row, status, data) -> None:
    found = []
    if data.get("email"):
        found.append(data["email"])
    if data.get("decision_maker_name"):
        found.append(f'{data["decision_maker_name"]} ({data.get("decision_maker_title", "")})')
    found += [k for k in ("linkedin", "instagram", "facebook") if data.get(k)]
    detail = ", ".join(found) if found else status
    log.info(f"   [{i}/{total}] {row['name']} -> {detail}")


def run_enrichment(store: Store, plan, areas, context) -> None:
    hits = selected_hits(store, plan, areas)
    rows = [r for r in store.places() if r["place_key"] in hits and r["website"] and not lead_problem(r)
            and r["enrich_attempts"] < ENRICH_MAX_ATTEMPTS and r["enrich_status"] in (None, "unreachable", "needs_js")]
    static = [r for r in rows if r["enrich_status"] != "needs_js"]
    needs_js = [r for r in rows if r["enrich_status"] == "needs_js"]
    robots = Robots()
    if static:
        log.info(f"Visiting {len(static)} websites (homepage + contact / about / team pages)...")
        pool = ThreadPoolExecutor(ENRICH_WORKERS)
        try:
            futures = {pool.submit(enrich_website, r["website"], r["name"], robots, S.SKIP_WEBSITE_DOMAINS): r
                       for r in static}
            for i, future in enumerate(as_completed(futures), 1):
                row = futures[future]
                try:
                    status, data = future.result()
                except Exception:
                    status, data = "unreachable", {}
                store.save_enrichment(row["place_key"], status, data)
                log_enrich(i, len(static), row, status, data)
                if status == "needs_js":
                    needs_js.append(row)
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
    if needs_js:
        log.info(f"Opening {len(needs_js)} websites that need a real browser to show their content...")
        page = context.new_page()
        try:
            fetch = browser_fetcher(page)
            for i, row in enumerate(needs_js, 1):
                try:
                    status, data = enrich_website(row["website"], row["name"], robots, S.SKIP_WEBSITE_DOMAINS,
                                                  fetch=fetch)
                except Exception:
                    status, data = "unreachable", {}
                if status == "needs_js":
                    status = "nothing"
                store.save_enrichment(row["place_key"], status, data)
                log_enrich(i, len(needs_js), row, status, data)
        finally:
            page.close()


# ───────────────────────────── CSV + summary ─────────────────────────────

def export(store: Store, plan, areas, out_dir: Path):
    hits = selected_hits(store, plan, areas)
    places = store.places()
    persona_of = {k.lower(): p for p, k in plan}
    variants = area_name_variants(S.AREAS)

    # chain = the same business name at 2+ different locations (among everything seen on Google Maps).
    # Pins ~20 m apart, or ~100 m apart with the same phone, are one location listed twice.
    locations = defaultdict(list)
    for p in places:
        here = locations[normalize_name(p["name"], variants)]
        lat, lng, phone = p["latitude"], p["longitude"], phone_digits(p["phone"] or p["card_phone"] or "")
        if lat is None or lng is None or not any(
                b_lat is not None and (
                    (abs(lat - b_lat) < 0.0002 and abs(lng - b_lng) < 0.0002)
                    or (abs(lat - b_lat) < 0.001 and abs(lng - b_lng) < 0.001 and (not phone or not b_phone
                                                                                    or phone == b_phone)))
                for b_lat, b_lng, b_phone in here):
            here.append((lat, lng, phone))

    by_key = {p["place_key"]: p for p in places}
    rows, seen = [], set()
    for key, hit in hits.items():
        p = by_key.get(key)
        if p is None or lead_problem(p):
            continue
        norm = normalize_name(p["name"], variants)
        duplicate_key = (norm, phone_digits(p["phone"])) if p["phone"] else None
        if duplicate_key in seen:
            continue
        if duplicate_key:
            seen.add(duplicate_key)
        rating = p["rating"] if p["rating"] is not None else p["card_rating"]
        reviews = p["review_count"] if p["review_count"] is not None else p["card_reviews"]
        rows.append({
            "persona": persona_of.get(hit["keyword"].lower(), hit["persona"]),
            "search_keyword": hit["keyword"],
            "business_name": p["name"],
            "google_category": p["google_category"] or p["card_category"] or "",
            "area": hit["area"],
            "address": p["address"] or p["card_address"] or "",
            "phone": p["phone"] or p["card_phone"] or "",
            "website": p["website"] or "",
            "email": p["email"] or "",
            "decision_maker_name": p["decision_maker_name"] or "",
            "decision_maker_title": p["decision_maker_title"] or "",
            "linkedin": p["linkedin"] or "",
            "instagram": p["instagram"] or "",
            "facebook": p["facebook"] or "",
            "rating": rating if rating is not None else "",
            "review_count": reviews if reviews is not None else "",
            "is_chain": "Yes" if len(locations[norm]) >= 2 else "No",
            "google_maps_url": maps_link(p["place_id"] or "", p["name"], p["maps_url"]),
            "place_id": p["place_id"] or p["ftid"] or "",
            "latitude": round(p["latitude"], 6) if p["latitude"] is not None else "",
            "longitude": round(p["longitude"], 6) if p["longitude"] is not None else "",
            "date_collected": (p["details_at"] or p["first_seen"] or "")[:10],
        })

    persona_order = [p for p, _ in plan] + ["Other"]
    area_order = [a for a, _ in areas]
    rows.sort(key=lambda r: (persona_order.index(r["persona"]) if r["persona"] in persona_order else 99,
                             area_order.index(r["area"]) if r["area"] in area_order else 99,
                             r["business_name"].lower()))
    path = out_dir / f"leads_{datetime.now():%Y-%m-%d_%H%M%S}.csv"
    with open(path, "w", encoding="utf-8-sig", newline="") as f:       # BOM: Excel shows Arabic correctly
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: excel_safe(v) for k, v in r.items()})
    return path, rows


def print_summary(rows, path: Path, stop_reason: str, started: float) -> None:
    line = "=" * 64
    log.info("")
    log.info(line)
    if stop_reason:
        log.info(f"STOPPED: {stop_reason}")
        log.info("Progress is saved. Run the same command again later and it continues where it stopped.")
        log.info(line)
    chains = sum(r["is_chain"] == "Yes" for r in rows)
    log.info(f"Leads in CSV: {len(rows)}   (independent: {len(rows) - chains}, chain branches: {chains})")
    log.info(f"File: {path.resolve()}")
    for title, column in (("Leads per persona", "persona"), ("Leads per area", "area")):
        log.info("")
        log.info(title)
        counts = Counter(r[column] for r in rows)
        if not counts:
            log.info("  (none yet)")
        for name, count in counts.most_common():
            log.info(f"  {name:<44}{count:>6}")
    log.info("")
    log.info(f"Time: {fmt_duration(time.time() - started)}")
    log.info(line)


# ───────────────────────────── main ─────────────────────────────

def main() -> None:
    plan, areas = build_plan()
    out_dir = Path(S.OUTPUT_FOLDER)
    out_dir.mkdir(parents=True, exist_ok=True)
    setup_logging(out_dir / "run.log")
    store = Store(out_dir / "progress.db")
    started = time.time()

    reviews = f">= {S.MIN_REVIEWS}" + (f" and <= {S.MAX_REVIEWS}" if S.MAX_REVIEWS is not None else "")
    log.info(f"Dubai Leads: {len(plan)} categories x {len(areas)} areas")
    log.info(f"Filters: rating >= {S.MIN_RATING} | reviews {reviews} | website: {S.WEBSITE} | phone: {S.PHONE}")
    done = store.count_tasks("done") + store.count_tasks("split")
    if done:
        log.info(f"Saved progress found ({done} searches already done) - continuing from there")

    stop_reason, exit_code = "", 0
    try:
        with sync_playwright() as pw:
            context = launch_browser(pw, out_dir, S.SHOW_BROWSER, getattr(S, "BROWSER", "chrome"))
            page = context.pages[0] if context.pages else context.new_page()
            try:
                needs_counts = bool(S.MIN_REVIEWS) or S.MAX_REVIEWS is not None
                maps = MapsClient(page, Pacer(S), needs_review_counts=needs_counts)
                run_searches(store, maps, plan, areas)
                catch_up_details(store, maps, plan, areas)
                if S.ENRICH_WEBSITES:
                    run_enrichment(store, plan, areas, context)
            except StopScraping:
                try:                                        # show the user what Google displayed
                    page.screenshot(path=str(out_dir / "last_google_page.png"))
                except Exception:
                    pass
                raise
            finally:
                try:
                    context.close()
                except Exception:
                    pass
    except StopScraping as e:
        stop_reason, exit_code = str(e), 2
        if (out_dir / "last_google_page.png").exists():
            stop_reason += f" What Google showed: {(out_dir / 'last_google_page.png').resolve()}"
    except KeyboardInterrupt:
        stop_reason, exit_code = "you stopped the run (Ctrl+C).", 130
    except Exception as e:                                     # keep what was collected, show the error
        log.exception("Unexpected error")
        stop_reason, exit_code = f"unexpected error: {e}", 1

    try:
        path, rows = export(store, plan, areas, out_dir)
    except PermissionError as e:
        log.info(f"Could not write the CSV ({e}). Close it in Excel and run again.")
        sys.exit(1)
    print_summary(rows, path, stop_reason, started)
    store.close()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
