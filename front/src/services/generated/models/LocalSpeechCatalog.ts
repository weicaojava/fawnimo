/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { LocalSpeechVoice } from './LocalSpeechVoice';
/**
 * An unavailable offline engine never silently falls back to a paid service.
 */
export type LocalSpeechCatalog = {
    enabled: boolean;
    reason: string;
    profile?: string;
    external_fee_fen?: number;
    voices: Array<LocalSpeechVoice>;
};

