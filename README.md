# 🤖 modAI Trader

> Professional AI-Powered Trading Bot for Binance Futures

[![License: WATAM](https://img.shields.io/badge/License-WATAM-blue.svg)](LICENSE)
[![Built with ❤️](https://img.shields.io/badge/Built%20with-❤️-red.svg)](https://github.com/WeAreTheArtMakers)
[![Version 1.0.10](https://img.shields.io/badge/version-1.0.10-blue.svg)](https://github.com/WeAreTheArtMakers/modaitrader/releases/latest)

<div align="center">
  <img src="assets/modai-logo.svg" alt="modAI Trader Logo" width="280"/>
  <br/>
  <br/>
  <img src="screenshots/dashboard-market-overview.png" alt="modAI Trader Dashboard" width="900"/>
</div>

---

## ✨ Features

### 🤖 AI-Powered Trading
- **Advanced AI Agent** with real-time market analysis
- **Automated Position Monitoring** and management
- **Intelligent Signal Generation** with confidence scoring
- **Market Sentiment Analysis** for better decisions
- **Chat to Control** with approval-based execution and trigger lifecycle (create/edit/cancel/execute)

### 📊 Professional Analytics
- **Real-time PnL Tracking** and performance metrics
- **Win Rate Analysis** and detailed trade history
- **Advanced Risk Management** dashboard
- **Portfolio Diversification** scoring

### 🔒 Enterprise Security
- **AES-256-GCM Encryption** for API credentials
- **Device-Bound License** system
- **Secure Local Storage** - no external data transmission
- **Military-Grade Security** for your funds

### ⚡ High-Performance Execution
- **Ultra-Low Latency** order execution
- **Multi-Bot Support** for parallel trading
- **Advanced Strategy System** with pre-built templates
- **Kelly Criterion** position sizing

### 🎯 Risk Management
- **Real-time Portfolio Risk** scoring (0-100)
- **Automated Stop-Loss** and take-profit
- **Trailing Stop-Loss** system
- **Emergency Panic Button** for instant exit
- **Risk Approval Step 2** in Chat Control for high-risk executions
- **Hard Budget Cap + Direction Lock** controls in Bot Control
- **Live Guard State Panel** (spread/portfolio/budget/min-notional counters)
- **One-click "Why didn't trade open?"** debug reason in Chat Control
- **Session Memory + Quick Chips** for symbol/budget/timeframe shortcuts in Chat Control
- **Duplicate Request Guard** (anti-repeat window)
- **Response Cards** with Signal / Risk / Reason / Next Action
- **Confidence Progress Bar + Apply as Preset** in chat responses

### 🔔 Real-Time Notifications
- Browser notifications for trades
- AI signal alerts
- Risk warnings
- Connection status updates

### 🧪 Testnet Support
- Safe testing with fake money
- Easy switch between testnet/mainnet
- Perfect for learning and strategy testing

---

## 🧭 What This App Does

modAI Trader is a desktop application that combines:
- A local Python trading engine
- A React/Electron user interface
- Binance Futures connectivity (Testnet and Mainnet)
- AI-assisted signal, risk, and portfolio monitoring

The app helps users run strategy-based futures trading with live analytics, controlled risk rules, and license-based access.

---

## 🚀 Quick Start

### 📥 Download

**Latest Release: v1.0.10**

<div align="center">

| Platform | Download | Size |
|----------|----------|------|
| 🍎 **macOS (Intel)** | [Download from latest release](https://github.com/WeAreTheArtMakers/modaitrader/releases/latest) | ~95 MB |
| 🍎 **macOS (Apple Silicon)** | [Download from latest release](https://github.com/WeAreTheArtMakers/modaitrader/releases/latest) | ~91 MB |
| 🪟 **Windows** (64-bit Installer) | [Download from latest release](https://github.com/WeAreTheArtMakers/modaitrader/releases/latest) | ~80 MB |
| 🪟 **Windows** (64-bit Portable) | [Download from latest release](https://github.com/WeAreTheArtMakers/modaitrader/releases/latest) | ~113 MB |
| 🐧 **Linux x64** | [Download from latest release](https://github.com/WeAreTheArtMakers/modaitrader/releases/latest) | ~113 MB |
| 🐧 **Linux ARM64** (Raspberry Pi 5) | [Download from latest release](https://github.com/WeAreTheArtMakers/modaitrader/releases/latest) | ~113 MB |

</div>

### 📦 Installation

#### macOS
1. Download the `.dmg` file
2. Open the downloaded file
3. Drag **modAI Trader** to Applications folder
4. Launch from Applications
5. If Gatekeeper blocks launch ("app is damaged" or "unidentified developer"):
   - First try: right click app -> **Open**
   - Or go to `System Settings -> Privacy & Security` and click **Open Anyway**
   - If your system still blocks startup, use **Sentinel.app** to remove quarantine on the downloaded app bundle, then open again
6. If you see "unidentified developer" warning:
   - Right-click the app → Open
   - Click "Open" in the dialog

#### Windows
1. Download the `.exe` installer
2. Run the installer
3. Follow the installation wizard
4. Launch from Start Menu or Desktop shortcut
5. If Windows shows "could not access the file/device/path":
   - Move installer from cloud-synced folder to `C:\Users\<you>\Desktop`
   - Right click file -> **Properties** -> **Unblock** (if visible)
   - Run as Administrator
   - If still blocked, use the **portable `.zip`** package from the same release

#### Linux (x64 / ARM64 Raspberry Pi 5)
1. Download the matching `.AppImage` file
2. Open terminal in the download folder
3. Run: `chmod +x "modAI Trader-1.0.10-arm64.AppImage"` (or x64 file)
4. Launch: `./modAI Trader-1.0.10-arm64.AppImage` (or x64 file)
5. If required, install base libraries (`libnss3`, `libgtk-3-0`, `libxss1`)

### 🔑 License Plans & Activation

modAI Trader uses device-ID based licensing.

**Current Plans**
- Trial 5 Days: Free
- Monthly: $250
- Yearly: $2700
- One-Time Lifetime: $5000

**How activation works**
1. Launch the app and copy your generated `device_id`.
2. Choose a plan.
3. Contact **studiobrn@gmail.com** with your device ID and payment reference for paid plans.
4. Receive your device-bound license key.
5. Enter the key in the app and activate.
6. Trial users can upgrade to Monthly/Yearly/Lifetime on the same Device ID.

**License Features**
- ✅ Device-ID bound activation
- ✅ Secure local validation
- ✅ Trial-to-paid upgrade path
- ✅ Full module unlock after activation
- ✅ Ongoing release updates

### ⚙️ Setup

1. **Launch the App**
   - The app will start automatically

2. **Enter License Key**
   - Paste your license key when prompted
   - Click "Activate"

3. **Get Binance API Keys**
   - **Testnet** (recommended for beginners): [testnet.binancefuture.com](https://testnet.binancefuture.com)
   - **Mainnet** (real trading): [binance.com/api-management](https://www.binance.com/en/my/settings/api-management)

4. **Configure API Credentials**
   - Go to "Bot Control" tab
   - Choose mode: 🧪 Testnet or 🔴 Mainnet
   - Enter API Key and Secret
   - Click "Save Credentials"

5. **Start Trading!**
   - Select a strategy template
   - Choose your trading pair (e.g., BTC/USDT)
   - Click "Start Bot"

---

## 🔑 License Purchase Steps (For Users)

1. Install and launch modAI Trader.
2. On the license activation page, copy your generated `device_id`.
3. Contact `studiobrn@gmail.com` with your device ID and payment reference.
4. Receive your device-bound license key.
5. Enter the key in the app and activate.
6. Start using all features immediately.

---

## 📖 Documentation

Comprehensive documentation is available in the [`docs/`](docs/) folder:

### Getting Started

#### 1. Dashboard Overview
- **Account Balance**: Real-time balance and PnL
- **Active Positions**: Current open trades
- **Performance Metrics**: Win rate, total trades, profit
- **Risk Score**: Portfolio risk level (0-100)

#### 2. AI Agent
- **Market Analysis**: Real-time market insights
- **Signal Generation**: AI-powered trade signals
- **Position Monitoring**: Automated position management
- **Confidence Scoring**: Signal reliability (0-100%)

#### 3. Risk Management
- **Portfolio Risk**: Overall risk exposure
- **Position Sizing**: Automatic Kelly Criterion
- **Stop Loss**: Automated risk protection
- **Diversification**: Multi-symbol trading

#### 4. Strategy Templates
- **Conservative (10x)**: Low risk, steady gains
- **Balanced (20x)**: Medium risk/reward
- **Aggressive (50x)**: High risk, high reward
- **Ultra Aggressive (100x)**: Maximum leverage
- **Liquidation Hunter**: Catch liquidation cascades
- **Swing Trader**: Multi-day positions
- **Contrarian Scalper**: Counter-trend scalping

### Best Practices

#### ⚠️ Risk Warning
- **Start with Testnet**: Always test strategies first
- **Use Stop Losses**: Never trade without protection
- **Manage Position Size**: Don't risk more than 1-2% per trade
- **Diversify**: Don't put all funds in one position
- **Monitor Regularly**: Check your positions daily

#### 🎯 Trading Tips
- **Test First**: Use testnet for at least 1 week
- **Start Small**: Begin with minimum position sizes
- **Use AI Signals**: Let AI guide your decisions
- **Follow Risk Score**: Keep it below 50 for safety
- **Set Notifications**: Enable browser alerts

#### 🔒 Security Tips
- **API Permissions**: Only enable "Reading" and "Futures"
- **Never Share Keys**: Keep API keys private
- **Use Testnet First**: Practice before real trading
- **Backup License**: Save your license key safely
- **Update Regularly**: Keep the app updated

---

## 📊 Screenshots

### Trading Workspace

| Dashboard | Account |
|---|---|
| ![Dashboard - Market Overview](screenshots/dashboard-market-overview.png) | ![Account Overview](screenshots/account-overview.png) |
| ![Analytics & Performance](screenshots/analytics-performance.png) | ![Risk Management](screenshots/risk-management.png) |

### Strategy & Execution

| AI Agent | Market Scanner |
|---|---|
| ![AI Agent](screenshots/ai-agent.png) | ![Advanced Market Scanner](screenshots/advanced-market-scanner.png) |
| ![Strategy Analyzer](screenshots/strategy-analyzer.png) | ![Live Decision Preview](screenshots/live-decision-preview.png) |
| ![Bot Control](screenshots/bot-control.png) | ![Configuration Templates](screenshots/templates.png) |

### Configuration & Support

| Configuration | Logs |
|---|---|
| ![Bot Configuration](screenshots/configuration.png) | ![Event Logs](screenshots/logs.png) |
| ![About Overview](screenshots/about-overview.png) | ![About Features](screenshots/about-features.png) |

---

## 🔐 Security & Privacy

### Data Security
- ✅ **Local Storage Only**: All data stored on your device
- ✅ **AES-256-GCM Encryption**: Military-grade encryption
- ✅ **No External Servers**: Direct connection to Binance only
- ✅ **Release-Only Distribution**: This repository publishes installers only

### API Permissions
Required Binance API permissions:
- ✅ **Enable Reading**: View account data
- ✅ **Enable Futures**: Trade futures
- ❌ **Enable Withdrawals**: NOT required (safer)

### License System
- **Device-Bound**: One license per Device ID
- **Secure Activation**: Hardware-based verification
- **Plan Options**: Trial 5 Days, Monthly, Yearly, One-Time Lifetime
- **Upgrade Path**: Trial users can move to paid plans on same device
- **Transfer Policy**: Contact us for device changes

---

## 💡 FAQ

### General Questions

**Q: Do I need coding knowledge?**
A: No! The app has a user-friendly interface. Just click and trade.

**Q: Is my money safe?**
A: Your funds stay in your Binance account. We never have access to your funds.

**Q: Can I use multiple bots?**
A: Yes! Run multiple strategies simultaneously.

**Q: What's the minimum deposit?**
A: Binance Futures minimum is ~$10 USDT, but we recommend $100+ for proper risk management.

### Technical Questions

**Q: Which operating systems are supported?**
A: macOS (Intel + Apple Silicon), Windows (64-bit installer + portable), Linux x64, and Linux ARM64 (AppImage, tested for Raspberry Pi 5).

**Q: Do I need to keep the app running?**
A: Yes, the app must be running for the bot to trade.

**Q: Can I run it on a VPS?**
A: Not currently. Desktop app only.

**Q: What's the difference between testnet and mainnet?**
A: Testnet uses fake money for practice. Mainnet uses real money.

### Licensing Questions

**Q: How much does a license cost?**
A: Plans are **Trial 5 Days (Free)**, **Monthly ($250)**, **Yearly ($2700)**, and **One-Time Lifetime ($5000)**.

**Q: Can I transfer my license?**
A: Contact us if you change devices. One transfer allowed.

**Q: Is there a free trial?**
A: Yes. A **5-day trial license** is available and can be upgraded on the same Device ID.

**Q: Do I get updates?**
A: Yes! All updates are free for license holders.

---

## 🛠️ System Requirements

### Minimum
- **OS**: macOS 10.15+, Windows 10+, or Linux ARM64 (Raspberry Pi OS 64-bit / Ubuntu 22.04+)
- **CPU**: 2 cores
- **RAM**: 4 GB
- **Storage**: 1 GB free space
- **Internet**: Stable connection

### Recommended
- **OS**: macOS 12+, Windows 11+, or Ubuntu 24.04 ARM64 / Raspberry Pi OS Bookworm 64-bit
- **CPU**: 4+ cores
- **RAM**: 8+ GB
- **Storage**: 5 GB free space
- **Internet**: Low-latency connection (<100ms to Binance)

---

## 🤝 Support

### Get Help

- 📖 **Documentation**: [Read the docs](docs/)
- 🐛 **Bug Reports**: [Open an issue](https://github.com/WeAreTheArtMakers/modaitrader/issues)
- 💬 **Discord**: [Join our community](https://discord.gg/watam)
- 📧 **Email**: studiobrn@gmail.com

### Contact Us

- **License Inquiries**: studiobrn@gmail.com
- **Technical Support**: admin@wearetheartmakers.com
- **General Questions**: admin@wearetheartmakers.com

---

## 📝 License

**WATAM License** - WeAreTheArtMakers

This software is proprietary and requires a valid license key.

### Terms
- ✅ Personal use with valid license
- ✅ Commercial use with valid license
- ❌ Redistribution prohibited
- ❌ Reverse engineering prohibited
- ❌ License system modification prohibited

For licensing inquiries: studiobrn@gmail.com

---

## 🎉 Credits

### Built with ❤️ by WeAreTheArtMakers

**Development Team:**
- AI & Trading Logic
- Security & Encryption
- UI/UX Design
- Risk Management Systems

**Technologies:**
- Python (Backend)
- React (Frontend)
- Electron (Desktop App)
- Binance API (Exchange)

---

## ⭐ Show Your Support

If you find this project useful:
- ⭐ Star the repository
- 🐛 Report bugs
- 💡 Suggest features
- 📖 Share with friends

---

<div align="center">

**Made with ❤️ by WeAreTheArtMakers**

*Professional AI-Powered Trading Bot*

[Download](https://github.com/WeAreTheArtMakers/modaitrader/releases) • [Documentation](docs/) • [Support](https://discord.gg/watam)

---

⚠️ **Risk Warning**: Trading cryptocurrencies involves substantial risk of loss. Only trade with money you can afford to lose. Past performance does not guarantee future results.

</div>
