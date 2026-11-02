/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { SoundAudioFacts } from './SoundAudioFacts';
/**
 * Verified candidate facts; selection still requires a separate timeline edit.
 */
export type SoundCandidateRead = {
    id: string;
    project_id: string;
    chapter_id: string;
    media_id: string;
    sha256: string;
    kind: 'voice' | 'effect' | 'music';
    name: string;
    origin: 'user_import' | 'local_speech';
    audio: SoundAudioFacts;
    model_quality_qualified?: boolean;
};

