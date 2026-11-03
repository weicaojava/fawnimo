/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Generate the exact original frozen dialogue using one explicitly selected voice.
 */
export type LocalSpeechRequest = {
    request_key: string;
    plan_id: string;
    dialogue_id: string;
    voice_id: string;
    rate?: number;
    profile: 'windows_sapi_pcm_v1' | 'linux_espeak_pcm_v1';
};

