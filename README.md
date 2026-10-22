# 오닐 RS 스캐너 - 빠른 모바일 버전

이 버전은 앱 실행 시 전체 종목을 재수집하지 않습니다. GitHub Actions에서 수집한 `data/rs_latest.csv`를 읽어 표시합니다.

## 설치

1. ZIP을 풀어 `app.py`, `build_data.py`, `requirements.txt`, `.github/workflows/update.yml`를 GitHub 저장소에 올립니다. `.github` 폴더 구조를 유지해야 합니다.
2. GitHub 저장소의 Settings → Actions → General에서 Actions 실행과 `Read and write permissions`가 허용되는지 확인합니다.
3. Actions → Update RS data → Run workflow를 실행합니다.
4. 성공하면 `data/rs_latest.csv`가 저장되고 Streamlit이 자동 재배포합니다.
5. 평일 17:30 KST 자동 수집을 시도합니다. GitHub Actions 스케줄은 지연되거나 실행이 누락될 수 있습니다.

## 주의

- KRX/FinanceDataReader 원천 데이터가 모두 실패하면 첫 실행 시 데이터가 없을 수 있습니다. 성공한 기존 CSV는 유지합니다.
- RS는 3/6/9/12개월 달력일 근사 수익률의 가중 백분위이며 공식 IBD RS가 아닙니다.
- 수급은 약 12달력일 기준이며 누락된 경우 미확인 처리합니다.
- 거래량과 채널은 선택 종목 차트에서만 확인하므로 전체 추천 순위의 필터는 아닙니다.
- 공개 저장소에는 개인 파일이나 비밀키를 올리지 마세요.
