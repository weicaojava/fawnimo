/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { BenchmarkDocument } from './BenchmarkDocument';
import type { Readiness } from './Readiness';
import type { SnapshotSummary } from './SnapshotSummary';
/**
 * Safe draft projection with no internal filesystem or runtime state.
 */
export type BenchmarkRead = {
    id: string;
    analysis_id: string;
    revision: number;
    document: BenchmarkDocument;
    readiness: Readiness;
    snapshots: Array<SnapshotSummary>;
};

