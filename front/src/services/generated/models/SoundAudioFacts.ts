/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Actual fully decoded PCM16 facts on a contiguous source sample clock.
 */
export type SoundAudioFacts = {
    sample_rate: 24000 | 48000;
    channels: 1 | 2;
    sample_frames: number;
    duration_us: number;
    coverage_us: Array<Array<number>>;
    peak_pcm16: number;
    rms_pcm16: number;
    envelope_pcm16: Array<number>;
};

