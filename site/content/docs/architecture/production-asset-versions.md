---
title: Project asset versions and adopted imports
---

The local production workspace reuses existing Project, Chapter, Character, Scene,
Prop, Shot and FileItem identities. The project workbench's asset-version tab is a
real API-backed view; it never uses mock data. ProductionAsset owns append-only
AssetVersion snapshots. Appearance and voice assets belong to an existing project
character; scene/prop/character creation also creates the existing library entity.

ProductionMedia records immutable bytes, hash, size, MIME and private local path.
Names supplied by clients are display data, never storage keys. Uploads are bounded,
signature checked and decoded for a short sample by an explicitly configured local
FFmpeg executable with fixed input formats, no network protocols and a time limit.
This is an ingestion check, not full-film quality or full-duration decode acceptance.

The media-only ZIP v1 requires exactly schema_version, project_id and files; each
file has path and sha256. Absolute/traversal/device/stream paths, links, duplicate
case-folded names, encrypted members, undeclared members, excessive counts, sizes
and compression ratios are rejected before persistent writes. All validated files
are committed together; failures clean only paths created by that import. This is
not a complete project archive/restore implementation.

ProductionTake stores imported output IDs and a frozen set of exact asset-version
IDs, with provenance=user_import. ShotSelection holds current adoption. Selecting
an earlier take changes only this pointer; historical snapshots and media remain.
ShotRead.selected_take exposes the same selection to the original shot studio,
whose preview prioritizes the adopted local content and labels it a user import.
No imported fixture is counted as AI generation.

SQLite guards reject parent reattachment, cross-project references in new and old
image/file/dialogue paths, immutable-row updates/deletes, and extra bindings outside
the frozen snapshot sets. Both asynchronous API and synchronous worker engines
enable foreign keys on every connection. Global legacy libraries remain separate;
they do not grant permission to use another project's ProductionMedia.

The legacy video compiler reads mutable drafts. It explicitly returns conflict for
a shot with frozen adoption until the version-aware compiler and real providers
pass their later gates. This blocks silent drift; it is not generation completion.

Deployment is currently qualified only for local SQLite, one loopback API process.
Run init_db.py for an empty database or migrate_production.py for a copy of the
fixed M1 schema. Migration checks the schema marker, local table columns, existing
foreign-key violations and inconsistent project scopes. Unknown/invalid databases
are refused. In-place destructive downgrade is unsupported; restore a consistent
backup into a separate path. MySQL production constraints are not qualified.
