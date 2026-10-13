"""
DUBAI LEADS - SETTINGS
======================
This is the only file you need to edit. Save it, then run:  python run.py

Names are not case-sensitive ("dental clinic" = "Dental Clinic").
Lists use square brackets and commas:  ["DIFC", "Business Bay"]
"""

# ════════════════════════════════════════════════════════════════════
# 1) WHAT TO SEARCH
# ════════════════════════════════════════════════════════════════════

# Personas to search - use names from PERSONAS below, or ["ALL"].
SEARCH_PERSONAS = []

# Individual categories to search - any keyword from PERSONAS below (or your own).
# Added on top of SEARCH_PERSONAS. Example: ["law firm", "PR agency"]
SEARCH_CATEGORIES = ["dental clinic"]

# Areas to search - use names from AREAS below, or ["ALL"].
SEARCH_AREAS = ["Business Bay"]

# Tip: start small (one category, one area) to check the results, then widen.
# Example of a big run:
#   SEARCH_PERSONAS = ["Healthcare", "Marketing, media & tech"]
#   SEARCH_AREAS = ["ALL"]


# ════════════════════════════════════════════════════════════════════
# 2) FILTERS
# ════════════════════════════════════════════════════════════════════

MIN_RATING = 4.0        # minimum Google star rating (0 = no minimum)
MIN_REVIEWS = 10        # minimum number of Google reviews
MAX_REVIEWS = None      # e.g. 300 to target newer, growing businesses. None = no maximum

WEBSITE = "either"      # "must_have" | "must_not_have" | "either"
PHONE = "either"        # "must_have" | "either"


# ════════════════════════════════════════════════════════════════════
# 3) CONTACT ENRICHMENT (businesses with a website)
# ════════════════════════════════════════════════════════════════════

# Visit the homepage + contact / about / team pages (robots.txt respected) to find
# the email, LinkedIn company page, Instagram, Facebook and a named decision-maker.
ENRICH_WEBSITES = True


# ════════════════════════════════════════════════════════════════════
# 4) SPEED & SAFETY (anti-bot)
# ════════════════════════════════════════════════════════════════════
# All ranges are (min, max) seconds; a random value in the range is used each time.
# Slower is safer. Delays stretch automatically when Google responds slowly or errors.

DELAY_BETWEEN_PAGES = (5, 11)       # wait before each Google Maps page load
SCROLL_PAUSE = (1.8, 4.0)           # wait between scrolls of the results list
LONG_BREAK_EVERY = (30, 50)         # take a long break after this many Google page loads...
LONG_BREAK_SECONDS = (90, 240)      # ...lasting this long

SLOW_PAGE_SECONDS = 20              # a page slower than this counts as throttling
BACKOFF_START_SECONDS = 60          # first wait after an error / throttling; doubles each time
BACKOFF_MAX_SECONDS = 1200          # longest single backoff wait (20 min)
STOP_AFTER_BLOCKS = 3               # CAPTCHA / "unusual traffic" pages in a row -> save and stop
STOP_AFTER_ERRORS = 6               # failed page loads in a row -> save and stop

SHOW_BROWSER = True                 # True = you can watch the browser window
BROWSER = "chrome"                  # "chrome" = your installed Google Chrome, on its own separate profile
                                    # (never signed in). "chromium" = the browser bundled with the scraper.


# ════════════════════════════════════════════════════════════════════
# 5) LARGE SEARCHES
# ════════════════════════════════════════════════════════════════════

# Google Maps shows at most ~120 results per search. A search returning at least
# this many is treated as cut off, and its box is split into 4 smaller boxes - unless
# the last results are all outside the box (Google pads short lists with far-away places).
CAP_THRESHOLD = 100
MAX_SPLIT_DEPTH = 4                 # how many times a box may be split (4 -> boxes 16x smaller)


# ════════════════════════════════════════════════════════════════════
# 6) OUTPUT
# ════════════════════════════════════════════════════════════════════

OUTPUT_FOLDER = "output"            # CSV, saved progress and log go here


# ════════════════════════════════════════════════════════════════════
# 7) PERSONAS -> GOOGLE MAPS CATEGORIES
# ════════════════════════════════════════════════════════════════════
# Add, remove or rename freely. Each keyword is searched as typed.

PERSONAS = {
    "Healthcare": [
        "dental clinic", "orthodontist", "dermatology clinic", "aesthetic clinic",
        "plastic surgery clinic", "medical center", "polyclinic", "physiotherapy clinic",
        "eye clinic", "IVF fertility clinic", "diagnostic lab", "veterinary clinic",
        "pharmacy",
    ],
    "Marketing, media & tech": [
        "marketing agency", "digital marketing agency", "advertising agency",
        "branding agency", "PR agency", "social media agency", "web design company",
        "software company", "app development company", "IT services company",
        "video production company", "event management company",
    ],
    "Professional services": [
        "law firm", "legal consultant", "accounting firm", "audit firm", "tax consultant",
        "management consultancy", "business setup consultant", "financial advisor",
        "wealth management firm", "investment company", "family office",
        "insurance broker", "recruitment agency", "architecture firm",
        "interior design company", "engineering consultancy",
    ],
    "Business owners & entrepreneurs": [
        "general trading company", "import export company", "logistics company",
        "contracting company", "fit-out company", "luxury car dealer", "jewelry store",
        "luxury boutique", "fine dining restaurant", "specialty coffee shop",
        "beauty salon", "spa", "fitness studio", "private nursery", "training center",
        "yacht charter", "travel agency",
    ],
}


# ════════════════════════════════════════════════════════════════════
# 8) AREAS
# ════════════════════════════════════════════════════════════════════
# Each area is a box:  (south_lat, north_lat, west_lng, east_lng)
# or a circle:         (center_lat, center_lng, radius_km)
# Boxes below come from Dubai Municipality / OpenStreetMap community boundaries.
# To get coordinates yourself: right-click a spot in Google Maps; the first line is lat, lng.

AREAS = {
    "DIFC":                             (25.2005, 25.2175, 55.2715, 55.2855),
    "Downtown Dubai":                   (25.1852, 25.2049, 55.2645, 55.2890),
    "Business Bay":                     (25.1684, 25.1964, 55.2496, 55.2941),
    "Trade Centre / Sheikh Zayed Road": (25.2012, 25.2312, 55.2690, 55.2924),
    "Dubai Healthcare City":            (25.2286, 25.2351, 55.3170, 55.3273),
    "Dubai Internet City":              (25.0869, 25.1043, 55.1516, 55.1734),
    "Dubai Media City":                 (25.0880, 25.0990, 55.1450, 55.1585),
    "Dubai Knowledge Park":             (25.0990, 25.1060, 55.1590, 55.1690),
    "JLT":                              (25.0605, 25.0841, 55.1323, 55.1583),
    "Dubai Marina":                     (25.0643, 25.0950, 55.1237, 55.1551),
    "JBR":                              (25.0718, 25.0837, 55.1283, 55.1414),
    "Palm Jumeirah":                    (25.0966, 25.1420, 55.1081, 55.1627),
    "Al Barsha":                        (25.0769, 25.1231, 55.1769, 55.2330),
    "Al Sufouh":                        (25.0869, 25.1369, 55.1480, 55.2020),
    "Jumeirah":                         (25.1696, 25.2511, 55.2165, 55.2725),
    "Umm Suqeim":                       (25.1280, 25.1916, 55.1824, 55.2277),
    "Al Wasl":                          (25.1801, 25.2121, 55.2399, 55.2709),
    "Al Safa":                          (25.1493, 25.1869, 55.2159, 55.2494),
    "City Walk":                        (25.1993, 25.2121, 55.2564, 55.2690),
    "Dubai Design District":            (25.1807, 25.1988, 55.2924, 55.3151),
    "Al Quoz":                          (25.1024, 25.1802, 55.2020, 55.2675),
    "Al Karama":                        (25.2347, 25.2548, 55.2954, 55.3142),
    "Oud Metha":                        (25.2287, 25.2478, 55.3050, 55.3174),
    "Bur Dubai":                        (25.2397, 25.2726, 55.2804, 55.3107),
    "Deira":                            (25.2347, 25.3279, 55.2921, 55.3624),
    "Al Garhoud":                       (25.2300, 25.2561, 55.3365, 55.3690),
    "Al Qusais":                        (25.2517, 25.3025, 55.3605, 55.4278),   # incl. industrial areas
    "Al Nahda":                         (25.2782, 25.2994, 55.3557, 55.3884),   # Dubai side only
    "Mirdif":                           (25.2070, 25.2352, 55.4028, 55.4424),
    "Dubai Silicon Oasis":              (25.1034, 25.1456, 55.3649, 55.4082),
    "Dubai Hills":                      (25.0850, 25.1250, 55.2330, 55.2750),
    "Arabian Ranches":                  (25.0273, 25.0659, 55.2494, 55.3012),   # Ranches 1 + 2
    "Motor City":                       (25.0345, 25.0560, 55.2235, 55.2518),
    "JVC":                              (25.0433, 25.0769, 55.1951, 55.2235),
    "Dubai Investment Park":            (24.9538, 25.0173, 55.1423, 55.2408),
    "Jebel Ali":                        (24.9196, 25.0234, 55.0191, 55.1582),
    "Dubai South":                      (24.8260, 24.9750, 55.0722, 55.2344),
    "Meydan":                           (25.1404, 25.1850, 55.2766, 55.3395),
}


# ════════════════════════════════════════════════════════════════════
# 9) EXCLUSIONS (competitors and government)
# ════════════════════════════════════════════════════════════════════
# A business is skipped if its Google category contains any of these words...
EXCLUDE_CATEGORY_KEYWORDS = [
    "real estate", "realtor", "estate agent", "property management", "property developer",
    "property investment", "housing development", "homeowners association",
    "government", "embassy", "consulate", "police", "courthouse", "city hall",
    "municipal", "ministry", "public prosecutor", "registry office", "homeowners",
]

# ...or its name contains any of these words/phrases (whole words only).
EXCLUDE_NAME_KEYWORDS = [
    "real estate", "realestate", "realty", "properties", "developments", "estates",
    "government", "ministry", "municipality", "police", "authority",
    "department of", "general directorate", "embassy", "consulate",
]

# Website domains that are platforms, not the business's own site (not visited).
SKIP_WEBSITE_DOMAINS = [
    "instagram.com", "facebook.com", "fb.com", "linkedin.com", "twitter.com", "x.com",
    "tiktok.com", "youtube.com", "wa.me", "whatsapp.com", "linktr.ee", "google.com",
    "goo.gl", "business.site", "practo.com", "okadoc.com", "fresha.com", "booksy.com",
    "vagaro.com", "setmore.com", "calendly.com", "talabat.com", "deliveroo.ae",
    "zomato.com", "tripadvisor.com", "booking.com", "bayut.com", "propertyfinder.ae",
    "dubizzle.com", "yellowpages.ae", "connect.ae",
]
