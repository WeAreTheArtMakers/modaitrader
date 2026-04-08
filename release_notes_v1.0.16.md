# modAI Trader v1.0.16

modAI Trader v1.0.16 focuses on decision-flow clarity, long-session runtime stability, and a cleaner update path for existing desktop users.

## Highlights

- Chat Control can now keep working toward a final answer instead of sending the user back for another prompt.
  - `ANY`-direction monitoring triggers are supported when the strongest side is not fixed up front.
  - Broad approval-ready prompts can produce a direct trade proposal when a candidate qualifies.
  - If no clean setup qualifies, the app can prepare a deferred re-check task automatically.
- Follow-up visibility is stronger.
  - `Next run` and `Resolved side` are shown in Chat Control.
  - The top strip now shows active follow-up state separately from fired trigger history.
- Runtime stability was hardened for longer uptime.
  - Exchange/session cleanup is stricter.
  - Runtime telemetry now reports `memory_rss_mb`, `peak_memory_rss_mb`, event loop lag, open session count, cache size, and cache maintenance runs.
  - Idle and night-window cache compaction reduces stale memory growth over long sessions.
  - Symbol alias cache now uses LRU-style pruning instead of a hard full reset.
- Market Regime is more useful as a global control surface.
  - Source selection, breadth summary, and posture details reflect a whole-market view instead of a single-symbol bias.
- Desktop update behavior is quieter and safer.
  - Update checks start later, use exponential backoff on repeated network failures, and keep platform-aware asset selection.

## Platform packages

- macOS Intel: `modAI Trader-1.0.16.dmg`
- macOS Apple Silicon: `modAI Trader-1.0.16-arm64.dmg`
- Windows x64 Installer: `modAI-Trader-1.0.16-x64.exe`
- Windows x64 Portable: `modAI-Trader-1.0.16-x64.zip`
- Linux x64: `modAI Trader-1.0.16.AppImage`
- Linux arm64: `modAI Trader-1.0.16-arm64.AppImage`

## Operator-facing changes

- Chat Control follow-up tasks keep context attached to the current symbol and selected language.
- Deferred re-check tasks can be approved directly from the chat flow.
- Fired trigger state is clearer when `ANY` resolves into `LONG` or `SHORT`.
- Scanner and Liq Hunter continue to receive performance-focused cleanup work to reduce interaction stalls under heavier symbol sets.

## Notes

- macOS builds are ad-hoc signed for testing but not notarized.
- Existing users on older public builds can use `Check for Updates` and should be routed to the correct package for their platform.
