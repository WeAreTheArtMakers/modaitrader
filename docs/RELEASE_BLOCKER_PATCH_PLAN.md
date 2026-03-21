# modAI Trader Release Blocker Patch Plan

Bu doküman, satış öncesi kritik riskleri doğrudan patch görevlerine dönüştürür.
Kapsam: backend + renderer + chat control + runtime logs + UAT gate.

## Release Policy

- P0 bloklayıcılar tamamlanmadan build/release alınmaz.
- Her P0 patch sonrası:
1. `bash scripts/rc_gate.sh --strict`
2. Manuel UAT (tabs/buttons/chat-control/trade flows)
3. Console error budget: **0** (network probe dahil)

## P0 Blockers

### RB-01: Unified Dashboard Snapshot Endpoint

- Risk: Renderer aynı anda çok sayıda endpoint çağırıyor; timeout/lag ve startup jitter üretiyor.
- Patch:
1. Backend’e yeni endpoint ekle: `GET /api/dashboard/snapshot`
2. Tek payload içinde döndür:
   - account summary
   - open positions
   - open orders summary
   - ledger summary/events (compact)
   - runner status
3. Endpoint’i kısa timeout + stale cache fallback ile güvenli hale getir.
- Etkilenen dosyalar:
  - `backend/api.py`
  - `frontend/src/api.ts`
  - `frontend/src/components/ProfessionalDashboard.tsx`
  - `frontend/src/components/AccountOverview.tsx`
  - `frontend/src/components/Analytics.tsx`
- Kabul kriteri:
  - Dashboard ilk yükleme tek ana çağrı ile gelir.
  - Ayrı çağrı sayısı belirgin azalır.
  - Console’da timeout hatası yok.

### RB-02: trade-history / ledger server-side TTL cache + zorunlu pagination

- Risk: büyük `limit` istekleri backend’i yavaşlatıyor; UI timeout/freeze algısı oluşuyor.
- Patch:
1. `trade-history`, `ledger/events`, `income-history` endpointlerine:
   - `page`, `page_size`, `cursor` (uygun olan) parametrelerini standartlaştır.
   - üst sınır: `page_size <= 100` (hard cap).
2. Backend-side kısa TTL cache:
   - default 5-10 sn (symbol + mode + page key’ine göre).
3. Frontend’de “load more” veya sayfalı fetch kullan.
- Etkilenen dosyalar:
  - `backend/api.py`
  - `frontend/src/api.ts`
  - `frontend/src/components/ProfessionalDashboard.tsx`
  - `frontend/src/components/AccountOverview.tsx`
  - `frontend/src/components/Analytics.tsx`
- Kabul kriteri:
  - `trade-history` ve `ledger/events` p95 < 6s.
  - Aynı sayfada tekrar çağrılar cache’den döner.
  - 200/timeout dengesinde timeout gözükmez.

### RB-03: Backend-ready Event Gating (polling başlamadan hazır kontrolü)

- Risk: Backend henüz hazır değilken polling başlıyor; `ERR_CONNECTION_REFUSED` spam oluşuyor.
- Patch:
1. Electron main process:
   - backend ready olduğunda renderer’a tek event gönder (`backend-ready`).
2. Preload:
   - güvenli event bridge (`onBackendReady`).
3. Renderer:
   - polling/scheduled fetch yalnız backend-ready sonrası başlasın.
   - ready olmadan interval kurma.
4. Port probe log seviyesini düşür (expected startup retries kullanıcıya error olarak yansımasın).
- Etkilenen dosyalar:
  - `electron/main.js`
  - `electron/preload.js`
  - `frontend/src/api.ts`
  - `frontend/src/App.tsx`
  - `frontend/src/utils/portfolioStore.ts`
- Kabul kriteri:
  - App açılışında `ERR_CONNECTION_REFUSED` yok.
  - Polling yalnız backend-ready sonrası başlar.

### RB-04: Chat Control Vision Job Queue

- Risk: Tek request içinde vision inference beklemek UI freeze/timeout hissi oluşturuyor.
- Patch:
1. Yeni queue modeli:
   - `POST /api/ai-agent/chat-control/vision/jobs` -> `job_id`
   - `GET /api/ai-agent/chat-control/vision/jobs/{job_id}` -> status/result/error
2. Job state: `queued | running | done | failed | timeout`.
3. Frontend:
   - upload sonrası immediate ACK (job created),
   - polling ile sonuç kartına düş.
   - kullanıcı mesajlaşması bloklanmasın.
4. Provider/model unavailable durumları standard error code ile dönsün.
- Etkilenen dosyalar:
  - `backend/api.py`
  - `frontend/src/api.ts`
  - `frontend/src/components/ChatControl.tsx`
- Kabul kriteri:
  - Chart yüklemede UI donmaz.
  - “Vision unavailable” ve “timeout” ayrımı net görünür.
  - Aynı session içinde normal chat ve vision paralel çalışır.

### RB-05: Runtime Logs Real-time Stream

- Risk: Kullanıcı canlı hata nedenini göremiyor; tanı süresi uzuyor.
- Patch:
1. Backend/electron log stream endpoint:
   - SSE: `GET /api/logs/stream`
   - fallback polling: `GET /api/logs/tail?cursor=...`
2. Frontend Logs tab:
   - canlı akış + level filter (`INFO/WARN/ERROR`)
   - source filter (`renderer/backend/electron`)
   - “copy diagnostic bundle” butonu.
3. Kritik action’lar için structured event log:
   - chat execute
   - close/cancel
   - trigger execution
- Etkilenen dosyalar:
  - `backend/api.py`
  - `electron/main.js`
  - `frontend/src/components/Logs.tsx`
  - `frontend/src/api.ts`
- Kabul kriteri:
  - Logs tab canlı akar.
  - Chat/trade hataları root-cause satırıyla görülebilir.

### RB-06: Pre-release UAT Console Zero-Error Gate

- Risk: Release öncesi console regressions kaçıyor.
- Patch:
1. Otomatik gate script:
   - browser/electron console capture
   - fail pattern list: `ERR_CONNECTION_REFUSED`, `404`, `405`, `500`, uncaught promise.
2. `scripts/rc_gate.sh` içine “console-clean” adımı ekle.
3. `docs/RELEASE_CANDIDATE_CHECKLIST.md` içine zorunlu imza alanı:
   - Console clean: PASS/FAIL
   - Owner + timestamp
- Etkilenen dosyalar:
  - `scripts/rc_gate.sh`
  - `scripts/release_candidate_smoke.py`
  - `docs/RELEASE_CANDIDATE_CHECKLIST.md`
- Kabul kriteri:
  - Console gate fail ise release durur.
  - “known benign” whitelist açıkça tanımlı olur.

## Patch Order (Doğrudan Uygulama Sırası)

1. RB-03 (backend-ready gating)
2. RB-01 (dashboard snapshot)
3. RB-02 (pagination + cache)
4. RB-04 (vision queue)
5. RB-05 (runtime log stream)
6. RB-06 (console clean release gate)

Not: RB-03 ilk sırada olmalı; diğer tüm test sonuçlarını daha temiz hale getirir.

## Test Matrix (Her Patch Sonrası)

1. `bash scripts/rc_gate.sh --strict`
2. Chat Control:
   - normal text plan -> approval -> execute
   - chart upload -> vision job -> done/fail akışı
3. Trade actions:
   - cancel all orders
   - close position
   - close all positions
4. Tabs:
   - Dashboard, Account, Analytics, Risk, AI Agent, Chat Control, Logs
5. Console:
   - startup 0 error
   - action flows 0 error

## Release Exit Criteria

- Tüm P0 maddeleri PASS.
- RC strict + manual UAT PASS.
- Console clean gate PASS.
- macOS + Windows + Linux smoke PASS.

