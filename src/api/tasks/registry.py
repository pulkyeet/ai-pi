"""kind -> handler binding (Phase 10 phase doc). The only place all seven
handlers are instantiated and wired into the Phase 02 executor's
`HandlerRegistry` — everything else in this package only ever sees the one
handler it implements.
"""

from __future__ import annotations

from api.executor.protocol import HandlerRegistry
from api.tasks.community import MineCommunityHandler
from api.tasks.context import HandlerDeps
from api.tasks.discover import DiscoverCompetitorsHandler
from api.tasks.funding import FindFundingHandler
from api.tasks.oss import OssProfileHandler
from api.tasks.pricing import ExtractPricingHandler
from api.tasks.profile import ProfileProductHandler
from api.tasks.trends import TrendSignalsHandler


def build_registry(deps: HandlerDeps) -> HandlerRegistry:
    registry = HandlerRegistry()
    registry.register(DiscoverCompetitorsHandler(deps))
    registry.register(ProfileProductHandler(deps))
    registry.register(ExtractPricingHandler(deps))
    registry.register(MineCommunityHandler(deps))
    registry.register(OssProfileHandler(deps))
    registry.register(FindFundingHandler(deps))
    registry.register(TrendSignalsHandler(deps))
    return registry


__all__ = ["build_registry"]
