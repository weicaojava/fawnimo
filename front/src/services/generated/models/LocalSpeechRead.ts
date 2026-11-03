/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { LocalSpeechVoice } from './LocalSpeechVoice';
import type { SoundCandidateRead } from './SoundCandidateRead';
/**
 * An original durable local job; completed output remains a candidate until separately adopted.
 */
export type LocalSpeechRead = {
    id: string;
    project_id: string;
    chapter_id: string;
    task_id: string;
    state: 'queued' | 'running' | 'ready' | 'failed' | 'cancelled';
    reason: string;
    attempts: number;
    plan_id: string;
    dialogue_id: string;
    text: string;
    speaker_id: (string | null);
    voice: LocalSpeechVoice;
    rate: number;
    profile: 'windows_sapi_pcm_v1' | 'linux_espeak_pcm_v1';
    external_fee_fen?: number;
    candidate?: (SoundCandidateRead | null);
};

