---
title: Remaining version-aware generation work
---

The asset-version and imported-take persistence layer is implemented. It does not
establish real model capability or a complete generated drama workflow.

M3A must freeze provider capabilities and request parameters, persist submission
attempts before sending, distinguish unknown acceptance from safe rejection,
reconcile polling/download retries and test worker recovery without duplicate sends.
The subsequent modality gates require real authenticated text/image/video/audio/
visual-analysis/ASR requests. Missing credentials remain external blockers.

The qualified compiler must consume exact AssetVersion snapshots and media hashes;
the legacy mutable compiler currently rejects already adopted shots. Future work
must replace that block with a validated immutable GenerationPlan path, without
rewriting history or counting imported fixtures as model outputs.

Complete project/media archives, Video DNA, rational media time mapping, original
and reconstruction quality, audiovisual synchronization and joint release retain
their separate acceptance gates. SQLite-only M2 constraints do not qualify MySQL,
multi-worker execution, network sharing or distributed object storage.
