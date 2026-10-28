/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DnaEvidence } from './DnaEvidence';
/**
 * An explicit unknown, human observation or reconstruction hypothesis.
 */
export type DnaClaim = {
    zh?: (string | null);
    en?: (string | null);
    status?: 'unknown' | 'observed' | 'inferred';
    reason?: string;
    confidence?: (number | null);
    evidence?: Array<DnaEvidence>;
};

