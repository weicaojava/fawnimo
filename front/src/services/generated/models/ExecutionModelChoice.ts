/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Expose selectable model identities without provider URLs, parameters or credentials.
 */
export type ExecutionModelChoice = {
    id: string;
    name: string;
    provider_name: string;
    configured: boolean;
    message: string;
    priced?: boolean;
    max_call_minor?: (number | null);
    price_fingerprint?: (string | null);
};

