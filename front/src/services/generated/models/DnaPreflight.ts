/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DnaIssue } from './DnaIssue';
/**
 * Availability and current-source hints are separate from historical content identity.
 */
export type DnaPreflight = {
    usable: boolean;
    source_available: boolean;
    asset_versions_available: boolean;
    source_current: boolean;
    issues: Array<DnaIssue>;
    human_declarations_only?: boolean;
};

