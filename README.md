# 멍청한 돈 (고수 vs 하수) — stupidmoney.io 한국어판

하이퍼리퀴드 공개 데이터로 누적 손익 상위(고수)·하위(하수) 트레이더가 지금 어디에 롱·숏을 걸었는지 보여주는 사이트입니다.

- 사이트: https://brianbrian060727-hash.github.io/gosu-hasu/
- 포지션 집계: GitHub Actions가 30분마다 코인(기본 시장)·주식/원자재(xyz)·비상장(io)을 서버 3대로 동시에 수집해 다시 배포합니다(실제 간격 30분~1시간).
- 가격·체결 알림: 브라우저가 하이퍼리퀴드 웹소켓에 직접 연결해 실시간으로 받습니다.

## 등급 (누적 손익 기준)

| 등급 | 기준 |
|---|---|
| 초고수 | $2M 이상 |
| 고수 | $200K ~ $2M |
| 하수 | −$100K ~ −$1M |
| 호구 | −$1M 이하 |

## 파일

- `site/index.html` — 사이트 전체 (한 파일)
- `build.py` — 수집기(표준 라이브러리만). 시장별 `fetch` → `merge`. 로컬에서 한 번에: `python build.py all 출력폴더 [--prev 직전traders.json]`
- `.github/workflows/update.yml` — 30분 자동 수집·배포

## 운영 메모

- 수동 갱신: Actions 탭 → "데이터 수집 및 배포" → Run workflow
- 공개 저장소에 60일간 커밋이 없으면 GitHub가 예약 실행을 멈춥니다. 메일이 오면 Actions 탭에서 다시 켜면 됩니다.
- 투자 조언이 아닙니다. 원작 아이디어: https://www.stupidmoney.io
