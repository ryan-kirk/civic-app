"""
Dallas County, Iowa assessor enrichment (address -> assessed value) for zoning workflows.

Reality check:
- Dallas County Assessor points "Property Search" to Beacon (Schneider). :contentReference[oaicite:2]{index=2}
- Beacon can be JS-heavy and may restrict automated access (paywall / bot detection).
- This script uses Playwright and is designed to be *selector-tunable*.

Run:
  pip install playwright pydantic
  playwright install chromium
  python dallas_assessor_enrich.py "1234 Example St, Waukee, IA"
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from typing import Optional, Dict, Any

from pydantic import BaseModel, Field
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


# ----------------------------
# Domain objects
# ----------------------------

class ZoningEvent(BaseModel):
    address: str
    city: Optional[str] = None
    county: str = "Dallas"
    state: str = "IA"
    event_type: str = Field(default="rezoning")


class ParcelEnrichment(BaseModel):
    parcel_id: Optional[str] = None
    owner_name: Optional[str] = None
    assessed_total: Optional[int] = None
    assessed_land: Optional[int] = None
    assessed_improvements: Optional[int] = None
    source: str
    source_url: Optional[str] = None
    confidence: float = 0.2
    raw_fields: Dict[str, Any] = Field(default_factory=dict)


# ----------------------------
# Helpers
# ----------------------------

_MONEY_RE = re.compile(r"\$?\s*([0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)")

def parse_money_int(s: str) -> Optional[int]:
    if not s:
        return None
    m = _MONEY_RE.search(s)
    if not m:
        return None
    return int(m.group(1).replace(",", ""))

def norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


# ----------------------------
# Beacon enrichment
# ----------------------------

@dataclass
class DallasBeaconConfig:
    # Start from the county Assessor page and click through to Beacon:
    assessor_landing_url: str = "https://www.dallascountyiowa.gov/158/Assessor"
    # If you discover the Dallas County Beacon "site" URL in your browser, you can set it here:
    # beacon_direct_url: str = "https://beacon.schneidercorp.com/..."
    beacon_direct_url: Optional[str] = None

    # Choose headless=False for debugging selectors.
    headless: bool = True
    timeout_ms: int = 45_000


def enrich_from_beacon(event: ZoningEvent, cfg: DallasBeaconConfig) -> ParcelEnrichment:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=cfg.headless)
        context = browser.new_context()
        page = context.new_page()
        page.set_default_timeout(cfg.timeout_ms)

        try:
            # 1) Navigate to Beacon
            if cfg.beacon_direct_url:
                page.goto(cfg.beacon_direct_url, wait_until="domcontentloaded")
            else:
                page.goto(cfg.assessor_landing_url, wait_until="domcontentloaded")

                # Dallas County assessor page links out to Beacon. :contentReference[oaicite:3]{index=3}
                # Click the first Beacon link on the page.
                beacon_link = page.locator("a[href*='beacon.schneidercorp.com']").first
                beacon_link.click()
                page.wait_for_load_state("domcontentloaded")

            beacon_entry_url = page.url

            # 2) Go to Beacon Search page (often /Search) if needed
            # Many Beacon installs have a Search page. :contentReference[oaicite:4]{index=4}
            if "Search" not in page.url and page.locator("a:has-text('Search')").count() > 0:
                page.locator("a:has-text('Search')").first.click()
                page.wait_for_load_state("domcontentloaded")

            # 3) Select "Search by address" if present
            if page.locator("text=Search by address").count() > 0:
                page.locator("text=Search by address").first.click()

            # 4) Fill address input(s)
            # Beacon UIs vary. Common: a single search box.
            # If you find the right selector, replace this with a more specific one.
            search_box = (
                page.locator("input[placeholder*='address' i]").first
                if page.locator("input[placeholder*='address' i]").count() > 0
                else page.locator("input[type='text']").first
            )
            search_box.fill(event.address)

            # 5) Click Search
            if page.locator("button:has-text('Search')").count() > 0:
                page.locator("button:has-text('Search')").first.click()
            elif page.locator("input[type='submit'][value*='Search' i]").count() > 0:
                page.locator("input[type='submit'][value*='Search' i]").first.click()
            else:
                # Sometimes Enter works
                search_box.press("Enter")

            page.wait_for_load_state("networkidle")

            # 6) Click first result / open details
            # Tune this once you see the results page DOM.
            candidates = [
                "a:has-text('Details')",
                "a:has-text('View')",
                "a[href*='Parcel']",
                "a[href*='Detail']",
                "table a",
            ]
            clicked = False
            for sel in candidates:
                loc = page.locator(sel)
                if loc.count() > 0:
                    loc.first.click()
                    clicked = True
                    break

            if clicked:
                page.wait_for_load_state("domcontentloaded")

            detail_url = page.url
            body_text = norm(page.locator("body").inner_text())

            # 7) Extract assessed value fields (heuristic text-based)
            # Best practice is label/value DOM parsing; this keeps you moving today.
            def find_after(label: str) -> Optional[int]:
                idx = body_text.lower().find(label.lower())
                if idx < 0:
                    return None
                window = body_text[idx : idx + 250]
                return parse_money_int(window)

            assessed_total = find_after("Assessed")
            assessed_land = find_after("Land")
            assessed_impr = find_after("Improvement")

            # Parcel ID & Owner (heuristic)
            parcel_id = None
            owner_name = None

            for line in body_text.split(" "):
                # weak, but sometimes parcel IDs are long numeric tokens
                if len(line) >= 8 and line.isdigit():
                    parcel_id = line
                    break

            # Owner labels vary; you’ll likely replace with a real selector.
            m_owner = re.search(r"(Owner|Owner Name)\s*[:\-]\s*([A-Z0-9 &',.\-]{3,})", body_text, re.IGNORECASE)
            if m_owner:
                owner_name = norm(m_owner.group(2))

            confidence = 0.7 if assessed_total else 0.3

            return ParcelEnrichment(
                parcel_id=parcel_id,
                owner_name=owner_name,
                assessed_total=assessed_total,
                assessed_land=assessed_land,
                assessed_improvements=assessed_impr,
                source="dallas_beacon",
                source_url=detail_url,
                confidence=confidence,
                raw_fields={"beacon_entry_url": beacon_entry_url},
            )

        except PlaywrightTimeoutError as e:
            return ParcelEnrichment(
                source="dallas_beacon",
                source_url=page.url if page else None,
                confidence=0.1,
                raw_fields={"error": "Timeout (likely selector mismatch / blocked access)", "detail": str(e)},
            )
        except Exception as e:
            return ParcelEnrichment(
                source="dallas_beacon",
                source_url=page.url if page else None,
                confidence=0.1,
                raw_fields={"error": "Unexpected error", "detail": repr(e)},
            )
        finally:
            try:
                context.close()
            except Exception:
                pass
            try:
                browser.close()
            except Exception:
                pass


def main():
    if len(sys.argv) < 2:
        print("Usage: python dallas_assessor_enrich.py \"123 Main St, Waukee, IA\"")
        raise SystemExit(2)

    address = sys.argv[1]
    event = ZoningEvent(address=address, city=None)

    # Debug mode tip: set headless=False to watch the browser and tune selectors quickly.
    cfg = DallasBeaconConfig(headless=False)

    enrichment = enrich_from_beacon(event, cfg)
    print(enrichment.model_dump_json(indent=2))


if __name__ == "__main__":
    main()