/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * An explicit audio placement on the final output clock, never an automatic retime.
 */
export type SoundTrack = {
    id: string;
    candidate_id: string;
    source_in_ms: number;
    source_out_ms: number;
    timeline_start_ms: number;
    gain_db?: number;
    muted?: boolean;
    dialogue_id?: (string | null);
    content_confirmed?: boolean;
};

