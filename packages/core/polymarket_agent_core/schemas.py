from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class LeaderboardEntry(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    proxy_wallet: str = Field(alias="proxyWallet")
    user_name: str | None = Field(default=None, alias="name")
    pseudonym: str | None = None
    x_username: str | None = Field(default=None, alias="xUsername")
    verified_badge: bool | None = Field(default=None, alias="verifiedBadge")
    profile_image: str | None = Field(default=None, alias="profileImage")
    pnl: float | None = None
    vol: float | None = None
    rank: int | None = None


class ActivityRecord(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    proxy_wallet: str = Field(alias="proxyWallet")
    timestamp: int
    transaction_hash: str = Field(alias="transactionHash")
    event_type: str = Field(alias="type")
    asset: str | None = None
    side: str | None = None
    size: float | None = None
    usdc_size: float | None = Field(default=None, alias="usdcSize")
    price: float | None = None
    condition_id: str | None = Field(default=None, alias="conditionId")
    title: str | None = None
    slug: str | None = None
    event_slug: str | None = Field(default=None, alias="eventSlug")
    outcome: str | None = None
    outcome_index: int | None = Field(default=None, alias="outcomeIndex")
