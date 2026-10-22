오닐 RS 전체시장 자동 수집 개선판

1. build_data.py 를 GitHub 저장소 최상위 기존 파일과 교체합니다.
2. .github/workflows/update.yml 을 동봉 update.yml 내용으로 교체합니다.
3. KIS_APP_KEY 및 KIS_APP_SECRET 은 GitHub Actions Secrets에만 보관합니다.
4. GitHub Actions의 Update RS data를 한 번 수동 실행하여 로그를 확인합니다.
5. data/collection_status.json 의 collected_symbols / remaining_symbols 를 확인합니다.

기존 data/kis_daily_universe.csv, universe_test.csv, 30종목 CSV, app.py 는 변경하지 않습니다.
평일 한국시간 10:20, 14:20, 18:20 예약 실행(UTC 01:20, 05:20, 09:20).
예약 실행은 GitHub 서비스 사정에 따라 지연/누락될 수 있습니다.
한 실행당 최대 20종목으로 시작하며, 전체 시장 최초 구축은 수주 이상 걸릴 수 있습니다.
한국투자증권 API 호출 한도 및 토큰 발급 제한은 별도로 적용됩니다.
기존에 확보한 일봉은 보존하지만 데이터 제공 오류로 새 데이터가 생성되지 않을 수 있습니다.
토큰이나 API 비밀키는 CSV/로그에 저장하지 않습니다.

주의: 현재 앱은 30종목 RS 시험판이며, 전체시장 RS는 아직 아닙니다.
