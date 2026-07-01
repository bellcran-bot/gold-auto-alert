# 금시세 일일 모니터링 자동화

이 프로그램은 매일 금시세를 확인하고 Markdown 리포트와 JSONL 기록 파일을 만드는 파이썬 자동화 도구입니다.

초기 버전은 아래 항목을 확인합니다.

- 국제 금 가격
- 원/달러 환율
- 원화 환산 금 가격 추정치
- 최근 7일 추세
- 관련 뉴스 2~3개
- 선택 사항: KRX 금시장 데이터
- 선택 사항: 중앙은행 금 보유량/순매입량 CSV

> 참고: 이 프로그램은 투자 조언이 아니라 개인 모니터링 자동화 도구입니다.

## 파일 구성

| 파일 | 설명 |
| --- | --- |
| `gold_monitor.py` | 실제로 실행하는 파이썬 프로그램 |
| `gold_monitor_config.example.json` | 설정 파일 예시 |
| `gold_monitoring_plan.md` | 자동화 설계와 운영 계획 |
| `reports/` | 매일 생성되는 Markdown 리포트 폴더 |
| `data/observations.jsonl` | 실행 결과가 누적되는 기계 판독용 기록 |
| `data/source_state.json` | CSV 다운로드 상태 저장 파일 |

## 전체 흐름

처음 사용하는 사람은 아래 순서대로 진행하면 됩니다.

1. 파이썬이 설치되어 있는지 확인한다.
2. 예시 설정 파일을 복사해서 내 설정 파일을 만든다.
3. 프로그램을 한 번 테스트 실행한다.
4. 생성된 리포트를 확인한다.
5. 필요하면 중앙은행 CSV 또는 KRX 설정을 추가한다.
6. 매일 실행되도록 cron 또는 OpenClaw 자동화를 붙인다.
7. 며칠 뒤 알림 기준과 뉴스 검색어를 조정한다.

## 1. 파이썬 확인

터미널에서 아래 명령을 실행합니다.

```bash
python3 --version
```

Python 3.10 이상을 권장합니다. 이 프로그램은 외부 패키지를 설치하지 않고 표준 라이브러리만 사용합니다.

## 2. 설정 파일 만들기

예시 설정 파일을 복사합니다.

```bash
cp gold_monitor_config.example.json gold_monitor_config.json
```

처음에는 아무것도 수정하지 않아도 실행할 수 있습니다.

기본 설정은 다음 데이터를 가져옵니다.

- Yahoo Finance chart endpoint: 국제 금 선물 `GC=F`
- Yahoo Finance chart endpoint: 원/달러 환율 `KRW=X`
- GDELT 뉴스 API
- GDELT 실패 시 Google News RSS

## 3. 테스트 실행

먼저 파일을 만들지 않고 화면에만 출력해봅니다.

```bash
python3 gold_monitor.py --config gold_monitor_config.json --dry-run
```

정상이라면 아래와 비슷한 리포트가 화면에 표시됩니다.

```text
# 금시세 일일 모니터링 - YYYY-MM-DD

- 국제 금 가격: ...
- 원/달러 환율: ...
- 원화 환산 금 가격 추정: ...
- 7일 국제 금 추세: ...
```

## 4. 실제 리포트 생성

테스트가 괜찮으면 실제 파일을 생성합니다.

```bash
python3 gold_monitor.py --config gold_monitor_config.json
```

실행 후 아래 파일이 만들어집니다.

```text
reports/gold_report_YYYY-MM-DD.md
data/observations.jsonl
```

`reports/` 안의 Markdown 파일은 사람이 읽기 좋은 일일 리포트입니다.

`data/observations.jsonl`은 나중에 차트, 통계, 데이터 분석을 만들 때 쓰기 좋은 누적 기록입니다.

## 5. 중앙은행 금 매입량 자동화

중앙은행 금 매입량은 매일 바뀌는 데이터가 아니라 보통 월간 또는 후행 데이터입니다. 따라서 매일 새 파일이 있는지 확인하되, 리포트에서는 "최신 월간 데이터 기준"으로 해석하는 것이 좋습니다.

CSV 다운로드 URL이 있다면 `gold_monitor_config.json`에서 아래 항목을 수정합니다.

```json
{
  "central_bank_csv": "data/central_bank_gold.csv",
  "central_bank_download": {
    "enabled": true,
    "url": "여기에_CSV_다운로드_URL",
    "target_path": "data/central_bank_gold.csv",
    "min_interval_hours": 24,
    "headers": {}
  },
  "central_bank_columns": {
    "date": "date",
    "country": "country",
    "tonnes": "tonnes",
    "change": ""
  }
}
```

동작 방식:

- 하루에 한 번만 CSV URL을 확인합니다.
- 서버가 지원하면 `ETag`, `Last-Modified`를 사용해 변경 여부만 확인합니다.
- 새 파일이면 `data/central_bank_gold.csv`에 저장합니다.
- 저장된 CSV를 읽어 순매입/순매도 상위 국가를 계산합니다.
- 체크 상태는 `data/source_state.json`에 저장합니다.

강제로 다시 다운로드하려면 아래 명령을 사용합니다.

```bash
python3 gold_monitor.py --config gold_monitor_config.json --force-download
```

### CSV 컬럼 설명

기본적으로 프로그램은 아래 컬럼명을 기대합니다.

| 컬럼 | 의미 | 예시 |
| --- | --- | --- |
| `date` | 기준월 또는 기준일 | `2026-04` |
| `country` | 국가명 | `China` |
| `tonnes` | 금 보유량 톤 단위 | `2300.5` |
| `change` | 순매입/순매도 톤 단위 | `8.0` |

`change` 컬럼이 없으면 프로그램은 직전 기준일과 최신 기준일의 `tonnes` 차이를 계산합니다.

CSV 컬럼명이 다르면 `central_bank_columns`에서 실제 컬럼명에 맞게 바꾸면 됩니다.

## 6. KRX 금시장 데이터 붙이기

KRX 금시장 데이터는 공공데이터포털 서비스 키와 API endpoint가 필요합니다.

설정 파일에서 `krx.enabled`를 `true`로 바꾸고, `api_url`과 필요한 파라미터를 채웁니다.

```json
{
  "krx": {
    "enabled": true,
    "api_url": "여기에_공공데이터_API_ENDPOINT",
    "service_key_env": "DATA_GO_KR_SERVICE_KEY",
    "params": {
      "resultType": "json"
    }
  }
}
```

서비스 키는 설정 파일에 직접 쓰지 말고 환경 변수로 넣는 것을 권장합니다.

```bash
export DATA_GO_KR_SERVICE_KEY="내_서비스_키"
python3 gold_monitor.py --config gold_monitor_config.json
```

## 7. 매일 자동 실행하기

한국 시간 기준 평일 오후 1시 35분 실행 예시입니다.

```cron
35 13 * * 1-5 cd /home/kjhak/.openclaw/workspace && python3 gold_monitor.py --config gold_monitor_config.json >> logs/gold_monitor.log 2>&1
```

`logs/` 폴더가 없다면 먼저 만듭니다.

```bash
mkdir -p logs
```

KRX 데이터까지 붙인다면 오후 1시 이후 실행하는 것이 좋습니다.

## 8. 매일 확인할 것

매일 리포트에서 아래 항목만 먼저 보면 됩니다.

1. `Signals`
2. 국제 금 가격 변동률
3. 원화 환산 금 가격 변동률
4. 7일 추세
5. 중앙은행 금 동향
6. 관련 뉴스 2~3개

`Signals`에 "설정된 임계값을 넘는 신호는 없습니다."라고 나오면 평소보다 크게 움직인 항목이 없다는 뜻입니다.

## 9. 설정을 바꾸는 기준

며칠 사용한 뒤 아래 항목을 조정하면 됩니다.

### 알림 기준

기본값은 1.5%입니다.

```json
"alert_threshold_pct": 1.5
```

너무 자주 신호가 뜨면 2.0으로 올립니다.

중요한 움직임을 더 빨리 보고 싶으면 1.0으로 낮춥니다.

### 뉴스 개수

기본값은 3개입니다.

```json
"news_max_records": 3
```

매일 보기에는 2개가 더 편할 수 있습니다.

### 뉴스 검색어

영문 뉴스 검색어는 아래 두 항목으로 조정합니다.

```json
"news_query": "(gold OR \"gold price\" OR \"central bank gold\" OR \"gold reserves\")",
"news_rss_query": "gold price central bank gold reserves"
```

중앙은행 중심으로 보고 싶다면 `central bank gold reserves` 비중을 높입니다.

## 10. 유지보수 체크리스트

일주일에 한 번:

- 최근 리포트가 정상 생성됐는지 확인합니다.
- 뉴스가 관련 없는 기사로 채워지지 않았는지 봅니다.
- `data/observations.jsonl`이 계속 쌓이고 있는지 확인합니다.

한 달에 한 번:

- 중앙은행 CSV가 최신 월로 갱신됐는지 확인합니다.
- 알림 임계값이 너무 민감하거나 둔하지 않은지 조정합니다.
- KRX 데이터와 국제 금 가격의 차이가 큰 날을 따로 확인합니다.

문제가 생겼을 때:

- 먼저 `--dry-run`으로 실행해 에러를 확인합니다.
- 네트워크 API가 실패해도 리포트 전체가 멈추지 않도록 설계되어 있습니다.
- 뉴스 API가 제한되면 RSS fallback을 사용합니다.
- 중앙은행 CSV 다운로드가 실패하면 마지막으로 저장된 CSV를 계속 사용할 수 있습니다.

## 자주 보는 명령어

```bash
# 화면 출력 테스트
python3 gold_monitor.py --config gold_monitor_config.json --dry-run

# 실제 리포트 생성
python3 gold_monitor.py --config gold_monitor_config.json

# 중앙은행 CSV 강제 재다운로드
python3 gold_monitor.py --config gold_monitor_config.json --force-download

# 도움말 보기
python3 gold_monitor.py --help
```

## 현재 한계

- 국제 금 가격은 Yahoo Finance chart endpoint를 사용하므로 대규모 상업적 사용에는 적합하지 않을 수 있습니다.
- 중앙은행 데이터는 공식 출처의 CSV 또는 API URL을 사용자가 제공해야 완전 자동화됩니다.
- KRX 금시장 데이터는 공공데이터포털 서비스 키와 정확한 endpoint 설정이 필요합니다.
- 뉴스 요약은 제목 중심이며, 기사 본문을 읽고 요약하지는 않습니다.

## 추천 다음 단계

1. `gold_monitor_config.example.json`을 `gold_monitor_config.json`으로 복사합니다.
2. `--dry-run`으로 한 번 실행합니다.
3. 리포트가 마음에 들면 cron으로 매일 실행합니다.
4. 중앙은행 CSV URL 또는 IMF API 연동 방식을 정합니다.
5. 며칠치 리포트를 본 뒤 알림 기준과 뉴스 검색어를 조정합니다.
