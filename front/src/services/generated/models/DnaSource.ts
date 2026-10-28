/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DnaFrame } from './DnaFrame';
/**
 * Server-owned frozen source; frame N is a sentinel with unknown terminal time.
 */
export type DnaSource = {
    analysis_id: string;
    analysis_revision: number;
    media_id: string;
    sha256: string;
    manifest_hash: string;
    frame_clock_hash: string;
    frames: Array<DnaFrame>;
    confirmed_cut_indices: Array<number>;
    audio_present: boolean;
    terminal_time_known?: boolean;
};

