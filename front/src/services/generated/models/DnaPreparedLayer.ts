/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DnaPreparedClaim } from './DnaPreparedClaim';
/**
 * One of seven ordered bilingual recipe layers with its original claim identities.
 */
export type DnaPreparedLayer = {
    key: 'GLOBAL_STYLE_PROMPT' | 'CHARACTER_LOCK_PROMPT' | 'SCENE_LOCK_PROMPT' | 'SHOT_IMAGE_PROMPT' | 'SHOT_VIDEO_PROMPT' | 'NEGATIVE_CONSTRAINTS' | 'AUDIO_AND_EDITING_PROMPTS';
    zh: (string | null);
    en: (string | null);
    claims: Array<DnaPreparedClaim>;
};

