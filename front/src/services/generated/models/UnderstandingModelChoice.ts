/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Show explicit input-protocol capability and non-secret server pricing without loading a saved key.
 */
export type UnderstandingModelChoice = {
    id: string;
    name: string;
    provider_name: string;
    kind: 'video_vision' | 'video_asr';
    configured: boolean;
    priced?: boolean;
    max_call_minor?: (number | null);
    price_fingerprint?: (string | null);
};

