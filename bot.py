"""
Runner Engine v2
----------------

Solana runner-detection bot using Telegram + DEX Screener.

The engine looks for a transition in market behavior rather than relying
only on a single 5-minute snapshot.

Current observable sequence:

DORMANT
    -> CONSOLIDATION
    -> ABSORPTION
    -> PARTICIPATION EXPANSION
    -> STRUCTURE BREAK
    -> IGNITION

Important:
- Token age is contextual, not a rejection filter.
- Buy/sell transaction counts are NOT treated as dollar flow.
- Buy USD / sell USD / unique-wallet data are intentionally not fabricated.
- Historical observations are kept in memory while the GitHub Actions process
  is running.
"""

from __future__ import annotations

import json
import logging
import math
import os
import signal
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib import error, parse, request


# ---------------------------------------------------------------------------
# LOGGING
# ---------------------------------------------------------------------------

LOGGER = logging.getLogger("runner_bot")


# ---------------------------------------------------------------------------
# CONSTANTS
# ---------------------------------------------------------------------------

POLL_TIMEOUT_SECONDS = 30
RETRY_DELAY_SECONDS = 5

START_MESSAGE = "Runner bot is online!"

DEX_API_BASE = "https://api.dexscreener.com"
SOLANA_CHAIN = "solana"

DEFAULT_SCAN_INTERVAL_SECONDS = 15
DEFAULT_MAX_TOKENS_PER_SCAN = 200

DEFAULT_ALERT_COOLDOWN_SECONDS = 3_600

MAX_TOKENS_PER_REQUEST = 30

# Runner target range.
DEFAULT_MIN_MARKET_CAP_USD = 20_000
DEFAULT_MAX_MARKET_CAP_USD = 200_000

DEFAULT_MIN_LIQUIDITY_USD = 10_000

# Minimum current activity.
DEFAULT_MIN_VOLUME_5M_USD = 5_000

# Runner-engine thresholds.
DEFAULT_MIN_OBSERVATIONS = 4

DEFAULT_CONSOLIDATION_RANGE_PERCENT = 18.0

DEFAULT_MIN_VOLUME_ACCELERATION = 1.30
DEFAULT_MIN_TRANSACTION_ACCELERATION = 1.20

DEFAULT_MAX_IGNITION_PRICE_CHANGE_5M = 35.0

DEFAULT_MIN_LIQUIDITY_RETENTION_PERCENT = 80.0

DEFAULT_BREAKOUT_BUFFER_PERCENT = 0.5

# How many historical observations are retained per token.
MAX_HISTORY_PER_TOKEN = 40

DISCLAIMER = (
    "Disclaimer: For informational purposes only. Not investment advice or a "
    "token advertisement. We accept no responsibility for investment decisions "
    "or losses. Do your own research and make your own investment decisions."
)


# ---------------------------------------------------------------------------
# EXCEPTIONS
# ---------------------------------------------------------------------------

@dataclass
class TelegramApiError(Exception):
    method: str
    description: str

    def __str__(self) -> str:
        return (
            f"Telegram API request {self.method!r} failed: "
            f"{self.description}"
        )


@dataclass
class DexScreenerApiError(Exception):
    url: str
    description: str

    def __str__(self) -> str:
        return (
            f"DEX Screener request failed for {self.url}: "
            f"{self.description}"
        )


# ---------------------------------------------------------------------------
# BASIC HELPERS
# ---------------------------------------------------------------------------

def _number(value: Any) -> float | None:
    """Return a finite number without converting missing data."""

    if isinstance(value, bool) or value is None:
        return None

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None

    if not math.isfinite(number):
        return None

    return number


def _count(value: Any) -> int | None:
    number = _number(value)

    if number is None or number < 0:
        return None

    return int(number)


def _window(data: Any, name: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}

    value = data.get(name)

    if value is None:
        value = data.get(name.replace("m", ""))

    return value if isinstance(value, dict) else {}


def _metric(data: Any, name: str) -> float | None:
    if not isinstance(data, dict):
        return None

    return _number(data.get(name))


def _format_compact_usd(value: float | None) -> str:
    if value is None:
        return "unavailable"

    absolute = abs(value)

    if absolute >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f}B"

    if absolute >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"

    if absolute >= 1_000:
        return f"{value / 1_000:.2f}K"

    return f"{value:,.2f}"


def _format_number(value: float | None, decimals: int = 2) -> str:
    if value is None:
        return "n/a"

    return f"{value:.{decimals}f}"


def _env_float(name: str, default: float) -> float:
    value = _number(os.environ.get(name))

    if value is None or value < 0:
        return default

    return value


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)

    if value is None:
        return default

    return value.strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


# ---------------------------------------------------------------------------
# TOKEN SNAPSHOT
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TokenSnapshot:
    """
    One source-backed observation of a Solana token pair.
    """

    address: str
    symbol: str

    market_cap_usd: float | None
    liquidity_usd: float | None

    volume_5m_usd: float | None
    volume_1h_usd: float | None

    price_usd: float | None

    price_change_5m_percent: float | None
    price_change_1h_percent: float | None

    buys_5m: int | None
    sells_5m: int | None

    pair_created_at_ms: int | None

    @property
    def transactions_5m(self) -> int | None:
        if self.buys_5m is None or self.sells_5m is None:
            return None

        return self.buys_5m + self.sells_5m

    @property
    def buy_sell_ratio_5m(self) -> float | None:
        if self.buys_5m is None or self.sells_5m is None:
            return None

        if self.sells_5m == 0:
            if self.buys_5m > 0:
                return float("inf")

            return None

        return self.buys_5m / self.sells_5m

    @property
    def token_age_hours(self) -> float | None:
        """
        Pair age, not guaranteed token deployment age.

        This is contextual information only.
        """

        if self.pair_created_at_ms is None:
            return None

        created_seconds = self.pair_created_at_ms / 1000

        age_seconds = time.time() - created_seconds

        if age_seconds < 0:
            return 0.0

        return age_seconds / 3600


# ---------------------------------------------------------------------------
# RUNNER FEATURES
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RunnerFeatures:
    observation_count: int

    volume_acceleration: float | None
    transaction_acceleration: float | None

    price_change_from_previous_percent: float | None
    market_cap_change_percent: float | None
    liquidity_change_percent: float | None

    recent_high: float | None
    recent_low: float | None

    consolidation_range_percent: float | None

    breakout_above_recent_high: bool

    price_above_recent_low: bool

    activity_expanding: bool

    liquidity_stable: bool

    state: str

    ignition_candidate: bool

    reasons: tuple[str, ...]


# ---------------------------------------------------------------------------
# MARKET HISTORY
# ---------------------------------------------------------------------------

class MarketHistory:
    """
    Keeps short rolling histories for tokens.

    This allows the engine to study transitions instead of isolated snapshots.
    """

    def __init__(self) -> None:
        self._history: dict[str, list[TokenSnapshot]] = {}

    def add(self, snapshot: TokenSnapshot) -> None:
        history = self._history.setdefault(snapshot.address, [])

        history.append(snapshot)

        if len(history) > MAX_HISTORY_PER_TOKEN:
            del history[:-MAX_HISTORY_PER_TOKEN]

    def get(self, address: str) -> list[TokenSnapshot]:
        return self._history.get(address, [])

    @staticmethod
    def _percent_change(
        previous: float | None,
        current: float | None,
    ) -> float | None:

        if previous is None or current is None:
            return None

        if previous == 0:
            return None

        return ((current - previous) / previous) * 100

    @staticmethod
    def _acceleration(
        previous: float | None,
        current: float | None,
    ) -> float | None:

        if previous is None or current is None:
            return None

        if previous <= 0:
            return None

        return current / previous

    def features(
        self,
        snapshot: TokenSnapshot,
    ) -> RunnerFeatures:

        history = self.get(snapshot.address)

        # The snapshot has already been added.
        observation_count = len(history)

        if len(history) < 2:
            return RunnerFeatures(
                observation_count=observation_count,
                volume_acceleration=None,
                transaction_acceleration=None,
                price_change_from_previous_percent=None,
                market_cap_change_percent=None,
                liquidity_change_percent=None,
                recent_high=None,
                recent_low=None,
                consolidation_range_percent=None,
                breakout_above_recent_high=False,
                price_above_recent_low=False,
                activity_expanding=False,
                liquidity_stable=False,
                state="NEW",
                ignition_candidate=False,
                reasons=(),
            )

        previous = history[-2]

        # ---------------------------------------------------------------
        # Previous-period changes
        # ---------------------------------------------------------------

        price_move = self._percent_change(
            previous.price_usd,
            snapshot.price_usd,
        )

        market_cap_move = self._percent_change(
            previous.market_cap_usd,
            snapshot.market_cap_usd,
        )

        liquidity_move = self._percent_change(
            previous.liquidity_usd,
            snapshot.liquidity_usd,
        )

        volume_acceleration = self._acceleration(
            previous.volume_5m_usd,
            snapshot.volume_5m_usd,
        )

        transaction_acceleration = self._acceleration(
            previous.transactions_5m,
            snapshot.transactions_5m,
        )

        # ---------------------------------------------------------------
        # Recent price structure
        # ---------------------------------------------------------------

        # Ignore the current observation when calculating the previous
        # structure. This prevents the current candle from defining its own
        # breakout level.
        previous_history = history[:-1]

        recent_prices = [
            item.price_usd
            for item in previous_history
            if item.price_usd is not None
        ]

        recent_high = max(recent_prices) if recent_prices else None
        recent_low = min(recent_prices) if recent_prices else None

        consolidation_range_percent = None

        if (
            recent_high is not None
            and recent_low is not None
            and recent_low > 0
        ):
            consolidation_range_percent = (
                (recent_high - recent_low)
                / recent_low
            ) * 100

        breakout_above_recent_high = False

        if (
            snapshot.price_usd is not None
            and recent_high is not None
            and recent_high > 0
        ):
            breakout_level = recent_high * (
                1 + DEFAULT_BREAKOUT_BUFFER_PERCENT / 100
            )

            breakout_above_recent_high = (
                snapshot.price_usd >= breakout_level
            )

        price_above_recent_low = (
            snapshot.price_usd is not None
            and recent_low is not None
            and snapshot.price_usd > recent_low
        )

        # ---------------------------------------------------------------
        # Activity expansion
        # ---------------------------------------------------------------

        volume_expanding = (
            volume_acceleration is not None
            and volume_acceleration >= DEFAULT_MIN_VOLUME_ACCELERATION
        )

        transactions_expanding = (
            transaction_acceleration is not None
            and transaction_acceleration
            >= DEFAULT_MIN_TRANSACTION_ACCELERATION
        )

        activity_expanding = (
            volume_expanding
            or transactions_expanding
        )

        # ---------------------------------------------------------------
        # Liquidity behavior
        # ---------------------------------------------------------------

        liquidity_stable = True

        if liquidity_move is not None:
            liquidity_stable = (
                liquidity_move
                >= -(
                    100
                    - DEFAULT_MIN_LIQUIDITY_RETENTION_PERCENT
                )
            )

        # ---------------------------------------------------------------
        # Determine market state
        # ---------------------------------------------------------------

        state = "OBSERVING"

        if (
            consolidation_range_percent is not None
            and consolidation_range_percent
            <= DEFAULT_CONSOLIDATION_RANGE_PERCENT
            and not activity_expanding
        ):
            state = "CONSOLIDATION"

        if activity_expanding and not breakout_above_recent_high:
            state = "EXPANSION"

        if (
            breakout_above_recent_high
            and activity_expanding
        ):
            state = "STRUCTURE BREAK"

        # ---------------------------------------------------------------
        # Ignition candidate
        # ---------------------------------------------------------------

        ignition_candidate = False
        reasons: list[str] = []

        if snapshot.market_cap_usd is not None:
            if (
                DEFAULT_MIN_MARKET_CAP_USD
                <= snapshot.market_cap_usd
                <= DEFAULT_MAX_MARKET_CAP_USD
            ):
                reasons.append("MC in runner range")

        if snapshot.liquidity_usd is not None:
            if snapshot.liquidity_usd >= DEFAULT_MIN_LIQUIDITY_USD:
                reasons.append("liquidity sufficient")

        if consolidation_range_percent is not None:
            if (
                consolidation_range_percent
                <= DEFAULT_CONSOLIDATION_RANGE_PERCENT
            ):
                reasons.append("recent range controlled")

        if volume_expanding:
            reasons.append("volume accelerating")

        if transactions_expanding:
            reasons.append("transaction activity accelerating")

        if breakout_above_recent_high:
            reasons.append("local structure break")

        if price_above_recent_low:
            reasons.append("price holding above recent low")

        if liquidity_stable:
            reasons.append("liquidity stable")

        price_change_ok = True

        if snapshot.price_change_5m_percent is not None:
            price_change_ok = (
                snapshot.price_change_5m_percent
                <= DEFAULT_MAX_IGNITION_PRICE_CHANGE_5M
            )

        # We deliberately require several independent pieces of evidence.
        ignition_conditions = [
            observation_count >= DEFAULT_MIN_OBSERVATIONS,
            snapshot.market_cap_usd is not None,
            DEFAULT_MIN_MARKET_CAP_USD
            <= (
                snapshot.market_cap_usd
                if snapshot.market_cap_usd is not None
                else -1
            )
            <= DEFAULT_MAX_MARKET_CAP_USD,
            snapshot.liquidity_usd is not None,
            snapshot.liquidity_usd >= DEFAULT_MIN_LIQUIDITY_USD
            if snapshot.liquidity_usd is not None
            else False,
            activity_expanding,
            breakout_above_recent_high,
            liquidity_stable,
            price_above_recent_low,
            price_change_ok,
        ]

        ignition_candidate = all(ignition_conditions)

        if ignition_candidate:
            state = "IGNITION"

        return RunnerFeatures(
            observation_count=observation_count,
            volume_acceleration=volume_acceleration,
            transaction_acceleration=transaction_acceleration,
            price_change_from_previous_percent=price_move,
            market_cap_change_percent=market_cap_move,
            liquidity_change_percent=liquidity_move,
            recent_high=recent_high,
            recent_low=recent_low,
            consolidation_range_percent=consolidation_range_percent,
            breakout_above_recent_high=breakout_above_recent_high,
            price_above_recent_low=price_above_recent_low,
            activity_expanding=activity_expanding,
            liquidity_stable=liquidity_stable,
            state=state,
            ignition_candidate=ignition_candidate,
            reasons=tuple(reasons),
        )


# ---------------------------------------------------------------------------
# PAIR SNAPSHOT
# ---------------------------------------------------------------------------

def snapshot_from_pair(
    pair: dict[str, Any],
) -> TokenSnapshot | None:

    base_token = pair.get("baseToken")

    if not isinstance(base_token, dict):
        return None

    address = base_token.get("address")

    if not isinstance(address, str) or not address:
        return None

    symbol = base_token.get("symbol")

    safe_symbol = (
        str(symbol).strip().replace("$", "")
        if symbol
        else "UNKNOWN"
    )

    txns = pair.get("txns")
    txns_5m = _window(txns, "m5")

    volume = pair.get("volume")
    liquidity = pair.get("liquidity")
    price_change = pair.get("priceChange")

    return TokenSnapshot(
        address=address,
        symbol=safe_symbol or "UNKNOWN",

        market_cap_usd=_number(
            pair.get("marketCap")
        ),

        liquidity_usd=_metric(
            liquidity,
            "usd",
        ),

        volume_5m_usd=_metric(
            volume,
            "m5",
        ),

        volume_1h_usd=_metric(
            volume,
            "h1",
        ),

        price_usd=_number(
            pair.get("priceUsd")
        ),

        price_change_5m_percent=_metric(
            price_change,
            "m5",
        ),

        price_change_1h_percent=_metric(
            price_change,
            "h1",
        ),

        buys_5m=_count(
            txns_5m.get("buys")
        ),

        sells_5m=_count(
            txns_5m.get("sells")
        ),

        pair_created_at_ms=_count(
            pair.get("pairCreatedAt")
        ),
    )


# ---------------------------------------------------------------------------
# PAIR SELECTION
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PairFallbackStats:
    missing_market_cap: int
    missing_liquidity_before: int
    missing_liquidity_after: int
    missing_price_change_before: int
    missing_price_change_after: int


def select_best_token_snapshots(
    pairs: list[dict[str, Any]],
) -> tuple[
    dict[str, TokenSnapshot],
    PairFallbackStats,
]:

    grouped: dict[
        str,
        list[TokenSnapshot],
    ] = {}

    for pair in pairs:

        if pair.get("chainId") != SOLANA_CHAIN:
            continue

        snapshot = snapshot_from_pair(pair)

        if snapshot is not None:
            grouped.setdefault(
                snapshot.address,
                [],
            ).append(snapshot)

    selected_snapshots: dict[
        str,
        TokenSnapshot,
    ] = {}

    missing_market_cap = 0
    missing_liquidity_before = 0
    missing_liquidity_after = 0
    missing_price_change_before = 0
    missing_price_change_after = 0

    for address, token_pairs in grouped.items():

        selected = max(
            token_pairs,
            key=lambda snapshot: (
                snapshot.liquidity_usd is not None,
                snapshot.liquidity_usd
                if snapshot.liquidity_usd is not None
                else -1,
            ),
        )

        liquidity = selected.liquidity_usd
        price_change = selected.price_change_5m_percent

        if selected.market_cap_usd is None:
            missing_market_cap += 1

        if liquidity is None:

            missing_liquidity_before += 1

            liquidity = next(
                (
                    snapshot.liquidity_usd
                    for snapshot in token_pairs
                    if snapshot.liquidity_usd is not None
                ),
                None,
            )

        if price_change is None:

            missing_price_change_before += 1

            price_change = next(
                (
                    snapshot.price_change_5m_percent
                    for snapshot in token_pairs
                    if snapshot.price_change_5m_percent is not None
                ),
                None,
            )

        if liquidity is None:
            missing_liquidity_after += 1

        if price_change is None:
            missing_price_change_after += 1

        selected_snapshots[address] = TokenSnapshot(
            address=selected.address,
            symbol=selected.symbol,
            market_cap_usd=selected.market_cap_usd,
            liquidity_usd=liquidity,
            volume_5m_usd=selected.volume_5m_usd,
            volume_1h_usd=selected.volume_1h_usd,
            price_usd=selected.price_usd,
            price_change_5m_percent=price_change,
            price_change_1h_percent=selected.price_change_1h_percent,
            buys_5m=selected.buys_5m,
            sells_5m=selected.sells_5m,
            pair_created_at_ms=selected.pair_created_at_ms,
        )

    return (
        selected_snapshots,
        PairFallbackStats(
            missing_market_cap=missing_market_cap,
            missing_liquidity_before=missing_liquidity_before,
            missing_liquidity_after=missing_liquidity_after,
            missing_price_change_before=missing_price_change_before,
            missing_price_change_after=missing_price_change_after,
        ),
    )


# ---------------------------------------------------------------------------
# TELEGRAM CLIENT
# ---------------------------------------------------------------------------

class TelegramClient:
    """Small standard-library Telegram API client."""

    def __init__(self, token: str) -> None:

        self._api_url = (
            f"https://api.telegram.org/bot{token}"
        )

    def call(
        self,
        method: str,
        **payload: Any,
    ) -> Any:

        encoded_payload = parse.urlencode(
            {
                key: (
                    json.dumps(value)
                    if isinstance(value, (dict, list))
                    else value
                )
                for key, value in payload.items()
                if value is not None
            }
        ).encode("utf-8")

        request_data = request.Request(
            f"{self._api_url}/{method}",
            data=encoded_payload,
            headers={
                "Content-Type":
                "application/x-www-form-urlencoded"
            },
            method="POST",
        )

        try:

            with request.urlopen(
                request_data,
                timeout=POLL_TIMEOUT_SECONDS + 10,
            ) as response:

                body = json.loads(
                    response.read().decode("utf-8")
                )

        except error.URLError as exc:

            raise TelegramApiError(
                method,
                str(exc.reason),
            ) from exc

        except (
            TimeoutError,
            json.JSONDecodeError,
        ) as exc:

            raise TelegramApiError(
                method,
                str(exc),
            ) from exc

        if not body.get("ok"):

            raise TelegramApiError(
                method,
                body.get(
                    "description",
                    "Unknown API error",
                ),
            )

        return body["result"]


# ---------------------------------------------------------------------------
# DEX SCREENER CLIENT
# ---------------------------------------------------------------------------

class DexScreenerClient:
    """Discover Solana pairs from public DEX Screener endpoints."""

    def _get_json(
        self,
        path: str,
    ) -> Any:

        url = f"{DEX_API_BASE}{path}"

        request_data = request.Request(
            url,
            headers={
                "User-Agent":
                "runner-bot/2.0"
            },
        )

        try:

            with request.urlopen(
                request_data,
                timeout=20,
            ) as response:

                body = json.loads(
                    response.read().decode("utf-8")
                )

        except error.HTTPError as exc:

            raise DexScreenerApiError(
                url,
                f"HTTP {exc.code}",
            ) from exc

        except error.URLError as exc:

            raise DexScreenerApiError(
                url,
                str(exc.reason),
            ) from exc

        except (
            TimeoutError,
            json.JSONDecodeError,
        ) as exc:

            raise DexScreenerApiError(
                url,
                str(exc),
            ) from exc

        if not isinstance(
            body,
            (dict, list),
        ):

            raise DexScreenerApiError(
                url,
                "response was not JSON",
            )

        return body

    @staticmethod
    def _solana_addresses(
        response: Any,
    ) -> list[str]:

        records = (
            response
            if isinstance(response, list)
            else []
        )

        addresses: list[str] = []
        seen: set[str] = set()

        for record in records:

            if (
                not isinstance(record, dict)
                or record.get("chainId")
                != SOLANA_CHAIN
            ):
                continue

            address = record.get(
                "tokenAddress"
            )

            if (
                isinstance(address, str)
                and address not in seen
            ):

                seen.add(address)
                addresses.append(address)

        return addresses

    @staticmethod
    def _pair_key(
        pair: dict[str, Any],
    ) -> str | None:

        pair_address = pair.get(
            "pairAddress"
        )

        if (
            isinstance(pair_address, str)
            and pair_address
        ):
            return pair_address

        base_token = pair.get(
            "baseToken"
        )

        quote_token = pair.get(
            "quoteToken"
        )

        if (
            not isinstance(base_token, dict)
            or not isinstance(quote_token, dict)
        ):
            return None

        base_address = base_token.get(
            "address"
        )

        quote_address = quote_token.get(
            "address"
        )

        dex_id = pair.get("dexId")

        if not all(
            isinstance(value, str)
            and value
            for value in (
                base_address,
                quote_address,
                dex_id,
            )
        ):
            return None

        return (
            f"{dex_id}:"
            f"{base_address}:"
            f"{quote_address}"
        )

    def _search_solana_pairs(
        self,
        query: str,
    ) -> list[dict[str, Any]]:

        response = self._get_json(
            "/latest/dex/search"
            f"?q={parse.quote(query, safe='')}"
        )

        pairs = (
            response.get("pairs", [])
            if isinstance(response, dict)
            else []
        )

        return [
            pair
            for pair in pairs
            if (
                isinstance(pair, dict)
                and pair.get("chainId")
                == SOLANA_CHAIN
            )
        ]

    def discover_solana_pairs(
        self,
        max_tokens: int,
    ) -> list[dict[str, Any]]:

        discovery_paths = (
            "/token-profiles/latest/v1",
            "/token-boosts/latest/v1",
            "/token-boosts/top/v1",
        )

        addresses: list[str] = []
        address_seen: set[str] = set()

        for path in discovery_paths:

            response = self._get_json(path)

            for address in self._solana_addresses(
                response
            ):

                if (
                    address not in address_seen
                    and len(addresses)
                    < max_tokens
                ):

                    address_seen.add(address)
                    addresses.append(address)

        pairs: list[dict[str, Any]] = []
        pair_seen: set[str] = set()

        for start in range(
            0,
            len(addresses),
            MAX_TOKENS_PER_REQUEST,
        ):

            chunk = addresses[
                start:
                start + MAX_TOKENS_PER_REQUEST
            ]

            encoded_addresses = ",".join(
                parse.quote(
                    address,
                    safe="",
                )
                for address in chunk
            )

            response = self._get_json(
                "/latest/dex/tokens/"
                f"{encoded_addresses}"
            )

            response_pairs = (
                response.get("pairs", [])
                if isinstance(response, dict)
                else []
            )

            if not isinstance(
                response_pairs,
                list,
            ):
                continue

            for pair in response_pairs:

                if (
                    not isinstance(pair, dict)
                    or pair.get("chainId")
                    != SOLANA_CHAIN
                ):
                    continue

                key = self._pair_key(pair)

                if (
                    key is not None
                    and key not in pair_seen
                ):

                    pair_seen.add(key)
                    pairs.append(pair)

        # Additional market coverage.
        for query in (
            "SOL",
            "USDC",
            "USDT",
            "WSOL",
        ):

            for pair in self._search_solana_pairs(
                query
            ):

                key = self._pair_key(pair)

                if (
                    key is not None
                    and key not in pair_seen
                ):

                    pair_seen.add(key)
                    pairs.append(pair)

        return pairs


# ---------------------------------------------------------------------------
# TELEGRAM SIGNAL FORMAT
# ---------------------------------------------------------------------------

def format_runner_signal(
    snapshot: TokenSnapshot,
    features: RunnerFeatures,
) -> str:

    market_cap = _format_compact_usd(
        snapshot.market_cap_usd
    )

    liquidity = _format_compact_usd(
        snapshot.liquidity_usd
    )

    volume_5m = _format_compact_usd(
        snapshot.volume_5m_usd
    )

    buys = (
        "n/a"
        if snapshot.buys_5m is None
        else str(snapshot.buys_5m)
    )

    sells = (
        "n/a"
        if snapshot.sells_5m is None
        else str(snapshot.sells_5m)
    )

    ratio = snapshot.buy_sell_ratio_5m

    if ratio is None:
        ratio_text = "n/a"
    elif ratio == float("inf"):
        ratio_text = "∞"
    else:
        ratio_text = f"{ratio:.2f}x"

    age = snapshot.token_age_hours

    age_text = (
        "n/a"
        if age is None
        else (
            f"{age:.1f}h"
            if age < 48
            else f"{age / 24:.1f}d"
        )
    )

    volume_accel = (
        "n/a"
        if features.volume_acceleration is None
        else f"{features.volume_acceleration:.2f}x"
    )

    tx_accel = (
        "n/a"
        if features.transaction_acceleration is None
        else f"{features.transaction_acceleration:.2f}x"
    )

    reasons = (
        "\n".join(
            f"• {reason}"
            for reason in features.reasons[:7]
        )
        if features.reasons
        else "• No additional reasons available"
    )

    return (
        "🔥 IGNITION DETECTED\n\n"
        f"${snapshot.symbol}\n"
        f"💎 MC: ${market_cap}\n"
        f"💧 Liquidity: ${liquidity}\n"
        f"⏱ Pair age: {age_text}\n\n"
        "📊 CURRENT ACTIVITY\n"
        f"5m volume: ${volume_5m}\n"
        f"5m buys/sells: {buys}/{sells}\n"
        f"Buy-count ratio: {ratio_text}\n\n"
        "⚡ ACCELERATION\n"
        f"Volume: {volume_accel}\n"
        f"Transactions: {tx_accel}\n"
        f"MC change: "
        f"{_format_number(features.market_cap_change_percent)}%\n"
        f"Liquidity change: "
        f"{_format_number(features.liquidity_change_percent)}%\n\n"
        "🏗 STRUCTURE\n"
        f"Recent high: {features.recent_high}\n"
        f"Recent low: {features.recent_low}\n"
        f"Range: "
        f"{_format_number(features.consolidation_range_percent)}%\n"
        "Local structure: BROKEN\n\n"
        "🔎 WHY IT TRIGGERED\n"
        f"{reasons}\n\n"
        "📋 CA\n"
        f"{snapshot.address}\n\n"
        f"{DISCLAIMER}"
    )


# ---------------------------------------------------------------------------
# RUNNER BOT
# ---------------------------------------------------------------------------

class RunnerBot:
    """
    Telegram long-polling bot with the Runner Engine.
    """

    def __init__(
        self,
        client: TelegramClient,
        dex_client: DexScreenerClient | None = None,
    ) -> None:

        self._client = client

        self._dex_client = (
            dex_client
            or DexScreenerClient()
        )

        self._next_update_id: int | None = None

        self._running = True

        self._subscriber_chat_ids: set[int] = set()

        self._subscriber_lock = (
            threading.Lock()
        )

        self._last_alert_at: dict[
            str,
            float,
        ] = {}

        # ---------------------------------------------------------------
        # Configuration
        # ---------------------------------------------------------------

        self._scan_interval_seconds = (
            _env_float(
                "DEX_SCAN_INTERVAL_SECONDS",
                DEFAULT_SCAN_INTERVAL_SECONDS,
            )
        )

        self._min_market_cap_usd = (
            _env_float(
                "RUNNER_MIN_MARKET_CAP_USD",
                DEFAULT_MIN_MARKET_CAP_USD,
            )
        )

        self._max_market_cap_usd = (
            _env_float(
                "RUNNER_MAX_MARKET_CAP_USD",
                DEFAULT_MAX_MARKET_CAP_USD,
            )
        )

        self._min_liquidity_usd = (
            _env_float(
                "RUNNER_MIN_LIQUIDITY_USD",
                DEFAULT_MIN_LIQUIDITY_USD,
            )
        )

        self._min_volume_5m_usd = (
            _env_float(
                "RUNNER_MIN_VOLUME_5M_USD",
                DEFAULT_MIN_VOLUME_5M_USD,
            )
        )

        self._alerts_enabled = (
            _env_bool(
                "HEATING_ALERTS_ENABLED",
                False,
            )
        )

        self._alert_cooldown_seconds = (
            _env_float(
                "HEATING_ALERT_COOLDOWN_SECONDS",
                DEFAULT_ALERT_COOLDOWN_SECONDS,
            )
        )

        # ---------------------------------------------------------------
        # Historical engine
        # ---------------------------------------------------------------

        self._market_history = (
            MarketHistory()
        )

    # -------------------------------------------------------------------
    # SHUTDOWN
    # -------------------------------------------------------------------

    def stop(
        self,
        _signum: int,
        _frame: Any,
    ) -> None:

        LOGGER.info(
            "Shutdown requested"
        )

        self._running = False

    # -------------------------------------------------------------------
    # MAIN LOOP
    # -------------------------------------------------------------------

    def run(self) -> None:

        self._client.call("getMe")

        LOGGER.info(
            "Runner Engine v2 is online"
        )

        LOGGER.info(
            "Runner range: MC=$%s-$%s | "
            "minimum liquidity=$%s | "
            "minimum 5m volume=$%s | "
            "scan interval=%ss",
            _format_compact_usd(
                self._min_market_cap_usd
            ),
            _format_compact_usd(
                self._max_market_cap_usd
            ),
            _format_compact_usd(
                self._min_liquidity_usd
            ),
            _format_compact_usd(
                self._min_volume_5m_usd
            ),
            self._scan_interval_seconds,
        )

        LOGGER.info(
            "Telegram alerts enabled: %s",
            self._alerts_enabled,
        )

        scanner_thread = threading.Thread(
            target=self._scan_loop,
            name="dex-scanner",
            daemon=True,
        )

        scanner_thread.start()

        try:

            while self._running:

                try:

                    updates = (
                        self._get_updates()
                    )

                    for update in updates:
                        self._handle_update(
                            update
                        )

                except TelegramApiError as exc:

                    LOGGER.error(
                        "%s",
                        exc,
                    )

                    if self._running:
                        time.sleep(
                            RETRY_DELAY_SECONDS
                        )

        finally:

            self._running = False

            scanner_thread.join(
                timeout=self._scan_interval_seconds
                + 5
            )

        LOGGER.info(
            "Runner Engine stopped"
        )

    # -------------------------------------------------------------------
    # SCANNER
    # -------------------------------------------------------------------

    def _scan_loop(self) -> None:

        while self._running:

            try:

                self._maybe_scan()

            except DexScreenerApiError as exc:

                LOGGER.error(
                    "%s",
                    exc,
                )

            except Exception:

                LOGGER.exception(
                    "Unexpected scanner error"
                )

            if self._running:
                time.sleep(
                    self._scan_interval_seconds
                )

    # -------------------------------------------------------------------
    # TELEGRAM
    # -------------------------------------------------------------------

    def _get_updates(
        self,
    ) -> list[dict[str, Any]]:

        updates = self._client.call(
            "getUpdates",
            offset=self._next_update_id,
            timeout=POLL_TIMEOUT_SECONDS,
            allowed_updates=["message"],
        )

        return (
            updates
            if isinstance(updates, list)
            else []
        )

    def _handle_update(
        self,
        update: dict[str, Any],
    ) -> None:

        update_id = update.get(
            "update_id"
        )

        if isinstance(update_id, int):

            self._next_update_id = (
                update_id + 1
            )

        message = update.get(
            "message"
        )

        if not isinstance(
            message,
            dict,
        ):
            return

        text = message.get("text")

        chat = message.get("chat")

        chat_id = (
            chat.get("id")
            if isinstance(chat, dict)
            else None
        )

        if (
            isinstance(text, str)
            and text.split(
                maxsplit=1
            )[0].split(
                "@",
                maxsplit=1,
            )[0] == "/start"
            and isinstance(chat_id, int)
        ):

            with self._subscriber_lock:

                self._subscriber_chat_ids.add(
                    chat_id
                )

            self._client.call(
                "sendMessage",
                chat_id=chat_id,
                text=START_MESSAGE,
            )

            LOGGER.info(
                "Replied to /start in chat %s",
                chat_id,
            )

    # -------------------------------------------------------------------
    # SCAN
    # -------------------------------------------------------------------

    def _maybe_scan(self) -> None:

        now = time.monotonic()

        max_tokens = int(
            _env_float(
                "DEX_MAX_TOKENS_PER_SCAN",
                200,
            )
        )

        pairs = (
            self._dex_client
            .discover_solana_pairs(
                max_tokens
            )
        )

        (
            current_snapshots,
            fallback_stats,
        ) = select_best_token_snapshots(
            pairs
        )

        ignition_count = 0

        # ---------------------------------------------------------------
        # Process every token
        # ---------------------------------------------------------------

        for snapshot in (
            current_snapshots.values()
        ):

            # Runner range filter.
            if (
                snapshot.market_cap_usd
                is None
            ):
                continue

            if not (
                self._min_market_cap_usd
                <= snapshot.market_cap_usd
                <= self._max_market_cap_usd
            ):
                continue

            if (
                snapshot.liquidity_usd
                is None
                or snapshot.liquidity_usd
                < self._min_liquidity_usd
            ):
                continue

            if (
                snapshot.volume_5m_usd
                is None
                or snapshot.volume_5m_usd
                < self._min_volume_5m_usd
            ):
                continue

            # Add the observation BEFORE calculating features.
            self._market_history.add(
                snapshot
            )

            features = (
                self._market_history.features(
                    snapshot
                )
            )

            LOGGER.info(
                "RUNNER DATA $%s | "
                "state=%s | obs=%s | "
                "MC=%s | "
                "5mVol=%s | "
                "buys/sells=%s/%s | "
                "volAccel=%s | "
                "txnAccel=%s | "
                "MCmove=%s%% | "
                "liqMove=%s%% | "
                "range=%s%% | "
                "breakout=%s | "
                "age=%s",
                snapshot.symbol,
                features.state,
                features.observation_count,
                _format_compact_usd(
                    snapshot.market_cap_usd
                ),
                _format_compact_usd(
                    snapshot.volume_5m_usd
                ),
                snapshot.buys_5m,
                snapshot.sells_5m,
                (
                    f"{features.volume_acceleration:.2f}x"
                    if features.volume_acceleration
                    is not None
                    else "n/a"
                ),
                (
                    f"{features.transaction_acceleration:.2f}x"
                    if features.transaction_acceleration
                    is not None
                    else "n/a"
                ),
                _format_number(
                    features.market_cap_change_percent
                ),
                _format_number(
                    features.liquidity_change_percent
                ),
                _format_number(
                    features.consolidation_range_percent
                ),
                features.breakout_above_recent_high,
                (
                    f"{snapshot.token_age_hours:.1f}h"
                    if snapshot.token_age_hours
                    is not None
                    else "n/a"
                ),
            )

            # -----------------------------------------------------------
            # IGNITION
            # -----------------------------------------------------------

            if not features.ignition_candidate:
                continue

            ignition_count += 1

            LOGGER.warning(
                "🔥 IGNITION CANDIDATE: $%s | CA=%s | reasons=%s",
                snapshot.symbol,
                snapshot.address,
                ", ".join(features.reasons),
            )

            if self._alerts_enabled:

                self._send_signal_if_allowed(
                    snapshot,
                    features,
                    now,
                )

        LOGGER.info(
            "DEX scan complete: "
            "%s Solana pairs discovered | "
            "%s unique tokens evaluated | "
            "%s ignition candidates | "
            "missing liquidity after fallback=%s",
            len(pairs),
            len(current_snapshots),
            ignition_count,
            fallback_stats.missing_liquidity_after,
        )

    # -------------------------------------------------------------------
    # ALERT
    # -------------------------------------------------------------------

    def _send_signal_if_allowed(
        self,
        snapshot: TokenSnapshot,
        features: RunnerFeatures,
        now: float,
    ) -> None:

        last_alert = (
            self._last_alert_at.get(
                snapshot.address
            )
        )

        if (
            last_alert is not None
            and now - last_alert
            < self._alert_cooldown_seconds
        ):
            return

        message = format_runner_signal(
            snapshot,
            features,
        )

        with self._subscriber_lock:

            subscriber_chat_ids = list(
                self._subscriber_chat_ids
            )

        if not subscriber_chat_ids:

            LOGGER.info(
                "Ignition detected for $%s "
                "but there are no subscribed Telegram chats",
                snapshot.symbol,
            )

            return

        failed_chats: list[int] = []

        for chat_id in subscriber_chat_ids:

            try:

                self._client.call(
                    "sendMessage",
                    chat_id=chat_id,
                    text=message,
                )

            except TelegramApiError as exc:

                failed_chats.append(
                    chat_id
                )

                LOGGER.error(
                    "Could not send "
                    "$%s alert to chat %s: %s",
                    snapshot.symbol,
                    chat_id,
                    exc,
                )

        if len(failed_chats) < len(
            subscriber_chat_ids
        ):

            self._last_alert_at[
                snapshot.address
            ] = now

            LOGGER.info(
                "Sent IGNITION alert for $%s",
                snapshot.symbol,
            )


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main() -> None:

    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(asctime)s "
            "%(levelname)s "
            "%(name)s: "
            "%(message)s"
        ),
    )

    token = os.environ.get(
        "TELEGRAM_BOT_TOKEN"
    )

    if not token:

        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is required. "
            "Add the token from BotFather as a secret."
        )

    bot = RunnerBot(
        TelegramClient(token)
    )

    signal.signal(
        signal.SIGINT,
        bot.stop,
    )

    signal.signal(
        signal.SIGTERM,
        bot.stop,
    )

    bot.run()


if __name__ == "__main__":
    main()
