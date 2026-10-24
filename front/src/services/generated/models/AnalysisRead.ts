/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ConfirmedCut } from './ConfirmedCut';
/**
 * Safe technical report with separate machine candidates and confirmed edits.
 */
export type AnalysisRead = {
    id: string;
    media_id: string;
    state: string;
    source_hash: string;
    revision: number;
    manifest: (Record<string, any> | null);
    confirmed_cuts: (Array<ConfirmedCut> | null);
    reason: string;
};

