# modAI Trader Release Candidate Checklist

Bu kontrol listesi, markete çıkmadan önce `v1.0.x` adayının uçtan uca doğrulaması için hazırlanmıştır.

Release blocker patch plan referansı:
- `docs/RELEASE_BLOCKER_PATCH_PLAN.md`

## RC Gate (zorunlu)

- [ ] `python3 scripts/release_candidate_smoke.py --base-url http://127.0.0.1:<port> --strict`
- [ ] `python3 -m pytest -q backend/test_api.py backend/test_indicators.py`
- [ ] `python3 scripts/console_clean_gate.py --log-file <runtime-log>` (backend-ready sonrası 0 kritik console hatası)
- [ ] `docs/RELEASE_CANDIDATE_CHECKLIST.md` manuel UAT adımları tamamlandı

RC_UAT_SIGNOFF: PENDING
RC_UAT_DATE: YYYY-MM-DD
RC_UAT_OWNER: <name>
RC_CONSOLE_CLEAN: PENDING

## 1) Build & Boot

- [ ] Electron app açılıyor (blank screen yok).
- [ ] Backend otomatik başlıyor (`/health` OK).
- [ ] İlk açılışta lisans doğrulama ekranı doğru geliyor.
- [ ] Geçerli lisans ile tek sefer giriş sonrası tekrar lisans istemiyor.
- [ ] DevTools toggle (`Cmd/Ctrl+Shift+I`) çalışıyor.

## 2) Licensing

- [ ] Trial (5 gün) lisansı aktif edilebiliyor.
- [ ] Aylık lisans aktif edilebiliyor.
- [ ] One-time lisans aktif edilebiliyor.
- [ ] Device ID tabanlı lisans atama ve güncelleme çalışıyor.
- [ ] Trial -> Paid upgrade (About/Admin flow) çalışıyor.

## 3) Credentials & Exchange Mode

- [ ] API Credentials paneli açılıyor.
- [ ] Continue butonu çalışıyor.
- [ ] Demo/Testnet ve Mainnet seçimi net görünüyor.
- [ ] API key değiştir/güncelle akışı çalışıyor.
- [ ] Credentials status endpoint tutarlı sonuç dönüyor.

## 4) Core Tabs / Buttons

- [ ] Dashboard yükleniyor.
- [ ] Account yükleniyor.
- [ ] Analytics yükleniyor.
- [ ] Risk paneli yükleniyor.
- [ ] AI Agent yükleniyor.
- [ ] Chat Control yükleniyor.
- [ ] Market Scanner yükleniyor.
- [ ] Strategy Analyzer yükleniyor.
- [ ] Bot Control yükleniyor.
- [ ] Configuration yükleniyor.
- [ ] Templates yükleniyor.
- [ ] Logs yükleniyor.
- [ ] About yükleniyor.

## 5) Trading & Risk Guards

- [ ] Direction Lock (`BOTH / LONG_ONLY / SHORT_ONLY`) doğru uygulanıyor.
- [ ] Hard Budget Cap aktifken bütçe üstü pozisyon açılmıyor.
- [ ] Min-notional block tetiklenince teknik sebep loglanıyor.
- [ ] Spread guard tetiklenince entry bloklanıyor/downgrade ediliyor.
- [ ] Portfolio guard tetiklenince risk azaltımı çalışıyor.
- [ ] Cancel Order endpointleri Binance’e gerçek iptal isteği gönderiyor.
- [ ] Close Position endpointleri Binance’e gerçek kapanış isteği gönderiyor.

## 6) Chat Control (Advanced)

- [ ] Symbol seçmeden auto-scan analizi çalışıyor (BTC/ETH/BNB/AVAX/DOT).
- [ ] Quick Chips (BTC, ETH, Top5, 10 USDT, 1m, 5m) çalışıyor.
- [ ] Session memory: son symbol/budget/timeframe korunuyor.
- [ ] Duplicate request guard (3-5 sn) aynı promptu engelliyor.
- [ ] Response card: Signal / Risk / Reason / Next action görünüyor.
- [ ] Confidence progress bar doğru render ediliyor.
- [ ] `Apply as preset` alanları doğru dolduruyor.
- [ ] Pinned context bar (active symbols, budget cap, direction lock) güncel.
- [ ] `Bu işlem neden açılmadı?` debug cevabı tek satır teknik sebep dönüyor.
- [ ] Trigger daemon aktif: koşul tutunca otomatik pending approval üretip doğrudan execute etmiyor.
- [ ] Trigger conflict guard: aynı sembolde LONG/SHORT çakışması create/edit aşamasında bloklanıyor.
- [ ] Trigger execution policy (once/repeat/cooldown/max_daily_trades) hem daemon hem manual execute için uygulanıyor.
- [ ] Trigger backtest preview (30g) create/update proposal ekranında görünüyor.
- [ ] Vision chart upload: chart image payload backend’e gidiyor ve destekli provider/API key varsa vision summary dönüyor.

### Chat Action Flows

- [ ] `start_trade` proposal -> onay -> işlem başlatma
- [ ] `update_config` proposal -> onay -> ayar uygulama
- [ ] `close_position` proposal -> onay -> seçili pozisyon kapatma
- [ ] `close_all_positions` proposal -> onay -> tüm pozisyonları kapatma
- [ ] `cancel_symbol_orders` proposal -> onay -> sembol emir iptali
- [ ] `cancel_all_orders` proposal -> onay -> tüm emirleri iptal

## 7) Indicators & Strategies

- [ ] Teknik indikatörler hesaplanıyor (hata/log yok).
- [ ] Strategy list endpoint stabil dönüyor.
- [ ] Strategy analyzer çıktıları boş/bozuk dönmüyor.
- [ ] Chat Control strateji önerisi + strateji uygula akışı çalışıyor.

## 8) Update / Versioning

- [ ] App version ekranı `v1.0.x` ile eşleşiyor.
- [ ] Update check en son GitHub release’i gösteriyor.
- [ ] Update butonu doğru release URL’ye gidiyor.

## 9) Packaging / Platform

- [ ] `npm --prefix electron run build:mac` (macOS x64 + arm64)
- [ ] `npm --prefix electron run build:win` (Windows x64 / NSIS)
- [ ] `npm --prefix electron run build:linux` (Linux x64 + arm64, AppImage)
- [ ] Opsiyonel `.deb`: Linux runner üzerinde `npm --prefix electron run build:linux:deb`
- [ ] macOS Apple Silicon build açılıyor.
- [ ] Windows build açılıyor.
- [ ] Linux/Raspberry Pi 5 (arm64) build açılıyor.
- [ ] Backend pydeps architecture mismatch hatası yok.

## 10) Regression Log

Her RC turunda aşağıdaki tabloyu doldur:

| Date | RC Version | Scope | Result | Notes |
|---|---|---|---|---|
| YYYY-MM-DD | v1.0.x-rcN | smoke + manual | PASS/FAIL | kısa not |
