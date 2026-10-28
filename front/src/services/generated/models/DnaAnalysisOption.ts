/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DnaIssue } from './DnaIssue';
/**
 * A bounded real R1 analysis and its non-authoritative create checklist.
 */
export type DnaAnalysisOption = {
    id: string;
    media_id: string;
    name: string;
    state: string;
    revision: number;
    source_hash: string;
    frame_count: number;
    confirmed_cut_indices: Array<number>;
    can_create: boolean;
    issues: Array<DnaIssue>;
};

