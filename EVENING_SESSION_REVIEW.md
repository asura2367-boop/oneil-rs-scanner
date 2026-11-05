# KRX evening session review

This change extends the existing engine, not its owner authorization. Source approval,
account activation, emergency stop, buy/sell controls, exposure limits, durable order
reservation and unknown-order recovery remain mandatory. No orders or account
activation were performed to implement or test this change.

## Implemented sessions

- KRX continuous regular trading: weekdays 09:00 inclusive to 15:20 exclusive, KST.
- KRX after-market: weekdays 16:00 inclusive to 20:00 exclusive, effective 2026-09-14.
- 15:20–16:00 auctions/closing-price sessions are excluded. NXT is not a fallback.
- Regular limit orders use ORD_DVSN 00. KRX after-market limit orders use 41 with
  EXCG_ID_DVSN_CD KRX. No market orders, IOC/FOK, auction orders or price guesses.
- Read-only buying-power requests use the same order code; rejection of 41 never
  falls back to 00. Insufficient or unverifiable evidence sends no order.
- After-market eligibility requires identified KOSPI/KOSDAQ stock metadata, no
  trading halt, ordinary eligible status, non-auction order-book evidence and no VI.
  ETF/ETN/ELW, KONEX and unknown classifications are rejected.
- Every order uses a current-day KRX bid or ask with positive available depth,
  at most three seconds old, a bounded spread, and a matching session/order code.
  Final transmission checks the session again. Holidays cannot pass without a
  current-day bar and fresh book. A clock window alone is never sufficient.
- Manual requests retain the 40-minute maximum, expire no later than the current
  session close, and cannot cross the exchange break or carry into tomorrow.
- Existing unresolved regular-session orders still block evening orders. Missing
  history is not assumed to mean cancelled. No automatic order retries were added.

The existing GitHub schedule runs every 20 minutes from 09:00 through 19:40 with a
20:00 close. GitHub delays and gaps remain possible; this is not continuous or
instantaneous monitoring. The close stops new submissions, not a forced liquidation.

## Candidate strategy and read-only activation

The existing 08:00 / 11:00 / 14:00 RS candidate process is unchanged. Evening entries
use that day's ranked strategy signals, with their original observation timestamps,
plus fresh execution evidence. They are not represented as newly scanned evening RS
signals. Prior-date candidates are rejected for evening entry. No new price or volume
estimate, evening rescan or strategy threshold was introduced.

Owner source approval can be applied during the weekday 09:00–20:00 monitoring
window, including the exchange break. This reconciles and records approval only;
actual orders separately require an execution session and all existing safeguards.

## Operational evidence still required

The official September 2026 order schema explicitly documents after-limit code 41.
The older public book schema documents new_mkop_cls_code 20 as continuous-session
ordinary trading, but does not explicitly document its September after-market use.
The implementation conservatively accepts only 20 plus a current after-market time,
correct instrument and inactive VI. Unknown or auction codes fail closed.

The buying-power schema accepts an intended order type but its older enumeration
omits 41. A read-only `broker-audit` during 16:00–20:00 must confirm current broker
book evidence and buying-power acceptance for an eligible stock. Its report separates
fresh-book, buying-power and after-market-path checks from real order/fill/restart
proof. Offline fixtures cannot establish those live broker facts. Audit failure is
not permission to loosen evidence or try an order. Owner approval is required again
for the changed release; this change does not activate trading.

## Authoritative sources

- KIS September 8 notice, KRX/NXT session changes effective September 14:
  https://www.truefriend.com/main/customer/notice/Notice.jsp?cmd=TF04ga000002&num=47532
- KIS order-cash schema (updated 2026-09-14):
  https://apiportal.koreainvestment.com/api/apis/guide/property/aade4c72-5fb7-418a-9ff2-254b4d5f0ceb
- Instrument metadata and eligibility:
  https://apiportal.koreainvestment.com/api/apis/guide/property/da0485b5-31f1-4c62-a3d4-294a4311ad39
- Current-price status classifications:
  https://apiportal.koreainvestment.com/api/apis/guide/property/07802512-4f49-4486-91b4-1050b6f5dc9d
- REST book schema:
  https://apiportal.koreainvestment.com/api/apis/guide/property/af3d3794-92c0-4f3b-8041-4ca4ddcda5de
- VI state schema:
  https://apiportal.koreainvestment.com/api/apis/guide/property/f54caf9c-65db-42ee-aa7e-a337c5a41778
- Read-only buying power schema:
  https://apiportal.koreainvestment.com/api/apis/guide/property/806e407c-3082-44c0-9d71-e8534db5ad54
- Order/fill history and cancelable-order schemas:
  https://apiportal.koreainvestment.com/api/apis/guide/property/bc51f9f7-146f-4971-a5ae-ebd574acec12
  https://apiportal.koreainvestment.com/api/apis/guide/property/d4537e9c-73f7-414c-9fb0-4eae3bc397d0
