/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Authorize one image, rejecting references and hidden provider routing overrides.
 */
export type AssetImageSubmit = {
    request_key: string;
    project_id: string;
    expected_price_fingerprint: string;
    model_id: string;
    prompt: string;
    size?: '256x256' | '512x512' | '1024x1024';
    confirm_model_call: boolean;
};

