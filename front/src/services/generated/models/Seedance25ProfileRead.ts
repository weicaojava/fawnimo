/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Describe this software slice separately from the provider's wider native abilities.
 */
export type Seedance25ProfileRead = {
    model_id: string;
    profile_hash: string;
    preparation_binding_hash: string;
    mode?: string;
    min_duration?: number;
    max_duration?: number;
    ratio?: string;
    resolution?: string;
    output_format?: string;
    generate_audio?: boolean;
    watermark?: boolean;
    execution_enabled?: boolean;
    model_qualified?: boolean;
    quote_minor?: null;
    currency?: string;
    unsupported_modes: Array<string>;
    message: string;
};

