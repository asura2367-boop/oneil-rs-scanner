# KIS 30종목 분할 수집 테스트

기존 `build_data.py`만 이 파일로 교체합니다. `update.yml`, `app.py`, Secrets는 그대로 둡니다.

GitHub Actions → Update RS data → Run workflow를 실행하면 한 번에 최대 5종목의 약 1년치 일봉을 수집해 `data/kis_daily_30stocks.csv`에 누적 저장합니다. 정상적으로 진행되면 최소 6회 실행이 필요합니다. 실패한 종목은 다음 실행에서 재시도합니다.

**주의**: 30종목은 수동 선정한 시험 목록이며 코스피·코스닥 전체시장 RS가 아닙니다. 이 버전은 데이터 수집 코드만 교체하며 기존 Streamlit 앱 화면은 바꾸지 않습니다. GitHub Actions는 기존 `data/` 커밋 단계가 있어야 누적 저장됩니다. 기존 3종목 CSV는 삭제하지 않습니다.

실전 API Key와 Secret은 GitHub Actions Secrets에만 저장합니다. 매매 주문은 없습니다.
