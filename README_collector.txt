전체시장 분할 수집 (KIS 실전 API, 조회 전용)

1. build_data.py 를 GitHub 저장소 최상위의 같은 이름 파일로 교체.
2. 기존 data/universe_test.csv, data/kis_daily_30stocks.csv, .github/workflows/update.yml 유지.
3. GitHub Actions -> Update RS data -> Run workflow 1회 실행.
4. 성공 시 data/kis_daily_universe.csv 확인.

기본 1회 최대 20종목 수집. KIS_BATCH_SIZE 환경변수로 1~50 설정 가능.
이미 수집된 종목의 마지막 일봉이 3일 이내면 재수집 생략.
초기 실행 시 기존 30종목 CSV를 시드로 사용.
전체시장 RS 등급은 전체 종목 수집 완료 및 품질 검증 전에는 산출/표시하지 않음.

주의: GitHub Actions의 예약 실행은 한 번에 최대 20종목만 수집하므로 2,675종목 완료에는 여러 실행이 필요합니다.
토큰 발급 403은 서버 정책에 따라 다시 발생할 수 있습니다. 재실행을 연속 반복하지 마세요.
GitHub Secrets의 KIS_APP_KEY / KIS_APP_SECRET을 사용하며 주문 기능은 없습니다.
