/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * An ordered video interval; endpoints use source video-zero milliseconds.
 */
export type EditClip = {
    shot_id: string;
    take_id: string;
    source_in_ms: number;
    source_out_ms: number;
    source_audio?: boolean;
};

