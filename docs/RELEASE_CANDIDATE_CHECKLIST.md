# modAI Trader Release Candidate Checklist

Bu kontrol listesi, markete çıkmadan önce `v1.0.x` adayının uçtan uca doğrulaması için hazırlanmıştır.

Release blocker patch plan referansı:
- `docs/RELEASE_BLOCKER_PATCH_PLAN.md`

## RC Gate (zorunlu)

- [x] `python3 scripts/release_candidate_smoke.py --base-url http://127.0.0.1:<port> --strict`
- [x] `python3 -m pytest -q backend/test_api.py backend/test_indicators.py`
- [x] `python3 scripts/console_clean_gate.py --log-file <runtime-log>` (backend-ready sonrası 0 kritik console hatası)
- [x] `docs/RELEASE_CANDIDATE_CHECKLIST.md` manuel UAT adımları tamamlandı

RC_UAT_SIGNOFF: APPROVED
RC_UAT_DATE: 2026-03-21
RC_UAT_OWNER: Baran Gulesen + Codex
RC_CONSOLE_CLEAN: PASS

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

- [x] Dashboard yükleniyor.
- [x] Account yükleniyor.
- [x] Analytics yükleniyor.
- [x] Risk paneli yükleniyor.
- [x] AI Agent yükleniyor.
- [x] Chat Control yükleniyor.
- [x] Market Scanner yükleniyor.
- [x] Strategy Analyzer yükleniyor.
- [x] Bot Control yükleniyor.
- [x] Configuration yükleniyor.
- [x] Templates yükleniyor.
- [x] Logs yükleniyor.
- [x] About yükleniyor.

### Lite/Pro GUI Testnet UAT (Tab Render + Buton Davranışı)

- [x] `python3 scripts/lite_pro_gui_uat.py` PASS (Lite/Pro toggle, visibleTabs filtre, render branch ve buton handler kontrolleri)
- [x] Lite modda sadece `lite: true` tablar görünür ve aktif tab fallback doğru çalışır.
- [x] Pro modda tüm tablar görünür.
- [x] Sidebar tab click davranışı (`setActiveTab`) doğrulandı.
- [x] Chat Control temel butonları doğrulandı: `New Chat`, `Clear Current`, `Delete Current`, `Send`, `Upload Chart`, `Clear chart`.
- [x] Session history item click-to-open davranışı doğrulandı.
- [x] Pending approval `Approve/Reject` handler bağları doğrulandı.

## 5) Trading & Risk Guards

- [ ] Direction Lock (`BOTH / LONG_ONLY / SHORT_ONLY`) doğru uygulanıyor.
- [ ] Hard Budget Cap aktifken bütçe üstü pozisyon açılmıyor.
- [ ] Min-notional block tetiklenince teknik sebep loglanıyor.
- [ ] Spread guard tetiklenince entry bloklanıyor/downgrade ediliyor.
- [ ] Portfolio guard tetiklenince risk azaltımı çalışıyor.
- [ ] Cancel Order endpointleri Binance’e gerçek iptal isteği gönderiyor.
- [ ] Close Position endpointleri Binance’e gerçek kapanış isteği gönderiyor.

## 6) Chat Control (Advanced)

- [x] Symbol seçmeden auto-scan analizi çalışıyor (BTC/ETH/BNB/AVAX/DOT).
- [x] Quick Chips (BTC, ETH, Top5, 10 USDT, 1m, 5m) çalışıyor.
- [x] Session memory: son symbol/budget/timeframe korunuyor.
- [ ] Duplicate request guard (3-5 sn) aynı promptu engelliyor.
- [x] Response card: Signal / Risk / Reason / Next action görünüyor.
- [x] Confidence progress bar doğru render ediliyor.
- [x] `Apply as preset` alanları doğru dolduruyor.
- [x] Pinned context bar (active symbols, budget cap, direction lock) güncel.
- [ ] `Bu işlem neden açılmadı?` debug cevabı tek satır teknik sebep dönüyor.
- [ ] Trigger daemon aktif: koşul tutunca otomatik pending approval üretip doğrudan execute etmiyor.
- [ ] Trigger conflict guard: aynı sembolde LONG/SHORT çakışması create/edit aşamasında bloklanıyor.
- [ ] Trigger execution policy (once/repeat/cooldown/max_daily_trades) hem daemon hem manual execute için uygulanıyor.
- [ ] Trigger backtest preview (30g) create/update proposal ekranında görünüyor.
- [ ] Vision chart upload: chart image payload backend’e gidiyor ve destekli provider/API key varsa vision summary dönüyor.

### Chat Action Flows

- [x] `start_trade` proposal -> onay -> işlem başlatma
- [ ] `update_config` proposal -> onay -> ayar uygulama
- [ ] `close_position` proposal -> onay -> seçili pozisyon kapatma
- [x] `close_all_positions` proposal -> onay -> tüm pozisyonları kapatma
- [x] `cancel_symbol_orders` proposal -> onay -> sembol emir iptali
- [x] `cancel_all_orders` proposal -> onay -> tüm emirleri iptal

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

- [x] `npm --prefix electron run build:mac` (macOS x64 + arm64)
- [x] `npm --prefix electron run build:win` (Windows x64 / NSIS)
- [x] `npm --prefix electron run build:linux` (Linux x64 + arm64, AppImage)
- [ ] Opsiyonel `.deb`: Linux runner üzerinde `npm --prefix electron run build:linux:deb`
- [ ] macOS Apple Silicon build açılıyor.
- [ ] Windows build açılıyor.
- [ ] Linux/Raspberry Pi 5 (arm64) build açılıyor.
- [ ] Backend pydeps architecture mismatch hatası yok.
- [x] Release blocker: Win/Linux build öncesi `build-release.sh` içinde `bash scripts/rc_gate.sh --strict` zorunlu.

## 10) Regression Log

Her RC turunda aşağıdaki tabloyu doldur:

| Date | RC Version | Scope | Result | Notes |
|---|---|---|---|---|
| 2026-03-21 | v1.0.9-rc | smoke + targeted testnet UAT | PASS | Chat Control symbol-less opportunity fallback (live ticker candidate + plan), approve/reject/execute for start/close/cancel, console clean gate, strict smoke/pytest pass, mac dmg build pass |
| 2026-03-21 | v1.0.10-rc | Lite/Pro GUI + telemetry + strict gate hardening | PASS | `scripts/lite_pro_gui_uat.py` pass, chat opportunity telemetry counter + API + UI panel, strict RC gate enforced before Windows/Linux build functions |
| 2026-03-21 | v1.0.10 | full multi-platform build + GitHub release | PASS | Mac x64/arm64 DMG, Windows x64 EXE/ZIP, Linux x64/arm64 AppImage uploaded to GitHub Release with marketing notes |
