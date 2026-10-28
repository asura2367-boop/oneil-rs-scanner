#!/usr/bin/env python3
"""Venue-specific session diagnostics. Time windows are NOT order authorization."""
import datetime as dt
from zoneinfo import ZoneInfo
KST=ZoneInfo("Asia/Seoul")

def phase(venue, now, *, business_day_confirmed=False):
    if now.tzinfo is None: raise ValueError("AWARE_CLOCK_REQUIRED")
    t=now.astimezone(KST)
    if t.weekday() >= 5: return "CLOSED_OR_UNVERIFIED"
    mins=t.hour*60+t.minute+t.second/60
    if venue=="KRX":
        if 540 <= mins < 930: return "REGULAR_WINDOW_ONLY"
        if 960 <= mins < 1200: return "EVENING_WINDOW_ONLY"
    elif venue=="NXT":
        if 480 <= mins < 530: return "PREMARKET_WINDOW_ONLY"
        if 540.5 <= mins < 920: return "REGULAR_WINDOW_ONLY"
        if 940 <= mins < 1200: return "EVENING_WINDOW_ONLY"
    else:
        return "UNKNOWN_VENUE"
    return "CLOSED_OR_UNVERIFIED"

def safety_status(venue, now, *, business_day_confirmed=False, ticker_eligible=False,
                  executable_book_attested=False, buying_power_venue_verified=False):
    observed=phase(venue,now,business_day_confirmed=business_day_confirmed)
    issues=[]
    if observed in ("CLOSED_OR_UNVERIFIED","UNKNOWN_VENUE"): issues.append("SESSION_WINDOW_NOT_SUPPORTED")
    if not business_day_confirmed: issues.append("EXCHANGE_BUSINESS_DAY_UNVERIFIED")
    if not ticker_eligible: issues.append("VENUE_TICKER_ELIGIBILITY_UNVERIFIED")
    if not executable_book_attested: issues.append("EXECUTABLE_PRICE_UNATTESTED")
    if not buying_power_venue_verified: issues.append("VENUE_SPECIFIC_BUYING_POWER_UNVERIFIED")
    issues.append("LIVE_ORDER_TRANSMISSION_DISABLED")
    return {"venue":venue,"phase":observed,"gate":"DENY","block_codes":issues,
            "LIVE_ENABLED":False,"KIS_ALLOW_ORDER":False,"orders_sent":0}
