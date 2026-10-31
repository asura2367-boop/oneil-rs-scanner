# 오닐 RS 자동 매수·매도 운영

기존 `oneil_v519_live_engine.py`가 후보 선정, 실제 KIS 조회, 매수·매도 전송, Supabase 원장, 체결 복구, 대시보드 갱신을 담당한다. 기본은 OFF다. 운영자가 검토한 소스 버전과 계좌 활성화가 모두 일치해야 예약 실행에서 주문을 보낸다.

## 소유자의 최종 시작 절차

1. 본인 전용 대시보드의 **실거래 시작 절차**에서 현재 소스 확인값을 복사한다. 표시가 없거나 수집이 오래됐으면 먼저 `observe`를 실행해 갱신한다.
2. GitHub 저장소 Settings → Secrets and variables → Actions → **Variables**에서 `V519_APPROVED_RELEASE`를 만들고 그 64자리 확인값을 저장한다. 이 값은 소스 승인값이며 KIS 키나 계좌 비밀값이 아니다.
3. 대시보드의 **신규 매수 허용**을 ON으로 저장한다. OFF에서는 신규 매수만 차단하며 기존 전략 보유분의 매도 관리는 계속된다.
4. 기존 통합 Actions의 **Run workflow → operation: activate**를 선택한다. 실전 첫 주문·장애 복구 실증 상태와 기본 손절 5%/익절 10%를 읽고 소유자 확인 체크박스를 선택한 뒤 직접 실행한다. 이 실행은 주문을 보내지 않고 사전 조회·복구 확인 후 계좌 활성화만 영구 저장한다.
5. 결과가 `LIVE_ACCOUNT_ACTIVATED`이면 다음 예약 실행부터 조건에 따라 자동 매수·매도가 가능하다. 기본값 ON이나 자동 활성화는 없다. 실행 모드·소스·계좌 활성화·비상정지는 대시보드에서 구분해 확인한다.

최종 시작 조작은 계좌 소유자가 직접 수행한다. `observe`, `recover`, `broker-audit`은 활성화 뒤에도 주문을 보내지 않는다. `trade`는 소유자가 확인 체크박스를 선택해 직접 요청하는 1회 실행이다. 단순 조회를 거래 시작으로 표현하지 않는다.

## 자동 실행과 정지

- 대시보드의 **자동손절 허용** 스위치는 손절만 제어한다. 기존 저장 OFF는 손절 잠금으로 이어지며 소유자 값을 바꾸지 않는다. OFF에서는 손절 알림만 영구 기록하고 신규 매수는 독립적으로 판단한다. 익절은 스위치 ON/OFF와 관계없이 자동 매도 대상이며 익절만 발생하면 알림 전용으로 종료하지 않는다. 익절도 실거래 소스 승인·계좌 활성화·비상정지·거래시간·호가·잔고·미해결 주문 검증을 통과해야 한다. 설정 연결 자체가 불명확하면 안전 차단한다. 최종 전송 직전에는 현재 계획의 STOP_LOSS/TAKE_PROFIT 사유로 스위치를 다시 적용한다. 잠금으로 확인된 미전송과 접수 불명은 구분한다. 신규 정책 scope는 STOP_LOSS_ONLY이며 기존 엔진 기록과 구분한다.
- 손절 잠금의 알림은 같은 계좌·거래일·종목·사유별로 한 건을 영구 보관하고 마지막 관측을 갱신한다. 대시보드의 매도 시점 알림과 본인 전용 `/api/report`에만 노출하며 계좌 스냅샷과 함께 암호화한다. **명시적인 손절 잠금는 신규 매수를 막지 않는다.** 매수 ON이면 손절 알림과 별도로 새 후보를 실제 예수금·주문가능금액·저장된 자금 비율 안에서 순차 매수한다. 보유종목은 손절 잠금여도 전체 투자 노출과 중복 매수 검사에 계속 포함되며, 가상의 매도 대금으로 매수 여력을 늘리지 않는다. 자동 실행할 익절·손절 매도의 기존 우선순위, 매도 설정 확인 불가 시 안전 차단, 미체결·접수 불명 주문의 중단·복구 규칙은 유지한다. 실제 전송 직전 매도 설정을 다시 확인한다. 설정 철회로 POST 전에 중단한 경우에만 `verified_not_sent`로 기록하고 새 현재 조건에서 재평가할 수 있다. 응답 유실·네트워크 장애 주문은 이 경로를 사용하지 않고 정지·복구 확인을 유지한다.
- 이 대화 알림은 기존 정기 오류 점검에서 새 매도 시점 알림을 확인해 전달한다. 엔진의 20분 예약 주기와 대화 점검 주기, GitHub 지연 때문에 실시간·즉시 전달을 보장하지 않는다. 손절 잠금은 알림을 확인하는 동안의 가격 변동을 막지 않는다. 손절 스위치 저장은 매수·소스 승인·계좌 활성화·비상정지·자금 한도를 변경하지 않는다.
- GitHub Actions는 한국시간 09:00~19:40에 20분 간격으로 실행한다. 실제 KRX 주문은 평일 09:00 이상 15:15 미만으로 제한하며 당일 거래일·3초 이내 호가도 확인한다. 휴장일은 당일 시세 근거가 없어 차단한다. NXT 및 야간 주문은 포함하지 않는다.
- 예약 실행의 실제 시작 시각이 평일 09:00~15:15 밖이면 주문 플래그를 켜지 않고 조회·체결 확인·기록 모드로 실행한다. 지연된 장중 예약도 실제 시작 시각으로 판단하며 엔진의 전송 직전 시간 제한은 계속 유지한다. 20시 마감 경로는 그대로다.
- GitHub 예약은 지연·누락될 수 있다. 9시 정각 시작과 예약 사이의 익절·손절 반응은 보장하지 않는다. 상시 감시가 필요하면 지속 실행 서버로 옮겨야 한다.
- 20시에는 조회 마감과 상태를 기록한다. 마감은 전량 매도나 비상정지 해제가 아니다. 시간 제한으로 전송을 중단하며 활성화 설정은 다음 거래일까지 유지한다.
- 비상정지: Run workflow → `halt`. `EMERGENCY_HALTED`를 확인한다. 즉시 엔진의 신규 전송을 차단하며 이미 접수된 주문은 자동 취소하지 않는다. 재개에는 소유자의 `activate` 조작이 필요하고 예약 작업이 비상정지를 자동 해제하지 않는다.
- 위험관리 코드나 워크플로가 바뀌면 승인된 확인값과 달라져 거래가 차단된다. 새 소스를 검토해 승인값을 갱신하고 계좌를 다시 활성화해야 한다.

## 후보·자금·체결

- 거래일 한국시간 08:00은 전일 확정 일봉·수급으로 장전 후보를 만들고, 11:00·14:00은 기존 `v4b_candidate_save_auto.yml`에서 KIS의 **당일 가격·실제 누적 거래량**을 새로 조회해 RS·거래량·상승 채널을 재계산한다. 외국인·기관 수급은 최근 확정 5거래일을 유지한다. 당일 수급 추정치나 마감 예상 거래량을 만들지 않는다. RS 90·거래량 1배·채널·수급의 기존 필터는 유지하며, 장중 새로 조건을 충족한 종목이 후보에 포함될 수 있다. 매수 보장은 아니다.
- 장중 스캔 범위는 저장된 가격 이력이 253개 이상이고 직전 거래일까지 유효한 종목이다. 아직 이력이 수집되지 않은 시장 전체를 스캔했다고 표시하지 않는다. 적격 종목 모두의 KIS 응답을 확인한 뒤 순위를 계산한다. 당일 거래 근거가 없거나 거래량이 0인 종목은 제외하며, HTTP·인증·형식 오류나 조회 중 10분 초과·15:15 경계 도달은 갱신 전체를 실패 처리해 부분 조회로 후보 순위를 바꾸지 않는다. GET 일시 장애만 한 차례 재조회하고 주문은 호출하지 않는다. 당일 스냅샷은 과거 확정 일봉 CSV에 섞어 저장하지 않는다.
- 오전 후보 원본은 보존하고 장중 갱신은 실행별로 따로 보관한다. 기존 진단 파일과 Actions summary에 실제 조회 시각·장전/장중 기준·기준일·조회 범위·후보 수를 기록한다. API 오류 시 기존 공개 후보 목록을 보존하고 실패를 기록하며, 이전 목록을 새 포착 결과라고 표시하지 않는다. 장중 후보는 다음 날짜에 매수 재사용을 차단한다. 다음 날 장전 갱신이 실패하면 회복·매도 관리는 유지하고 해당 장중 신호 신규 매수는 차단한다.
- GitHub 예약은 지연·누락될 수 있다. 11시·14시는 예약 시각이며 수집 완료 후 다음 통합 엔진 실행부터 반영된다. 15:15 이후 시작하는 지연된 장중 후보 수집은 건너뛴다. 후보 갱신 자체는 주문이나 통합 엔진 재실행을 요청하지 않는다. 실제 주문은 기존 호가 신선도·계좌·원장·자금·매수 스위치·비상정지·시간 검사를 모두 통과해야 한다. 수집용 토큰은 기존 암호화 캐시로 재사용하며 평문 토큰을 로그·파일·아티팩트에 남기지 않는다. 새 후보 생성기·수집기·예약 파일도 소스 승인 확인값에 포함하므로 변경 후에는 소유자의 새 소스 승인·계좌 재활성화가 필요하다.
- 기존 V5.9 `analyze()`의 RS 90 이상·거래량·상승 채널·수급 조건을 재사용한다. 신호 날짜·형식 오류는 매수를 차단한다. 후보는 RS 높은 순, 동반 수급 우선, 종목코드 순으로 정렬한다. 이 순서가 수익률 향상을 입증했다는 뜻은 아니다.
- 기존 보유분의 익절·손절 매도를 우선 평가한다. 같은 실행에서 자금이 허용하는 후보를 순차 매수한다. 앞선 매수의 주문번호·전체 체결과 갱신된 실제 보유 잔고를 확인한 뒤 다음 후보를 평가한다. 미체결·부분 체결·잔고 반영 지연·조회 장애가 있으면 후속 매수를 중단한다. 매 주문 직전 호가·깊이·매수가능액·매수 스위치·비상정지·거래 시간을 다시 확인한다. 최초 남은 매매한도에서 이미 접수한 주문 비용을 누적 차감하며, 갱신된 예수금·전체 노출로 계산한 한도와 종목한도 중 더 작은 값을 적용한다. 실행 중 한도가 늘어도 최초 예산을 초과하지 않고 매도대금을 같은 묶음 매수의 재원으로 쓰지 않는다. 익절·손절 매도가 먼저 필요하면 그 매도를 처리하고 신규 매수 묶음은 시작하지 않는다.
- 고정 100만원을 사용하지 않는다. 기본값은 실제 예수금의 40% 안전자금, 60% 매매한도, 매매자금의 30% 종목한도다. 소유자는 본인 전용 대시보드의 자금 비율 설정에서 안전자금과 종목당 매수 비율을 0~100의 정수로 저장할 수 있다. 매매 비율은 100−안전자금 비율이다. 100% 안전자금이나 0% 종목한도는 신규 매수 금액을 0으로 만든다. 설정은 D1에 버전과 변경 이력을 영구 저장하고 동시 수정 충돌을 거부한다. 매수마다 소유자 설정 버전을 재확인하며 실행 중 바뀌면 후속 매수를 중단하고 다음 실행에서 새 값으로 다시 계산한다. 설정 조회 실패·누락·잘못된 값은 신규 매수를 차단하고 익절·손절 매도 판단은 유지한다. 설정 변경은 기존 보유분을 강제 매도하지 않는다. 저장 값과 마지막 엔진 확인 버전을 구분하며 비율 저장은 소스 승인·계좌 활성화·매수 OFF·비상정지를 변경하지 않는다. 전체 보유 노출과 실제 주문가능금액을 반영하고 매수 비용 여유 0.5%를 포함한다. 실제 수수료·세율을 뜻하지 않는다.
- 전략 원장의 누적 체결 수량, 실제 보유 수량과 매도가능수량 내에서만 매도한다. 수동 보유분은 자동 편입하지 않는다. 확정된 일부 매도 체결 뒤에는 남은 전략 수량을 별도 의도로 처리할 수 있다. 접수 불명·미해결 주문은 새 주문을 차단하며 자동 재전송하지 않는다. 같은 실행에서 정상 접수된 주문도 체결·잔고 확인을 생략하는 예외는 없다.
- 주문 의도는 Supabase에 먼저 커밋한다. 계좌 잠금·정지 행 잠금·중복 제약을 적용하고 주문번호, 거래일, 방향, 종목, 수량이 일치할 때만 체결을 반영한다. 주문번호 저장 실패나 전송 결과 유실은 정지 후 수동 확인 상태로 남긴다.
- 미체결이나 거래정지·갭·가격 제한 때문에 지정가 매도가 체결되지 않을 수 있다. 손익 기준 도달과 실제 체결은 구분한다.

## 대시보드와 비밀값

[본인 전용 대시보드](https://oneil-control-desk.asura2367.chatgpt.site)는 종목명·코드, 실제 계좌 보유·평가손익, RS 후보, 주문·체결 및 실행 기록을 표시한다. 매수 스위치는 영구 저장하고 요청 설정과 마지막 엔진 확인을 구분한다. 운영 모드는 수집된 원천 상태에 따라 표시하며 고정된 관찰 모드 문구로 대체하지 않는다.

계좌 스냅샷은 프로세스 메모리에서 RSA-OAEP/SHA256 + AES-256-GCM으로 암호화한 뒤에만 공개 저장소에 전달한다. 비밀키·소유자 정보는 Sites 서버에 보관한다. 공개 로그·아티팩트에는 원장 주문번호·계좌 금액·비밀값을 넣지 않는다. 직접/session TLS DB 연결을 사용하며 6543 트랜잭션 풀은 차단한다.

KIS 자격, `SUPABASE_DB_URL`, `DASHBOARD_SITE_ACCESS_TOKEN`은 GitHub Secrets에 등록돼 있다. [연결 확인 실행](https://github.com/asura2367-boop/oneil-rs-scanner/actions/runs/40772036901)에서 실제 KIS·Supabase·대시보드 설정 연결을 확인했다. 토큰은 소유자의 명시적 저장 승인으로 등록했으며 공개 소스에 포함하지 않는다.

## 검증 상태와 수익률 목표

개발 목표는 거래비용을 반영한 순수익과 최대낙폭을 함께 평가하는 것이다. 현재 화면의 수익률은 수수료·세금 전 평가손익이다. 입출금이 섞인 자산 추이를 전략 수익률로 표시하지 않는다. 실제 체결 단가·비용 자료와 충분한 표본이 없으므로 순수익 개선이나 최고 수익률을 입증했다고 주장하지 않는다.

[실전 조회 점검](https://github.com/asura2367-boop/oneil-rs-scanner/actions/runs/40773684884)에서 계좌와 최근 5일 이력 조회는 성공했으나 실제 주문·체결 행은 없었다. 실제 주문 접수·장애 재시작 복구 실증은 아직 완료되지 않았다. 대시보드는 실제 접수 이력, 체결 반영, 장애 복구 시험을 구분한다. 주문이 없다는 사실을 실전 검증 성공으로 처리하지 않는다. 모의계좌 절차는 사용자의 요청에 따라 진행하지 않는다.

`broker-audit`은 기존 엔진의 읽기 전용 점검이며 최근 5일 이력, 당일 호가, 주문가능금액을 확인한다. 정상 대시보드 상태를 덮어쓰지 않는다. 오류 점검과 20:20 마감 보고는 별도 예약돼 있다.

## 대시보드에서 새 버전 승인

소유자가 본인 전용 대시보드의 `버전 검토 · 승인`에서 변경 내용과 성공한 테스트를 확인하고 동의한 뒤 `새 버전 승인`을 누른다. GitHub Variables에 확인값을 복사하거나 activate를 수동 실행할 필요가 없다. 승인 API는 소유자 로그인·동일 origin을 확인하고, 최신 main의 위험관리 소스 16개를 성공한 CI의 Git blob SHA 및 릴리스 SHA-256과 대조한 뒤 D1에 CAS 방식으로 버전과 감사 기록을 저장한다. 검토 후 코드 변경·테스트 실패·확인 불가는 저장을 거부한다. 서비스 자격은 승인 POST를 대신할 수 없다.

기존 예약 워크플로의 auto 분기만 이 요청을 읽는다. 정확히 일치하는 승인 버전만 해당 실행의 환경에 적용하며 저장소 V519_APPROVED_RELEASE 변수는 변경하지 않는다. 새 계좌 활성화가 필요하면 기존 조회·체결 복구를 먼저 확인하고 활성화만 기록한다(이 실행은 주문하지 않음). 이후 거래 시간의 예약 실행이 매수·매도 스위치와 자금 한도에 따라 판단한다. 새 승인 경로는 비상정지를 해제하지 않으며 미체결·접수 불명·승인 변경은 차단한다. 기존 수동 소유자 activate 절차는 유지한다.

승인 요청 저장과 엔진의 활성화 완료는 별도 상태다. GitHub 예약은 지연될 수 있고, 거래 시간 이후 승인은 다음 거래 시간에 반영될 수 있다. 코드 수정은 계속 새 버전 승인이 필요하지만 긴 확인값·수동 워크플로 조작은 필요 없다. 공개 release_review.json에는 코드 SHA·테스트 실행 ID·소스 파일 blob SHA만 포함한다.
# RS coverage refresh

RS compares real, aligned 253-session histories ending on the same confirmed KRX session. Registered, uncollected, stale, misaligned and invalid histories are reported separately. The weighted 63/126/189/252-session formula and entry thresholds are preserved. Partial coverage is never labelled the entire market.

Collectors refresh every stale existing symbol within a 30-minute read budget, alongside up to 100 new histories per run. Incremental bars overlap old data; adjusted close changes or absent overlap require full-window refetch. Live daily candles before 16:00 are excluded using the KRX calendar. Short IPO histories are stored without padding. Unfinished work resumes oldest-first. Status distinguishes latest-session coverage from ever-collected counts.

V9/V10 share one encrypted token cache with order flags false. Current-session high-RS histories get flow refresh first (up to 250 symbols). Intraday collection uses at most eight independent HTTP sessions with globally spaced 200ms starts; token acquisition happens once. The existing complete-universe, same-session and 10-minute freshness gates remain. Any failure preserves the previous candidate output. Cumulative volume still uses confirmed 20-day daily volume as its benchmark, without projected closing volume.

Collector/workflow/dependency files are covered by the owner-reviewed source fingerprint. Approval, activation, switches, risk ratios and halt state are unchanged. Full coverage and actual KIS latency require a successful real read-only collection; fixture tests do not prove this.
# Bounded KIS read resilience

The integrated broker retries only the exact balance, daily-price and order-book GET endpoint/TR pairs. HTTP 429/500/502/503/504, transport timeouts and the explicit EGW00201 rate-limit response get at most three total attempts with 0.5s/1s waits. Pagination retries keep the same cursor and continuation header. Invalid JSON, authentication/permanent HTTP failures and business rejections remain blocking.

Cash-order POSTs and unknown endpoint/TR pairs never retry. An HTTP 500 or lost response on an order still follows existing uncertain-submission recovery; no automatic resend is added. Exhausted reads return no cached balance and retain the existing safety block. This is bounded read resilience, not proof that the upstream HTTP 500 cause is fixed.

Public diagnostics contain only a fixed API label, attempt count, HTTP status and recognized KIS error-code formats. Broker message text, response payloads, account data, credentials and URLs are excluded. Source approval, account activation, monetary limits, switches, stop-loss/target policy and emergency halt are unchanged.


## BB/Ichimoku trend policy (BB_ICHI_TREND_V1)

Entry retains RS >=90, actual volume, rising channel and positive foreign OR institution flow, and additionally requires a new close breakout above BB(20,2), above the displayed Ichimoku cloud, with conversion(9) >= base(26). Cloud spans use the values computed 26 sessions earlier (52-session span B); future bars and close-as-high/low proxies are forbidden. Intraday candidates use actual current-day high/low/price and cumulative volume; these are provisional until the close. Execution rechecks price against the saved upper band/cloud with all existing account/session/quote guards.

For bot-managed holdings only, a confirmed daily close below both BB middle and Ichimoku base adds a technical exit. If the current execution price exceeds average cost it is TAKE_PROFIT_SIGNAL; otherwise STOP_LOSS_SIGNAL obeys the owner's stop-loss lock. Percentage stop(-5% default) and profit(+10% default) retain priority and settings. A profit signal is pre-fee/tax and is not a guaranteed net profit. Missing/stale/non-session OHLC disables only the additional technical exit, never the existing percentage exits. Unknown settings still fail closed.

KIS history collection now preserves actual high/low. Legacy close-only histories are scheduled for full OHLC backfill. Until validated history and new candidates exist, old RS-only candidates cannot authorize new buys. Owner source approval and per-account activation remain required; no setting or approval is auto-changed by deployment.

Validation limitation: the pre-change stored history has no high/low, so real historical performance uplift cannot be measured yet. Formula tests and synthetic execution fixtures are correctness tests, not a profit backtest or paper account. Compare out-of-sample historical results with fees/taxes/slippage once actual OHLC backfill completes before claiming return improvement.


## Essential live requests and shared session

Automatic KIS consumers are the integrated engine, 08/11/14 candidate refresh and the single 18:30 confirmed history/flow collector. V4-A/V4-E duplicate experimental quote schedules and V10 extra automatic collection are retired; manual workflows remain available only for explicit diagnostics/backfill. Offline analysis/paper jobs do not call KIS and are not used as live evidence.

The existing token-cache workflow now provides a short reusable preparation job under `kis-shared-token-cache`. It checks the latest main ciphertext, reuses valid sessions and issues once only when absent/near expiry. One hour of validity is required before long collectors; consumers require at least five minutes. It persists ciphertext before returning only a `ready` boolean. No token is passed through job outputs, plaintext files, logs, artifacts, or approval values. Corrupt cache/key failures remain blocked, without fresh-token fallback.

Engine, candidate collector and shared price/flow collector read the latest ciphertext into their tested checkout without changing source HEAD. They never request a fresh token. Long collection jobs do not hold the short issuance lock. The emergency-halt path does not depend on token preparation. Session failures block consumers rather than causing independent auth retries. Existing live release/account activation, stop switch, unknown-order recovery, post-order balance/history verification and risk limits are unchanged. Weekend preparation sends no token request.

Live cross-workflow cache reuse still requires observation during the next weekday run; offline tests are not evidence of a production token refresh.
