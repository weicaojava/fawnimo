/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Human-declared interaction/action/camera evidence interval.
 */
export type BenchmarkEvent = {
    id: string;
    kind: 'interaction' | 'action' | 'camera_change';
    description?: string;
    start_frame: number;
    end_frame: number;
};

