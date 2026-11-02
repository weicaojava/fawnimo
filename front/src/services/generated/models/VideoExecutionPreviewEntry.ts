/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PlanIssue } from './PlanIssue';
/**
 * Inspect text wire bodies or an explicitly labelled reference metadata summary, never inline media or delivery tokens.
 */
export type VideoExecutionPreviewEntry = {
    shot_id: string;
    entry_hash: string;
    body: (Record<string, any> | null);
    request_summary?: (Record<string, any> | null);
    input_validation?: (string | null);
    issues: Array<PlanIssue>;
};

