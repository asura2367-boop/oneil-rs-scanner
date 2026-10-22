오닐 RS V10 수집기

기존 GitHub 저장소 최상위의 build_data.py, build_investor.py 두 파일만 교체합니다.
app.py, .github/workflows/update.yml, investor.yml, data/ CSV, Secrets는 그대로 둡니다.

일봉: KIS_BATCH_SIZE=50(기본값 20인 기존 코드 설정은 워크플로 환경변수로 50을 명시하는 것이 안전), KIS_REFRESH_BATCH_SIZE=10
수급: KIS_FLOW_BATCH_SIZE=20, KIS_FLOW_REFRESH_BATCH_SIZE=5

주의: 현재 update.yml에서 KIS_BATCH_SIZE를 50으로 지정하지 않으면 build_data.py의 기본값 20이 적용될 수 있습니다.
실행 전 update.yml의 env: 에 KIS_BATCH_SIZE: '50'이 있는지 확인하세요.

신규/갱신은 각각 최대 목표값이며 갱신 대상이 부족하면 남는 슬롯을 신규 수집에 배정합니다.
기존 수집일이 최신 주가일보다 오래된 종목을 갱신합니다. 모든 종목이 최신이면 갱신 0건이 정상입니다.
실제 API 테스트는 하지 않았으며 실행당 토큰 발급 1회 방식은 유지됩니다.
