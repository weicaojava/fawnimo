/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Expose only the effective trusted expiry and active-space project count, without credentials or other users' identities.
 */
export type TrialAccessRead = {
    profile?: string;
    expires_at: number;
    read_only: boolean;
    project_limit?: (number | null);
};

