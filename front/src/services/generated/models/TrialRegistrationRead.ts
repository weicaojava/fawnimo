/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Explain finite registration availability, trial duration and project count; these are resource counts, not a shared money budget.
 */
export type TrialRegistrationRead = {
    capacity: number;
    remaining: number;
    duration_seconds: number;
    project_limit: number;
};

