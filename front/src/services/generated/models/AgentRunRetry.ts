/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Create a new full attempt only after acknowledging uncertain prior provider effects.
 */
export type AgentRunRetry = {
    request_key: string;
    expected_price_fingerprint: string;
    acknowledge_duplicate_charge: boolean;
};

