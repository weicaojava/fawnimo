/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Expose configuration guidance without URLs, model parameters or credentials.
 */
export type ImageModelChoice = {
    id: string;
    name: string;
    configured: boolean;
    message: string;
    priced?: boolean;
    max_call_minor?: (number | null);
    price_fingerprint?: (string | null);
    protocol?: ('openai_b64_v1' | 'volcengine_seedream_json_v1' | null);
    supports_references?: boolean;
    max_reference_images?: number;
    allowed_sizes?: Array<string>;
    response_formats?: Array<'b64_json' | 'url'>;
};

