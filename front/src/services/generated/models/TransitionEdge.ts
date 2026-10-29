/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * One actual adjacent boundary; crossfade changes output time, never original source time.
 */
export type TransitionEdge = {
    left_shot_id: string;
    right_shot_id: string;
    kind?: 'hard_cut' | 'crossfade';
    duration_ms?: number;
};

