/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { VideoReferenceInput } from './VideoReferenceInput';
/**
 * Keep first-frame, first-and-last-frame and semantic references mutually exclusive.
 */
export type VideoReferenceSelection = {
    schema_version?: number;
    mode: 'reference' | 'first_frame' | 'first_last_frames';
    references: Array<VideoReferenceInput>;
};

