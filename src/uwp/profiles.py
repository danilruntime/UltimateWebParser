"""Profile identities, browser launch flags and the profile lifecycle manager.

A profile is a coherent "machine": platform, browser brand, locale, timezone and
viewport are picked together from :data:`MACHINE_PROFILES`, so the fingerprint
never mixes, say, a Windows platform with a macOS timezone.
"""
import random
import shutil
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from uwp.config import Settings
from uwp.db.repo.profiles import ProfilesRepo
from uwp.domain import Profile, ProfileKind, ProfileStatus
from uwp.timeutil import utcnow

CLEAN_TTL = timedelta(hours=24)

# Coherent machine profiles weighted roughly by real-world browser share.
MACHINE_PROFILES: list[dict] = [
    {
        "platform": "windows",
        "brand": "Chrome",
        "brand_version": "150",
        "locale": "en-US",
        "accept_lang": "en-US,en",
        "timezone": "America/New_York",
        "viewport": (1920, 1080),
        "country": "US",
        "weight": 30,
    },
    {
        "platform": "windows",
        "brand": "Edge",
        "brand_version": "150",
        "locale": "en-GB",
        "accept_lang": "en-GB,en",
        "timezone": "Europe/London",
        "viewport": (1920, 1080),
        "country": "GB",
        "weight": 10,
    },
    {
        "platform": "macos",
        "brand": "Chrome",
        "brand_version": "150",
        "locale": "en-US",
        "accept_lang": "en-US,en",
        "timezone": "America/Los_Angeles",
        "viewport": (1512, 982),
        "country": "US",
        "weight": 15,
    },
    {
        "platform": "linux",
        "brand": "Chrome",
        "brand_version": "150",
        "locale": "de-DE",
        "accept_lang": "de-DE,de",
        "timezone": "Europe/Berlin",
        "viewport": (1920, 1080),
        "country": "DE",
        "weight": 5,
    },
    {
        "platform": "windows",
        "brand": "Chrome",
        "brand_version": "150",
        "locale": "ru-RU",
        "accept_lang": "ru-RU,ru",
        "timezone": "Europe/Moscow",
        "viewport": (1920, 1080),
        "country": "RU",
        "weight": 20,
    },
    {
        "platform": "windows",
        "brand": "Chrome",
        "brand_version": "150",
        "locale": "ja-JP",
        "accept_lang": "ja-JP,ja",
        "timezone": "Asia/Tokyo",
        "viewport": (1920, 1080),
        "country": "JP",
        "weight": 5,
    },
]


@dataclass(frozen=True)
class Identity:
    seed: int
    platform: str
    brand: str
    brand_version: str
    timezone: str
    locale: str
    accept_lang: str
    viewport: tuple[int, int]


def random_identity(rng: random.Random | None = None, country: str | None = None) -> Identity:
    """Pick a coherent machine profile, weighted by ``weight``.

    With ``country`` only profiles of that country are considered; when none
    match, the full list is used.
    """
    rng = rng or random.Random()
    candidates = MACHINE_PROFILES
    if country is not None:
        matching = [p for p in MACHINE_PROFILES if p["country"] == country.upper()]
        if matching:
            candidates = matching
    chosen = rng.choices(candidates, weights=[p["weight"] for p in candidates], k=1)[0]
    return Identity(
        seed=rng.randint(1, 2**31 - 1),
        platform=chosen["platform"],
        brand=chosen["brand"],
        brand_version=chosen["brand_version"],
        timezone=chosen["timezone"],
        locale=chosen["locale"],
        accept_lang=chosen["accept_lang"],
        viewport=tuple(chosen["viewport"]),
    )


def identity_from_seed(seed: int) -> Identity:
    """Rebuild the identity of a stored profile from its seed.

    The seed is the fingerprint seed, so it is kept as is; the machine fields
    are drawn from a generator seeded with it, which makes them stable across
    runs of a persistent profile.
    """
    rng = random.Random(seed)
    chosen = rng.choices(MACHINE_PROFILES, weights=[p["weight"] for p in MACHINE_PROFILES], k=1)[0]
    return Identity(
        seed=seed,
        platform=chosen["platform"],
        brand=chosen["brand"],
        brand_version=chosen["brand_version"],
        timezone=chosen["timezone"],
        locale=chosen["locale"],
        accept_lang=chosen["accept_lang"],
        viewport=tuple(chosen["viewport"]),
    )


def browser_flags(
    identity: Identity,
    user_data_dir: Path,
    proxy_url: str | None,
    headless: bool,
    no_sandbox: bool = False,
) -> list[str]:
    """Fingerprint-chromium launch flags in the order fixed by the spec."""
    flags = [
        f"--fingerprint={identity.seed}",
        f"--fingerprint-platform={identity.platform}",
        f"--fingerprint-brand={identity.brand}",
        f"--fingerprint-brand-version={identity.brand_version}",
        f"--timezone={identity.timezone}",
        f"--lang={identity.locale}",
        f"--accept-lang={identity.accept_lang}",
        f"--user-data-dir={user_data_dir}",
        "--disable-non-proxied-udp",
    ]
    if proxy_url:
        flags.append(f"--proxy-server={proxy_url}")
    flags += [
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=Translate",
    ]
    if headless:
        flags.append("--headless=new")
    if no_sandbox:
        flags.append("--no-sandbox")
    return flags


class ProfileManager:
    """Creates, leases and removes profile rows together with their folders."""

    def __init__(self, settings: Settings, repo: ProfilesRepo) -> None:
        self.settings = settings
        self.repo = repo

    async def create_persistent(
        self,
        name: str,
        identity: Identity | None = None,
        proxy_id: int | None = None,
    ) -> Profile:
        """Create a persistent profile with a fixed folder and identity."""
        identity = identity or random_identity()
        user_data_dir = self.settings.profiles_dir / str(uuid.uuid4())
        user_data_dir.mkdir(parents=True, exist_ok=True)
        profile = Profile(
            name=name,
            kind=ProfileKind.persistent,
            seed=identity.seed,
            platform=identity.platform,
            brand=identity.brand,
            brand_version=identity.brand_version,
            timezone=identity.timezone,
            locale=identity.locale,
            user_data_dir=str(user_data_dir),
            proxy_id=proxy_id,
            status=ProfileStatus.free,
        )
        try:
            profile.id = await self.repo.create(profile)
        except sqlite3.IntegrityError:
            # ``profiles.name`` is UNIQUE; a duplicate is an admin mistake, not
            # a crash, and the bot must say so in the wizard.
            shutil.rmtree(user_data_dir, ignore_errors=True)
            raise ValueError(f"профиль с именем «{name}» уже есть") from None
        except BaseException:
            shutil.rmtree(user_data_dir, ignore_errors=True)
            raise
        return profile

    async def create_clean(self, run_id: int, country: str | None = None) -> Profile:
        """Create a one-shot profile for ``run_id``, locked until released."""
        identity = random_identity(country=country)
        suffix = uuid.uuid4().hex[:8]
        user_data_dir = self.settings.profiles_dir / "tmp" / f"{run_id}-{suffix}"
        user_data_dir.mkdir(parents=True, exist_ok=True)
        profile = Profile(
            name=f"clean-{run_id}-{suffix}",
            kind=ProfileKind.clean,
            seed=identity.seed,
            platform=identity.platform,
            brand=identity.brand,
            brand_version=identity.brand_version,
            timezone=identity.timezone,
            locale=identity.locale,
            user_data_dir=str(user_data_dir),
            status=ProfileStatus.locked,
            expires_at=utcnow() + CLEAN_TTL,
        )
        try:
            profile.id = await self.repo.create(profile)
        except BaseException:
            shutil.rmtree(user_data_dir, ignore_errors=True)
            raise
        return profile

    async def lease(self, profile_id: int, ttl_seconds: int) -> bool:
        """Lock a free profile for ``ttl_seconds``; ``False`` if it was not free."""
        return await self.repo.lease(profile_id, until=utcnow() + timedelta(seconds=ttl_seconds))

    async def release(self, profile_id: int) -> None:
        """Free a persistent profile; a clean profile becomes ``expired`` (folder kept)."""
        profile = await self.repo.get(profile_id)
        if profile is None:
            return
        if profile.kind == ProfileKind.clean:
            await self.repo.update(profile_id, status=ProfileStatus.expired, lease_until=None)
        else:
            await self.repo.release(profile_id)

    async def cleanup_clean(self, now: datetime) -> int:
        """Delete folders and rows of clean profiles whose ``expires_at`` has passed."""
        removed = 0
        for profile in await self.repo.list():
            if profile.kind != ProfileKind.clean:
                continue
            if profile.expires_at is None or profile.expires_at >= now:
                continue
            shutil.rmtree(profile.user_data_dir, ignore_errors=True)
            await self.repo.delete(profile.id)
            removed += 1
        return removed

    async def delete(self, profile_id: int) -> None:
        """Remove a profile and its folder; refuses a locked persistent profile."""
        profile = await self.repo.get(profile_id)
        if profile is None:
            return
        if profile.kind == ProfileKind.persistent and profile.status == ProfileStatus.locked:
            raise ValueError(f"profile {profile_id} is locked")
        shutil.rmtree(profile.user_data_dir, ignore_errors=True)
        await self.repo.delete(profile_id)
