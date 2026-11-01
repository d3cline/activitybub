# Changelog

## 0.3.1

- Direction-aware preview preloading: visible posts first, two viewports ahead,
  and one behind. Queued images are reprioritized after fast scrolling.
- Short, interruptible mouse-wheel easing with direct pixel scrolling for
  touchpads, including native momentum events.
- Wheel travel and reading anchors survive image resizing and rolling-feed trims.
- Coalesced image checks and layout updates, cached card geometry, and a bounded
  16 MiB cache of fitted previews, including high-DPI variants.
- Covered media remains unfetched until revealed. Existing worker, original
  image-cache, and request limits still apply to all preloaded previews.

## 0.3.0

- MAX by default: separate 10 discovery, 16 live-feed, and six history jobs.
- Saved Quiet/Balanced/MAX profiles with CPU, memory, and UI-lag feedback.
- Public federated timeline merging with public-local fallback, shared host
  cooldowns, app-wide request pacing, and deduplication across servers.
- Faster live catch-up, independent history timers, and automatic unread prefetch.
- Unseen arrivals append at the bottom after cached history is exhausted, so
  backfilled posts cannot strand the scroll above its chronological cursor.
- Compact live network/job footer and engine room with rates, latency, errors,
  buffer size, CPU/RAM, and process threads. Counters are local only.
- Batched discovery writes, cached candidate selection, and coalesced UI events.
- Graceful operation when process counters are unavailable; bounded cache, UI
  model, media workers, and request budgets remain in place.
- Offline concurrency benchmark and regression coverage for independent pools,
  rate limits, public-feed fallback, adaptive budgets, and continuous scrolling.

## 0.2.0

- Original 8-bit sunglasses app icon with SVG, PNG, and Windows ICO assets.
- Six complete, persistent palettes, including a light paper theme.
- Local explorer passport: ranks, XP, eight badges, and first-time world/save stamps.
- Surprise me opens a random cached post while respecting your filters and mutes.
- Optional rewards; no accounts, telemetry, streak penalties, or extra network calls.
- Portable Linux/Windows build tooling, wheel/sdist packaging, checksums,
  headless native smoke tests, CI, and a draft GitHub Release workflow.
- Qt Essentials replaces the larger all-modules dependency; existing environments
  can continue using their installed PySide6 modules.
- Frozen-app backend probes now relaunch with a dedicated internal flag.

## 0.1.3

Centered compact feed, viewport-fitted full images, short cards, collapsed
filters, and an on-demand reader for half-screen use.

## 0.1.2

App-drawn clear icon, checked XWayland fallback, raster-only image decoding,
duplicate PNG EXIF cleanup, and background image decoding.

## 0.1.1

Whole-image feed rendering, preserved proportions, and stable scroll anchoring.

## 0.1.0

Initial public-feed desktop explorer with recursive discovery and local caching.
