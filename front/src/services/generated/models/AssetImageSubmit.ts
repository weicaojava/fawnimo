/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AssetImageReferenceInput } from './AssetImageReferenceInput';
/**
 * Authorize one image with exact optional project references and a declared result format, never routing overrides.
 */
export type AssetImageSubmit = {
    request_key: string;
    project_id: string;
    expected_price_fingerprint: string;
    model_id: string;
    prompt: string;
    size?: '256x256' | '512x512' | '1024x1024' | '2048x2048' | '2848x1600' | '1600x2848' | '1536x1024' | '1024x1536';
    references?: Array<AssetImageReferenceInput>;
    response_format?: 'b64_json' | 'url';
    confirm_model_call: boolean;
};

