# 전체시장 종목 목록 연결 테스트

기존 앱과 데이터 수집 프로그램은 변경하지 않습니다.

GitHub 저장소 최상위에 `build_universe.py`를 업로드하고, 기존 GitHub Actions에서 새 작업을 따로 만들거나 Actions의 임시 테스트 워크플로를 이용해 실행합니다.

```bash
pip install -r requirements.txt
python build_universe.py
```

성공 시 `data/universe_test.csv`가 생성됩니다. FinanceDataReader의 종목 목록 제공처에 따라 실패할 수 있으며, 실패 시 기존 데이터를 덮어쓰지 않습니다. 이 파일은 종목 목록만 저장하며 RS 계산을 하지 않습니다.
