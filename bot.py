"""Telegram bot for conservative, early Solana "heating up" signals.

The bot uses Telegram long polling and the public DEX Screener API. It uses
5-minute transaction counts for directional activity and never treats them as
dollar net flow.
"""

from __future__ import annotations

import json
import logging
import math
import os
import signal
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any
from urllib import error, parse, request


LOGGER = logging.getLogger("runner_bot")

POLL_TIMEOUT_SECONDS = 30
RETRY_DELAY_SECONDS = 5
START_MESSAGE = "Runner bot is online!"
DEX_API_BASE = "https://api.dexscreener.com"
SOLANA_CHAIN = "solana"
DEFAULT_SCAN_INTERVAL_SECONDS = 15
DEFAULT_MAX_TOKENS_PER_SCAN = 200
DEFAULT_ALERT_COOLDOWN_SECONDS = 3_600
MAX_TOKENS_PER_REQUEST = 30

DISCLAIMER = (
    "Disclaimer: For informational purposes only. Not investment advice or a "
    "token advertisement. We accept no responsibility for investment decisions "
    "or losses. Do your own research and make your own investment decisions."
)


@dataclass
class TelegramApiError(Exception):
    """Raised when Telegram returns an unsuccessful API response."""

    method: str
    description: str

    def __str__(self) -> str:
        return f"Telegram API request {self.method!r} failed: {self.description}"


@dataclass
class DexScreenerApiError(Exception):
    """Raised when a DEX Screener request cannot be completed."""

    url: str
    description: str

    def __str__(self) -> str:
        return f"DEX Screener request failed for {self.url}: {self.description}"


@dataclass(frozen=True)
class TokenSnapshot:
    """The source-backed metrics used to decide whether a token is heating up."""

    address: str
    symbol: str
    market_cap_usd: float | None
    liquidity_usd: float | None
    volume_5m_usd: float | None
    price_change_5m_percent: float | None
    buys_5m: int | None
    sells_5m: int | None

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
            return float("inf") if self.buys_5m > 0 else None
        return self.buys_5m / self.sells_5m


@dataclass(frozen=True)
class PairFallbackStats:
    """Field availability before and after same-token pair fallback."""

    missing_market_cap: int
    missing_liquidity_before: int
    missing_liquidity_after: int
    missing_price_change_before: int
    missing_price_change_after: int


@dataclass(frozen=True)
class FreshnessObservation:
    """A source-backed metric snapshot for one 7/7 observation."""

    observed_at: str
    market_cap_usd: float | None
    liquidity_usd: float | None
    volume_5m_usd: float | None
    buys_5m: int | None
    sells_5m: int | None
    buy_sell_ratio_5m: float | None
    price_change_5m_percent: float | None


@dataclass
class FreshnessRecord:
    """One uninterrupted 7/7 episode for a token."""

    address: str
    symbol: str
    first_reached_at: str
    consecutive_scans: int
    observations: list[FreshnessObservation]
    active: bool = True
    ended_at: str | None = None
    end_reason: str | None = None

    @property
    def first_observation(self) -> FreshnessObservation:
        return self.observations[0]

    @property
    def final_observation(self) -> FreshnessObservation:
        return self.observations[-1]


def _number(value: Any) -> float | None:
    """Return a finite numeric value without coercing missing data."""

    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _count(value: Any) -> int | None:
    number = _number(value)
    if number is None or number < 0:
        return None
    return int(number)


def _window(data: Any, name: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    value = data.get(name) or data.get(name.replace("m", ""))
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


def snapshot_from_pair(pair: dict[str, Any]) -> TokenSnapshot | None:
    """Convert one DEX Screener pair into a strictly source-backed snapshot."""

    base_token = pair.get("baseToken")
    if not isinstance(base_token, dict):
        return None

    address = base_token.get("address")
    if not isinstance(address, str) or not address:
        return None

    symbol = base_token.get("symbol")
    safe_symbol = str(symbol).strip().replace("$", "") if symbol else "UNKNOWN"
    txns = pair.get("txns")
    txns_5m = _window(txns, "m5")
    volume = pair.get("volume")
    liquidity = pair.get("liquidity")
    price_change = pair.get("priceChange")

    return TokenSnapshot(
        address=address,
        symbol=safe_symbol or "UNKNOWN",
        market_cap_usd=_number(pair.get("marketCap")),
        liquidity_usd=_metric(liquidity, "usd"),
        volume_5m_usd=_metric(volume, "m5"),
        price_change_5m_percent=_metric(price_change, "m5"),
        buys_5m=_count(txns_5m.get("buys")),
        sells_5m=_count(txns_5m.get("sells")),
    )


def select_best_token_snapshots(
    pairs: list[dict[str, Any]],
) -> tuple[dict[str, TokenSnapshot], PairFallbackStats]:
    """Select one pair per token and fill only explicitly missing pair fields.

    The selected pair is the highest-liquidity pair with a known liquidity
    value. If the selected pair lacks liquidity or 5m price change, only that
    missing field is taken from another valid Solana pair for the same token.
    No metric is estimated from another field.
    """

    grouped: dict[str, list[TokenSnapshot]] = {}
    for pair in pairs:
        if pair.get("chainId") != SOLANA_CHAIN:
            continue
        snapshot = snapshot_from_pair(pair)
        if snapshot is not None:
            grouped.setdefault(snapshot.address, []).append(snapshot)

    selected_snapshots: dict[str, TokenSnapshot] = {}
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
                snapshot.liquidity_usd if snapshot.liquidity_usd is not None else -1,
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

        selected_snapshots[address] = replace(
            selected,
            liquidity_usd=liquidity,
            price_change_5m_percent=price_change,
        )

    return selected_snapshots, PairFallbackStats(
        missing_market_cap=missing_market_cap,
        missing_liquidity_before=missing_liquidity_before,
        missing_liquidity_after=missing_liquidity_after,
        missing_price_change_before=missing_price_change_before,
        missing_price_change_after=missing_price_change_after,
    )


class FreshnessTracker:
    """Track uninterrupted 7/7 streaks without sending Telegram alerts."""

    def __init__(self, condition_config: dict[str, float]) -> None:
        self._condition_config = condition_config
        self._records: dict[str, list[FreshnessRecord]] = {}
        self._active: dict[str, FreshnessRecord] = {}

    @staticmethod
    def _observation(
        snapshot: TokenSnapshot, observed_at: str
    ) -> FreshnessObservation:
        return FreshnessObservation(
            observed_at=observed_at,
            market_cap_usd=snapshot.market_cap_usd,
            liquidity_usd=snapshot.liquidity_usd,
            volume_5m_usd=snapshot.volume_5m_usd,
            buys_5m=snapshot.buys_5m,
            sells_5m=snapshot.sells_5m,
            buy_sell_ratio_5m=snapshot.buy_sell_ratio_5m,
            price_change_5m_percent=snapshot.price_change_5m_percent,
        )

    @staticmethod
    def _failed_conditions(results: dict[str, bool]) -> str:
        failed = [name for name, passed in results.items() if not passed]
        return ", ".join(failed) if failed else "unknown"

    def _finish(self, record: FreshnessRecord, ended_at: str, reason: str) -> None:
        record.active = False
        record.ended_at = ended_at
        record.end_reason = reason
        self._active.pop(record.address, None)

    def observe_scan(
        self,
        snapshots: dict[str, TokenSnapshot],
        observed_at: str,
    ) -> None:
        observed_addresses: set[str] = set()

        for address, snapshot in snapshots.items():
            observed_addresses.add(address)
            results = heating_condition_results(snapshot, **self._condition_config)
            if not all(results.values()):
                active_record = self._active.get(address)
                if active_record is not None:
                    self._finish(
                        active_record,
                        observed_at,
                        f"failed conditions: {self._failed_conditions(results)}",
                    )
                continue

            record = self._active.get(address)
            if record is None:
                record = FreshnessRecord(
                    address=address,
                    symbol=snapshot.symbol,
                    first_reached_at=observed_at,
                    consecutive_scans=0,
                    observations=[],
                )
                self._records.setdefault(address, []).append(record)
                self._active[address] = record
            record.consecutive_scans += 1
            record.observations.append(self._observation(snapshot, observed_at))

        for address, record in list(self._active.items()):
            if address not in observed_addresses:
                self._finish(record, observed_at, "token was not discovered in the scan")

    def records(self) -> list[FreshnessRecord]:
        return [
            record
            for token_records in self._records.values()
            for record in token_records
        ]


def heating_condition_results(
    current: TokenSnapshot,
    *,
    min_market_cap_usd: float,
    max_market_cap_usd: float,
    min_liquidity_usd: float,
    min_volume_5m_usd: float,
    min_buy_sell_ratio: float,
    max_price_change_5m_percent: float,
) -> dict[str, bool]:
    """Return each source-backed heating condition independently."""

    return {
        "market_cap": (
            current.market_cap_usd is not None
            and min_market_cap_usd <= current.market_cap_usd <= max_market_cap_usd
        ),
        "liquidity": (
            current.liquidity_usd is not None
            and current.liquidity_usd >= min_liquidity_usd
        ),
        "volume_5m": (
            current.volume_5m_usd is not None
            and current.volume_5m_usd >= min_volume_5m_usd
        ),
        "buys_gt_sells": (
            current.buys_5m is not None
            and current.sells_5m is not None
            and current.buys_5m > current.sells_5m
        ),
        "buy_sell_ratio": (
            current.buy_sell_ratio_5m is not None
            and current.buy_sell_ratio_5m >= min_buy_sell_ratio
        ),
        "positive_price_change": (
            current.price_change_5m_percent is not None
            and current.price_change_5m_percent > 0
        ),
        "price_change_within_cap": (
            current.price_change_5m_percent is not None
            and current.price_change_5m_percent <= max_price_change_5m_percent
        ),
    }


def is_heating_up(
    current: TokenSnapshot,
    *,
    min_market_cap_usd: float,
    max_market_cap_usd: float,
    min_liquidity_usd: float,
    min_volume_5m_usd: float,
    min_buy_sell_ratio: float,
    max_price_change_5m_percent: float,
) -> bool:
    """Require all seven configured conditions before alerting."""

    return all(
        heating_condition_results(
            current,
            min_market_cap_usd=min_market_cap_usd,
            max_market_cap_usd=max_market_cap_usd,
            min_liquidity_usd=min_liquidity_usd,
            min_volume_5m_usd=min_volume_5m_usd,
            min_buy_sell_ratio=min_buy_sell_ratio,
            max_price_change_5m_percent=max_price_change_5m_percent,
        ).values()
    )


def format_heating_signal(snapshot: TokenSnapshot) -> str:
    """Render the user-requested signal format."""

    market_cap = _format_compact_usd(snapshot.market_cap_usd)
    volume_5m = _format_compact_usd(snapshot.volume_5m_usd)
    buys_5m = "unavailable" if snapshot.buys_5m is None else str(snapshot.buys_5m)
    sells_5m = "unavailable" if snapshot.sells_5m is None else str(snapshot.sells_5m)
    ratio = snapshot.buy_sell_ratio_5m
    ratio_text = (
        "unavailable"
        if ratio is None
        else "infinite"
        if ratio == float("inf")
        else f"{ratio:.2f}"
    )

    return (
        "👀 HEATING UP\n\n"
        f"${snapshot.symbol}\n"
        f"💎 MC at call: ${market_cap}\n\n"
        "👀 Keep this ticker close. The setup is warming up; "
        "follow-through still matters.\n\n"
        "📊 5m activity is heating up.\n"
        f"🔥 ${volume_5m} volume in 5m.\n"
        f"🟢 5m buys/sells: {buys_5m}/{sells_5m} (ratio {ratio_text}).\n\n"
        "📋 CA\n"
        f"{snapshot.address}\n\n"
        f"{DISCLAIMER}"
    )


class TelegramClient:
    """Small standard-library client for the Telegram Bot API."""

    def __init__(self, token: str) -> None:
        self._api_url = f"https://api.telegram.org/bot{token}"

    def call(self, method: str, **payload: Any) -> Any:
        encoded_payload = parse.urlencode(
            {
                key: json.dumps(value) if isinstance(value, (dict, list)) else value
                for key, value in payload.items()
                if value is not None
            }
        ).encode("utf-8")
        request_data = request.Request(
            f"{self._api_url}/{method}",
            data=encoded_payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )

        try:
            with request.urlopen(
                request_data, timeout=POLL_TIMEOUT_SECONDS + 10
            ) as response:
                body = json.loads(response.read().decode("utf-8"))
        except error.URLError as exc:
            raise TelegramApiError(method, str(exc.reason)) from exc
        except (TimeoutError, json.JSONDecodeError) as exc:
            raise TelegramApiError(method, str(exc)) from exc

        if not body.get("ok"):
            raise TelegramApiError(method, body.get("description", "Unknown API error"))
        return body["result"]


class DexScreenerClient:
    """Client for discovering a broad, deduplicated Solana pair pool."""

    def _get_json(self, path: str) -> Any:
        url = f"{DEX_API_BASE}{path}"
        request_data = request.Request(url, headers={"User-Agent": "runner-bot/1.0"})
        try:
            with request.urlopen(request_data, timeout=20) as response:
                body = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            raise DexScreenerApiError(url, f"HTTP {exc.code}") from exc
        except error.URLError as exc:
            raise DexScreenerApiError(url, str(exc.reason)) from exc
        except (TimeoutError, json.JSONDecodeError) as exc:
            raise DexScreenerApiError(url, str(exc)) from exc

        if not isinstance(body, (dict, list)):
            raise DexScreenerApiError(url, "response was not a JSON object or list")
        return body

    @staticmethod
    def _solana_addresses(response: Any) -> list[str]:
        records = response if isinstance(response, list) else []
        addresses: list[str] = []
        seen: set[str] = set()
        for record in records:
            if not isinstance(record, dict) or record.get("chainId") != SOLANA_CHAIN:
                continue
            address = record.get("tokenAddress")
            if isinstance(address, str) and address not in seen:
                seen.add(address)
                addresses.append(address)
        return addresses

    @staticmethod
    def _pair_key(pair: dict[str, Any]) -> str | None:
        pair_address = pair.get("pairAddress")
        if isinstance(pair_address, str) and pair_address:
            return pair_address
        base_token = pair.get("baseToken")
        quote_token = pair.get("quoteToken")
        if not isinstance(base_token, dict) or not isinstance(quote_token, dict):
            return None
        base_address = base_token.get("address")
        quote_address = quote_token.get("address")
        dex_id = pair.get("dexId")
        if not all(isinstance(value, str) and value for value in (base_address, quote_address, dex_id)):
            return None
        return f"{dex_id}:{base_address}:{quote_address}"

    def _search_solana_pairs(self, query: str) -> list[dict[str, Any]]:
        response = self._get_json(
            f"/latest/dex/search?q={parse.quote(query, safe='')}"
        )
        pairs = response.get("pairs", []) if isinstance(response, dict) else []
        return [
            pair
            for pair in pairs
            if isinstance(pair, dict) and pair.get("chainId") == SOLANA_CHAIN
        ]

    def discover_solana_pairs(self, max_tokens: int) -> list[dict[str, Any]]:
        """Combine several public discovery feeds, then remove duplicate pairs."""

        discovery_paths = (
            "/token-profiles/latest/v1",
            "/token-boosts/latest/v1",
            "/token-boosts/top/v1",
        )
        addresses: list[str] = []
        address_seen: set[str] = set()
        for path in discovery_paths:
            for address in self._solana_addresses(self._get_json(path)):
                if address not in address_seen and len(addresses) < max_tokens:
                    address_seen.add(address)
                    addresses.append(address)

        pairs: list[dict[str, Any]] = []
        pair_seen: set[str] = set()
        for start in range(0, len(addresses), MAX_TOKENS_PER_REQUEST):
            chunk = addresses[start : start + MAX_TOKENS_PER_REQUEST]
            encoded_addresses = ",".join(
                parse.quote(address, safe="") for address in chunk
            )
            pair_response = self._get_json(f"/latest/dex/tokens/{encoded_addresses}")
            response_pairs = (
                pair_response.get("pairs", [])
                if isinstance(pair_response, dict)
                else []
            )
            if isinstance(response_pairs, list):
                for pair in response_pairs:
                    if not isinstance(pair, dict) or pair.get("chainId") != SOLANA_CHAIN:
                        continue
                    key = self._pair_key(pair)
                    if key is not None and key not in pair_seen:
                        pair_seen.add(key)
                        pairs.append(pair)

        # Pair search adds market coverage beyond the latest-profile and boost
        # feeds. Search results are filtered to Solana and deduplicated above.
        for query in ("SOL", "USDC", "USDT", "WSOL"):
            for pair in self._search_solana_pairs(query):
                key = self._pair_key(pair)
                if key is not None and key not in pair_seen:
                    pair_seen.add(key)
                    pairs.append(pair)
        return pairs


class RunnerBot:
    """Long-polling bot that subscribes chats and sends heating-up alerts."""

    def __init__(
        self,
        client: TelegramClient,
        dex_client: DexScreenerClient | None = None,
    ) -> None:
        self._client = client
        self._dex_client = dex_client or DexScreenerClient()
        self._next_update_id: int | None = None
        self._running = True
        self._subscriber_chat_ids: set[int] = set()
        self._subscriber_lock = threading.Lock()
        self._last_alert_at: dict[str, float] = {}
        self._scan_interval_seconds = _env_float(
            "DEX_SCAN_INTERVAL_SECONDS", DEFAULT_SCAN_INTERVAL_SECONDS
        )
        self._min_market_cap_usd = _env_float(
            "HEATING_MIN_MARKET_CAP_USD", 20_000
        )
        self._max_market_cap_usd = _env_float(
            "HEATING_MAX_MARKET_CAP_USD", 150_000
        )
        self._min_liquidity_usd = _env_float(
            "HEATING_MIN_LIQUIDITY_USD", 10_000
        )
        self._min_volume_5m_usd = _env_float(
            "HEATING_MIN_VOLUME_5M_USD", 5_000
        )
        self._min_buy_sell_ratio = _env_float(
            "HEATING_MIN_BUY_SELL_RATIO", 1.5
        )
        self._max_price_change_5m_percent = _env_float(
            "HEATING_MAX_PRICE_CHANGE_5M_PERCENT", 50
        )
        self._condition_config = {
            "min_market_cap_usd": self._min_market_cap_usd,
            "max_market_cap_usd": self._max_market_cap_usd,
            "min_liquidity_usd": self._min_liquidity_usd,
            "min_volume_5m_usd": self._min_volume_5m_usd,
            "min_buy_sell_ratio": self._min_buy_sell_ratio,
            "max_price_change_5m_percent": self._max_price_change_5m_percent,
        }
        self._freshness_tracker = FreshnessTracker(self._condition_config)
        self._alerts_enabled = _env_bool("HEATING_ALERTS_ENABLED", False)
        self._alert_cooldown_seconds = _env_float(
            "HEATING_ALERT_COOLDOWN_SECONDS", DEFAULT_ALERT_COOLDOWN_SECONDS
        )

    def stop(self, _signum: int, _frame: Any) -> None:
        LOGGER.info("Shutdown requested")
        self._running = False

    def run(self) -> None:
        self._client.call("getMe")
        LOGGER.info("Runner bot is online and listening for Telegram messages")
        LOGGER.info(
            "Heating scanner configured: MC=$%s-$%s, min liquidity=$%s, "
            "min 5m volume=$%s, min buy/sell ratio=%s, max 5m change=%s%%, "
            "scan interval=%ss",
            _format_compact_usd(self._min_market_cap_usd),
            _format_compact_usd(self._max_market_cap_usd),
            _format_compact_usd(self._min_liquidity_usd),
            _format_compact_usd(self._min_volume_5m_usd),
            self._min_buy_sell_ratio,
            self._max_price_change_5m_percent,
            self._scan_interval_seconds,
        )
        LOGGER.info("Telegram alerts enabled: %s", self._alerts_enabled)

        scanner_thread = threading.Thread(
            target=self._scan_loop,
            name="dex-scanner",
            daemon=True,
        )
        scanner_thread.start()
        try:
            while self._running:
                try:
                    updates = self._get_updates()
                    for update in updates:
                        self._handle_update(update)
                except TelegramApiError as exc:
                    LOGGER.error("%s", exc)
                    if self._running:
                        time.sleep(RETRY_DELAY_SECONDS)
        finally:
            self._running = False
            scanner_thread.join(timeout=self._scan_interval_seconds + 5)

        LOGGER.info("Runner bot stopped")

    def _scan_loop(self) -> None:
        while self._running:
            try:
                self._maybe_scan()
            except DexScreenerApiError as exc:
                LOGGER.error("%s", exc)
            if self._running:
                time.sleep(self._scan_interval_seconds)

    def _get_updates(self) -> list[dict[str, Any]]:
        updates = self._client.call(
            "getUpdates",
            offset=self._next_update_id,
            timeout=POLL_TIMEOUT_SECONDS,
            allowed_updates=["message"],
        )
        return updates if isinstance(updates, list) else []

    def _handle_update(self, update: dict[str, Any]) -> None:
        update_id = update.get("update_id")
        if isinstance(update_id, int):
            self._next_update_id = update_id + 1

        message = update.get("message")
        if not isinstance(message, dict):
            return

        text = message.get("text")
        chat = message.get("chat")
        chat_id = chat.get("id") if isinstance(chat, dict) else None
        if (
            isinstance(text, str)
            and text.split(maxsplit=1)[0].split("@", maxsplit=1)[0] == "/start"
            and isinstance(chat_id, int)
        ):
            with self._subscriber_lock:
                self._subscriber_chat_ids.add(chat_id)
            self._client.call("sendMessage", chat_id=chat_id, text=START_MESSAGE)
            LOGGER.info("Replied to /start in chat %s", chat_id)

    def _maybe_scan(self) -> None:
        now = time.monotonic()

        pairs = self._dex_client.discover_solana_pairs(
            int(_env_float("DEX_MAX_TOKENS_PER_SCAN", DEFAULT_MAX_TOKENS_PER_SCAN))
        )
        current_snapshots, _fallback_stats = select_best_token_snapshots(pairs)
        observed_at = datetime.now(timezone.utc)
        self._freshness_tracker.observe_scan(
            current_snapshots,
            observed_at.isoformat(),
        )
        candidate_count = 0

        for snapshot in current_snapshots.values():
            if is_heating_up(
                snapshot,
                **self._condition_config,
            ):
                candidate_count += 1
                if self._alerts_enabled:
                    self._send_signal_if_allowed(snapshot, now)
                else:
                    LOGGER.info(
                        "7/7 candidate observed for $%s; Telegram alerts disabled",
                        snapshot.symbol,
                    )

        LOGGER.info(
            "DEX scan complete: %s Solana pairs discovered, %s unique tokens evaluated, "
            "%s heating candidates",
            len(pairs),
            len(current_snapshots),
            candidate_count,
        )

    def _send_signal_if_allowed(self, snapshot: TokenSnapshot, now: float) -> None:
        last_alert = self._last_alert_at.get(snapshot.address)
        if last_alert is not None and now - last_alert < self._alert_cooldown_seconds:
            return

        message = format_heating_signal(snapshot)
        with self._subscriber_lock:
            subscriber_chat_ids = list(self._subscriber_chat_ids)
        failed_chats: list[int] = []
        for chat_id in subscriber_chat_ids:
            try:
                self._client.call("sendMessage", chat_id=chat_id, text=message)
            except TelegramApiError as exc:
                failed_chats.append(chat_id)
                LOGGER.error("Could not send %s alert to chat %s: %s", snapshot.symbol, chat_id, exc)

        if len(failed_chats) < len(subscriber_chat_ids):
            self._last_alert_at[snapshot.address] = now
            LOGGER.info("Sent HEATING UP alert for $%s", snapshot.symbol)


def _env_float(name: str, default: float) -> float:
    value = _number(os.environ.get(name))
    return value if value is not None and value >= 0 else default


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is required. Add the token from BotFather as a secret."
        )

    bot = RunnerBot(TelegramClient(token))
    signal.signal(signal.SIGINT, bot.stop)
    signal.signal(signal.SIGTERM, bot.stop)
    bot.run()


if __name__ == "__main__":
    main()