"""Tier 3: Bitcoin common-input-ownership clustering.

Spending a UTXO requires its private key, so when several addresses appear as
inputs to one transaction, one party controlled all of them. Union-find over
co-spend sets therefore grows a wallet cluster, and any label anywhere in that
cluster attaches to every member.

Caveat that matters for evidence: CoinJoin transactions deliberately break this
assumption, so clusters built from suspiciously uniform transactions are
down-weighted rather than trusted.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.attribution.labels import LabelStore
from app.attribution.models import Candidate
from app.chains.base import Transfer
from app.core.enums import AttributionMethod, Chain, EntityType
from app.db.models import SyntheticTransfer

MAX_CLUSTER_SIZE = 500
MAX_EXPANSION_ROUNDS = 3
# A transaction with many equal-value inputs and outputs is CoinJoin-shaped and
# violates the common-input assumption.
COINJOIN_MIN_PARTICIPANTS = 5


class UnionFind:
    def __init__(self) -> None:
        self._parent: dict[str, str] = {}

    def add(self, item: str) -> None:
        self._parent.setdefault(item, item)

    def find(self, item: str) -> str:
        self.add(item)
        root = item
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[item] != root:  # path compression
            self._parent[item], item = root, self._parent[item]
        return root

    def union(self, a: str, b: str) -> None:
        root_a, root_b = self.find(a), self.find(b)
        if root_a != root_b:
            self._parent[root_b] = root_a

    def members(self, item: str) -> set[str]:
        root = self.find(item)
        return {node for node in self._parent if self.find(node) == root}


@dataclass
class ClusterResult:
    root: str
    members: set[str]
    cospend_transactions: list[str] = field(default_factory=list)
    coinjoin_suspected: bool = False
    truncated: bool = False


def build_cluster_from_transfers(address: str, transfers: list[Transfer]) -> ClusterResult:
    """Cluster from co-spend sets already present in fetched transfers."""
    uf = UnionFind()
    uf.add(address)
    tx_hashes: list[str] = []
    coinjoin = False

    groups: dict[str, set[str]] = {}
    for transfer in transfers:
        if not transfer.cospend_inputs or len(transfer.cospend_inputs) < 2:
            continue
        key = transfer.input_group or transfer.tx_hash
        groups.setdefault(key, set()).update(transfer.cospend_inputs)

    for tx_hash, inputs in groups.items():
        if len(inputs) >= COINJOIN_MIN_PARTICIPANTS:
            coinjoin = True
        tx_hashes.append(tx_hash)
        members = sorted(inputs)
        for other in members[1:]:
            uf.union(members[0], other)

    members = uf.members(address)
    return ClusterResult(
        root=address,
        members=members,
        cospend_transactions=tx_hashes,
        coinjoin_suspected=coinjoin,
        truncated=len(members) >= MAX_CLUSTER_SIZE,
    )


async def expand_cluster_from_seed(
    session: AsyncSession, address: str, *, rounds: int = MAX_EXPANSION_ROUNDS
) -> ClusterResult:
    """Grow a cluster from the seeded dataset's recorded co-spend groups.

    Only the synthetic source is walked transitively; doing the same against a
    live explorer would mean one request per transaction and would exhaust the
    free-tier quota within a single trace.
    """
    uf = UnionFind()
    uf.add(address)
    frontier = {address}
    seen_groups: set[str] = set()
    tx_hashes: list[str] = []
    coinjoin = False

    for _ in range(rounds):
        if not frontier:
            break
        group_rows = await session.execute(
            select(SyntheticTransfer.input_group)
            .where(
                SyntheticTransfer.chain == Chain.BITCOIN,
                SyntheticTransfer.src_address.in_(frontier),
                SyntheticTransfer.input_group.is_not(None),
            )
            .distinct()
        )
        groups = {g for (g,) in group_rows.all() if g and g not in seen_groups}
        if not groups:
            break
        seen_groups.update(groups)

        member_rows = await session.execute(
            select(SyntheticTransfer.input_group, SyntheticTransfer.src_address).where(
                SyntheticTransfer.input_group.in_(groups)
            )
        )
        by_group: dict[str, set[str]] = {}
        for group, src in member_rows.all():
            by_group.setdefault(group, set()).add(src)

        new_members: set[str] = set()
        for group, inputs in by_group.items():
            if len(inputs) >= COINJOIN_MIN_PARTICIPANTS:
                coinjoin = True
            tx_hashes.append(group)
            ordered = sorted(inputs)
            for other in ordered[1:]:
                uf.union(ordered[0], other)
            new_members.update(inputs)

        frontier = new_members - uf.members(address) | (new_members - frontier)
        if len(uf.members(address)) >= MAX_CLUSTER_SIZE:
            break

    members = uf.members(address)
    return ClusterResult(
        root=address,
        members=members,
        cospend_transactions=sorted(set(tx_hashes)),
        coinjoin_suspected=coinjoin,
        truncated=len(members) >= MAX_CLUSTER_SIZE,
    )


async def attribute_via_cluster(
    store: LabelStore,
    session: AsyncSession,
    address: str,
    transfers: list[Transfer] | None = None,
) -> Candidate | None:
    """Attribute a Bitcoin address from a label elsewhere in its wallet cluster."""
    cluster = build_cluster_from_transfers(address, transfers or [])
    if len(cluster.members) <= 1:
        cluster = await expand_cluster_from_seed(session, address)
    siblings = cluster.members - {address}
    if not siblings:
        return None

    await store.prefetch(Chain.BITCOIN, list(siblings))
    best: Candidate | None = None
    matched_sibling: str | None = None

    for sibling in sorted(siblings):
        for label in await store.get(Chain.BITCOIN, sibling):
            entity_type = EntityType(label.entity_type)
            if entity_type is EntityType.UNKNOWN:
                continue
            confidence = 0.82 * label.confidence
            if cluster.coinjoin_suspected:
                # CoinJoin breaks the ownership assumption this tier rests on.
                confidence *= 0.55
            if len(cluster.members) > 50:
                confidence *= 0.9
            candidate = Candidate(
                entity_name=label.entity_name or label.label,
                entity_type=entity_type,
                method=AttributionMethod.COMMON_INPUT_CLUSTER,
                confidence=confidence,
                vasp_slug=label.vasp_slug,
                is_sanctioned=label.is_sanctioned,
                evidence={},
            )
            if best is None or candidate.confidence > best.confidence:
                best = candidate
                matched_sibling = sibling

    if best is None:
        return None

    best.evidence = {
        "cluster_size": len(cluster.members),
        "matched_address": matched_sibling,
        "cospend_transactions": cluster.cospend_transactions[:10],
        "coinjoin_suspected": cluster.coinjoin_suspected,
        "cluster_truncated": cluster.truncated,
        "heuristic": "common-input-ownership",
        "caveat": (
            "CoinJoin-shaped transactions detected; common-input ownership may not hold"
            if cluster.coinjoin_suspected
            else "Co-spending requires control of every input key"
        ),
    }
    return best
