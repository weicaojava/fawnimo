/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DnaImpactReason } from './DnaImpactReason';
/**
 * Comparison is an advisory dependency report, never an automatic rewrite or regeneration.
 */
export type DnaCompareRead = {
    base_snapshot_id: string;
    other_snapshot_id: string;
    changed_paths: Array<string>;
    affected_shot_ids: Array<string>;
    dependency_index: Record<string, Array<string>>;
    reasons: Array<DnaImpactReason>;
    execution_enabled?: boolean;
};

