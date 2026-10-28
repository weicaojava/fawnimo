/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DnaIssue } from './DnaIssue';
/**
 * Structural freeze readiness does not assert correctness of the claims.
 */
export type DnaReadiness = {
    can_freeze: boolean;
    issues: Array<DnaIssue>;
    source_current: boolean;
    human_declarations_only?: boolean;
};

