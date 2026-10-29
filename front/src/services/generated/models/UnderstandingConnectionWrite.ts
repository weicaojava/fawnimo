/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Save the visible dedicated configuration without overwriting another editor's changes.
 */
export type UnderstandingConnectionWrite = {
    enabled?: boolean;
    base_url?: string;
    vision_model?: string;
    transcribe_audio?: boolean;
    audio_model?: string;
    audio_base_url?: string;
    credential_env?: string;
    audio_credential_env?: string;
    max_frames?: number;
    timeout_seconds?: number;
    max_output_tokens?: number;
    expected_revision: number;
};

