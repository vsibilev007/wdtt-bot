"""
Генерация графиков трафика через matplotlib.
"""

from __future__ import annotations

import copy as _copy
import io
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    from matplotlib.ticker import FuncFormatter
    HAS_MPL = True
except ImportError:
    HAS_MPL = False


if HAS_MPL:
    # matplotlib <=3.10.x: Path.__deepcopy__ делает copy.deepcopy(super()),
    # что на Python >=3.14 уходит в бесконечную рекурсию (RecursionError
    # на первом же графике). Патчим, только если зонд падает — на старых
    # Python и исправленных matplotlib он не применяется.
    from matplotlib.path import Path as _MplPath

    try:
        _copy.deepcopy(_MplPath([[0.0, 0.0]]))
    except RecursionError:
        def _path_deepcopy(self, memo=None):
            p = object.__new__(type(self))
            p.__dict__.update(_copy.deepcopy(self.__dict__, memo))
            p._readonly = False
            return p

        _MplPath.__deepcopy__ = _path_deepcopy
        _MplPath.deepcopy = _path_deepcopy
        logger.debug("Заплатка matplotlib Path.__deepcopy__ для Python >=3.14 применена")


# ─── Тема ─────────────────────────────────────────────────────────────────────

_BG       = "#1e2029"
_FG       = "#e0e0e0"
_GRID     = "#2e3040"
_ACCENT   = "#4f9eff"
_ACCENT2  = "#ff6b6b"
_GREEN    = "#50fa7b"


def _apply_theme(fig, ax):
    fig.patch.set_facecolor(_BG)
    ax.set_facecolor(_BG)
    ax.tick_params(colors=_FG, labelsize=8)
    ax.xaxis.label.set_color(_FG)
    ax.yaxis.label.set_color(_FG)
    ax.title.set_color(_FG)
    for spine in ax.spines.values():
        spine.set_color(_GRID)
    ax.grid(True, color=_GRID, linewidth=0.6, linestyle="--", alpha=0.7)


def _bytes_fmt(value: float, _pos=None) -> str:
    for unit, thr in [("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)]:
        if value >= thr:
            return f"{value / thr:.1f}{unit}"
    return f"{value:.0f}B"


# ─── График истории трафика одного пользователя ───────────────────────────────

def render_user_traffic(
    rows: list[dict],
    user_password: str,
    days: int,
    server_name: str = "",
) -> Optional[io.BytesIO]:
    """
    rows: список dict с ключами sampled_at (unix), traffic_bytes, online
    """
    if not HAS_MPL or len(rows) < 2:
        return None

    try:
        times, deltas, online_counts = [], [], []
        for i in range(1, len(rows)):
            dt = datetime.fromtimestamp(rows[i]["sampled_at"], tz=timezone.utc)
            d = max(0, rows[i]["traffic_bytes"] - rows[i - 1]["traffic_bytes"])
            times.append(dt)
            deltas.append(d)
            online_counts.append(rows[i]["online"])

        total = sum(deltas)

        fig, ax = plt.subplots(figsize=(9, 4))

        ax.fill_between(times, deltas, alpha=0.25, color=_ACCENT)
        ax.plot(times, deltas, color=_ACCENT, linewidth=1.4, marker=".", markersize=3)
        ax.set_title(
            f"Трафик — {user_password[:12]}…  ({days}д, всего {_bytes_fmt(total)})"
            + (f"  •  {server_name}" if server_name else ""),
            fontsize=10, pad=8,
        )
        ax.yaxis.set_major_formatter(FuncFormatter(_bytes_fmt))
        _apply_theme(fig, ax)

        if days <= 1:
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        elif days <= 7:
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b %H:%M"))
        else:
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
        # На почти нулевом диапазоне (2 точки подряд) AutoDateLocator
        # выдаёт мусорные подписи — растягиваем ось минимум до часа.
        if (times[-1] - times[0]).total_seconds() < 3600:
            pad = timedelta(seconds=3600 - (times[-1] - times[0]).total_seconds()) / 2
            ax.set_xlim(times[0] - pad, times[-1] + pad)
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=25, ha="right", fontsize=7)

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=130, bbox_inches="tight", facecolor=_BG)
        plt.close(fig)
        buf.seek(0)
        return buf
    except Exception:
        logger.exception("Ошибка отрисовки графика трафика")
        return None


# ─── Сводный график топ-пользователей ────────────────────────────────────────

def render_traffic_report(
    deltas: list[dict],
    days: int,
    server_name: str = "",
    top_n: int = 15,
) -> Optional[io.BytesIO]:
    if not HAS_MPL or not deltas:
        return None

    try:
        top = deltas[:top_n]
        names  = [d["user_password"][:12] for d in reversed(top)]
        values = [d["delta_bytes"] for d in reversed(top)]

        fig, ax = plt.subplots(figsize=(9, max(4, len(names) * 0.42)))

        colors = [_ACCENT if v == max(values) else _ACCENT2 if v > max(values) * 0.5 else _GRID
                  for v in values]
        bars = ax.barh(names, values, color=colors, edgecolor="none", height=0.6)

        for bar, val in zip(bars, values):
            ax.text(
                bar.get_width() * 1.01, bar.get_y() + bar.get_height() / 2,
                _bytes_fmt(val), va="center", ha="left",
                fontsize=7.5, color=_FG,
            )

        ax.set_title(
            f"Топ пользователей по трафику — {days}д"
            + (f"  •  {server_name}" if server_name else ""),
            fontsize=10, pad=8,
        )
        ax.xaxis.set_major_formatter(FuncFormatter(_bytes_fmt))
        ax.set_xlim(0, max(values) * 1.20)
        _apply_theme(fig, ax)
        plt.setp(ax.xaxis.get_majorticklabels(), fontsize=7)

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=130, bbox_inches="tight", facecolor=_BG)
        plt.close(fig)
        buf.seek(0)
        return buf
    except Exception:
        logger.exception("Ошибка отрисовки отчёта по трафику")
        return None
