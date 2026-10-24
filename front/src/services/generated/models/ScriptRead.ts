/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ScriptDocument } from './ScriptDocument';
import type { ScriptReadiness } from './ScriptReadiness';
import type { ScriptSnapshotSummary } from './ScriptSnapshotSummary';
/**
 * Safe preparation draft and historical snapshot summaries.
 */
export type ScriptRead = {
    id: string;
    chapter_id: string;
    revision: number;
    document: ScriptDocument;
    readiness: ScriptReadiness;
    snapshots: Array<ScriptSnapshotSummary>;
};

