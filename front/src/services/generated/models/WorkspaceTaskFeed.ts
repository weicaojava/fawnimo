/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { WorkspaceTaskSource } from './WorkspaceTaskSource';
import type { WorkspaceTaskSummary } from './WorkspaceTaskSummary';
/**
 * Bound each source and the final feed; this is a recent summary, never an all-history count.
 */
export type WorkspaceTaskFeed = {
    items: Array<WorkspaceTaskSummary>;
    sources: Array<WorkspaceTaskSource>;
    complete: boolean;
    limit: number;
    per_source_limit: number;
    read_at_ts: number;
};

