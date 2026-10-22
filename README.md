# 오닐 RS 스캐너 V4
기존 V3의 저장된 CSV와 GitHub 진행률을 읽는 구조를 유지하고 모바일 화면만 압축했습니다.
- 기존 저장소 최상위 app.py 교체
- requirements.txt는 변경 불필요 (참고용 포함)
- build_data.py, update.yml, GitHub Secrets, data/ 파일 변경 금지
- 외국인/기관 수급은 미연동이며 1~99는 수집 표본 내 시험 RS입니다.
- 조회 오류 시 GitHub 원본 CSV 및 Streamlit 로그 확인
