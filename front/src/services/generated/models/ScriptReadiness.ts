/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ScriptCoverage } from './ScriptCoverage';
import type { ScriptIssue } from './ScriptIssue';
/**
 * Checklist completeness and current usability without real generation claims.
 */
export type ScriptReadiness = {
    can_publish: boolean;
    issues: Array<ScriptIssue>;
    coverage: ScriptCoverage;
    requirement_changes: Array<string>;
    latest_snapshot_usable: boolean;
};

