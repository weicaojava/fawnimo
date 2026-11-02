/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ImageModelChoice } from './ImageModelChoice';
/**
 * Distinguish this limited image protocol from legacy Film and video generation.
 */
export type AssetImageAvailability = {
    available: boolean;
    message: string;
    default_model_id: (string | null);
    models: Array<ImageModelChoice>;
    protocol?: 'openai_b64_v1' | 'volcengine_seedream_json_v1';
    supports_references?: boolean;
    real_model_qualified?: boolean;
};

