/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Expose configuration readiness without credential values, paths or upstream error bodies.
 */
export type VideoRuntimeSetupRead = {
    configured: boolean;
    enabled: boolean;
    running: boolean;
    issue_code: string;
    message: string;
    model_id: string;
    durations: Array<number>;
    quote_minor: (number | null);
    quote_note: string;
    iteration_limit: number;
    credential_present: boolean;
    output_description: string;
    real_model_qualified?: boolean;
    legacy_actions_available?: boolean;
};

