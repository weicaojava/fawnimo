/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ScriptIssue } from './ScriptIssue';
/**
 * Reject stale preparation without claiming provider or video readiness.
 */
export type ScriptPreflight = {
    usable: boolean;
    issues: Array<ScriptIssue>;
};

