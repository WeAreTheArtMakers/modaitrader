"""
Risk Calculator for portfolio management.
Provides basic risk metrics without third-party math dependencies.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List


@dataclass
class Position:
    """Trading position snapshot."""

    symbol: str
    side: str
    size: float
    entry_price: float
    current_price: float
    leverage: int
    unrealized_pnl: float
    notional: float


@dataclass
class RiskMetrics:
    """Portfolio risk output."""

    portfolio_risk_score: int
    risk_level: str
    total_exposure: float
    exposure_percentage: float
    max_loss_potential: float
    diversification_score: int
    leverage_risk: str
    avg_leverage: float
    position_count: int
    recommendations: List[str]
    timestamp: str


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _safe_int(value: object, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except Exception:
        return default


def _mean(values: List[float]) -> float:
    return (sum(values) / len(values)) if values else 0.0


class RiskCalculator:
    """Compute position sizing and portfolio-level risk signals."""

    def __init__(self, balance: float):
        self.balance = max(0.0, _safe_float(balance))

    def calculate_portfolio_risk(self, positions: List[Dict]) -> RiskMetrics:
        if not positions or self.balance <= 0:
            return self._empty_metrics()

        parsed: List[Position] = []
        for raw in positions:
            try:
                parsed.append(
                    Position(
                        symbol=str(raw.get("symbol", "")),
                        side=str(raw.get("side", "LONG")),
                        size=_safe_float(raw.get("positionAmt"), 0.0),
                        entry_price=_safe_float(raw.get("entryPrice"), 0.0),
                        current_price=_safe_float(raw.get("markPrice"), 0.0),
                        leverage=max(1, _safe_int(raw.get("leverage"), 1)),
                        unrealized_pnl=_safe_float(raw.get("unRealizedProfit"), 0.0),
                        notional=_safe_float(raw.get("notional"), 0.0),
                    )
                )
            except Exception:
                continue

        if not parsed:
            return self._empty_metrics()

        total_exposure = sum(abs(p.notional) for p in parsed)
        exposure_pct = (total_exposure / self.balance) * 100.0 if self.balance > 0 else 0.0

        max_loss = sum(abs(p.unrealized_pnl) for p in parsed if p.unrealized_pnl < 0)
        max_loss_pct = (max_loss / self.balance) * 100.0 if self.balance > 0 else 0.0

        unique_symbols = len({p.symbol for p in parsed if p.symbol})
        diversification_score = min(100, unique_symbols * 20)
        avg_leverage = _mean([float(p.leverage) for p in parsed])

        if avg_leverage <= 5:
            leverage_risk = "LOW"
        elif avg_leverage <= 15:
            leverage_risk = "MEDIUM"
        else:
            leverage_risk = "HIGH"

        risk_score = self._calculate_risk_score(
            exposure_pct=exposure_pct,
            avg_leverage=avg_leverage,
            unique_symbols=unique_symbols,
            max_loss_pct=max_loss_pct,
        )

        if risk_score < 30:
            risk_level = "LOW"
        elif risk_score < 60:
            risk_level = "MEDIUM"
        else:
            risk_level = "HIGH"

        recommendations = self._generate_recommendations(
            exposure_pct=exposure_pct,
            avg_leverage=avg_leverage,
            unique_symbols=unique_symbols,
            max_loss_pct=max_loss_pct,
            position_count=len(parsed),
        )

        return RiskMetrics(
            portfolio_risk_score=min(100, risk_score),
            risk_level=risk_level,
            total_exposure=total_exposure,
            exposure_percentage=exposure_pct,
            max_loss_potential=max_loss,
            diversification_score=diversification_score,
            leverage_risk=leverage_risk,
            avg_leverage=avg_leverage,
            position_count=len(parsed),
            recommendations=recommendations,
            timestamp=datetime.now().isoformat(),
        )

    def calculate_position_size(
        self,
        risk_percentage: float,
        entry_price: float,
        stop_loss_price: float,
        leverage: int = 1,
    ) -> Dict[str, float]:
        if self.balance <= 0 or entry_price <= 0 or stop_loss_price <= 0:
            return {"position_size": 0.0, "risk_amount": 0.0, "max_loss": 0.0, "error": "Invalid parameters"}

        safe_leverage = max(1, int(leverage))
        risk_amount = self.balance * (max(0.0, float(risk_percentage)) / 100.0)

        price_risk = abs(float(entry_price) - float(stop_loss_price))
        price_risk_pct = (price_risk / float(entry_price)) * 100.0 if entry_price > 0 else 0.0
        if price_risk_pct <= 0:
            return {
                "position_size": 0.0,
                "risk_amount": risk_amount,
                "max_loss": 0.0,
                "error": "Stop loss too close to entry",
            }

        position_size = risk_amount / (price_risk_pct / 100.0)
        position_size = position_size / safe_leverage

        return {
            "position_size": position_size,
            "risk_amount": risk_amount,
            "max_loss": price_risk * position_size,
            "notional_value": position_size * float(entry_price) * safe_leverage,
        }

    def _calculate_risk_score(
        self,
        exposure_pct: float,
        avg_leverage: float,
        unique_symbols: int,
        max_loss_pct: float,
    ) -> int:
        risk_score = 0

        if exposure_pct < 20:
            risk_score += 5
        elif exposure_pct < 50:
            risk_score += 15
        elif exposure_pct < 80:
            risk_score += 30
        else:
            risk_score += 40

        if avg_leverage <= 5:
            risk_score += 5
        elif avg_leverage <= 15:
            risk_score += 15
        else:
            risk_score += 30

        if unique_symbols >= 5:
            risk_score += 0
        elif unique_symbols >= 3:
            risk_score += 10
        else:
            risk_score += 20

        if max_loss_pct < 5:
            risk_score += 0
        elif max_loss_pct < 10:
            risk_score += 5
        else:
            risk_score += 10

        return int(risk_score)

    def _generate_recommendations(
        self,
        exposure_pct: float,
        avg_leverage: float,
        unique_symbols: int,
        max_loss_pct: float,
        position_count: int,
    ) -> List[str]:
        tips: List[str] = []

        if exposure_pct > 80:
            tips.append(f"CRITICAL: Very high exposure ({exposure_pct:.1f}%). Reduce position size.")
        elif exposure_pct > 50:
            tips.append(f"High exposure ({exposure_pct:.1f}%). Consider reducing risk.")

        if avg_leverage > 20:
            tips.append(f"CRITICAL: Very high leverage ({avg_leverage:.1f}x).")
        elif avg_leverage > 15:
            tips.append(f"High average leverage ({avg_leverage:.1f}x).")

        if unique_symbols < 2:
            tips.append("Low diversification. Consider adding uncorrelated symbols.")
        elif unique_symbols < 3:
            tips.append("Moderate diversification. More symbols can reduce concentration risk.")

        if max_loss_pct > 15:
            tips.append(f"CRITICAL: Potential loss {max_loss_pct:.1f}% of balance.")
        elif max_loss_pct > 10:
            tips.append(f"Potential loss is elevated ({max_loss_pct:.1f}%).")

        if position_count > 10:
            tips.append(f"Many open positions ({position_count}). Consolidation may reduce operational risk.")

        if not tips:
            tips.append("Risk levels are acceptable. Continue monitoring.")
        return tips

    def _empty_metrics(self) -> RiskMetrics:
        return RiskMetrics(
            portfolio_risk_score=0,
            risk_level="LOW",
            total_exposure=0.0,
            exposure_percentage=0.0,
            max_loss_potential=0.0,
            diversification_score=100,
            leverage_risk="LOW",
            avg_leverage=0.0,
            position_count=0,
            recommendations=["No open positions. Safe to trade."],
            timestamp=datetime.now().isoformat(),
        )
