"""Generator for the seeded fraud dataset.

Ten scenarios, one per typology in the problem statement, each with a known
ground-truth VASP so attribution accuracy is measurable rather than anecdotal.

Design rule: exchange **hot wallets** get labels (they are public knowledge),
but exchange **deposit addresses** never do. The deposit address must be
discovered by the consolidation heuristic, exactly as it would be on mainnet.
Every deposit address therefore gets realistic background traffic - unrelated
depositors paying in, and periodic sweeps out to the hot wallet.
"""

from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.core.enums import Chain, EntityType, FraudCategory, Typology
from app.seeds.addresses import make_address, make_tx_hash

# Rough spot prices; exact values do not matter, consistent ones do.
PRICE_USD: dict[str, float] = {
    "USDT": 1.0,
    "USDC": 1.0,
    "BTC": 64_000.0,
    "ETH": 3_300.0,
    "BNB": 590.0,
    "POL": 0.48,
    "TRX": 0.14,
}

USD_TO_INR = 83.5

BLOCK_SECONDS: dict[Chain, int] = {
    Chain.BITCOIN: 600,
    Chain.ETHEREUM: 12,
    Chain.BSC: 3,
    Chain.POLYGON: 2,
    Chain.ARBITRUM: 1,
    Chain.BASE: 2,
    Chain.TRON: 3,
}

GENESIS_BLOCK: dict[Chain, int] = {
    Chain.BITCOIN: 840_000,
    Chain.ETHEREUM: 20_100_000,
    Chain.BSC: 39_800_000,
    Chain.POLYGON: 58_600_000,
    Chain.ARBITRUM: 225_000_000,
    Chain.BASE: 16_400_000,
    Chain.TRON: 62_400_000,
}

TOKEN_CONTRACTS: dict[tuple[Chain, str], str] = {
    (Chain.TRON, "USDT"): "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t",
    (Chain.ETHEREUM, "USDT"): "0xdac17f958d2ee523a2206206994597c13d831ec7",
    (Chain.ETHEREUM, "USDC"): "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
    (Chain.BSC, "USDT"): "0x55d398326f99059ff775485246999027b3197955",
    (Chain.POLYGON, "USDT"): "0xc2132d05d31c914a87c6611c10748aeb04b58e8f",
}


@dataclass
class SynTransfer:
    scenario: str
    chain: Chain
    tx_hash: str
    trace_index: int
    block_number: int
    ts: datetime
    src_address: str
    dst_address: str
    asset: str
    amount: Decimal
    amount_usd: float
    is_token: bool
    token_contract: str | None
    input_group: str | None = None


@dataclass
class SynLabel:
    chain: Chain
    address: str
    label: str
    entity_name: str
    entity_type: EntityType
    vasp_slug: str | None
    confidence: float = 0.98
    is_sanctioned: bool = False


@dataclass
class Scenario:
    key: str
    title: str
    fraud_category: FraudCategory
    chain: Chain
    seed_address: str
    expected_vasp_slug: str
    expected_deposit_address: str
    expected_hops: int
    expected_typologies: list[Typology]
    victim_name: str
    victim_state: str
    police_station: str
    loss_amount_inr: float
    incident_at: datetime
    narrative: str
    transfers: list[SynTransfer] = field(default_factory=list)
    labels: list[SynLabel] = field(default_factory=list)
    notes: str = ""


class ScenarioBuilder:
    """Accumulates transfers for one scenario with consistent time and blocks."""

    def __init__(self, key: str, chain: Chain, start: datetime, rng_seed: int) -> None:
        self.key = key
        self.chain = chain
        self.start = start
        self.rng = random.Random(rng_seed)
        self.transfers: list[SynTransfer] = []
        self.labels: list[SynLabel] = []
        self._tx_counter = 0
        self._input_group_seq: defaultdict[str, int] = defaultdict(int)

    # -- helpers ---------------------------------------------------------

    def addr(self, role: str) -> str:
        return make_address(self.chain, f"{self.key}:{role}")

    def addr_on(self, chain: Chain, role: str) -> str:
        return make_address(chain, f"{self.key}:{role}")

    def _block_for(self, ts: datetime, chain: Chain) -> int:
        elapsed = max(0, int((ts - self.start).total_seconds()))
        return GENESIS_BLOCK[chain] + elapsed // BLOCK_SECONDS[chain]

    def transfer(
        self,
        src: str,
        dst: str,
        amount: float | Decimal,
        asset: str,
        *,
        minutes: float,
        chain: Chain | None = None,
        input_group: str | None = None,
        tag: str | None = None,
    ) -> SynTransfer:
        chain = chain or self.chain
        ts = self.start + timedelta(minutes=minutes)
        self._tx_counter += 1
        # Co-spent inputs belong to one on-chain transaction, so they share a
        # hash and are distinguished by trace index - which is precisely what
        # the common-input-ownership heuristic keys on.
        if input_group is not None:
            label = input_group
            trace_index = self._input_group_seq[input_group]
            self._input_group_seq[input_group] += 1
        else:
            label = tag or f"{self.key}:{self._tx_counter}"
            trace_index = 0
        amount_dec = Decimal(str(amount))
        token_contract = TOKEN_CONTRACTS.get((chain, asset))
        record = SynTransfer(
            scenario=self.key,
            chain=chain,
            tx_hash=make_tx_hash(chain, label),
            trace_index=trace_index,
            block_number=self._block_for(ts, chain),
            ts=ts,
            src_address=src,
            dst_address=dst,
            asset=asset,
            amount=amount_dec,
            amount_usd=round(float(amount_dec) * PRICE_USD.get(asset, 1.0), 2),
            is_token=token_contract is not None,
            token_contract=token_contract,
            input_group=input_group,
        )
        self.transfers.append(record)
        return record

    def label(
        self,
        address: str,
        label: str,
        entity_name: str,
        entity_type: EntityType,
        vasp_slug: str | None = None,
        *,
        chain: Chain | None = None,
        is_sanctioned: bool = False,
    ) -> None:
        self.labels.append(
            SynLabel(
                chain=chain or self.chain,
                address=address,
                label=label,
                entity_name=entity_name,
                entity_type=entity_type,
                vasp_slug=vasp_slug,
                is_sanctioned=is_sanctioned,
            )
        )

    # -- composite structures --------------------------------------------

    def hot_wallet(self, vasp_slug: str, vasp_name: str, chain: Chain | None = None) -> str:
        """A publicly known exchange hot wallet - always labelled."""
        chain = chain or self.chain
        address = make_address(chain, f"hotwallet:{vasp_slug}:{chain.value}")
        self.label(
            address,
            f"{vasp_name} Hot Wallet",
            vasp_name,
            EntityType.CEX_HOT_WALLET,
            vasp_slug,
            chain=chain,
        )
        return address

    def deposit_address(
        self,
        vasp_slug: str,
        vasp_name: str,
        ref: str,
        *,
        chain: Chain | None = None,
        background_depositors: int = 7,
        asset: str = "USDT",
        first_minutes: float = -6_000,
    ) -> tuple[str, str]:
        """An exchange deposit address, left deliberately unlabelled.

        Emits the consolidation footprint the heuristic looks for: several
        unrelated payers in, then a full sweep out to the hot wallet.
        """
        chain = chain or self.chain
        hot = self.hot_wallet(vasp_slug, vasp_name, chain=chain)
        deposit = make_address(chain, f"deposit:{vasp_slug}:{ref}")

        cursor = first_minutes
        for i in range(background_depositors):
            payer = make_address(chain, f"payer:{vasp_slug}:{ref}:{i}")
            amount = round(self.rng.uniform(120, 4200), 2)
            cursor += self.rng.uniform(180, 900)
            self.transfer(
                payer, deposit, amount, asset, minutes=cursor, chain=chain,
                tag=f"{self.key}:bg:{vasp_slug}:{ref}:{i}",
            )
            # The sweep to the hot wallet is the signature of a deposit address.
            self.transfer(
                deposit, hot, amount, asset,
                minutes=cursor + self.rng.uniform(3, 40),
                chain=chain,
                tag=f"{self.key}:sweep:{vasp_slug}:{ref}:{i}",
            )
        return deposit, hot

    def sweep_to_hot(
        self, deposit: str, hot: str, amount: float, asset: str, *, minutes: float,
        chain: Chain | None = None, tag: str | None = None,
    ) -> None:
        self.transfer(deposit, hot, amount, asset, minutes=minutes, chain=chain, tag=tag)

    def noise_out(self, src: str, count: int, amount_range: tuple[float, float], asset: str,
                  *, start_minutes: float, prefix: str) -> list[str]:
        """Unrelated small outflows so real fan-out has something to hide in."""
        targets = []
        for i in range(count):
            dst = self.addr(f"{prefix}:{i}")
            self.transfer(
                src, dst, round(self.rng.uniform(*amount_range), 2), asset,
                minutes=start_minutes + i * self.rng.uniform(2, 25),
                tag=f"{self.key}:{prefix}:{i}",
            )
            targets.append(dst)
        return targets


# ---------------------------------------------------------------------------
# Scenario definitions
# ---------------------------------------------------------------------------

BASE_TIME = datetime(2026, 6, 14, 9, 30, tzinfo=UTC)


def _investment_scam_tron() -> Scenario:
    """The canonical Indian case: USDT-TRC20, three layers, ends at Binance."""
    key = "investment_scam_tron"
    b = ScenarioBuilder(key, Chain.TRON, BASE_TIME, 101)

    victim = b.addr("victim-wallet")
    collector = b.addr("scam-collector")
    layer1 = b.addr("layer-1")
    layer2 = b.addr("layer-2")
    deposit, _hot = b.deposit_address("binance", "Binance", "inv-01", background_depositors=8)

    b.label(collector, "Reported fraud collection wallet", "Unknown", EntityType.SCAM)

    # Victim pays in three tranches as the "platform" escalates its demands.
    b.transfer(victim, collector, 4_200, "USDT", minutes=0, tag=f"{key}:v1")
    b.transfer(victim, collector, 11_500, "USDT", minutes=1_440, tag=f"{key}:v2")
    b.transfer(victim, collector, 6_800, "USDT", minutes=4_320, tag=f"{key}:v3")

    # Other victims of the same operation pay the same collector.
    for i in range(5):
        other = b.addr(f"other-victim-{i}")
        b.transfer(other, collector, round(b.rng.uniform(1_500, 9_000), 2), "USDT",
                   minutes=200 + i * 620, tag=f"{key}:ov{i}")

    b.transfer(collector, layer1, 41_800, "USDT", minutes=4_500, tag=f"{key}:l1")
    b.noise_out(layer1, 4, (200, 900), "USDT", start_minutes=4_520, prefix="l1-noise")
    b.transfer(layer1, layer2, 39_200, "USDT", minutes=4_560, tag=f"{key}:l2")
    b.transfer(layer2, deposit, 38_400, "USDT", minutes=4_615, tag=f"{key}:l3")
    b.sweep_to_hot(deposit, b.hot_wallet("binance", "Binance"), 38_400, "USDT",
                   minutes=4_640, tag=f"{key}:final-sweep")

    return Scenario(
        key=key,
        title="Fake crypto trading platform - victim funds routed to Binance",
        fraud_category=FraudCategory.INVESTMENT_SCAM,
        chain=Chain.TRON,
        seed_address=victim,
        expected_vasp_slug="binance",
        expected_deposit_address=deposit,
        expected_hops=4,
        expected_typologies=[Typology.RAPID_RELAY],
        victim_name="Complainant A (fictional)",
        victim_state="Maharashtra",
        police_station="Cyber Police Station, Pune City",
        loss_amount_inr=round(22_500 * USD_TO_INR, 0),
        incident_at=BASE_TIME,
        narrative=(
            "Victim was onboarded through a Telegram group promising 12% monthly returns on a "
            "'licensed' trading app, and made three USDT-TRC20 deposits. Funds passed through "
            "two intermediary wallets before reaching a Binance deposit address."
        ),
        transfers=b.transfers,
        labels=b.labels,
        notes="Straight-line layering; the deposit address is only discoverable via consolidation.",
    )


def _task_fraud_tron() -> Scenario:
    """Many small victims aggregated, then split across two exchanges."""
    key = "task_fraud_tron"
    b = ScenarioBuilder(key, Chain.TRON, BASE_TIME + timedelta(days=3), 202)

    victim = b.addr("victim-wallet")
    aggregator = b.addr("task-aggregator")
    splitter = b.addr("splitter")
    dep_dcx, hot_dcx = b.deposit_address("coindcx", "CoinDCX", "task-01", background_depositors=6)
    dep_okx, hot_okx = b.deposit_address("okx", "OKX", "task-02", background_depositors=6)

    b.label(aggregator, "Task-fraud collection wallet", "Unknown", EntityType.SCAM)

    b.transfer(victim, aggregator, 780, "USDT", minutes=0, tag=f"{key}:v1")
    b.transfer(victim, aggregator, 2_400, "USDT", minutes=95, tag=f"{key}:v2")
    b.transfer(victim, aggregator, 5_100, "USDT", minutes=340, tag=f"{key}:v3")

    # 14 co-victims: classic task-based (prepaid "commission") fraud fan-in.
    for i in range(14):
        other = b.addr(f"covictim-{i}")
        b.transfer(other, aggregator, round(b.rng.uniform(300, 3_800), 2), "USDT",
                   minutes=30 + i * 47, tag=f"{key}:cv{i}")

    b.transfer(aggregator, splitter, 34_900, "USDT", minutes=700, tag=f"{key}:agg")

    # Structuring: amounts kept deliberately below a round reporting threshold.
    for i, amount in enumerate([9_800, 9_600, 9_400]):
        b.transfer(splitter, dep_dcx, amount, "USDT", minutes=760 + i * 31, tag=f"{key}:s-dcx{i}")
    for i, amount in enumerate([2_900, 3_100]):
        b.transfer(splitter, dep_okx, amount, "USDT", minutes=780 + i * 44, tag=f"{key}:s-okx{i}")

    b.sweep_to_hot(dep_dcx, hot_dcx, 28_800, "USDT", minutes=900, tag=f"{key}:sweep-dcx")
    b.sweep_to_hot(dep_okx, hot_okx, 6_000, "USDT", minutes=930, tag=f"{key}:sweep-okx")

    return Scenario(
        key=key,
        title="Task-based fraud - 15 victims aggregated and structured into two exchanges",
        fraud_category=FraudCategory.TASK_BASED_FRAUD,
        chain=Chain.TRON,
        seed_address=victim,
        expected_vasp_slug="coindcx",
        expected_deposit_address=dep_dcx,
        expected_hops=3,
        expected_typologies=[Typology.FAN_IN_AGGREGATION, Typology.STRUCTURING],
        victim_name="Complainant B (fictional)",
        victim_state="Karnataka",
        police_station="CEN Crime Police Station, Bengaluru",
        loss_amount_inr=round(8_280 * USD_TO_INR, 0),
        incident_at=BASE_TIME + timedelta(days=3),
        narrative=(
            "Victim joined a 'YouTube like-and-earn' task group, completed unpaid tasks, then "
            "was induced to make three prepaid deposits. Funds from 15 victims were aggregated "
            "and structured in sub-10k tranches across CoinDCX and OKX."
        ),
        transfers=b.transfers,
        labels=b.labels,
        notes="Two competing VASP endpoints; the ranked list must put CoinDCX first on value share.",
    )


def _sextortion_btc() -> Scenario:
    """Bitcoin case solved by common-input-ownership clustering."""
    key = "sextortion_btc"
    b = ScenarioBuilder(key, Chain.BITCOIN, BASE_TIME + timedelta(days=6), 303)

    victim = b.addr("victim-wallet")
    extortion = b.addr("extortion-wallet")
    sibling_a = b.addr("cluster-sibling-a")
    sibling_b = b.addr("cluster-sibling-b")
    consolidation = b.addr("cluster-consolidation")
    deposit, hot = b.deposit_address(
        "wazirx", "WazirX", "sxt-01", background_depositors=5, asset="BTC",
    )

    b.label(extortion, "Sextortion demand wallet", "Unknown", EntityType.SCAM)

    b.transfer(victim, extortion, 0.0085, "BTC", minutes=0, tag=f"{key}:v1")
    for i in range(6):
        other = b.addr(f"other-victim-{i}")
        b.transfer(other, extortion if i % 2 else sibling_a,
                   round(b.rng.uniform(0.002, 0.012), 6), "BTC",
                   minutes=60 + i * 220, tag=f"{key}:ov{i}")

    # Co-spend: these three addresses appear as inputs to one transaction, so
    # common-input-ownership puts them in a single wallet cluster.
    cospend = f"{key}:cospend-1"
    b.transfer(extortion, consolidation, 0.031, "BTC", minutes=1_500,
               input_group=cospend, tag=f"{key}:cs1")
    b.transfer(sibling_a, consolidation, 0.018, "BTC", minutes=1_500,
               input_group=cospend, tag=f"{key}:cs2")
    b.transfer(sibling_b, consolidation, 0.009, "BTC", minutes=1_500,
               input_group=cospend, tag=f"{key}:cs3")

    b.transfer(consolidation, deposit, 0.057, "BTC", minutes=1_740, tag=f"{key}:to-dep")
    b.sweep_to_hot(deposit, hot, 0.057, "BTC", minutes=1_800, tag=f"{key}:sweep")

    return Scenario(
        key=key,
        title="Sextortion - Bitcoin proceeds clustered and cashed out at WazirX",
        fraud_category=FraudCategory.SEXTORTION,
        chain=Chain.BITCOIN,
        seed_address=victim,
        expected_vasp_slug="wazirx",
        expected_deposit_address=deposit,
        expected_hops=3,
        expected_typologies=[Typology.FAN_IN_AGGREGATION],
        victim_name="Complainant C (fictional)",
        victim_state="Delhi",
        police_station="Cyber Cell, North District, Delhi",
        loss_amount_inr=round(0.0085 * PRICE_USD["BTC"] * USD_TO_INR, 0),
        incident_at=BASE_TIME + timedelta(days=6),
        narrative=(
            "Victim was blackmailed with fabricated intimate imagery and paid a single BTC "
            "ransom. The demand wallet co-spends with two sibling addresses, exposing the "
            "operator's full wallet cluster and a WazirX cash-out."
        ),
        transfers=b.transfers,
        labels=b.labels,
        notes="Exercises BTC common-input-ownership clustering.",
    )


def _ransomware_btc_peel() -> Scenario:
    """Peel chain: a long tail of small peels off a shrinking principal."""
    key = "ransomware_btc"
    b = ScenarioBuilder(key, Chain.BITCOIN, BASE_TIME + timedelta(days=9), 404)

    victim = b.addr("victim-wallet")
    ransom = b.addr("ransom-wallet")
    deposit, hot = b.deposit_address(
        "kraken", "Kraken", "rw-01", background_depositors=5, asset="BTC",
    )

    b.label(ransom, "Ransomware payment wallet", "Unknown", EntityType.RANSOMWARE)

    b.transfer(victim, ransom, 0.42, "BTC", minutes=0, tag=f"{key}:ransom")
    for i in range(3):
        other = b.addr(f"other-victim-{i}")
        b.transfer(other, ransom, round(b.rng.uniform(0.15, 0.6), 6), "BTC",
                   minutes=300 + i * 900, tag=f"{key}:ov{i}")

    # Peel chain: each hop sheds a small slice and forwards the remainder.
    principal = 1.32
    current = ransom
    minute = 1_200.0
    for i in range(7):
        peel = round(principal * b.rng.uniform(0.04, 0.09), 6)
        principal = round(principal - peel, 6)
        nxt = b.addr(f"peel-{i}")
        cashout = b.addr(f"peel-cashout-{i}")
        b.transfer(current, cashout, peel, "BTC", minutes=minute + 5, tag=f"{key}:peel{i}")
        b.transfer(current, nxt, principal, "BTC", minutes=minute + 8, tag=f"{key}:chain{i}")
        current = nxt
        minute += b.rng.uniform(120, 400)

    b.transfer(current, deposit, principal, "BTC", minutes=minute + 60, tag=f"{key}:to-dep")
    b.sweep_to_hot(deposit, hot, principal, "BTC", minutes=minute + 140, tag=f"{key}:sweep")

    return Scenario(
        key=key,
        title="Ransomware payout - seven-hop peel chain terminating at Kraken",
        fraud_category=FraudCategory.RANSOMWARE,
        chain=Chain.BITCOIN,
        seed_address=victim,
        expected_vasp_slug="kraken",
        expected_deposit_address=deposit,
        # victim -> ransom -> seven peel hops -> deposit
        expected_hops=9,
        expected_typologies=[Typology.PEEL_CHAIN],
        victim_name="Hospital IT Dept (fictional)",
        victim_state="Telangana",
        police_station="Cyber Crime PS, Hyderabad",
        loss_amount_inr=round(0.42 * PRICE_USD["BTC"] * USD_TO_INR, 0),
        incident_at=BASE_TIME + timedelta(days=9),
        narrative=(
            "Ransomware operator collected payments from four victims and moved the proceeds "
            "through a peel chain, shedding small amounts at each hop before cashing out."
        ),
        transfers=b.transfers,
        labels=b.labels,
        notes=(
            "Peel depth exceeds the default 5-hop limit, so at default settings the trace "
            "stops short and recommends a re-run with a higher hop budget."
        ),
    )


def _phishing_eth_mixer() -> Scenario:
    """Sanctioned mixer in the path - attribution must break and say so."""
    key = "phishing_eth"
    b = ScenarioBuilder(key, Chain.ETHEREUM, BASE_TIME + timedelta(days=12), 505)

    victim = b.addr("victim-wallet")
    drainer = b.addr("wallet-drainer")
    staging = b.addr("pre-mix-staging")
    tornado = "0x910cbd523d972eb0a6f4cae4618ad62622b39dbf"
    post_mix = b.addr("post-mix")
    deposit, hot = b.deposit_address(
        "binance", "Binance", "ph-01", background_depositors=6, asset="ETH",
    )

    b.label(drainer, "Wallet drainer (phishing)", "Unknown", EntityType.SCAM)
    b.label(tornado, "Tornado Cash 10 ETH Pool", "Tornado Cash", EntityType.MIXER,
            is_sanctioned=True)

    b.transfer(victim, drainer, 6.4, "ETH", minutes=0, tag=f"{key}:drain")
    for i in range(4):
        other = b.addr(f"other-victim-{i}")
        b.transfer(other, drainer, round(b.rng.uniform(0.8, 5.5), 4), "ETH",
                   minutes=15 + i * 130, tag=f"{key}:ov{i}")

    b.transfer(drainer, staging, 17.2, "ETH", minutes=600, tag=f"{key}:stage")
    for i in range(2):
        b.transfer(staging, tornado, 10.0, "ETH", minutes=640 + i * 18, tag=f"{key}:mix{i}")

    # Withdrawals come out of the pool with no cryptographic link to the deposits.
    b.transfer(tornado, post_mix, 10.0, "ETH", minutes=2_800, tag=f"{key}:unmix1")
    b.transfer(tornado, post_mix, 9.7, "ETH", minutes=3_100, tag=f"{key}:unmix2")
    b.transfer(post_mix, deposit, 19.4, "ETH", minutes=3_400, tag=f"{key}:to-dep")
    b.sweep_to_hot(deposit, hot, 19.4, "ETH", minutes=3_500, tag=f"{key}:sweep")

    return Scenario(
        key=key,
        title="Phishing drainer - proceeds through Tornado Cash before Binance",
        fraud_category=FraudCategory.PHISHING,
        chain=Chain.ETHEREUM,
        seed_address=victim,
        expected_vasp_slug="binance",
        expected_deposit_address=deposit,
        expected_hops=5,
        expected_typologies=[Typology.MIXER_INTERACTION, Typology.SANCTIONS_EXPOSURE],
        victim_name="Complainant D (fictional)",
        victim_state="Gujarat",
        police_station="Cyber Crime PS, Ahmedabad",
        loss_amount_inr=round(6.4 * PRICE_USD["ETH"] * USD_TO_INR, 0),
        incident_at=BASE_TIME + timedelta(days=12),
        narrative=(
            "Victim signed a malicious approval on a spoofed airdrop site and had 6.4 ETH "
            "drained. The operator consolidated several victims and deposited into a sanctioned "
            "Tornado Cash pool."
        ),
        transfers=b.transfers,
        labels=b.labels,
        notes=(
            "Deliberate hard case: the post-mix link is a timing/value inference, not proof. "
            "The trace must terminate at the mixer and flag the break in the chain of custody."
        ),
    )


def _darknet_btc() -> Scenario:
    key = "darknet_btc"
    b = ScenarioBuilder(key, Chain.BITCOIN, BASE_TIME + timedelta(days=15), 606)

    buyer = b.addr("buyer-wallet")
    market_escrow = b.addr("market-escrow")
    vendor = b.addr("vendor-wallet")
    hop = b.addr("vendor-hop")
    deposit, hot = b.deposit_address(
        "htx", "HTX", "dn-01", background_depositors=5, asset="BTC",
    )

    b.label(market_escrow, "Darknet market escrow", "Unknown Market", EntityType.DARKNET)
    b.label(vendor, "Darknet vendor payout wallet", "Unknown", EntityType.DARKNET)

    b.transfer(buyer, market_escrow, 0.021, "BTC", minutes=0, tag=f"{key}:buy")
    for i in range(9):
        other = b.addr(f"buyer-{i}")
        b.transfer(other, market_escrow, round(b.rng.uniform(0.004, 0.05), 6), "BTC",
                   minutes=40 + i * 190, tag=f"{key}:b{i}")

    b.transfer(market_escrow, vendor, 0.183, "BTC", minutes=2_100, tag=f"{key}:payout")
    b.transfer(vendor, hop, 0.178, "BTC", minutes=2_400, tag=f"{key}:hop")
    b.transfer(hop, deposit, 0.174, "BTC", minutes=2_900, tag=f"{key}:to-dep")
    b.sweep_to_hot(deposit, hot, 0.174, "BTC", minutes=3_050, tag=f"{key}:sweep")

    return Scenario(
        key=key,
        title="Darknet marketplace vendor payout cashed out at HTX",
        fraud_category=FraudCategory.DARKNET,
        chain=Chain.BITCOIN,
        seed_address=buyer,
        expected_vasp_slug="htx",
        expected_deposit_address=deposit,
        expected_hops=4,
        expected_typologies=[Typology.FAN_IN_AGGREGATION],
        victim_name="Seized device - buyer wallet (fictional)",
        victim_state="Punjab",
        police_station="Cyber Crime PS, Ludhiana",
        loss_amount_inr=round(0.021 * PRICE_USD["BTC"] * USD_TO_INR, 0),
        incident_at=BASE_TIME + timedelta(days=15),
        narrative=(
            "Wallet recovered from a seized device paid a darknet market escrow. Tracing the "
            "escrow forward identifies the vendor payout route and an HTX cash-out point."
        ),
        transfers=b.transfers,
        labels=b.labels,
        notes="Seed is a buyer wallet, so the trace runs through escrow rather than from a victim.",
    )


def _crosschain_bridge() -> Scenario:
    """TRON to Ethereum via a bridge - the cross-chain correlator's test case."""
    key = "crosschain_bridge"
    b = ScenarioBuilder(key, Chain.TRON, BASE_TIME + timedelta(days=18), 707)

    victim = b.addr("victim-wallet")
    collector = b.addr("collector")
    bridge_in = b.addr("bridge-deposit-tron")
    bridge_contract = "0x8731d54e9d02c286767d56ac03e8037c07e01e98"
    bridge_out = b.addr_on(Chain.ETHEREUM, "bridge-out-eth")
    eth_hop = b.addr_on(Chain.ETHEREUM, "eth-hop")

    deposit = make_address(Chain.ETHEREUM, f"{key}:deposit:bybit:xc-01")
    hot_bybit = make_address(Chain.ETHEREUM, "hotwallet:bybit:ethereum")

    b.label(collector, "Organised fraud collection wallet", "Unknown", EntityType.SCAM)
    b.label(bridge_in, "Stargate bridge inbound (TRON)", "Stargate", EntityType.BRIDGE)
    b.label(bridge_contract, "Stargate Router", "Stargate", EntityType.BRIDGE,
            chain=Chain.ETHEREUM)
    b.label(hot_bybit, "Bybit Hot Wallet", "Bybit", EntityType.CEX_HOT_WALLET, "bybit",
            chain=Chain.ETHEREUM)

    b.transfer(victim, collector, 26_500, "USDT", minutes=0, tag=f"{key}:v1")
    for i in range(6):
        other = b.addr(f"covictim-{i}")
        b.transfer(other, collector, round(b.rng.uniform(3_000, 18_000), 2), "USDT",
                   minutes=90 + i * 260, tag=f"{key}:cv{i}")

    b.transfer(collector, bridge_in, 96_400, "USDT", minutes=1_500, tag=f"{key}:to-bridge")

    # The bridge hop: value reappears on Ethereum minutes later, minus fees.
    b.transfer(bridge_contract, bridge_out, 95_150, "USDT", minutes=1_517,
               chain=Chain.ETHEREUM, tag=f"{key}:bridge-out")
    b.transfer(bridge_out, eth_hop, 95_000, "USDT", minutes=1_580,
               chain=Chain.ETHEREUM, tag=f"{key}:eth-hop")
    b.transfer(eth_hop, deposit, 94_600, "USDT", minutes=1_700,
               chain=Chain.ETHEREUM, tag=f"{key}:to-dep")

    # Deposit-address footprint on the Ethereum side.
    for i in range(6):
        payer = make_address(Chain.ETHEREUM, f"payer:bybit:xc-01:{i}")
        amount = round(b.rng.uniform(400, 5_000), 2)
        minute = -3_000 + i * 400
        b.transfer(payer, deposit, amount, "USDT", minutes=minute,
                   chain=Chain.ETHEREUM, tag=f"{key}:bg{i}")
        b.transfer(deposit, hot_bybit, amount, "USDT", minutes=minute + 25,
                   chain=Chain.ETHEREUM, tag=f"{key}:bgsweep{i}")
    b.transfer(deposit, hot_bybit, 94_600, "USDT", minutes=1_760,
               chain=Chain.ETHEREUM, tag=f"{key}:sweep")

    return Scenario(
        key=key,
        title="Organised syndicate - TRON to Ethereum bridge hop ending at Bybit",
        fraud_category=FraudCategory.ORGANISED_CYBER_CRIME,
        chain=Chain.TRON,
        seed_address=victim,
        expected_vasp_slug="bybit",
        expected_deposit_address=deposit,
        expected_hops=5,
        expected_typologies=[Typology.BRIDGE_INTERACTION, Typology.FAN_IN_AGGREGATION],
        victim_name="Complainant E (fictional)",
        victim_state="Tamil Nadu",
        police_station="Cyber Crime PS, Chennai",
        loss_amount_inr=round(26_500 * USD_TO_INR, 0),
        incident_at=BASE_TIME + timedelta(days=18),
        narrative=(
            "High-value organised fraud. Proceeds from seven victims were bridged from TRON to "
            "Ethereum through Stargate and deposited at Bybit 4 hours after the last victim "
            "payment."
        ),
        transfers=b.transfers,
        labels=b.labels,
        notes="Bridge crossing is a time/value inference and must be presented with its confidence.",
    )


def _layering_eth_fanout() -> Scenario:
    key = "layering_eth"
    b = ScenarioBuilder(key, Chain.ETHEREUM, BASE_TIME + timedelta(days=21), 808)

    victim = b.addr("victim-wallet")
    collector = b.addr("collector")
    dep_a, hot_a = b.deposit_address("bybit", "Bybit", "lay-01", background_depositors=5,
                                     asset="USDT")
    dep_b, hot_b = b.deposit_address("kucoin", "KuCoin", "lay-02", background_depositors=5,
                                     asset="USDT")

    b.label(collector, "Investment scam collection wallet", "Unknown", EntityType.SCAM)
    b.transfer(victim, collector, 58_000, "USDT", minutes=0, tag=f"{key}:v1")

    # Wide fan-out: 12 first-layer wallets, each forwarding through a second layer.
    layer1 = []
    for i in range(12):
        node = b.addr(f"l1-{i}")
        b.transfer(collector, node, round(58_000 / 12 - b.rng.uniform(10, 80), 2), "USDT",
                   minutes=120 + i * 9, tag=f"{key}:fan{i}")
        layer1.append(node)

    for i, node in enumerate(layer1):
        target = dep_a if i % 3 else dep_b
        mid = b.addr(f"l2-{i}")
        amount = round(4_700 - b.rng.uniform(20, 160), 2)
        b.transfer(node, mid, amount, "USDT", minutes=300 + i * 11, tag=f"{key}:l2-{i}")
        b.transfer(mid, target, round(amount - b.rng.uniform(5, 40), 2), "USDT",
                   minutes=460 + i * 13, tag=f"{key}:l3-{i}")

    b.sweep_to_hot(dep_a, hot_a, 37_000, "USDT", minutes=900, tag=f"{key}:sweep-a")
    b.sweep_to_hot(dep_b, hot_b, 18_500, "USDT", minutes=930, tag=f"{key}:sweep-b")

    return Scenario(
        key=key,
        title="Investment scam - 12-way fan-out layering across Bybit and KuCoin",
        fraud_category=FraudCategory.INVESTMENT_SCAM,
        chain=Chain.ETHEREUM,
        seed_address=victim,
        expected_vasp_slug="bybit",
        expected_deposit_address=dep_a,
        expected_hops=4,
        expected_typologies=[Typology.FAN_OUT_LAYERING],
        victim_name="Complainant F (fictional)",
        victim_state="Uttar Pradesh",
        police_station="Cyber Crime PS, Noida",
        loss_amount_inr=round(58_000 * USD_TO_INR, 0),
        incident_at=BASE_TIME + timedelta(days=21),
        narrative=(
            "Single large victim payment split 12 ways and re-converged on two exchange deposit "
            "addresses. Tests fan-out pruning and taint-weighted ranking."
        ),
        transfers=b.transfers,
        labels=b.labels,
        notes="Exercises max_fanout pruning; ~66% of value lands at Bybit.",
    )


def _rapid_bsc() -> Scenario:
    key = "rapid_bsc"
    b = ScenarioBuilder(key, Chain.BSC, BASE_TIME + timedelta(days=24), 909)

    victim = b.addr("victim-wallet")
    mule = b.addr("mule-wallet")
    deposit, hot = b.deposit_address("gate-io", "Gate.io", "rap-01",
                                     background_depositors=5, asset="USDT")

    b.label(mule, "Mule wallet (phishing proceeds)", "Unknown", EntityType.SCAM)

    b.transfer(victim, mule, 12_300, "USDT", minutes=0, tag=f"{key}:v1")
    # Sub-minute forwarding: an automated cash-out script, not a human.
    b.transfer(mule, deposit, 12_250, "USDT", minutes=0.6, tag=f"{key}:relay")
    b.sweep_to_hot(deposit, hot, 12_250, "USDT", minutes=4, tag=f"{key}:sweep")

    for i in range(5):
        other = b.addr(f"other-victim-{i}")
        rush = b.addr(f"rush-{i}")
        b.transfer(other, rush, round(b.rng.uniform(900, 7_000), 2), "USDT",
                   minutes=30 + i * 60, tag=f"{key}:ov{i}")
        b.transfer(rush, deposit, round(b.rng.uniform(850, 6_900), 2), "USDT",
                   minutes=30.7 + i * 60, tag=f"{key}:ovr{i}")

    return Scenario(
        key=key,
        title="Phishing - automated sub-minute relay to a Gate.io deposit address",
        fraud_category=FraudCategory.PHISHING,
        chain=Chain.BSC,
        seed_address=victim,
        expected_vasp_slug="gate-io",
        expected_deposit_address=deposit,
        expected_hops=2,
        expected_typologies=[Typology.RAPID_RELAY, Typology.DIRECT_TO_EXCHANGE],
        victim_name="Complainant G (fictional)",
        victim_state="West Bengal",
        police_station="Cyber PS, Kolkata",
        loss_amount_inr=round(12_300 * USD_TO_INR, 0),
        incident_at=BASE_TIME + timedelta(days=24),
        narrative=(
            "Funds left the victim's wallet and reached an exchange deposit address in under a "
            "minute, indicating scripted cash-out. Fastest possible attribution path."
        ),
        transfers=b.transfers,
        labels=b.labels,
        notes="Shortest scenario; good for demonstrating sub-second attribution.",
    )


def _dormant_polygon_p2p() -> Scenario:
    key = "dormant_polygon"
    b = ScenarioBuilder(key, Chain.POLYGON, BASE_TIME + timedelta(days=27), 1010)

    victim = b.addr("victim-wallet")
    dormant = b.addr("dormant-wallet")
    p2p_desk = b.addr("p2p-desk")

    b.label(dormant, "Dormant collection wallet", "Unknown", EntityType.SCAM)

    # Long-dormant wallet: one old inbound, then a burst of activity months later.
    b.transfer(b.addr("legacy-payer"), dormant, 400, "USDT", minutes=-260_000,
               tag=f"{key}:legacy")
    b.transfer(victim, dormant, 9_400, "USDT", minutes=0, tag=f"{key}:v1")
    for i in range(8):
        other = b.addr(f"covictim-{i}")
        b.transfer(other, dormant, round(b.rng.uniform(600, 4_500), 2), "USDT",
                   minutes=5 + i * 12, tag=f"{key}:cv{i}")

    # High-volume unregistered P2P desk: many payers in, many payouts out.
    for i in range(9):
        payer = b.addr(f"p2p-payer-{i}")
        b.transfer(payer, p2p_desk, round(b.rng.uniform(500, 9_000), 2), "USDT",
                   minutes=-4_000 + i * 300, tag=f"{key}:p2pin{i}")
    for i in range(9):
        payee = b.addr(f"p2p-payee-{i}")
        b.transfer(p2p_desk, payee, round(b.rng.uniform(400, 8_000), 2), "USDT",
                   minutes=-3_800 + i * 320, tag=f"{key}:p2pout{i}")

    b.transfer(dormant, p2p_desk, 28_600, "USDT", minutes=200, tag=f"{key}:to-p2p")
    b.label(p2p_desk, "High-volume P2P/OTC desk (unregistered)", "Unregistered P2P Desk",
            EntityType.P2P_MERCHANT, "unknown-p2p-desk", )

    return Scenario(
        key=key,
        title="Task fraud - dormant wallet burst cashed out through an unregistered P2P desk",
        fraud_category=FraudCategory.TASK_BASED_FRAUD,
        chain=Chain.POLYGON,
        seed_address=victim,
        expected_vasp_slug="unknown-p2p-desk",
        expected_deposit_address=p2p_desk,
        expected_hops=2,
        expected_typologies=[Typology.DORMANT_BURST],
        victim_name="Complainant H (fictional)",
        victim_state="Rajasthan",
        police_station="Cyber Crime PS, Jaipur",
        loss_amount_inr=round(9_400 * USD_TO_INR, 0),
        incident_at=BASE_TIME + timedelta(days=27),
        narrative=(
            "A wallet dormant for six months received nine victim payments within two hours and "
            "forwarded everything to a high-volume P2P desk with no registered entity."
        ),
        transfers=b.transfers,
        labels=b.labels,
        notes=(
            "Terminus is an unregistered P2P operator, not a servable VASP - the system must say "
            "so instead of implying a freeze request can be sent."
        ),
    )


def _direct_deposit_tron() -> Scenario:
    key = "direct_tron"
    b = ScenarioBuilder(key, Chain.TRON, BASE_TIME + timedelta(days=30), 1111)

    victim = b.addr("victim-wallet")
    deposit, hot = b.deposit_address("zebpay", "ZebPay", "dir-01", background_depositors=6)

    b.transfer(victim, deposit, 3_150, "USDT", minutes=0, tag=f"{key}:v1")
    b.sweep_to_hot(deposit, hot, 3_150, "USDT", minutes=52, tag=f"{key}:sweep")

    return Scenario(
        key=key,
        title="Sextortion - single-hop payment straight into a ZebPay deposit address",
        fraud_category=FraudCategory.SEXTORTION,
        chain=Chain.TRON,
        seed_address=victim,
        expected_vasp_slug="zebpay",
        expected_deposit_address=deposit,
        expected_hops=1,
        expected_typologies=[Typology.DIRECT_TO_EXCHANGE],
        victim_name="Complainant I (fictional)",
        victim_state="Kerala",
        police_station="Cyber Police Station, Kochi",
        loss_amount_inr=round(3_150 * USD_TO_INR, 0),
        incident_at=BASE_TIME + timedelta(days=30),
        narrative=(
            "Victim paid the extortion demand directly into what turns out to be a ZebPay "
            "deposit address - the best possible outcome for a freeze request."
        ),
        transfers=b.transfers,
        labels=b.labels,
        notes="One-hop baseline; the address is unlabelled, so tier-1 must miss and tier-2 must hit.",
    )


SCENARIO_FACTORIES = [
    _investment_scam_tron,
    _task_fraud_tron,
    _sextortion_btc,
    _ransomware_btc_peel,
    _phishing_eth_mixer,
    _darknet_btc,
    _crosschain_bridge,
    _layering_eth_fanout,
    _rapid_bsc,
    _dormant_polygon_p2p,
    _direct_deposit_tron,
]


def build_scenarios() -> list[Scenario]:
    """Build every scenario. Deterministic: same input, same graph, every time."""
    return [factory() for factory in SCENARIO_FACTORIES]
