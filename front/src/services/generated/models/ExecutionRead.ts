/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Safe task projection deliberately excluding snapshot, result and remote identity.
 */
export type ExecutionRead = {
    id: string;
    project_id: string;
    state: string;
    state_label: string;
    reason: string;
    cancel_requested: boolean;
    query_retries: number;
    download_retries: number;
    can_resume: boolean;
    can_cancel: boolean;
    can_pause: boolean;
};

