/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * ASR hypotheses retain both original audio-relative times and their original-frame mapping.
 */
export type UnderstandingTranscript = {
    id: string;
    text: string;
    audio_start_us: number;
    audio_end_us: number;
    video_start_us: number;
    video_end_us: number;
    start_frame: (number | null);
    end_frame: (number | null);
};

