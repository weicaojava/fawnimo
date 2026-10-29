/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PlanIssue } from './PlanIssue';
/**
 * Inspect the actual fixed wire body from an owned immutable compiled entry.
 */
export type VideoExecutionPreviewEntry = {
    shot_id: string;
    entry_hash: string;
    body: (Record<string, any> | null);
    issues: Array<PlanIssue>;
};

