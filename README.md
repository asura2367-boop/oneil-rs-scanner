# KIS 1년 일봉 검증
기존 Streamlit 앱의 `app.py`와 `requirements.txt`를 교체합니다.
Streamlit Secrets의 `KIS_APP_KEY`, `KIS_APP_SECRET`은 기존 값을 유지합니다.
세 종목의 약 1년 일봉을 80달력일 단위로 분할 조회합니다.
API 조회 성공 여부는 배포 후 확인해야 합니다.
전체시장 RS 등급을 계산하는 버전은 아닙니다.
