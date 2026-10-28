/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DnaDocument_Output } from './DnaDocument_Output';
import type { DnaReadiness } from './DnaReadiness';
import type { DnaSnapshotSummary } from './DnaSnapshotSummary';
import type { DnaSource } from './DnaSource';
/**
 * Complete source and document fields for a controlled editor.
 */
export type DnaDraft = {
    id: string;
    project_id: string;
    analysis_id: string;
    revision: number;
    source: DnaSource;
    document: DnaDocument_Output;
    readiness: DnaReadiness;
    snapshots: Array<DnaSnapshotSummary>;
};

