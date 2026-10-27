/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Candidate provenance only; output is not automatically adopted.
 */
export type PlannedJobRead = {
    job_id: string;
    task_id: string;
    plan_id: string;
    shot_id: string;
    entry_hash: string;
    state: string;
    reason: string;
    media_id: (string | null);
    media_available: boolean;
};

