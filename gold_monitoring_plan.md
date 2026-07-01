# 금시세 일일 모니터링 자동화 계획

## 목표

매일 금 가격과 관련 신호를 한 번에 확인할 수 있는 요약 리포트를 만든다.

리포트는 아래 질문에 답해야 한다.

- 오늘 금값은 올랐나, 내렸나?
- 원화 기준 움직임은 국제 금값과 다른가?
- 최근 7일 흐름은 상승, 하락, 횡보 중 무엇인가?
- 중앙은행 금 매입/매도 흐름에 의미 있는 변화가 있는가?
- 오늘 읽을 만한 관련 뉴스 2~3개는 무엇인가?

## 기존 솔루션 사전 검토

완전히 새로 만들기 전에 다음 기존 도구를 우선 활용한다.

- 금융위원회 공공데이터포털 일반상품시세정보: KRX 금시장 금시세를 제공한다. 무료지만 서비스 키가 필요하고, 기준일 다음 영업일 오후 1시 이후 갱신된다.
- yfinance 또는 Yahoo Finance chart endpoint: 국제 금 선물(`GC=F`)과 원/달러(`KRW=X`) 확인에 쓸 수 있다. 개인 리서치 용도로 적합하며, 상업적/대규모 사용은 약관 확인이 필요하다.
- GDELT DOC API: 키 없이 최신 뉴스 검색 결과를 JSON으로 받을 수 있다. 금융 뉴스 전문 API만큼 정제되어 있지는 않지만 무료 자동화 시작점으로 충분하다.
- Google News RSS: GDELT가 제한되거나 실패할 때 fallback 뉴스 소스로 사용한다.
- World Gold Council Goldhub: 중앙은행 금 보유/매입 데이터의 기준 자료로 적합하다. 다만 다운로드/로그인이 필요할 수 있어 자동 수집보다는 CSV 파일 입력부터 시작한다.

이번 프로그램은 위 도구들을 연결하는 가벼운 커스텀 자동화로 만든다. 이유는 국내 금시세, 국제 금값, 환율, 중앙은행 데이터, 뉴스 요약을 한 번에 묶어주는 무료 단일 도구가 마땅치 않기 때문이다.

## 데이터 범위

### 매일 수집

- 국제 금 선물 가격: Yahoo Finance `GC=F`
- 원/달러 환율: Yahoo Finance `KRW=X`
- 관련 뉴스: GDELT DOC API, 실패 시 Google News RSS
- 선택 사항: KRX 금시장 가격

### 월간 또는 수동 갱신

- 중앙은행 금 보유량 또는 순매입량 CSV
- 출처 후보: World Gold Council Goldhub, IMF IFS 기반 자료

중앙은행 데이터는 매일 바뀌는 데이터가 아니므로 일일 리포트에는 "최신 월간 데이터 기준"으로 표시한다.

### 중앙은행 CSV 자동 갱신

중앙은행 데이터는 아래 방식으로 자동화한다.

- 설정 파일의 `central_bank_download.enabled`를 `true`로 켠다.
- `central_bank_download.url`에 CSV 다운로드 URL을 넣는다.
- 프로그램은 `data/source_state.json`에 마지막 체크 시각, ETag, Last-Modified, 파일 크기를 저장한다.
- 다음 실행부터는 `If-None-Match`, `If-Modified-Since` 헤더를 보내 서버가 지원하면 변경분만 확인한다.
- 기본값은 24시간에 한 번만 체크한다.
- 새 파일이 내려오면 `central_bank_csv` 입력으로 사용해 순매입/순매도 상위 국가를 다시 계산한다.

WGC Goldhub는 전체 대시보드 다운로드가 로그인 뒤에 제공될 수 있으므로, 직접 로그인 세션을 긁기보다 공식적으로 접근 가능한 CSV URL, 수동으로 받은 CSV, 또는 IMF API 기반 파일을 우선 사용한다.

## 리포트 형식

매일 `reports/gold_report_YYYY-MM-DD.md` 파일을 만든다.

포함 항목:

- 실행 시각
- 국제 금 가격 및 전일 대비 변화율
- 원/달러 환율 및 전일 대비 변화율
- 원화 환산 금 가격 추정치
- 7일 추세
- 중앙은행 순매입/순매도 상위 국가
- 뉴스 2~3개
- 간단한 해석

기계 판독용 기록은 `data/observations.jsonl`에 한 줄씩 저장한다.

## 알림 조건

초기 알림 조건은 보수적으로 둔다.

- 국제 금 가격 전일 대비 1.5% 이상 변동
- 원화 환산 금 가격 전일 대비 1.5% 이상 변동
- 최근 7일 고점 또는 저점 돌파
- 중앙은행 최신 데이터에서 특정 국가 순매입량이 5t 이상
- 관련 뉴스에 `central bank`, `Federal Reserve`, `China`, `geopolitical`, `ETF` 같은 키워드 포함

알림 전송은 이 버전에서는 하지 않고, 리포트 파일에 `Signals` 섹션으로 표시한다. 나중에 OpenClaw cron, 이메일, 메신저 알림을 붙일 수 있다.

## 실행 계획

1. 의존성 없는 파이썬 스크립트로 MVP를 만든다.
2. 국제 금값, 환율, 뉴스는 키 없이 바로 가져온다.
3. KRX와 중앙은행 데이터는 설정 파일로 확장 가능하게 만든다.
4. 중앙은행 CSV URL이 있으면 하루 한 번 조건부 다운로드한다.
5. 매일 한 번 실행해 Markdown 리포트와 JSONL 로그를 남긴다.
6. 며칠치 결과를 본 뒤 알림 임계값과 뉴스 검색어를 조정한다.

## 운영 예시

수동 실행:

```bash
python3 gold_monitor.py --config gold_monitor_config.example.json
```

크론 예시:

```cron
35 13 * * 1-5 cd /home/kjhak/.openclaw/workspace && python3 gold_monitor.py --config gold_monitor_config.json >> logs/gold_monitor.log 2>&1
```

KRX 공공데이터는 기준일 다음 영업일 오후 1시 이후 갱신되므로, 한국 시간 평일 13:35 이후 실행을 권장한다.

## 다음 확장 후보

- 공공데이터포털 서비스 키를 넣어 KRX 금시장 가격 자동 수집
- WGC/IMF CSV 다운로드 후 중앙은행 데이터 자동 갱신
- 리포트를 OpenClaw heartbeat나 cron으로 매일 전달
- 가격 차트 PNG 생성
- SQLite 저장소 추가
- Slack, Telegram, WhatsApp 알림
