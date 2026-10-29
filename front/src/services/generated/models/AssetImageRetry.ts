/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Create a distinct attempt after acknowledging uncertain remote charges.
 */
export type AssetImageRetry = {
    request_key: string;
    expected_price_fingerprint: string;
    acknowledge_duplicate_charge: boolean;
};

