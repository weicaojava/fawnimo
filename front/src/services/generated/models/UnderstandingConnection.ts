/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Select managed input models; old endpoint fields remain readable for historical journals only.
 */
export type UnderstandingConnection = {
    enabled?: boolean;
    base_url?: string;
    vision_model?: string;
    transcribe_audio?: boolean;
    audio_model?: string;
    audio_base_url?: string;
    credential_env?: string;
    audio_credential_env?: string;
    vision_model_id?: string;
    audio_model_id?: string;
    max_frames?: number;
    timeout_seconds?: number;
    max_output_tokens?: number;
};

