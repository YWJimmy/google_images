from __future__ import annotations

from dataclasses import dataclass

from .ranking import domain_matches
from .structured_domains import SourceDomainObservation


@dataclass(frozen=True)
class RankingDecision:
    result_code: int
    matched_rank: int | None = None
    matched_url: str | None = None
    message: str | None = None


def decide_source_rank(
    observations: list[SourceDomainObservation],
    target_domain: str,
    max_results: int,
    include_subdomains: bool,
) -> RankingDecision:
    """Fail closed unless every position needed by the decision is resolved."""
    by_rank = {item.rank: item for item in observations if 1 <= item.rank <= max_results}
    for rank in range(1, max_results + 1):
        item = by_rank.get(rank)
        if not item or item.status != "resolved" or len(item.domains) != 1:
            continue
        if not domain_matches(item.domains[0], target_domain, include_subdomains):
            continue
        unresolved_before = [
            prior
            for prior in range(1, rank + 1)
            if prior not in by_rank or by_rank[prior].status != "resolved"
        ]
        if unresolved_before:
            return RankingDecision(
                -5,
                message=(
                    "target domain appeared, but preceding image positions were not "
                    f"fully resolved: {unresolved_before[:10]}"
                ),
            )
        return RankingDecision(
            rank,
            matched_rank=rank,
            matched_url=item.source_url or f"https://{item.domains[0]}/",
        )

    missing = [rank for rank in range(1, max_results + 1) if rank not in by_rank]
    if missing:
        return RankingDecision(
            -5,
            message=f"result depth incomplete: {len(by_rank)} of required {max_results} image positions",
        )
    unresolved = [
        rank for rank in range(1, max_results + 1) if by_rank[rank].status != "resolved"
    ]
    if unresolved:
        return RankingDecision(
            -5,
            message=f"source-domain parsing incomplete at positions: {unresolved[:10]}",
        )
    return RankingDecision(-1)
