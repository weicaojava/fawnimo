/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Expose explicitly supported managed native-video metadata and pricing without reading any credential value.
 */
export type NativeVideoModelChoice = {
    id: string;
    name: string;
    provider_name: string;
    configured: boolean;
    message: string;
    priced?: boolean;
    max_call_minor?: (number | null);
    price_fingerprint?: (string | null);
};

