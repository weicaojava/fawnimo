/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { VideoReferenceSelection } from './VideoReferenceSelection';
/**
 * Select explicit immutable versions and owned media for one actual shot.
 */
export type PlanShotInput_Input = {
    shot_id: string;
    version_ids?: Array<string>;
    reference_media_ids?: Array<string>;
    video_reference?: (VideoReferenceSelection | null);
    prompt_addition?: string;
};

