/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Bind a single submission to the exact displayed source and connection revisions.
 */
export type UnderstandingCreate = {
    analysis_id: string;
    expected_analysis_revision: number;
    expected_connection_revision: number;
    request_key: string;
    confirm_model_call: boolean;
    expected_price_fingerprints: Record<string, string>;
};

