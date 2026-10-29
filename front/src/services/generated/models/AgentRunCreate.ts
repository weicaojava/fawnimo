/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Require an explicit bounded model invocation using a saved Agent revision.
 */
export type AgentRunCreate = {
    request_key: string;
    project_id: string;
    expected_price_fingerprint: string;
    input_text: string;
    model_id?: (string | null);
    expected_version: number;
    max_tokens?: number;
    confirm_model_call: boolean;
};

